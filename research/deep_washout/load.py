"""Fetch and record Yahoo OHLCV. Never synthesize bars."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
import yfinance as yf

from finbot.data import _normalize_ohlcv

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.yaml"
CACHE_DIR = Path(__file__).resolve().parent / "cache"


@dataclass(frozen=True)
class Provenance:
    symbol: str
    source: str
    period: str
    interval: str
    auto_adjust: bool
    fetched_at_utc: str
    first_bar: str | None
    last_bar: str | None
    n_bars: int
    n_missing_ohlcv_rows_dropped: int
    n_duplicate_index_dropped: int
    business_days_span: int
    missing_vs_weekdays: int
    dividend_rows: int
    split_rows: int
    empty: bool
    note: str = ""


def load_protocol_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Protocol config must be a mapping: {cfg_path}")
    return raw


def fetch_history(symbol: str, cache_dir: Path | None = CACHE_DIR) -> tuple[pd.DataFrame, Provenance]:
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ticker = yf.Ticker(symbol)
    raw = ticker.history(period="max", interval="1d", auto_adjust=True)
    n_raw = 0 if raw is None else len(raw)
    if raw is None or raw.empty:
        prov = Provenance(
            symbol=symbol,
            source="yfinance.Ticker.history",
            period="max",
            interval="1d",
            auto_adjust=True,
            fetched_at_utc=fetched_at,
            first_bar=None,
            last_bar=None,
            n_bars=0,
            n_missing_ohlcv_rows_dropped=0,
            n_duplicate_index_dropped=0,
            business_days_span=0,
            missing_vs_weekdays=0,
            dividend_rows=_safe_len(_try_attr(ticker, "dividends")),
            split_rows=_safe_len(_try_attr(ticker, "splits")),
            empty=True,
            note="Yahoo returned no OHLCV; series skipped (not synthesized).",
        )
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]), prov

    before_norm = len(raw)
    frame = _normalize_ohlcv(raw)
    dropped_na = before_norm - len(frame)
    # _normalize_ohlcv already drops duplicate index; approximate via raw vs clean
    dupes = int(raw.index.duplicated().sum()) if hasattr(raw.index, "duplicated") else 0

    weekday_span = 0
    missing_weekdays = 0
    if len(frame) >= 2:
        weekdays = pd.bdate_range(frame.index[0], frame.index[-1], freq="C")
        weekday_span = len(weekdays)
        missing_weekdays = int(len(weekdays.difference(frame.index)))

    prov = Provenance(
        symbol=symbol,
        source="yfinance.Ticker.history",
        period="max",
        interval="1d",
        auto_adjust=True,
        fetched_at_utc=fetched_at,
        first_bar=str(frame.index[0].date()),
        last_bar=str(frame.index[-1].date()),
        n_bars=len(frame),
        n_missing_ohlcv_rows_dropped=max(0, dropped_na),
        n_duplicate_index_dropped=dupes,
        business_days_span=weekday_span,
        missing_vs_weekdays=missing_weekdays,
        dividend_rows=_safe_len(_try_attr(ticker, "dividends")),
        split_rows=_safe_len(_try_attr(ticker, "splits")),
        empty=False,
        note=f"raw_rows={n_raw}; close is split/dividend adjusted.",
    )
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache_dir / f"{symbol}.csv")
        (cache_dir / f"{symbol}_provenance.json").write_text(
            json.dumps(asdict(prov), indent=2), encoding="utf-8"
        )
    return frame, prov


def _try_attr(ticker: Any, name: str) -> Any:
    try:
        return getattr(ticker, name)
    except Exception:  # noqa: BLE001 — yfinance mixed failures
        return None


def _safe_len(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(len(value))
    except TypeError:
        return 0
