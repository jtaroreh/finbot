"""Causal feature panel: no future bars in weekly RSI, swings, or prior highs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from finbot.config import TickerConfig, default_ticker
from finbot.indicators import atr, rsi, sma, swing_lows, weekly_close
from finbot.strategy import evaluate
from finbot.indicators import build_snapshot


def completed_week_end(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Friday of the last *completed* W-FRI week as of each daily bar.

    On Friday the week ending today is complete. On other weekdays the
    in-progress week is dropped (same intent as weekly_rsi complete_only=True).
    """
    idx = pd.DatetimeIndex(index).tz_localize(None) if getattr(index, "tz", None) else pd.DatetimeIndex(index)
    week_end = idx.to_period("W-FRI").to_timestamp(how="end").normalize()
    is_friday = idx.weekday == 4
    completed = week_end.where(is_friday, week_end - pd.Timedelta(days=7))
    return pd.DatetimeIndex(completed)


def map_completed_weekly_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    weekly = weekly_close(close, complete_only=False)
    wrsi = rsi(weekly, period)
    wrsi.index = pd.DatetimeIndex(wrsi.index).normalize()
    labels = completed_week_end(pd.DatetimeIndex(close.index))
    mapped = wrsi.reindex(labels).to_numpy()
    return pd.Series(mapped, index=close.index, name="rsi_weekly_completed")


def causal_bullish_rsi_divergence(
    low: pd.Series,
    rsi_daily: pd.Series,
    left: int = 10,
    right: int = 10,
) -> pd.Series:
    """True when the two latest *confirmed* 10/10 swing lows are a lower low with higher RSI.

    A swing at center j is first known at bar j+right.
    """
    swings = swing_lows(low, left=left, right=right)
    n = len(low)
    out = np.zeros(n, dtype=bool)
    prices: list[float] = []
    rsis: list[float] = []
    low_np = low.to_numpy(dtype=float)
    rsi_np = rsi_daily.to_numpy(dtype=float)
    sw_np = swings.to_numpy(dtype=float)
    for i in range(n):
        j = i - right
        if j >= left and np.isfinite(sw_np[j]):
            prices.append(float(low_np[j]))
            r_j = rsi_np[j]
            rsis.append(float(r_j) if np.isfinite(r_j) else float("nan"))
        if len(prices) >= 2 and np.isfinite(rsis[-1]) and np.isfinite(rsis[-2]):
            if prices[-1] < prices[-2] and rsis[-1] > rsis[-2]:
                out[i] = True
    return pd.Series(out, index=low.index, name="rsi_bullish_div_causal")


def build_panel(ohlcv: pd.DataFrame, cfg: TickerConfig | None = None) -> pd.DataFrame:
    """One row per session with indicators known at that close."""
    cfg = cfg or default_ticker("RESEARCH")
    close = ohlcv["Close"]
    high = ohlcv["High"]
    low = ohlcv["Low"]
    volume = ohlcv["Volume"]
    out = ohlcv.copy()
    out["sma_50"] = sma(close, cfg.sma_mid)
    out["sma_200"] = sma(close, cfg.sma_slow)
    out["rsi_14"] = rsi(close, cfg.rsi_period)
    out["atr_14"] = atr(high, low, close, cfg.atr_period)
    out["rsi_weekly"] = map_completed_weekly_rsi(close, cfg.rsi_period)
    out["vol_avg_20_prior"] = volume.shift(1).rolling(cfg.volume_avg_period, min_periods=cfg.volume_avg_period).mean()
    out["vol_mult"] = volume / out["vol_avg_20_prior"]
    out["sma_50_slope_up"] = out["sma_50"] > out["sma_50"].shift(cfg.sma50_slope_lookback)
    out["sma_200_slope_up"] = out["sma_200"] > out["sma_200"].shift(cfg.sma200_slope_lookback)
    out["dist_sma50_pct"] = (close / out["sma_50"] - 1.0) * 100.0
    out["dist_sma200_pct"] = (close / out["sma_200"] - 1.0) * 100.0
    out["pct_below_sma200"] = -out["dist_sma200_pct"]
    out["atr_below_sma200"] = (out["sma_200"] - close) / out["atr_14"]
    resid = close - out["sma_200"]
    out["z_sma200"] = resid / resid.rolling(200, min_periods=200).std()
    out["high_52w"] = high.rolling(252, min_periods=252).max()
    out["drawdown_52w_pct"] = (close / out["high_52w"] - 1.0) * 100.0
    out["prior_high_5"] = high.shift(1).rolling(5, min_periods=5).max()
    out["prior_high_10"] = high.shift(1).rolling(10, min_periods=10).max()
    out["close_above_prior_high_5"] = close > out["prior_high_5"]
    out["close_above_prior_high_10"] = close > out["prior_high_10"]
    out["history_bars"] = np.arange(1, len(out) + 1)
    out["sma200_mature"] = out["history_bars"] >= 350
    swings = swing_lows(low, cfg.swing_left, cfg.swing_right)
    out["swing_support_known"] = swings.shift(cfg.swing_right).ffill()
    out["rsi_div_causal"] = causal_bullish_rsi_divergence(low, out["rsi_14"], cfg.swing_left, cfg.swing_right)
    out["next_open"] = out["Open"].shift(-1)
    out["has_next_session"] = out["next_open"].notna() & (out["next_open"] > 0)
    return out


