# Examples

Each of these assumes OpenMetric is running (`openmetric serve`) and that you have issued
a virtual key:

```bash
openmetric vkey create --name my-app --project my-app --use-case llm-summary
export OPENMETRIC_KEY=om_live_...
```

| File | What it shows |
|---|---|
| [`openai_sdk.py`](openai_sdk.py) | The two-line change to an existing OpenAI/OpenRouter integration |
| [`multi_project.py`](multi_project.py) | One virtual key serving several projects and use cases |
| [`scraping_api.py`](scraping_api.py) | Non-LLM APIs (Firecrawl, Serper) through the generic proxy |
| [`node_openai.mjs`](node_openai.mjs) | The same change in JavaScript |
| [`curl.sh`](curl.sh) | No SDK at all |
| [`query_api.py`](query_api.py) | Reading your own usage data back out programmatically |

None of these contain real keys. They read from the environment.
