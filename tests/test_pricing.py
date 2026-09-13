from openmetric import pricing


def test_exact_model_match():
    cost, source = pricing.price_tokens("gpt-4o-mini", 1_000_000, 1_000_000)
    assert source == pricing.CATALOG
    assert cost == round(0.15 + 0.60, 8)


def test_vendor_prefixed_model_resolves_to_base_rate():
    prefixed, _ = pricing.price_tokens("anthropic/claude-sonnet-4", 1_000_000, 0)
    plain, _ = pricing.price_tokens("claude-sonnet-4", 1_000_000, 0)
    assert prefixed == plain == 3.0


def test_openrouter_variant_suffix_is_stripped():
    cost, source = pricing.price_tokens("anthropic/claude-sonnet-4:beta", 1_000_000, 0)
    assert (cost, source) == (3.0, pricing.CATALOG)


def test_unknown_model_is_reported_not_zeroed_silently():
    cost, source = pricing.price_tokens("nobody/knows-this-model", 500, 500)
    assert cost == 0.0
    assert source == pricing.UNKNOWN


def test_cached_tokens_are_discounted():
    full, _ = pricing.price_tokens("gpt-4o", 1_000_000, 0)
    cached, _ = pricing.price_tokens("gpt-4o", 1_000_000, 0, cached_tokens=1_000_000)
    assert cached < full


def test_unpriced_provider_returns_unknown():
    cost, source, unit = pricing.price_units("firecrawl", 5)
    assert (cost, source) == (0.0, pricing.UNKNOWN)
    assert unit == "request"


def test_local_override_makes_a_provider_priced():
    pricing.set_local_price(provider="firecrawl", per_request=0.002)
    cost, source, _ = pricing.price_units("firecrawl", 5)
    assert source == pricing.CATALOG
    assert cost == 0.01


def test_local_override_can_add_an_unknown_model():
    pricing.set_local_price(model="nobody/knows-this-model", input_rate=1.0, output_rate=2.0)
    cost, source = pricing.price_tokens("nobody/knows-this-model", 1_000_000, 1_000_000)
    assert (cost, source) == (3.0, pricing.CATALOG)


def test_prefix_pattern_is_a_last_resort():
    cost, source = pricing.price_tokens("gpt-4o-2024-11-20", 1_000_000, 0)
    assert (cost, source) == (2.5, pricing.CATALOG)


def test_price_changes_are_picked_up_without_restarting_the_process():
    """A running gateway must see `openmetric price set`, not need a restart.

    The CLI tells you "new calls will be priced with it". Before this was fixed,
    that was false for an already-running server: the catalog was cached for the
    life of the process, so every call kept recording as unpriced.
    """
    import yaml

    assert pricing.price_units("firecrawl", 1)[1] == pricing.UNKNOWN  # warms the cache

    # Another process (the CLI) writes the override. No reload call here on purpose.
    pricing.local_pricing_path().write_text(
        yaml.safe_dump({"providers": {"firecrawl": {"unit_price_usd": 0.002}}})
    )
    cost, source, _ = pricing.price_units("firecrawl", 1)
    assert (cost, source) == (0.002, pricing.CATALOG)


def test_a_later_edit_to_the_same_file_is_also_seen():
    import yaml

    path = pricing.local_pricing_path()
    path.write_text(yaml.safe_dump({"providers": {"serper": {"unit_price_usd": 0.001}}}))
    assert pricing.price_units("serper", 1)[0] == 0.001

    path.write_text(
        yaml.safe_dump({"providers": {"serper": {"unit_price_usd": 0.25, "unit_kind": "search"}}})
    )
    cost, source, unit = pricing.price_units("serper", 1)
    assert (cost, source, unit) == (0.25, pricing.CATALOG, "search")


def test_a_provider_absent_from_the_catalog_can_be_priced_entirely_from_the_override():
    """Any API you add yourself is priceable - nothing needs to ship in catalog.yaml."""
    assert pricing.provider_defaults("some-api-nobody-shipped") == {}
    pricing.set_local_price(provider="some-api-nobody-shipped", per_request=0.004)
    cost, source, _ = pricing.price_units("some-api-nobody-shipped", 3)
    assert (cost, source) == (0.012, pricing.CATALOG)
