"""Tests for SMA, RSI, ATR, volume average, and swing extrema."""

from __future__ import annotations

import numpy as np
import pandas as pd

from finbot.config import default_ticker, load_config
from finbot.indicators import (
    add_indicators,
    atr,
    build_snapshot,
    rsi,
    sma,
    swing_highs,
    swing_lows,
    true_range,
    volume_average_prior,
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


def test_watchlist_loads_ibm_defaults():
    config = load_config()
    ibm = config.ticker("IBM")
    assert ibm.earnings_blackout_days == 5
    assert ibm.volume_min_multiple == 1.2
    assert ibm.stop_atr_multiple == 1.5
    assert ibm.target_r_multiples == (2, 3)
    assert [t.symbol for t in config.tickers] == ["IBM"]
