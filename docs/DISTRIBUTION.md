# Distribution: getting found on GitHub, and paying for it without poisoning the well

The two goals — be discovered and loved by open-source people, and fund the work with a
paid product — only conflict if the paid product is built by taking things away from the
free one. The pattern that works (PostHog, Plausible, Cal.com, Langfuse, Supabase) is the
opposite: the open-source thing is complete and excellent, and the paid thing is *running
it for you* plus what only makes sense with a server and a team.

## Part 1 — Getting discovered

### The repo page is the landing page most people see

GitHub is where developers decide in fifteen seconds. Above the fold on the README:

1. One sentence that names the pain, not the category. Ours: *"Every API key you use.
   One gateway. One number per project."* — not "an LLM observability platform".
2. A screenshot with real-looking numbers on it (the demo data exists for exactly this).
3. The install command, three lines max, that works.
4. Badges only for things that are true and green: CI, license, Python version.

Everything else — architecture, comparison, security — lives below, or in `docs/`.

### Repository settings (do these once, they compound)

- **Topics.** Set at least: `llm`, `openrouter`, `openai`, `api-gateway`, `cost-tracking`,
  `finops`, `observability`, `self-hosted`, `fastapi`, `python`, `llmops`, `developer-tools`.
  Topics are how GitHub's explore and search surface a repo. Add `helicone-alternative`
  and `litellm-alternative` — people search for exactly those phrases.
- **Description** with the website URL in it, and the **website field** set.
- **Social preview image** (Settings → General): upload `site/og.png`. This is what shows
  when the repo link is pasted into Slack, Twitter, Discord. A repo without one looks
  abandoned.
- **Default branch `main`**, branch protection with CI required.
- **Discussions** enabled: it is a free forum, a waitlist substitute, and a place to
  point "how do I…" questions so issues stay clean.
- **Releases**: the `Release` workflow publishes to PyPI and creates a GitHub release on
  every `v*` tag. Tag early and often; "last release 2 days ago" is a trust signal.

### Channels, in the order they pay off

| Channel | What to do | Why it works |
|---|---|---|
| **PyPI** | Publish `openmetric` (the name is free as of this writing). | `pip install openmetric` is the whole install story; being on PyPI makes the README true. |
| **The Helicone migration** | A short page/post: "Moving off Helicone? Here's the one-line swap." Link from README and site. | Helicone entered maintenance mode in March 2026; that is a real cohort with an urgent need and a search query. |
| **Show HN** | Title with the pain, not the product: *"Show HN: I couldn't tell which side project spent my $40 in API bills, so I built a gateway that can."* Post Tuesday–Thursday, morning US Pacific. Reply to every comment for the first 3 hours. | HN rewards a specific, honest story and punishes marketing copy. The POSITIONING doc is the kind of thing HN respects. |
| **r/selfhosted, r/LocalLLaMA, r/SideProject** | One post each, days apart, written for that community. r/selfhosted cares about Docker and privacy; r/LocalLLaMA cares about provider coverage. | Different audiences; the same post in all three gets flagged as spam. |
| **awesome lists** | PR to `awesome-selfhosted` (strict rules: read them), `awesome-llmops`, `awesome-openrouter` if it exists. | Evergreen backlinks that keep sending traffic for years. |
| **Product Hunt** | Only after the site and Cloud waitlist exist; launch with the hosted story. | PH audiences want a hosted product; it converts the waitlist, not the repo. |
| **Docker Hub / GHCR** | Publish an image so `docker run` works without cloning. | Self-hosters expect it. |
| **Twitter/X, LinkedIn** | Screenshots of Blindspots findings — "OpenMetric just told me 20% of my calls have no price attached" is a better post than any feature list. | The product generates its own content. |

### What not to do

- Don't buy stars, run star-for-star, or add "star this repo" nags in the CLI.
- Don't add telemetry to the OSS build. Say so loudly; it is a feature for this audience.
- Don't post the same text in five places on the same day.

### Metrics you can track without spying on anyone

