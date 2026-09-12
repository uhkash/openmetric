# Contributing

Contributions are welcome. Two kinds are especially useful and easy:

## 1. Price updates

Model prices drift constantly and the built-in catalog goes stale. Fixing a rate is a
one-line change to [`src/openmetric/data/catalog.yaml`](src/openmetric/data/catalog.yaml):

```yaml
token_prices:
  gpt-4o: { input: 2.50, output: 10.00 }   # USD per 1M tokens
```

Please link the provider's public pricing page in the pull request so the number can be
checked. Prices are list prices in USD; do not add rates specific to your own contract.

## 2. New providers

Also one file. Add an entry to the same catalog:

```yaml
providers:
  myprovider:
    name: My Provider
    base_url: https://api.myprovider.com/v1
    kind: llm          # llm | scrape | search | other
    auth_style: bearer # bearer | header | query | basic
    env_var: MYPROVIDER_API_KEY
```

If the provider reports usage in a shape OpenMetric does not yet parse, add it to
`extract_usage()` in `src/openmetric/usage.py` with a test in `tests/test_usage.py`.

**Never include a real API key** in an example, a test, or a fixture. Tests use obviously
fake values like `sk-or-v1-THIS-IS-A-TEST-KEY-000000000000`.

## Development setup

```bash
git clone https://github.com/uhkash/openmetric.git
cd openmetric
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest              # the suite runs offline; upstream calls are mocked with respx
ruff check .
ruff format .
```

## Pull requests

- One concern per PR.
- Add a test for behaviour changes. The security tests in `tests/test_security.py` are
  not optional — if you touch credential handling, extend them.
- Keep the no-runtime-JS-dependencies rule for the dashboard: it must work offline.
- Run `pytest` and `ruff check .` before pushing.

## Design principles

1. **A wrong number is worse than no number.** Anything that cannot be priced is reported
   as unpriced, never silently counted as zero.
2. **Secrets have exactly one home.** An encrypted column. Everything else sees a hint.
3. **Local by default.** No telemetry, no account, no outbound call that is not a proxied
   request the user asked for.
4. **Boring and inspectable.** SQLite, plain SQL, no build step. Someone should be able to
   read this codebase in an afternoon.
