# finbot

Weekday post-market scanner for a high-confluence long entry. The default watchlist is **IBM only**. On a pass it opens one GitHub Issue with a markdown snapshot of levels and gate states. On a fail it logs status and does not open an Issue.

This repository publishes scanner output. It is not a recommendation to buy or sell anything.

## How it works

1. Load `config/watchlist.yaml` (defaults plus per-ticker overrides).
2. Fetch daily OHLCV and earnings dates with yfinance (no other market-data APIs).
3. Compute 20 / 50 / 200 SMA, 14 RSI, 14 ATR, volume vs the prior 20-day average, and recent swing support / resistance.
4. Skip entries inside an earnings blackout (default: 5 calendar days before/around earnings).
5. Require all confluence gates: trend, pullback, support, volume.
6. If every gate passes: entry = signal close, stop = entry − 1.5× ATR, targets at 2:1 and 3:1. Create **one** Issue titled `[ENTRY] TICKER YYYY-MM-DD`. Duplicate ticker+date Issues are skipped (open or closed).
7. If any gate fails: print / log `NO ENTRY` and create no Issue.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Scan IBM from the default watchlist; print markdown; do not create an Issue
python -m finbot.cli --dry-run --ticker IBM

# Alternate config path
python -m finbot.cli --dry-run --config config/watchlist.yaml
```

`--dry-run` always prints the snapshot table and gate states. Without `--dry-run`, an Issue is created only when an entry triggers **and** `GITHUB_TOKEN` plus `GITHUB_REPOSITORY` are set.

Tests (no network except optional live dry-run):

```bash
pytest
```

## GitHub Actions

`.github/workflows/daily_scan.yml` runs:

- cron `30 21 * * 1-5` (weekdays ~21:30 UTC, after US cash close)
- `workflow_dispatch` for a manual run

The job checks out the repo, installs `requirements.txt`, and runs `python -m finbot.cli` with the default `GITHUB_TOKEN`. Permissions are `issues: write` and `contents: read`. No extra secrets are required; this repo is public so Actions minutes are free.

Alerts are **GitHub Issues**, not email or chat. One dedicated Issue per ticker + signal date.

## Watchlist

`config/watchlist.yaml` currently lists IBM. The engine already loops all enabled tickers; add another symbol under `tickers:` with optional overrides (blackout window, RSI band, volume multiple, and so on).
