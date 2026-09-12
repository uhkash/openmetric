"""Non-LLM APIs go through the generic proxy and are measured in requests.

openmetric key add firecrawl --label firecrawl-main
openmetric key add serper --label serper-main
export OPENMETRIC_KEY=om_live_...
python examples/scraping_api.py
"""

import os

import httpx

GATEWAY = "http://localhost:8099"
HEADERS = {
    "Authorization": f"Bearer {os.environ['OPENMETRIC_KEY']}",
    "X-OpenMetric-Project": "market-research",
}

# /proxy/{provider}/{the provider's own path}
scrape = httpx.post(
    f"{GATEWAY}/proxy/firecrawl/v1/scrape",
    headers={**HEADERS, "X-OpenMetric-Use-Case": "web-scraping"},
    json={"url": "https://example.com", "formats": ["markdown"]},
    timeout=120,
)
print("firecrawl:", scrape.status_code)

search = httpx.post(
    f"{GATEWAY}/proxy/serper/search",
    headers={**HEADERS, "X-OpenMetric-Use-Case": "search"},
    json={"q": "openmetric api gateway"},
    timeout=60,
)
print("serper:", search.status_code)

# These providers bill in credits, and credit rates differ per plan, so OpenMetric
# reports them as *unpriced* until you tell it your effective rate:
#
#     openmetric price set firecrawl --per-request 0.002
#     openmetric price set serper --per-request 0.001
#
# Until then they appear under `openmetric blindspots` rather than silently as $0.
