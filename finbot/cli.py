"""CLI: scan watchlist for long-term accumulation / buy-tranche confluence."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Callable, Sequence

from finbot.config import AppConfig, TickerConfig, load_config
from finbot.data import DataError, StaleDataError, ensure_fresh, fetch_earnings_dates, fetch_ohlcv
from finbot.indicators import build_snapshot
from finbot.notifier import NotifyError, alert_payload, format_notify_plan, notify
from finbot.sessions import expected_session
from finbot.strategy import ScanResult, evaluate, format_report

logger = logging.getLogger("finbot")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m finbot.cli",
        description="Scan watchlist tickers for a long-term accumulation buy-tranche.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print markdown + webhook JSON; do not create an Issue or POST. "
        "Webhook would be sent every run when secrets exist; Issue only on accumulation confluence.",
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
        "--stale-retries",
        type=int,
        default=_env_int("FINBOT_STALE_RETRIES", 3),
        help="Extra fetch rounds when Yahoo has not yet published the session that just "
        "closed (default: 3, env FINBOT_STALE_RETRIES).",
    )
    parser.add_argument(
        "--stale-wait",
        type=float,
        default=_env_float("FINBOT_STALE_WAIT_SECONDS", 300.0),
        help="Seconds between stale-data retry rounds (default: 300, env FINBOT_STALE_WAIT_SECONDS).",
    )
    parser.add_argument(
        "--resend",
        action="store_true",
        help="Re-POST the webhook even if this session's Issue records it was already sent.",
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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def scan_ticker(cfg: TickerConfig, expected: date | None = None) -> ScanResult:
    ohlcv = fetch_ohlcv(cfg.symbol, period=cfg.lookback_period)
    if expected is not None:
        ensure_fresh(ohlcv, cfg.symbol, expected)
    earnings = fetch_earnings_dates(cfg.symbol, asset_type=cfg.asset_type)
    snapshot = build_snapshot(ohlcv, cfg)
    return evaluate(snapshot, earnings, cfg)


def run(
    argv: Sequence[str] | None = None,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    clock = now or (lambda: datetime.now(timezone.utc))
    config = load_config(args.config)
    try:
        tickers = select_tickers(config, args.ticker)
    except KeyError as exc:
        logger.error("%s", exc)
        return 1
    if not tickers:
        logger.error("No tickers selected")
        return 1

    expected = expected_session(clock())
    logger.info("Expected session: %s", expected.isoformat())

    failures = 0
    pending = list(tickers)
    retries_left = max(0, args.stale_retries)
    while pending:
        stale: list[TickerConfig] = []
        for cfg in pending:
            outcome = _scan_and_notify(cfg, expected, args)
            if outcome == "stale":
                stale.append(cfg)
            elif outcome == "failed":
                failures += 1
        if not stale:
            break
        if retries_left <= 0:
            for cfg in stale:
                logger.error(
                    "%s: Yahoo still has no %s bar; skipped Issue and webhook rather than "
                    "re-grading an earlier session",
                    cfg.symbol,
                    expected.isoformat(),
                )
            failures += len(stale)
            break
        retries_left -= 1
        logger.warning(
            "Stale daily bars for %s; retrying in %.0fs (%d retries left)",
            ", ".join(cfg.symbol for cfg in stale),
            args.stale_wait,
            retries_left,
        )
        sleep(args.stale_wait)
        pending = stale

    if failures:
        return 1
    return 0


def _scan_and_notify(cfg: TickerConfig, expected: date, args: argparse.Namespace) -> str:
    """Return "ok", "stale" (retry later, nothing sent), or "failed"."""
    try:
        result = scan_ticker(cfg, expected)
    except StaleDataError as exc:
        logger.warning("%s", exc)
        return "stale"
    except (DataError, ValueError) as exc:
        logger.error("%s: scan failed: %s", cfg.symbol, exc)
        return "failed"
    except Exception:
        logger.exception("%s: unexpected scan error", cfg.symbol)
        return "failed"

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
        url = notify(result, dry_run=args.dry_run, resend=args.resend)
    except NotifyError as exc:
        logger.error("%s: notify failed: %s", cfg.symbol, exc)
        return "failed"
    if url:
        logger.info("%s: issue %s", cfg.symbol, url)
    return "ok"


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
