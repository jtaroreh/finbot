# finbot

This repo is the **only scanner**. Grok Bot must not run its own yfinance scan. Finbot evaluates the watchlist after the US cash close, then uses a dual path:

1. **Daily webhook snapshot** — on every successful scan, POST the full ticker JSON (gates, indicators, earnings blackout, suggested levels) to a Grok Bot webhook so the Bot can make a holistic judgment. This happens for `ENTRY` and `NO_ENTRY`.
2. **GitHub Issue only on technical confluence** — open one Issue only when the python gates all pass (`signal=ENTRY`, `confluence=true`). No Issue on `NO_ENTRY` days.

The default watchlist is **IBM only**. The engine already loops every enabled ticker. This repository publishes scanner output; it is not a recommendation to buy or sell anything.

## Architecture

1. **One finbot repo, many tickers.** Add symbols under `tickers:` in `config/watchlist.yaml`. One weekday Actions workflow scans all of them.
2. **Grok Bot does not scan.** A Bot per ticker is optional. Each Bot should use a **webhook trigger** (When to run → webhook), not its own cron / yfinance job. Finbot is the only place that fetches market data.
3. **Dual path.** Actions POSTs a snapshot webhook **every successful run**. It creates an Issue **only** when python confluence / high-conviction technical ENTRY passes.

## How the scan works

1. Load `config/watchlist.yaml` (defaults plus per-ticker overrides).
2. Fetch daily OHLCV and earnings dates with yfinance (no other market-data APIs).
3. Compute 20 / 50 / 200 SMA, 14 RSI, 14 ATR, volume vs the prior 20-day average, and recent swing support / resistance.
4. Evaluate an earnings blackout (default: 5 calendar days before/around earnings) plus trend, pullback, support, and volume gates.
5. Always compute suggested levels: entry = signal close, stop = entry − 1.5× ATR, targets at 2:1 and 3:1 (even when confluence fails).
6. POST one JSON snapshot per ticker to the Grok Bot webhook (`signal`: `ENTRY` | `NO_ENTRY`, `confluence`: true/false, full gate/indicator/earnings/levels payload).
7. If every gate passes: create **one** Issue titled `[ENTRY] TICKER YYYY-MM-DD`. Duplicate ticker+date Issues are skipped (open or closed).
8. If any gate fails: print / log `NO ENTRY`, create **no** Issue, still POST the webhook snapshot.

A failed data fetch for a ticker is not a successful scan — no webhook for that ticker.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Scan IBM from the default watchlist; print markdown + webhook JSON; do not POST or create an Issue
python -m finbot.cli --dry-run --ticker IBM

# Alternate config path
python -m finbot.cli --dry-run --config config/watchlist.yaml
```

`--dry-run` prints the snapshot table, gate states, suggested levels, and the webhook JSON. It never POSTs and never creates an Issue. It does print:

- `Webhook: would POST … every run` when `FINBOT_GROK_WEBHOOK_URL` and `FINBOT_GROK_WEBHOOK_SECRET` are set (otherwise `Webhook: skipped … unset`)
- `Issue: would create [ENTRY] …` only on technical ENTRY; otherwise `Issue: skipped (technical confluence did not pass)`

Without `--dry-run`:

- The Grok Bot webhook is POSTed on **every successful scan** when both secrets are set (ENTRY and NO_ENTRY). If either secret is missing, the webhook is skipped quietly.
- An Issue is created only when python confluence passes **and** `GITHUB_TOKEN` plus `GITHUB_REPOSITORY` are set.

Tests (no network except optional live dry-run):

```bash
pytest
```

## GitHub Actions

`.github/workflows/daily_scan.yml` runs:

- cron `30 21 * * 1-5` (weekdays ~21:30 UTC, after US cash close)
- `workflow_dispatch` for a manual run

The job checks out the repo, installs `requirements.txt`, and runs `python -m finbot.cli`. Permissions are `issues: write` and `contents: read`. The default `GITHUB_TOKEN` is enough to open Issues. This repo is public so Actions minutes are free.

On every successful ticker scan, Actions POSTs the JSON snapshot to the Grok Bot webhook. On **technical ENTRY only**, it also creates a dedicated Issue.

### Secrets (Grok Bot webhook)

Do not commit webhook URLs or keys. Add them on the repo:

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `FINBOT_GROK_WEBHOOK_URL` | Routine **POST to** URL from the Grok Bot webhook panel |
| `FINBOT_GROK_WEBHOOK_SECRET` | Routine **key** (`crsr_…`) from that same panel |

Optional per-ticker overrides (one Bot per ticker): `FINBOT_GROK_WEBHOOK_URL_IBM` and `FINBOT_GROK_WEBHOOK_SECRET_IBM` (replace `IBM` with the symbol). Those win over the repo-wide pair when set.

If the secrets are unset, the workflow still scans and still opens Issues on ENTRY; it just skips the webhook.

### Auth header (match the Grok Bot routine panel)

Grok Bot webhook senders use a Bearer token. Finbot sends:

```http
Authorization: Bearer <FINBOT_GROK_WEBHOOK_SECRET>
Content-Type: application/json
```

Copy **POST to** and **key** from the Bot routine after it is saved and Active. The panel’s **header** field is that same `Authorization: Bearer …` line. A `200` means the Bot accepted the call and started a run.

JSON body (one POST per ticker, every successful scan):

```json
{
  "ticker": "IBM",
  "signal": "ENTRY",
  "confluence": true,
  "signal_date": "2026-09-14",
  "summary": "ENTRY",
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

On `NO_ENTRY` days `signal` is `"NO_ENTRY"`, `confluence` is `false`, suggested `entry`/`stop`/`target_*` are still present, and `issue_url` is omitted.

## Watchlist

`config/watchlist.yaml` currently lists IBM. The engine already loops all enabled tickers; add another symbol under `tickers:` with optional overrides (blackout window, RSI band, volume multiple, and so on).
