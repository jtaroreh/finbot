# finbot

This repo is the **only scanner**. Grok Bot must not run its own yfinance scan. Finbot is a **long-term accumulation / buy-tranche** scanner (IBM-first, multi-ticker engine). It is **not** a short-term swing-entry system: there is no 1.5× ATR stop and no 2R/3R take-profit in the alert thesis.

It evaluates the watchlist on weekdays at 12:20pm America/Los_Angeles, then uses a dual path:

1. **Daily webhook snapshot** — on every successful scan, POST the full ticker JSON (gates, indicators, earnings blackout, tranche metadata) to a Grok Bot webhook so the Bot can make a holistic judgment. This happens for `ENTRY` and `NO_ENTRY`.
2. **GitHub Issue only on technical confluence** — open one Issue only when the python gates all pass (`signal=ENTRY`, `confluence=true`). No Issue on `NO_ENTRY` days. The Issue is framed as a **buy tranche**, not a swing trade.

The default watchlist is **IBM only**. The engine already loops every enabled ticker. This repository publishes scanner output; it is not a recommendation to buy or sell anything.

## Architecture

1. **One finbot repo, many tickers.** Add symbols under `tickers:` in `config/watchlist.yaml`. One weekday Actions workflow scans all of them.
2. **Grok Bot does not scan.** A Bot per ticker is optional. Each Bot should use a **webhook trigger** (When to run → webhook), not its own cron / yfinance job. Finbot is the only place that fetches market data.
3. **Dual path.** Actions POSTs a snapshot webhook **every successful run**. It creates an Issue **only** when python confluence / high-conviction accumulation ENTRY passes.

## How the scan works

1. Load `config/watchlist.yaml` (defaults plus per-ticker overrides).
2. Fetch daily OHLCV and earnings dates with yfinance (no other market-data APIs).
3. Compute 20 / 50 / 200 SMA, daily 14 RSI, **weekly 14 RSI** (daily closes resampled to Friday bars), 14 ATR, volume vs the prior 20-day average, and confirmed structural swing support / resistance (`swing_left` / `swing_right` default 10/10).
4. Evaluate an earnings blackout (default: 5 calendar days before/around earnings) plus trend, RSI, support, and volume gates. Volume expansion is **off** by default (`volume_min_multiple: 0`) — quiet buying is allowed.
5. Always compute **tranche metadata** (even when confluence fails): entry = signal close, next tranche = entry − 2.25× ATR, invalidation hint = sustainably below the 200 SMA (wide context, not a 1.5× ATR swing stop). 2R/3R targets are not part of the plan.
6. POST one JSON snapshot per ticker to the Grok Bot webhook (`signal`: `ENTRY` | `NO_ENTRY`, `confluence`: true/false, `thesis`: `long_term_accumulation`, full gate/indicator/earnings/tranche payload).
7. If every gate passes: create **one** Issue titled `[ACCUMULATION] TICKER YYYY-MM-DD`. Duplicate ticker+date Issues are skipped (open or closed).
8. If any gate fails: print / log `NO ENTRY`, create **no** Issue, still POST the webhook snapshot.

A failed data fetch for a ticker is not a successful scan — no webhook for that ticker.

### SMA 200 rule (primary trend / location filter)

Pass if **either**:

- **(A) Pullback to the institutional 200 SMA.** The 200 is upward-sloping (`SMA200[t] > SMA200[t − 20]`) **and** the close is inside **−1% to +3%** of it (sitting on the average counts). This is the “price came back to the 200” buy-tranche.
- **(B) Reclaim after a deep washout.** Close is back above the 200 SMA, at least one bar in the last 60 sessions was **≥ 5% below** the 200, and price traded at or below the 200 within the last 10 sessions.

A 50/200 golden cross or a close crossing the 50 SMA is **not** a trend pass by itself. Thresholds are configurable (`sma200_slope_lookback`, `sma200_proximity_pct`, `sma200_undershoot_pct`, `sma200_washout_pct`, `sma200_washout_lookback`, `sma200_reclaim_recent_bars`).

### RSI rule

**Primary:** weekly RSI (14, from resampled daily bars) **< 40** (soft cyclical / oversold zone).

