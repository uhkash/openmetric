#!/usr/bin/env bash
# No SDK required - it is an HTTP proxy.
#   export OPENMETRIC_KEY=om_live_...
#   ./examples/curl.sh
set -euo pipefail

GATEWAY="${GATEWAY:-http://localhost:8099}"

echo "== An LLM call, tagged =="
curl -sS "$GATEWAY/v1/chat/completions" \
  -H "Authorization: Bearer $OPENMETRIC_KEY" \
  -H "X-OpenMetric-Project: shell-scripts" \
  -H "X-OpenMetric-Use-Case: llm-summary" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"Say hi in five words."}]}' \
  | head -c 400
echo

echo "== Streaming works too =="
curl -sSN "$GATEWAY/v1/chat/completions" \
  -H "Authorization: Bearer $OPENMETRIC_KEY" \
  -H "X-OpenMetric-Project: shell-scripts" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-4o-mini","stream":true,"messages":[{"role":"user","content":"Count to three."}]}' \
  | head -5
echo

echo "== What did that cost? =="
curl -sS "$GATEWAY/api/analytics/summary?days=1&project=shell-scripts"
echo
