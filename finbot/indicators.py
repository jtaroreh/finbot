"""SMA, RSI (daily + weekly), ATR, volume ratio, and structural swing levels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from finbot.config import TickerConfig


@dataclass(frozen=True)
class Snapshot:
    ticker: str
    signal_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    sma_20: float | None
    sma_50: float | None
    sma_200: float | None
    rsi_14: float | None
    rsi_14_prev: float | None
    rsi_weekly: float | None
    rsi_weekly_prev: float | None
    rsi_weekly_live: float | None
    atr_14: float | None
    volume_avg_20: float | None
    volume_multiple: float | None
    swing_support: float | None
    swing_resistance: float | None
    golden_cross_recent: bool
    close_crossed_above_sma50: bool
    sma50_above_sma200: bool
    sma_50_slope_up: bool
    sma_50_distance_pct: float | None
    sma_200_slope_up: bool
    sma_200_distance_pct: float | None
    sma_200_reclaim: bool
    history_bars: int = 0
    sma200_is_mature: bool = False


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi_value = 100.0 - (100.0 / (1.0 + rs))
    rsi_value = rsi_value.where(avg_loss != 0.0, 100.0)
    rsi_value = rsi_value.where(avg_gain != 0.0, 0.0)
    both_zero = (avg_gain == 0.0) & (avg_loss == 0.0)
    return rsi_value.where(~both_zero, 50.0)


def weekly_close(close: pd.Series, complete_only: bool = False) -> pd.Series:
    """Resample daily closes to weekly Friday bars.
    
    If complete_only is True and the last bar in close is not a Friday,
    the last in-progress weekly bar is excluded for signal stability.
    """
    if close.empty:
        return close.copy()
    series = close.copy()
    if not isinstance(series.index, pd.DatetimeIndex):
        series.index = pd.to_datetime(series.index)
    weekly = series.resample("W-FRI").last().dropna()
    if complete_only and len(weekly) > 1 and series.index[-1].weekday() != 4:
        weekly = weekly.iloc[:-1]
    return weekly


def weekly_rsi(close: pd.Series, period: int = 14, complete_only: bool = False) -> pd.Series:
    """Wilder RSI on weekly closes resampled from daily bars."""
    return rsi(weekly_close(close, complete_only=complete_only), period)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ATR."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def volume_average_prior(volume: pd.Series, period: int = 20) -> pd.Series:
    """Average of the prior `period` sessions (excludes the current bar)."""
    return volume.shift(1).rolling(window=period, min_periods=period).mean()


def swing_lows(low: pd.Series, left: int = 10, right: int = 10) -> pd.Series:
    return _fractal_extrema(low, left=left, right=right, mode="low")


def swing_highs(high: pd.Series, left: int = 10, right: int = 10) -> pd.Series:
    return _fractal_extrema(high, left=left, right=right, mode="high")


def latest_confirmed_swing(swings: pd.Series) -> float | None:
    valid = swings.dropna()
    if valid.empty:
        return None
    return float(valid.iloc[-1])


def sma_is_rising(series: pd.Series, lookback: int) -> bool:
    """True when the last value is strictly above the value `lookback` bars earlier."""
    if lookback <= 0 or len(series) <= lookback:
        return False
    last = _opt_float(series.iloc[-1])
    prior = _opt_float(series.iloc[-1 - lookback])
    if last is None or prior is None:
        return False
    return last > prior


def is_sma200_reclaim(
    close: pd.Series,
    sma200: pd.Series,
    *,
    washout_pct: float,
    washout_lookback: int,
    recent_bars: int,
) -> bool:
    """Close is back above the 200 SMA after a deep washout and a recent stay below it."""
    if close.empty or sma200.empty:
        return False
    last_close = _opt_float(close.iloc[-1])
    last_sma = _opt_float(sma200.iloc[-1])
    if last_close is None or last_sma is None or last_close <= last_sma:
        return False

    aligned = pd.concat({"close": close, "sma": sma200}, axis=1).dropna()
    if aligned.empty:
        return False

    wash = aligned.iloc[-washout_lookback:] if washout_lookback > 0 else aligned
    dist = (wash["close"] - wash["sma"]) / wash["sma"]
    had_washout = bool((dist <= -washout_pct / 100.0).any())

    if recent_bars <= 0:
        was_below = False
    else:
        prior = aligned.iloc[-(recent_bars + 1) : -1]
        was_below = bool((prior["close"] <= prior["sma"]).any()) if not prior.empty else False

    return had_washout and was_below


def add_indicators(ohlcv: pd.DataFrame, cfg: TickerConfig) -> pd.DataFrame:
    out = ohlcv.copy()
    close = out["Close"]
    out["SMA_20"] = sma(close, cfg.sma_fast)
    out["SMA_50"] = sma(close, cfg.sma_mid)
    out["SMA_200"] = sma(close, cfg.sma_slow)
    out["RSI_14"] = rsi(close, cfg.rsi_period)
    out["ATR_14"] = atr(out["High"], out["Low"], close, cfg.atr_period)
    out["VOL_AVG_20"] = volume_average_prior(out["Volume"], cfg.volume_avg_period)
    out["VOL_MULT"] = out["Volume"] / out["VOL_AVG_20"]
    out["SWING_LOW"] = swing_lows(out["Low"], cfg.swing_left, cfg.swing_right)
    out["SWING_HIGH"] = swing_highs(out["High"], cfg.swing_left, cfg.swing_right)
    return out


def build_snapshot(ohlcv: pd.DataFrame, cfg: TickerConfig) -> Snapshot:
    if ohlcv.empty:
        raise ValueError(f"No bars for {cfg.symbol}")

    marked = add_indicators(ohlcv, cfg)
    last = marked.iloc[-1]
    prev = marked.iloc[-2] if len(marked) > 1 else None
    lookback = marked.iloc[-cfg.golden_cross_lookback :]

    sma50 = _opt_float(last.get("SMA_50"))
    sma200 = _opt_float(last.get("SMA_200"))
    last_close = float(last["Close"])
    golden = False
    if {"SMA_50", "SMA_200"}.issubset(lookback.columns) and len(lookback) >= 2:
        prev_50 = lookback["SMA_50"].shift(1)
        prev_200 = lookback["SMA_200"].shift(1)
        crossed = (lookback["SMA_50"] > lookback["SMA_200"]) & (prev_50 <= prev_200)
        golden = bool(crossed.fillna(False).any())

    crossed_50 = False
    if prev is not None and sma50 is not None:
        prev_sma50 = _opt_float(prev.get("SMA_50"))
        prev_close = _opt_float(prev.get("Close"))
        if prev_sma50 is not None and prev_close is not None:
            crossed_50 = last_close > sma50 and prev_close <= prev_sma50

    # Completed weekly closes for stable signal evaluation
    wrsi_stable = weekly_rsi(marked["Close"], cfg.rsi_period, complete_only=True)
    wrsi_stable_valid = wrsi_stable.dropna()
    rsi_weekly = float(wrsi_stable_valid.iloc[-1]) if not wrsi_stable_valid.empty else None
    rsi_weekly_prev = float(wrsi_stable_valid.iloc[-2]) if len(wrsi_stable_valid) > 1 else None

    # Live in-progress weekly RSI
    wrsi_live = weekly_rsi(marked["Close"], cfg.rsi_period, complete_only=False)
    wrsi_live_valid = wrsi_live.dropna()
    rsi_weekly_live = float(wrsi_live_valid.iloc[-1]) if not wrsi_live_valid.empty else None

    sma_50_slope_up = sma_is_rising(marked["SMA_50"], cfg.sma50_slope_lookback)
    sma_50_distance_pct = ((last_close / sma50) - 1.0) * 100.0 if sma50 else None

    slope_up = sma_is_rising(marked["SMA_200"], cfg.sma200_slope_lookback)
    distance_pct = ((last_close / sma200) - 1.0) * 100.0 if sma200 else None
    reclaim = is_sma200_reclaim(
        marked["Close"],
        marked["SMA_200"],
        washout_pct=cfg.sma200_washout_pct,
        washout_lookback=cfg.sma200_washout_lookback,
        recent_bars=cfg.sma200_reclaim_recent_bars,
    )

    history_bars = len(ohlcv)
    sma200_is_mature = history_bars >= 350

    signal_ts = pd.Timestamp(marked.index[-1])
    return Snapshot(
        ticker=cfg.symbol,
        signal_date=signal_ts.date(),
        open=float(last["Open"]),
        high=float(last["High"]),
        low=float(last["Low"]),
        close=last_close,
        volume=float(last["Volume"]),
        sma_20=_opt_float(last.get("SMA_20")),
        sma_50=sma50,
        sma_200=sma200,
        rsi_14=_opt_float(last.get("RSI_14")),
        rsi_14_prev=_opt_float(prev.get("RSI_14")) if prev is not None else None,
        rsi_weekly=rsi_weekly,
        rsi_weekly_prev=rsi_weekly_prev,
        rsi_weekly_live=rsi_weekly_live,
        atr_14=_opt_float(last.get("ATR_14")),
        volume_avg_20=_opt_float(last.get("VOL_AVG_20")),
        volume_multiple=_opt_float(last.get("VOL_MULT")),
        swing_support=latest_confirmed_swing(marked["SWING_LOW"].iloc[:-1]),
        swing_resistance=latest_confirmed_swing(marked["SWING_HIGH"].iloc[:-1]),
        golden_cross_recent=golden,
        close_crossed_above_sma50=crossed_50,
        sma50_above_sma200=bool(sma50 is not None and sma200 is not None and sma50 > sma200),
        sma_50_slope_up=sma_50_slope_up,
        sma_50_distance_pct=sma_50_distance_pct,
        sma_200_slope_up=slope_up,
        sma_200_distance_pct=distance_pct,
        sma_200_reclaim=reclaim,
        history_bars=history_bars,
        sma200_is_mature=sma200_is_mature,
    )


def _fractal_extrema(series: pd.Series, left: int, right: int, mode: str) -> pd.Series:
    values = series.to_numpy(dtype=float)
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(left, n - right):
        window = values[i - left : i + right + 1]
        center = values[i]
        if np.isnan(center) or np.isnan(window).any():
            continue
        if mode == "low" and center == np.min(window) and (window == center).sum() == 1:
            out[i] = center
        elif mode == "high" and center == np.max(window) and (window == center).sum() == 1:
            out[i] = center
    return pd.Series(out, index=series.index)


def _opt_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number):
        return None
    return number
