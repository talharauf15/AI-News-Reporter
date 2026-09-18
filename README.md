# AI & Tech Daily Digest

A daily briefing of AI/tech news — collected from Hacker News, RSS feeds, arXiv
and optionally X — ranked by an LLM and delivered to Discord. Runs on GitHub
Actions. Hosting and the free sources cost nothing; the only expense is a
fraction of a cent per day in OpenAI tokens, plus X if you enable it.

## Files

| File | Purpose |
|---|---|
| `digest.py` | Fetch, dedupe, summarise, send |
| `.github/workflows/digest.yml` | Daily schedule + commit-back |
| `requirements.txt` | Two dependencies |
| `seen.json` | Story IDs already sent — do not delete |
| `README.md` | This file |

## Setup

1. Create a GitHub repo and push all five files, keeping `digest.yml` at
   `.github/workflows/digest.yml`.
2. Make a Discord webhook: channel → Edit Channel → Integrations → Webhooks →
   New Webhook → Copy Webhook URL.
3. Add repo secrets under Settings → Secrets and variables → Actions:

   | Secret | Required? | Where from |
   |---|---|---|
   | `OPENAI_API_KEY` | yes | platform.openai.com |
   | `DISCORD_WEBHOOK_URL` | yes | step 2 |
   | `APIFY_TOKEN` | no | console.apify.com — only if you want X |

4. Settings → Actions → General → Workflow permissions → **Read and write**.
   Without this the job fails when it tries to save `seen.json`.
5. Actions tab → AI Digest → Run workflow. Check Discord.

If the manual run works, the daily cron will too.

## Sources

Free, always on:

- **Hacker News** via the Algolia API — no key, filtered by `HN_MIN_POINTS`.
- **RSS feeds** listed in `RSS_FEEDS` — TechCrunch, Ars Technica, VentureBeat,
  Hugging Face and others. A broken feed is logged and skipped, not fatal.
- **arXiv** cs.AI and cs.LG — research announcements rather than news.

Optional, paid:

- **X / Twitter** via an Apify actor. Skipped entirely when `APIFY_TOKEN` is
  unset. See below.

### Enabling X

1. Sign up free at console.apify.com.
2. Settings → API & Integrations → copy your **Personal API token**
   (starts with `apify_api_`).
3. Add it as the repo secret `APIFY_TOKEN`.

The default actor is `kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest`.
Note the `~` — the API uses it where the website shows `/`.

To use a different actor, set `APIFY_ACTOR` in the workflow. Open the actor in
Apify Console at least once so it is added to your account, run it manually
through the UI, then click the **API** button to get the exact input JSON and
paste it over the `payload` dict in `fetch_x()`. Actor input schemas differ and
change over time, so verifying through the UI beats guessing.

X knobs in `digest.py`:

- `X_ACCOUNTS` — handles to follow, without the `@`.
- `X_SEARCH_TERMS` — keyword searches. Empty by default; each term adds results
  and therefore cost.
- `X_MIN_LIKES` — engagement floor. Without it you get every reply and "gm".
- `X_MAX_ITEMS` — hard cap on results fetched, i.e. your spend ceiling.

### X cost

Apify's free plan gives $5 of credit per month, renewed monthly, and blocks
rather than bills you when it runs out — you cannot get a surprise charge.
At roughly $0.25 per 1,000 results, a daily run at `X_MAX_ITEMS = 200` costs
about $1.50/month and stays inside the free credit.

Note that dedupe saves tokens but not Apify credits: you pay for results at
fetch time, before the script knows what is new. `X_MAX_ITEMS` is the only
real control on spend.

Check Console → Billing after the first few runs to see your actual rate.

## Tuning

Knobs at the top of `digest.py`:

- `RSS_FEEDS` — add or remove sources.
- `HN_MIN_POINTS` — raise to 80 for less noise, lower to 20 for more coverage.
- `MIN_ITEMS_TO_SEND` — how quiet a day has to be before it sends nothing.
- `MAX_ITEMS_TO_LLM` — token-cost ceiling.
- `LOOKBACK_HOURS` — 26 by default, a deliberate overlap with the 24h schedule
  so nothing slips through a gap. Dedupe handles the repeats.

Delivery time is the `cron` line in the workflow, in UTC. `0 8 * * *` is 1pm
Pakistan time; subtract 5 from the local hour you want. GitHub's scheduler can
drift 5–15 minutes under load.

To change what gets selected, edit `PROMPT` in `digest.py`. That prompt is the
actual editorial policy — it matters more than any other setting here.

## Model choice

Set via `OPENAI_MODEL` in the workflow file. Default is `gpt-5.6-luna`.

| Tier | Rate (per 1M in/out) | Use for |
|---|---|---|
| `gpt-5.6-luna` | $0.20 / $1.20 | This job. Summarising and classifying. |
| `gpt-5.6-terra` | $2 / $12 | If Luna's ranking feels shallow after a week. |
| `gpt-5.6-sol` | $5 / $30 | Overkill here. |

Never use the bare string `gpt-5.6` — it routes to Sol.

Rates were current in September 2026 and OpenAI has changed them more than once
this year. Check your dashboard rather than trusting this table.

## Troubleshooting

| Symptom | Cause |
|---|---|
| 403 on `git push` | Workflow permissions still read-only (setup step 4) |
| 401 from OpenAI | Secret name misspelled, or a stray space in the key |
| Nothing in Discord | Webhook URL truncated when copied |
| 404 from Apify | Actor ID uses `/` instead of `~`, or never opened in Console |
| X returns 0 items | `payload` doesn't match the actor's schema — regenerate it |
| Digest arrives empty | Quiet day; `MIN_ITEMS_TO_SEND` suppressed it, by design |

The Actions log names the failing step in every case.

## Notes

- GitHub disables scheduled workflows after 60 days of repo inactivity. The
  commit-back step counts as activity, so this stays alive on its own.
- `seen.json` grows to 3000 entries then trims itself.
- Newer reasoning models reject a custom `temperature`, so the script omits it.
- Adding a source means writing one `fetch_*()` function that returns
  `{title, url, source, summary}` dicts and appending it in `main()`.