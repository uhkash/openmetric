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
