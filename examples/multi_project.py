"""One key, several projects - the case OpenMetric is built for.

A virtual key carries a default project. Headers override it per call, so a single
key can serve every project you run without a config file per project.

    export OPENMETRIC_KEY=om_live_...
    python examples/multi_project.py
"""

import os

import httpx

GATEWAY = "http://localhost:8099"
KEY = os.environ["OPENMETRIC_KEY"]

WORK = [
    ("recipe-app", "llm-summary", "openai/gpt-4o-mini", "Summarise: 2 eggs, flour, milk."),
    (
        "client-dashboard",
        "classification",
        "openai/gpt-4o-mini",
        "Is 'refund please' billing or support?",
    ),
    (
        "newsletter-bot",
        "llm-summary",
        "anthropic/claude-sonnet-4",
        "One-line summary of today's AI news.",
    ),
]


def call(project: str, use_case: str, model: str, prompt: str) -> str:
    response = httpx.post(
        f"{GATEWAY}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {KEY}",
            # These two are the whole trick. Projects and use cases are created on
            # first sight, so you can add a tag mid-build with no setup step.
            "X-OpenMetric-Project": project,
            "X-OpenMetric-Use-Case": use_case,
            # Anything else you want to slice by later:
            "X-OpenMetric-Tags": '{"env": "dev"}',
        },
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


for project, use_case, model, prompt in WORK:
    print(f"[{project} / {use_case}] {call(project, use_case, model, prompt)[:80]}")

print("\nNow run:  openmetric report --by use_case")
