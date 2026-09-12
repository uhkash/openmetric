from openmetric import usage


def test_openai_style_usage():
    parsed = usage.extract_usage(
        {"model": "gpt-4o", "usage": {"prompt_tokens": 120, "completion_tokens": 45}}
    )
    assert (parsed.input_tokens, parsed.output_tokens, parsed.model) == (120, 45, "gpt-4o")


def test_anthropic_style_usage():
    parsed = usage.extract_usage(
        {"model": "claude-sonnet-4", "usage": {"input_tokens": 200, "output_tokens": 80}}
    )
    assert (parsed.input_tokens, parsed.output_tokens) == (200, 80)


def test_anthropic_cache_tokens_are_added_to_input():
    parsed = usage.extract_usage(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 10,
                "cache_read_input_tokens": 400,
                "cache_creation_input_tokens": 50,
            }
        }
    )
    assert parsed.input_tokens == 550
    assert parsed.cached_tokens == 400


def test_gemini_style_usage():
    parsed = usage.extract_usage(
        {"usageMetadata": {"promptTokenCount": 33, "candidatesTokenCount": 12}}
    )
    assert (parsed.input_tokens, parsed.output_tokens) == (33, 12)


def test_openrouter_reported_cost_is_captured():
    parsed = usage.extract_usage(
        {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0123}}
    )
    assert parsed.upstream_cost_usd == 0.0123


def test_sse_stream_usage_is_found_in_the_final_chunk():
    chunks = [
        b'data: {"model":"gpt-4o","choices":[{"delta":{"content":"hi"}}]}\n\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":90,"completion_tokens":31}}\n\n',
        b"data: [DONE]\n\n",
    ]
    parsed = usage.extract_usage_from_sse(chunks)
    assert (parsed.input_tokens, parsed.output_tokens, parsed.model) == (90, 31, "gpt-4o")


def test_malformed_sse_lines_are_skipped():
    parsed = usage.extract_usage_from_sse(
        [b"data: not-json\n\n", b'data: {"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n']
    )
    assert parsed.input_tokens == 5


def test_units_counted_for_non_llm_providers():
    assert usage.guess_units("search", {"organic": [1, 2, 3]}) == 3.0
    assert usage.guess_units("scrape", {"ok": True}) == 1.0
    assert usage.guess_units("llm", {"results": [1, 2]}) == 0.0


def test_operation_label():
    assert usage.operation_from_path("chat/completions") == "chat"
    assert usage.operation_from_path("embeddings") == "embedding"
    assert usage.operation_from_path("v1/scrape") == "scrape"
