"""Fetch daily OHLCV and earnings dates via yfinance."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class DataError(RuntimeError):
    """Raised when market data cannot be loaded."""


def fetch_ohlcv(symbol: str, period: str = "2y") -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    try:
        frame = ticker.history(period=period, interval="1d", auto_adjust=True)
    except Exception as exc:  # noqa: BLE001 — yfinance raises mixed types
        raise DataError(f"yfinance history failed for {symbol}: {exc}") from exc

    if frame is None or frame.empty:
        raise DataError(f"No OHLCV returned for {symbol}")

    frame = _normalize_ohlcv(frame)
    if len(frame) < 200:
        logger.warning("%s: only %s daily bars; 200 SMA may be incomplete", symbol, len(frame))
    return frame


def fetch_earnings_dates(symbol: str) -> list[date]:
    ticker = yf.Ticker(symbol)
    found: set[date] = set()

    try:
        earnings = ticker.get_earnings_dates(limit=16)
        found.update(_extract_dates(earnings))
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: get_earnings_dates failed: %s", symbol, exc)

    try:
        found.update(_extract_dates(getattr(ticker, "calendar", None)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: calendar earnings lookup failed: %s", symbol, exc)

    dates = sorted(found)
    if not dates:
        logger.warning("%s: no earnings dates available", symbol)
    return dates


def _normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(col[0]) for col in out.columns]

    rename = {str(col): str(col).title() for col in out.columns}
    out = out.rename(columns=rename)

    missing = [col for col in OHLCV_COLUMNS if col not in out.columns]
    if missing:
        raise DataError(f"OHLCV missing columns: {missing}")

    out = out.loc[:, list(OHLCV_COLUMNS)].dropna(how="any")
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    out.index = idx
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if out.empty:
        raise DataError("OHLCV empty after cleaning")
    return out


def _extract_dates(value: Any) -> list[date]:
    if value is None:
        return []

    if isinstance(value, pd.DataFrame):
        dates = list(_iter_datelike(value.index))
        for column in value.columns:
            name = str(column).lower()
            if "earn" in name or "date" in name:
                dates.extend(_iter_datelike(value[column]))
        return dates

    if isinstance(value, pd.Series):
        name = str(value.name).lower() if value.name is not None else ""
        dates = list(_iter_datelike(value.index))
        if "earn" in name or "date" in name:
            dates.extend(_iter_datelike(value))
        elif value.index.dtype == object or "date" in str(value.index.dtype):
            dates.extend(_iter_datelike(value))
        else:
            dates.extend(_iter_datelike(value))
        return dates

    if isinstance(value, dict):
        dates: list[date] = []
        for key, item in value.items():
            key_l = str(key).lower()
            if "earn" in key_l or "date" in key_l:
                dates.extend(_extract_dates(item))
            else:
                dates.extend(_iter_datelike([item] if not isinstance(item, (list, tuple)) else item))
        return dates

    if isinstance(value, (list, tuple, set)):
        return list(_iter_datelike(value))

    return list(_iter_datelike([value]))


def _iter_datelike(values: Iterable[Any]) -> Iterable[date]:
    for item in values:
        parsed = _as_date(item)
        if parsed is not None:
            yield parsed


def _as_date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York").tz_localize(None)
    return ts.date()