**Alternate:** daily RSI **< 25** (extreme washout). The old 30–45 daily “swing pullback” band is not a pass. Both thresholds are in config (`rsi_weekly_max`, `rsi_daily_extreme`); set a threshold to `0` to disable that path.

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

`--dry-run` prints the snapshot table, gate states, tranche metadata, and the webhook JSON. It never POSTs and never creates an Issue. It does print:

- `Webhook: would POST … every run` when `FINBOT_GROK_WEBHOOK_URL` and `FINBOT_GROK_WEBHOOK_SECRET` are set (otherwise `Webhook: skipped … unset`)
- `Issue: would create [ACCUMULATION] …` only on technical accumulation confluence; otherwise `Issue: skipped (technical confluence did not pass)`

Without `--dry-run`:

- The Grok Bot webhook is POSTed on **every successful scan** when both secrets are set (ENTRY and NO_ENTRY). If either secret is missing, the webhook is skipped quietly.
- An Issue is created only when python confluence passes **and** `GITHUB_TOKEN` plus `GITHUB_REPOSITORY` are set.

Tests (no network except optional live dry-run):

```bash
pytest
```

## GitHub Actions

`.github/workflows/daily_scan.yml` runs:

- cron `20 19 * * 1-5` (weekdays 12:20pm America/Los_Angeles; PDT = 19:20 UTC. GitHub cron is UTC-only, so this is 11:20am PST in winter)
- `workflow_dispatch` for a manual run

The job checks out the repo, installs `requirements.txt`, and runs `python -m finbot.cli`. Permissions are `issues: write` and `contents: read`. The default `GITHUB_TOKEN` is enough to open Issues. This repo is public so Actions minutes are free.

On every successful ticker scan, Actions POSTs the JSON snapshot to the Grok Bot webhook. On **technical accumulation ENTRY only**, it also creates a dedicated Issue.

### Secrets (Grok Bot webhook)

Do not commit webhook URLs or keys. Add them on the repo:

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `FINBOT_GROK_WEBHOOK_URL` | Routine **POST to** URL from the Grok Bot webhook panel |
| `FINBOT_GROK_WEBHOOK_SECRET` | Routine **key** (`crsr_…`) from that same panel |

Optional per-ticker overrides (one Bot per ticker): `FINBOT_GROK_WEBHOOK_URL_IBM` and `FINBOT_GROK_WEBHOOK_SECRET_IBM` (replace `IBM` with the symbol). Those win over the repo-wide pair when set.

If the secrets are unset, the workflow still scans and still opens Issues on accumulation confluence; it just skips the webhook.

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
  "thesis": "long_term_accumulation",
  "signal_date": "2026-09-14",
  "summary": "ENTRY",
  "entry": 249.13,
  "next_tranche": 240.01,
  "tranche_spacing_atr": 2.25,
  "invalidation_level": 257.78,
  "invalidation_hint": "Not a swing take-profit plan. Suggested next buy tranche ~2.25× ATR below entry (240.01). Wide invalidation context: daily close sustainably below SMA 200 (257.78), not a 1.5× ATR stop.",
  "gates": { "earnings": { "passed": true, "detail": "..." }, "...": {} },
  "indicators": { "close": 249.13, "sma_200": 257.78, "rsi_14": 61.08, "rsi_weekly": 48.2, "sma_200_slope_up": true, "...": {} },
  "earnings": { "next": "2026-10-21", "blackout_passed": true, "detail": "..." },
  "issue_url": "https://github.com/owner/finbot/issues/12"
}
```

On `NO_ENTRY` days `signal` is `"NO_ENTRY"`, `confluence` is `false`, tranche fields (`entry` / `next_tranche` / `invalidation_*`) are still present, and `issue_url` is omitted. There is no `stop` / `target_2r` / `target_3r` headline.

## Watchlist

`config/watchlist.yaml` currently lists IBM with accumulation defaults (rising-200 pullback band, weekly RSI, 10/10 structural pivots, volume expansion off, 2.25× ATR tranche spacing). The engine already loops all enabled tickers; add another symbol under `tickers:` with optional overrides (blackout window, RSI thresholds, SMA 200 band, and so on).
