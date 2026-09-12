# Is this a real problem? An honest look at the market

This document exists so nobody has to take the README's word for it. It covers what
already solves the problem OpenMetric targets, where those tools stop, and the case
for building this at all. Sources are linked; check them.

*Last reviewed: September 2026.*

## The short version

- **The core problem is validated, not hypothetical.** "Change one base URL, get cost per
  key / project / feature" is exactly what LiteLLM, Helicone, Portkey and Cloudflare AI
  Gateway sell, and providers themselves now ship per-key spend and project budgets.
  Nobody builds four of the same product for an imaginary need.
- **The space consolidated hard in early 2026**, and the tool that served solo builders
  best stopped shipping. That opened a gap.
- **OpenMetric's slot is narrow and specific:** one person or a small team, several
  projects, several providers *including non-LLM ones*, self-hosted in one command,
  with a dashboard that reports what its own numbers are missing.
- **The honest risk:** if everything you use is OpenRouter, OpenRouter's own per-key
  dashboard covers most of this. The value here is cross-provider, non-LLM, local, and
  Blindspots. If you don't need those, you may not need this.

## What already exists

### LiteLLM (open source proxy)

The most widely adopted open-source LLM proxy. It has [virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys)
issued instead of provider keys, [spend tracking](https://docs.litellm.ai/docs/proxy/cost_tracking)
per key, user, team, org and tag across 100+ providers, and [tag budgets](https://docs.litellm.ai/docs/proxy/tag_budgets)
that reset daily or monthly. That is, feature for feature, the LLM half of OpenMetric,
built by a larger team.

Where it stops: it is enterprise-shaped. It [requires Postgres](https://docs.litellm.ai/docs/proxy/virtual_keys)
for key management and budget tracking, the config surface is large, and the
[pricing structure](https://www.truefoundry.com/blog/litellm-pricing-guide) is aimed at
platform teams. It is LLM-only: your scraping, search and email APIs are invisible to it.
And it reports spend; it does not report what the spend figure is missing.

### Helicone (observability-first proxy)

Helicone was the closest thing to OpenMetric's user experience: change the base URL,
[every request is logged with tokens, cost, latency, status, users and custom properties](https://docs.helicone.ai/references/open-source),
self-hostable with one Docker image. By early 2026 it had
[served roughly 16,000 organisations and processed 14.2 trillion tokens](https://agentping.io/blog/helicone-acquisition-what-it-means).
That is the single best piece of evidence that the "change one line, see your costs"
product has real demand.

Then, in March 2026, [Helicone joined Mintlify](https://www.helicone.ai/blog/joining-mintlify).
The service is [in maintenance mode: security patches and new models continue, but active
feature development has ended](https://chatforest.com/reviews/helicone-llm-observability-gateway/),
and Mintlify has committed to helping customers [migrate to other platforms](https://www.mintlify.com/blog/mintlify-acquires-helicone).
"Moving off Helicone" is now [a live search query](https://agentping.io/from-helicone).

### Portkey, Cloudflare AI Gateway, Bifrost, Kong

Portkey [was acquired by Palo Alto Networks in May 2026](https://www.edenai.co/post/best-portkey-alternatives-7-ai-gateways-compared)
and is heading toward enterprise security. Cloudflare AI Gateway is
[zero-ops at the edge](https://ecorpit.com/ai-gateway-comparison-litellm-cloudflare-kong-bifrost-2026/)
but your traffic and keys live with Cloudflare. Bifrost is a
[Go gateway for Kubernetes-native enterprise environments](https://www.edenai.co/post/best-portkey-alternatives-7-ai-gateways-compared).
Kong is for governing APIs and AI together across an organisation. None of these is
aimed at a person with four side projects and a laptop.

### The providers themselves

OpenAI [added per-API-key usage and spend tracking, with monthly limits at the
organisation or project level](https://www.toriihq.com/articles/how-to-monitor-spending-openai).
OpenRouter's advice for people with several projects is to
[use separate API keys so spend segments itself in their dashboard](https://coworker.ai/blog/openrouter-pricing),
and its blog covers [spend controls for teams](https://openrouter.ai/blog/insights/governing-team-ai-spend/).

This is the strongest validation of all: providers only build spend-attribution
features because customers keep asking for them. It is also the clearest limit on
any gateway's value: **each provider can only see itself.** OpenAI's dashboard cannot
tell you what your OpenRouter key spent, and neither can tell you what Firecrawl did.

### General API analytics (Moesif, Treblle)

These are [built for API *producers*](https://www.moesif.com/blog/monitoring/How-Moesif-API-Observability-Compares-To-Treblle/):
companies that sell an API and want to see what their customers do with it. They are not
for the consumer side, which is where OpenMetric sits.

## Where the gap is

Put together:

| Need | LiteLLM | Helicone | Provider dashboards | OpenMetric |
|---|---|---|---|---|
| Cost per project / use case | yes | yes (was) | per key, one provider only | yes |
| Several keys per provider, attributed | yes | partial | yes, within that provider | yes |
| Non-LLM APIs in the same ledger | no | no | no | yes |
| Runs from one command, SQLite, no infra | no | no (Docker stack) | n/a | yes |
| Still actively developed | yes | **no** | yes | yes |
| Reports what the numbers are *missing* | no | no | no | **Blindspots** |
| Local; nothing leaves your machine | self-host | self-host | no | yes |

Three things are not served anywhere else:

1. **Non-LLM spend in the same view.** A vibe-coded product uses an LLM, a scraper, a
   search API and an email API. Every gateway above stops at the LLM.
2. **Zero-infrastructure self-hosting.** `pip install`, one command, SQLite. LiteLLM's
   own docs say Postgres is required for the features that matter here.
3. **Blindspots.** Every tool reports spend. None reports untagged spend, unpriced calls
   counted as $0, idle keys, keys shared across projects, or spend burned on failed
   calls. That is the difference between a dashboard and an answer.

## Who this is for, and who it is not for

**For:** one person or a small team; more than one project; more than one provider;
wants an answer to "what did project X cost" without running a platform.

**Not for:** an enterprise platform team (use LiteLLM or Kong); someone who wants
zero-ops and doesn't mind traffic leaving their network (use Cloudflare); someone who
only uses one provider and one key per project (that provider's dashboard is enough).

## What we borrowed, and from whom

- **From Helicone:** the "change one line" integration, the first-run "waiting for your
  first request" state, per-request custom dimensions.
- **From LiteLLM:** virtual keys, budgets, tags on the key so every request inherits them.
- **From Grafana:** [one time-range picker scoping everything below it](https://grafana.com/docs/grafana/latest/visualizations/dashboards/use-dashboards/),
  [drill-down from an overview into a pre-filtered detail view](https://grafana.com/docs/grafana/latest/visualizations/simplified-exploration/metrics/drill-down-metrics/),
  URL-addressable dashboard state, [empty states that teach setup](https://github.com/grafana/grafana/issues/63175),
  and the general principle that a dashboard should be
  [obvious without explanation](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/).

## What would make this wrong

If any of the following happens, revisit whether OpenMetric should exist:

- LiteLLM ships a SQLite-only, single-command mode aimed at individuals.
- A provider (OpenRouter is the obvious one) adds passthrough for arbitrary non-LLM APIs
  with unified billing.
- Helicone's open-source code is forked and revived by an active maintainer.

None of these has happened as of this review.