def live_tier_flags(panel: pd.DataFrame, cfg: TickerConfig) -> pd.DataFrame:
    """Vectorized live Tier 1 / Tier 2, matching strategy.evaluate (no earnings for ETFs)."""
    c = panel["Close"]
    low = panel["Low"]
    a = panel["atr_14"]
    s50 = panel["sma_50"]
    s200 = panel["sma_200"]
    swing = panel["swing_support_known"]

    def near(level: pd.Series) -> pd.Series:
        dist = np.minimum((c - level).abs(), (low - level).abs())
        tol = cfg.support_atr_multiple * a
        return (dist <= tol) & (c >= level - tol)

    support1 = near(s50)
    if swing.notna().any():
        support1 = support1 | near(swing)
    support2 = near(s200)
    if swing.notna().any():
        support2 = support2 | near(swing)

    d50 = panel["dist_sma50_pct"]
    t1 = (
        cfg.tier1_enabled
        & panel["sma_50_slope_up"].fillna(False)
        & d50.between(-cfg.sma50_undershoot_pct, cfg.sma50_proximity_pct)
        & panel["rsi_14"].between(cfg.rsi_daily_dip_min, cfg.rsi_daily_dip_max)
        & support1.fillna(False)
        & s50.notna()
        & a.notna()
    )

    d200 = panel["dist_sma200_pct"]
    band = d200.between(-cfg.sma200_undershoot_pct, cfg.sma200_proximity_pct)
    washed = (d200 <= -cfg.sma200_washout_pct).astype(float).rolling(
        cfg.sma200_washout_lookback, min_periods=1
    ).max() > 0
    below_recent = (c <= s200).astype(float).shift(1).rolling(
        cfg.sma200_reclaim_recent_bars, min_periods=1
    ).max() > 0
    reclaim = (c > s200) & washed & below_recent
    trend2 = (panel["sma_200_slope_up"].fillna(False) & band) | reclaim.fillna(False)
    pull2 = (panel["rsi_weekly"] < cfg.rsi_weekly_max) | (panel["rsi_14"] < cfg.rsi_daily_extreme)
    t2 = cfg.tier2_enabled & s200.notna() & trend2 & pull2.fillna(False) & support2.fillna(False)

    out = panel.copy()
    out["tier2"] = t2.fillna(False)
    out["tier1"] = t1.fillna(False) & ~out["tier2"]
    out["tier12"] = out["tier1"] | out["tier2"]
    out["next_tranche"] = c - cfg.tranche_spacing_atr * a
    return out


def parity_sample(
    ohlcv: pd.DataFrame,
    cfg: TickerConfig,
    panel: pd.DataFrame,
    n_days: int = 40,
    seed: int = 42,
) -> dict[str, object]:
    """Compare vectorized tiers with strategy.evaluate on truncated history."""
    rng = np.random.default_rng(seed)
    start = 400 if len(ohlcv) > 450 else max(60, len(ohlcv) // 3)
    eligible = np.arange(start, len(ohlcv))
    if len(eligible) == 0:
        return {"n": 0, "mismatches": [], "ok": True}
    pick = rng.choice(eligible, size=min(n_days, len(eligible)), replace=False)
    mismatches: list[dict[str, object]] = []
    for i in sorted(int(x) for x in pick):
        snap = build_snapshot(ohlcv.iloc[: i + 1], cfg)
        result = evaluate(snap, [], cfg)
        live = result.signal_tier
        vec = "TIER_2_MAJOR" if bool(panel["tier2"].iloc[i]) else (
            "TIER_1_ROUTINE" if bool(panel["tier1"].iloc[i]) else "NONE"
        )
        if live != vec:
            mismatches.append(
                {
                    "date": str(ohlcv.index[i].date()),
                    "evaluate": live,
                    "vectorized": vec,
                    "summary": result.summary,
                }
            )
    return {"n": len(pick), "mismatches": mismatches, "ok": len(mismatches) == 0}
