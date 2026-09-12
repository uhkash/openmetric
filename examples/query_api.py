"""Your usage data is queryable - the dashboard is just one client of this API.

python examples/query_api.py
"""

import httpx

GATEWAY = "http://localhost:8099"
# If you set OPENMETRIC_ADMIN_TOKEN, add:
#   headers={"X-OpenMetric-Admin-Token": os.environ["OPENMETRIC_ADMIN_TOKEN"]}
api = httpx.Client(base_url=GATEWAY, timeout=30)

totals = api.get("/api/analytics/summary", params={"days": 30}).json()
print(
    f"30 days: {totals['requests']:,} calls, ${totals['cost_usd']:.2f}, "
    f"{totals['total_tokens']:,} tokens, p95 {totals['p95_latency_ms']}ms"
)

print("\nSpend by use case:")
for row in api.get("/api/analytics/group", params={"by": "use_case", "days": 30}).json()["rows"]:
    print(f"  {row['label']:<20} ${row['cost_usd']:>8.2f}  {row['requests']:>6,} calls")

print("\nProject x provider:")
matrix = api.get(
    "/api/analytics/matrix",
    params={"rows_by": "project", "cols_by": "provider", "metric": "cost_usd", "days": 30},
).json()
header = " " * 22 + "".join(f"{c:>14}" for c in matrix["cols"])
print(header)
for name, cells in zip(matrix["rows"], matrix["cells"], strict=True):
    print(f"  {name:<20}" + "".join(f"{v:>14.2f}" for v in cells))

print("\nBlindspots:")
for finding in api.get("/api/blindspots", params={"days": 30}).json()["findings"]:
    print(f"  [{finding['severity'].upper():<4}] {finding['title']}")
