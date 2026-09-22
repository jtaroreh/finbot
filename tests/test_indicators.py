"""Tests for SMA, RSI, ATR, volume average, and swing extrema."""

from __future__ import annotations

import numpy as np
import pandas as pd

from finbot.config import default_ticker, load_config
from finbot.indicators import (
    add_indicators,
    atr,
    build_snapshot,
    is_sma200_reclaim,
    rsi,
    sma,
    sma_is_rising,
    swing_highs,
    swing_lows,
    true_range,
    volume_average_prior,
    weekly_close,
    weekly_rsi,
)


def test_sma_matches_known_sequence():
    close = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0])
    result = sma(close, 3)
    expected = pd.Series([np.nan, np.nan, 11.0, 12.0, 13.0, 14.0, 15.0])
    pd.testing.assert_series_equal(result, expected)


def test_rsi_all_up_is_100_after_warmup():
    close = pd.Series(np.arange(1.0, 40.0))
    values = rsi(close, 14)
    warmed = values.dropna()
    assert not warmed.empty
    assert warmed.iloc[-1] == 100.0
    assert warmed.iloc[0] == 100.0


def test_rsi_all_down_is_0_after_warmup():
    close = pd.Series(np.arange(40.0, 1.0, -1.0))
    values = rsi(close, 14)
    assert values.iloc[-1] == 0.0


def test_rsi_mixed_sequence_is_between_0_and_100():
    close = pd.Series(
        [44.0, 44.3, 43.9, 44.5, 45.0, 45.4, 45.1, 44.6, 44.2, 44.8, 45.2, 45.7, 46.0, 45.5, 45.1, 44.7]
    )
    values = rsi(close, 14)
    last = values.dropna().iloc[-1]
    assert 0.0 < last < 100.0
    assert values.isna().sum() >= 13


def test_atr_equals_constant_true_range():
    n = 30
    close = pd.Series(np.full(n, 100.0))
    high = close + 1.0
    low = close - 1.0
    tr = true_range(high, low, close)
    assert np.allclose(tr.iloc[1:], 2.0)
    values = atr(high, low, close, 14)
    assert np.allclose(values.iloc[14:], 2.0, atol=1e-9)


def test_volume_average_excludes_current_bar():
    volume = pd.Series([10.0] * 20 + [100.0])
    avg = volume_average_prior(volume, 20)
    assert np.isnan(avg.iloc[-2])
    assert avg.iloc[-1] == 10.0


def test_swing_low_and_high_at_clear_fractals():
    low = pd.Series([5, 4, 3, 2, 1, 2, 3, 4, 5, 4, 3, 2, 3, 4, 5], dtype=float)
    high = pd.Series([10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 12, 11, 10], dtype=float)
    lows = swing_lows(low, left=2, right=2)
    highs = swing_highs(high, left=2, right=2)
    assert lows.iloc[4] == 1.0
    assert highs.iloc[4] == 14.0
    assert lows.iloc[11] == 2.0


def test_weekly_rsi_resamples_daily_closes():
    idx = pd.date_range("2023-01-06", periods=120, freq="B")  # Fridays included
    close = pd.Series(np.linspace(100.0, 40.0, 120), index=idx)
    weekly = weekly_close(close)
    assert len(weekly) < len(close)
    assert weekly.iloc[-1] == float(close.iloc[-1])
    values = weekly_rsi(close, 14)
    warmed = values.dropna()
    assert not warmed.empty
    assert 0.0 <= warmed.iloc[-1] <= 40.0


def test_sma_is_rising_compares_lookback():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert sma_is_rising(series, 2)
    assert not sma_is_rising(pd.Series([5.0, 4.0, 3.0, 2.0, 1.0]), 2)
    assert not sma_is_rising(series, 20)


def test_sma200_reclaim_requires_washout_and_recent_below():
    n = 80
    close = pd.Series([100.0] * n)
    sma200 = pd.Series([100.0] * n)
    close.iloc[-30:-2] = 90.0  # 10% washout, still below until last bar
    close.iloc[-1] = 101.0
    assert is_sma200_reclaim(
        close, sma200, washout_pct=5.0, washout_lookback=60, recent_bars=10
    )
    close_no_reclaim = close.copy()
    close_no_reclaim.iloc[-1] = 99.0
    assert not is_sma200_reclaim(
        close_no_reclaim, sma200, washout_pct=5.0, washout_lookback=60, recent_bars=10
    )


def test_add_indicators_and_snapshot_use_last_bar():
    n = 250
    idx = pd.date_range("2023-01-03", periods=n, freq="B")
    close = pd.Series(np.linspace(100, 150, n), index=idx)
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )
    cfg = default_ticker("IBM")
    marked = add_indicators(frame, cfg)
    assert marked["SMA_200"].notna().sum() >= 1
    snap = build_snapshot(frame, cfg)
    assert snap.ticker == "IBM"
    assert snap.signal_date == idx[-1].date()
    assert snap.close == float(close.iloc[-1])
    assert snap.sma_200 is not None
    assert snap.atr_14 is not None
    assert snap.volume_multiple is not None
    assert snap.rsi_weekly is not None
    assert snap.sma_200_distance_pct is not None
    assert isinstance(snap.sma_50_slope_up, bool)
    assert isinstance(snap.sma_200_slope_up, bool)
    assert isinstance(snap.sma_200_reclaim, bool)
    assert snap.history_bars == n


def test_watchlist_loads_ibm_accumulation_defaults():
    config = load_config()
    assert [t.symbol for t in config.tickers] == ["IBM", "QTUM", "AIPO"]
    assert config.ticker("IBM").asset_type == "equity"
    assert config.ticker("QTUM").asset_type == "etf"
    assert config.ticker("AIPO").asset_type == "etf"
    for symbol in ("IBM", "QTUM", "AIPO"):
        item = config.ticker(symbol)
        assert item.enabled is True
        assert item.tier1_enabled is True
        assert item.tier2_enabled is True
        assert item.earnings_blackout_days == 5
        assert item.volume_min_multiple == 0.0
        assert item.swing_left == 10
        assert item.swing_right == 10
        assert item.rsi_weekly_max == 50.0
        assert item.rsi_daily_extreme == 30.0
        assert item.sma200_proximity_pct == 3.0
        assert item.sma200_undershoot_pct == 1.0
        assert item.tranche_spacing_atr == 2.25
        assert item.invalidation_atr_multiple == 3.5
