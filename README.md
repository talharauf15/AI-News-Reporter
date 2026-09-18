# AI & Tech Daily Digest

A daily briefing of AI/tech news, collected from free sources, ranked by an LLM,
delivered to Discord. Runs on GitHub Actions. Hosting and data cost nothing;
the only expense is a fraction of a cent per day in OpenAI tokens.

## Setup

1. Create a GitHub repo and push these four files.
2. Make a Discord webhook: server → Edit Channel → Integrations → Webhooks →
   New Webhook → Copy Webhook URL.
3. In the repo: Settings → Secrets and variables → Actions → New repository secret.
   Add `OPENAI_API_KEY` and `DISCORD_WEBHOOK_URL`.
4. Actions tab → AI Digest → Run workflow. Check Discord.

If the manual run works, the daily cron will too.

## Files

| File | Purpose |
|---|---|
| `digest.py` | Fetch, dedupe, summarise, send |
| `.github/workflows/digest.yml` | Daily schedule + commit-back |
| `requirements.txt` | Two dependencies |
| `seen.json` | Story IDs already sent — do not delete |

## Tuning

All the knobs are at the top of `digest.py`:

- `RSS_FEEDS` — add or remove sources. A broken feed is skipped, not fatal.
- `HN_MIN_POINTS` — raise to 80 for less noise, lower to 20 for more coverage.
- `MIN_ITEMS_TO_SEND` — how quiet a day has to be before it sends nothing.
- `MAX_ITEMS_TO_LLM` — token-cost ceiling.

Delivery time is the `cron` line in the workflow, in UTC. `0 8 * * *` is 1pm
Pakistan time. GitHub's scheduler can drift 5–15 minutes under load.

To change what gets selected, edit `PROMPT` in `digest.py`. That prompt is the
actual editorial policy — it matters more than any other setting.

## Model choice

Set via `OPENAI_MODEL` in the workflow file. Default is `gpt-5.6-luna`.

| Tier | Rate (per 1M in/out) | Use for |
|---|---|---|
| `gpt-5.6-luna` | $0.20 / $1.20 | This job. Summarising and classifying. |
| `gpt-5.6-terra` | $2 / $12 | If Luna's ranking feels shallow after a week. |
| `gpt-5.6-sol` | $5 / $30 | Overkill here. |

Never use the bare string `gpt-5.6` — it routes to Sol.

Rates were current in September 2026 and OpenAI has changed them more than
once this year. Check your dashboard rather than trusting this table.

## Notes

- GitHub disables scheduled workflows after 60 days of repo inactivity. The
  commit-back step in the workflow counts as activity, so this stays alive.
- `seen.json` grows to 3000 entries then trims itself.
- Newer reasoning models reject a custom `temperature`, so the script omits it.
