"""CLI: scan watchlist, print markdown, Issue on ENTRY, webhook every successful scan."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Sequence

from finbot.config import AppConfig, TickerConfig, load_config
from finbot.data import DataError, fetch_earnings_dates, fetch_ohlcv
from finbot.indicators import build_snapshot
from finbot.notifier import NotifyError, alert_payload, format_notify_plan, notify
from finbot.strategy import ScanResult, evaluate, format_report

logger = logging.getLogger("finbot")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m finbot.cli",
        description="Scan watchlist tickers for a high-confluence long entry.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print markdown + webhook JSON; do not create an Issue or POST. "
        "Webhook would be sent every run when secrets exist; Issue only on ENTRY.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to watchlist YAML (default: config/watchlist.yaml).",
    )
    parser.add_argument(
        "--ticker",
        default=None,
        help="Limit the scan to one symbol (e.g. IBM).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def select_tickers(config: AppConfig, ticker: str | None) -> list[TickerConfig]:
    if ticker:
        chosen = config.ticker(ticker)
        return [chosen]
    return [item for item in config.tickers if item.enabled]


def scan_ticker(cfg: TickerConfig) -> ScanResult:
    ohlcv = fetch_ohlcv(cfg.symbol, period=cfg.lookback_period)
    earnings = fetch_earnings_dates(cfg.symbol)
    snapshot = build_snapshot(ohlcv, cfg)
    return evaluate(snapshot, earnings, cfg)


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    try:
        tickers = select_tickers(config, args.ticker)
    except KeyError as exc:
        logger.error("%s", exc)
        return 1
    if not tickers:
        logger.error("No tickers selected")
        return 1

    failures = 0
    for cfg in tickers:
        try:
            result = scan_ticker(cfg)
        except (DataError, ValueError) as exc:
            logger.error("%s: scan failed: %s", cfg.symbol, exc)
            failures += 1
            continue
        except Exception:
            logger.exception("%s: unexpected scan error", cfg.symbol)
            failures += 1
            continue

        report = format_report(result)
        print(report)
        print(format_notify_plan(result, dry_run=args.dry_run))
        if args.dry_run:
            print("## Webhook JSON (not posted)")
            print(json.dumps(alert_payload(result), indent=2))
            print()
        if result.entry_triggered:
            logger.info("%s %s: ENTRY", result.ticker, result.signal_date)
        else:
            logger.info("%s %s: %s", result.ticker, result.signal_date, result.summary)

        try:
            url = notify(result, dry_run=args.dry_run)
        except NotifyError as exc:
            logger.error("%s: notify failed: %s", cfg.symbol, exc)
            failures += 1
            continue
        if url:
            logger.info("%s: issue %s", cfg.symbol, url)

    if failures:
        return 1
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