GitHub stars and traffic (Insights → Traffic), PyPI downloads (pypistats.org), Docker
pulls, site analytics on Vercel, waitlist signups. That is enough to know if this is
working. None of it requires the OSS build to phone home.

## Part 2 — The paid product

### The rule that makes open core work

**The open-source gateway is the complete product for one person.** Nothing in this repo
is gated, throttled, watermarked, or "community edition". A solo builder should never
hit a wall that says "upgrade". The moment that happens, the people who gave you the
stars become the people who write the "OpenMetric went closed" post.

The paid product sells things that are *genuinely* only possible or only sensible with a
server you operate and more than one human:

| Free, forever (this repo) | OpenMetric Cloud (paid, separate) |
|---|---|
| The gateway, all providers, all routes | The same gateway, hosted, on a stable URL |
| Dashboard, Blindspots, budgets, cross-tab | Same dashboard, no server to run |
| Unlimited projects, keys, virtual keys | Teams: members, roles, per-member virtual keys, SSO |
| Local SQLite (or your own Postgres) | Managed storage, backups, long retention |
| Budget shows as a finding | Alerts: Slack, email, webhook when a budget or blindspot trips |
| Price catalog updated via git | Price catalog kept current for you, with drift notifications |
| CSV export | Invoice reconciliation: match provider bills to attributed spend |
| You keep the keys | Bring-your-own-key, encrypted with a key we never see |

Everything in the right column is *convenience or coordination*. Nothing in it is
"the feature we removed from the free version".

### Pricing sketch (validate before believing)

- **Solo**: free. Self-host. This is most users and that is fine — they are the funnel.
- **Cloud Starter**: ~$19/mo — hosted, one user, alerts, 90-day retention. For the person
  who would rather pay than run a Docker container.
- **Cloud Team**: ~$49/user/mo or a flat ~$149/mo for 5 — teams, SSO, reconciliation,
  1-year retention. This is where the revenue is; a team lead with a budget is the buyer.

Compare: Helicone's paid tiers, Portkey's $49/mo + $9 per 100K logs. You are cheaper to
start and priced on seats, not logs, which the audience will find honest.

### Licensing

- **This repo stays MIT.** It maximises adoption and contributions, and it is what the
  audience trusts. Yes, someone could host it commercially. In practice nobody outcompetes
  the maintainer at hosting their own project (Plausible, Cal.com and PostHog are all
  permissively licensed and all sell hosting).
- **Cloud lives in a separate, private repo** that depends on this one as a library.
  Anything that improves the gateway lands here first; Cloud is a thin layer of
  multi-tenancy, billing and alerting on top.
- Register the name. "OpenMetric" as a trademark is what stops a fork from calling
  itself OpenMetric; MIT does not cover that. Add a short `TRADEMARK.md` when Cloud launches.

### The funnel, and how not to be annoying

```
README (one line: "want it hosted? → openmetric.dev")
   └─▶ site (Vercel): pain → demo → self-host CTA and Cloud CTA side by side
          └─▶ waitlist (Tally / Loops / a form): email + "how many projects, how many people"
                 └─▶ Cloud
```

- **One link in the README**, above the fold, phrased as an offer not a nag.
- **One optional link in the dashboard footer**, only once Cloud exists — and only on
  the self-hosted build, never a modal, never a banner, never a countdown.
- **Everything the OSS build says about Cloud must remain true** when someone reads it
  a year later. "Coming soon" that stays "coming soon" costs trust.
- Answer the "why is this free / how do you make money" question in the README before
  anyone asks it. This audience asks it first.

### Sequence

1. Merge to `main`, tag `v0.1.0`, publish to PyPI. *(Now.)*
2. Deploy `site/` to Vercel with a real domain. Replace the waitlist placeholder with a
   real form. Upload `og.png` as the social preview. Set topics.
3. Show HN + the Helicone-migration post. Watch what people ask for.
4. Build Cloud only from what the waitlist and issues actually say — the first three
   asks will probably be alerts, a hosted URL, and team keys, in some order.
5. Launch Cloud to the waitlist first, Product Hunt second.
