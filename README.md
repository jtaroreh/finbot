# finbot

This repo is the **only scanner**. Grok Bot must not run its own yfinance scan. Finbot evaluates the watchlist after the US cash close; on a high-conviction `ENTRY` it does both:

1. Opens one GitHub Issue with the markdown snapshot (levels and gate states)
2. POSTs the same alert as JSON to a Grok Bot webhook (if secrets are configured)

On a fail it logs `NO ENTRY` and creates neither an Issue nor a webhook call.

The default watchlist is **IBM only**. The engine already loops every enabled ticker. This repository publishes scanner output; it is not a recommendation to buy or sell anything.

## Architecture

1. **One finbot repo, many tickers.** Add symbols under `tickers:` in `config/watchlist.yaml`. One weekday Actions workflow scans all of them.
2. **Grok Bot does not scan.** A Bot per ticker is optional. Each Bot should use a **webhook trigger** (When to run → webhook), not its own cron / yfinance job. Finbot is the only place that fetches market data.
3. **Alerts fire on entry only.** Actions creates Issues **and** pings the Bot webhook only when every confluence gate passes.

## How the scan works

1. Load `config/watchlist.yaml` (defaults plus per-ticker overrides).
2. Fetch daily OHLCV and earnings dates with yfinance (no other market-data APIs).
3. Compute 20 / 50 / 200 SMA, 14 RSI, 14 ATR, volume vs the prior 20-day average, and recent swing support / resistance.
4. Skip entries inside an earnings blackout (default: 5 calendar days before/around earnings).
5. Require all confluence gates: trend, pullback, support, volume.
6. If every gate passes: entry = signal close, stop = entry − 1.5× ATR, targets at 2:1 and 3:1. Create **one** Issue titled `[ENTRY] TICKER YYYY-MM-DD` and POST one JSON webhook per firing ticker. Duplicate ticker+date Issues are skipped (open or closed).
7. If any gate fails: print / log `NO ENTRY` and create no Issue and no webhook.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Scan IBM from the default watchlist; print markdown; do not create an Issue or POST
python -m finbot.cli --dry-run --ticker IBM

# Alternate config path
python -m finbot.cli --dry-run --config config/watchlist.yaml
```

`--dry-run` always prints the snapshot table and gate states. It never creates an Issue and never POSTs the webhook.

Without `--dry-run`:

- An Issue is created only when an entry triggers **and** `GITHUB_TOKEN` plus `GITHUB_REPOSITORY` are set.
- The Grok Bot webhook is POSTed only when an entry triggers **and** both `FINBOT_GROK_WEBHOOK_URL` and `FINBOT_GROK_WEBHOOK_SECRET` are set. If either is missing (typical local run), the webhook is skipped quietly; Issue / dry-run output still happens.

Tests (no network except optional live dry-run):

```bash
pytest
```

## GitHub Actions

`.github/workflows/daily_scan.yml` runs:

- cron `30 21 * * 1-5` (weekdays ~21:30 UTC, after US cash close)
- `workflow_dispatch` for a manual run

The job checks out the repo, installs `requirements.txt`, and runs `python -m finbot.cli`. Permissions are `issues: write` and `contents: read`. The default `GITHUB_TOKEN` is enough to open Issues. This repo is public so Actions minutes are free.

On **entry only**, Actions creates a dedicated Issue **and** POSTs the JSON alert to the Grok Bot webhook.

### Secrets (Grok Bot webhook)

Do not commit webhook URLs or keys. Add them on the repo:

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `FINBOT_GROK_WEBHOOK_URL` | Routine **POST to** URL from the Grok Bot webhook panel |
| `FINBOT_GROK_WEBHOOK_SECRET` | Routine **key** (`crsr_…`) from that same panel |

Optional per-ticker overrides (one Bot per ticker): `FINBOT_GROK_WEBHOOK_URL_IBM` and `FINBOT_GROK_WEBHOOK_SECRET_IBM` (replace `IBM` with the symbol). Those win over the repo-wide pair when set.

If the secrets are unset, the workflow still scans and still opens Issues; it just skips the webhook.

### Auth header (match the Grok Bot routine panel)

Grok Bot webhook senders use a Bearer token. Finbot sends:

```http
Authorization: Bearer <FINBOT_GROK_WEBHOOK_SECRET>
Content-Type: application/json
```

Copy **POST to** and **key** from the Bot routine after it is saved and Active. The panel’s **header** field is that same `Authorization: Bearer …` line. A `200` means the Bot accepted the call and started a run.

JSON body (one POST per firing ticker):

```json
{
  "ticker": "IBM",
  "signal": "ENTRY",
  "signal_date": "2026-09-14",
  "entry": 249.13,
  "stop": 238.21,
  "target_2r": 270.97,
  "target_3r": 281.89,
  "gates": { "earnings": { "passed": true, "detail": "..." }, "...": {} },
  "indicators": { "close": 249.13, "sma_200": 257.78, "rsi_14": 61.08, "...": {} },
  "earnings": { "next": "2026-10-21", "blackout_passed": true, "detail": "..." },
  "issue_url": "https://github.com/owner/finbot/issues/12"
}
```

`issue_url` is omitted when no Issue was created.

## Watchlist

`config/watchlist.yaml` currently lists IBM. The engine already loops all enabled tickers; add another symbol under `tickers:` with optional overrides (blackout window, RSI band, volume multiple, and so on).
