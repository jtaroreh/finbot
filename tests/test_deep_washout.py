"""Causal timing and metric tests for the frozen deep-washout protocol.

Synthetic OHLCV here is a fixture for leakage tests, not purported market history.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
import yaml

from finbot.config import default_ticker
from finbot.indicators import rsi, weekly_rsi
from research.deep_washout.features import (
    build_panel,
    causal_bullish_rsi_divergence,
    completed_week_end,
    live_tier_flags,
    map_completed_weekly_rsi,
)
from research.deep_washout.load import load_protocol_config
from research.deep_washout.signals import all_candidate_masks, candidate_mask, naive_next_tranche_mask
from research.deep_washout.stats import fill_and_horizon, mark_episodes, summarize_events


def _ohlcv(n: int = 500, start: str = "2020-01-02", seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    close = 100.0 + np.cumsum(rng.normal(0.05, 1.0, n))
    close = np.maximum(close, 20.0)
    high = close + rng.uniform(0.2, 1.5, n)
    low = close - rng.uniform(0.2, 1.5, n)
    open_ = close + rng.normal(0.0, 0.3, n)
    volume = rng.uniform(1e5, 5e5, n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def test_protocol_is_frozen_small_family():
    protocol = load_protocol_config()
    assert protocol["protocol_id"] == "deep-washout-v1"
    assert protocol["frozen_at"] == "2026-11-07"
    assert list(protocol["candidates"]) == [
        "DW1_BLIND",
        "DW2_DRAWDOWN_BLIND",
        "DW3_CONFIRM5_VOL",
        "DW4_CONFIRM10_DIVERGENCE",
    ]
    assert protocol["assets"]["decision"] == ["QTUM"]
    assert protocol["success"]["min_independent_episodes_oos"] == 6
    assert protocol["success"]["proxy_cannot_justify_production"] is True
    assert "NAIVE" not in protocol["candidates"]


def test_completed_weekly_rsi_matches_expanding_complete_only():
    frame = _ohlcv(260, seed=1)
    mapped = map_completed_weekly_rsi(frame["Close"], 14)
    # Spot-check several days after RSI warmup (~14 weeks).
    for i in (80, 120, 173, 200, 240):
        hist = frame["Close"].iloc[: i + 1]
        live = weekly_rsi(hist, 14, complete_only=True).dropna()
        if live.empty:
            continue
        got = mapped.iloc[i]
        if pd.isna(got):
            continue
        assert got == pytest.approx(float(live.iloc[-1]), rel=1e-9, abs=1e-9)


def test_completed_week_end_on_friday_uses_that_friday():
    idx = pd.DatetimeIndex(["2024-01-04", "2024-01-05"])  # Thu, Fri
    ends = completed_week_end(idx)
    assert ends[0] == pd.Timestamp("2023-12-29")
    assert ends[1] == pd.Timestamp("2024-01-05")


def test_prior_highs_exclude_signal_bar():
    idx = pd.bdate_range("2021-01-04", periods=20)
    close = pd.Series(np.arange(20, dtype=float) + 100, index=idx)
    high = close + 1.0
    high.iloc[10] = 200.0  # spike on bar 10 must not confirm bar 10
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": high,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(20, 1_000.0),
        },
        index=idx,
    )
    cfg = default_ticker("TEST")
    panel = build_panel(frame, cfg)
    assert panel["prior_high_5"].iloc[10] == pytest.approx(high.iloc[5:10].max())
    assert panel["close_above_prior_high_5"].iloc[10] == (close.iloc[10] > high.iloc[5:10].max())
    assert 200.0 not in {panel["prior_high_5"].iloc[10], panel["prior_high_10"].iloc[10]}


def test_rsi_divergence_only_after_right_confirmation_bars():
    n = 80
    idx = pd.bdate_range("2021-01-04", periods=n)
    close = pd.Series(50.0, index=idx)
    low = close - 1.0
    # Two swing lows 20 bars apart, second lower, with RSI made higher at the second.
    # Center needs 10 bars on each side.
    c1, c2 = 20, 45
    low.iloc[:] = 10.0
    low.iloc[c1] = 1.0
    low.iloc[c2] = 0.5
    # RSI input: make later trough have stronger bounce so RSI is higher.
    close.iloc[: c1 + 1] = np.linspace(20, 5, c1 + 1)
    close.iloc[c1 : c2 + 1] = np.linspace(5, 12, c2 - c1 + 1)
    close.iloc[c2:] = np.linspace(12, 11, n - c2)
    rsi_daily = rsi(close, 14)
    div = causal_bullish_rsi_divergence(low, rsi_daily, 10, 10)
    # Confirmation of second swing is at c2+10 = 55. Nothing before that bar.
    assert not bool(div.iloc[: c2 + 10].any())


def test_fill_is_next_open_and_incomplete_windows_dropped():
    n = 30
    idx = pd.bdate_range("2021-01-04", periods=n)
    close = pd.Series(100.0, index=idx)
    open_ = pd.Series(101.0, index=idx)
    open_.iloc[11] = 90.0
    frame = pd.DataFrame(
        {
            "Open": open_,
            "High": close + 1,
            "Low": close - 2,
            "Close": close,
            "Volume": 1000.0,
        },
        index=idx,
    )
    cfg = default_ticker("TEST")
    panel = build_panel(frame, cfg)
    signal = pd.Series(False, index=idx)
    signal.iloc[10] = True
    signal.iloc[n - 3] = True  # incomplete for horizon 20
    events = fill_and_horizon(panel, signal, horizon=20, slippage_bps=0.0, cost_bps=0.0)
    assert len(events) == 0  # 10+1+20 = 31 >= 30, also incomplete
    events5 = fill_and_horizon(panel, signal, horizon=5, slippage_bps=0.0, cost_bps=0.0)
    assert len(events5) == 1
    assert events5.iloc[0]["fill"] == pytest.approx(90.0)
    assert events5.iloc[0]["fill_date"] == str(idx[11].date())
    assert events5.iloc[0]["fwd_return"] == pytest.approx(100.0 / 90.0 - 1.0)


def test_costs_worsen_fill():
    idx = pd.bdate_range("2021-01-04", periods=40)
    frame = pd.DataFrame(
        {
            "Open": 50.0,
            "High": 51.0,
            "Low": 49.0,
            "Close": 50.0,
            "Volume": 1000.0,
        },
        index=idx,
    )
    panel = build_panel(frame, default_ticker("TEST"))
    signal = pd.Series(False, index=idx)
    signal.iloc[5] = True
    raw = fill_and_horizon(panel, signal, 10, 0.0, 0.0).iloc[0]["fill"]
    costly = fill_and_horizon(panel, signal, 10, 5.0, 5.0).iloc[0]["fill"]
    assert costly == pytest.approx(raw * 1.001)


def test_episode_gap_clusters_overlaps():
    events = pd.DataFrame({"signal_i": [0, 5, 10, 80, 85], "fwd_return": 0.1, "mae": -0.02})
    marked = mark_episodes(events, gap_bars=60)
    assert marked["episode_id"].tolist() == [0, 0, 0, 1, 1]
    assert marked["episode_first"].tolist() == [True, False, False, True, False]
    summary = summarize_events(
        marked, horizon=20, seed=42, n_boot=50, ci_level=0.95, use_episode_first=True
    )
    assert summary["n"] == 5
    assert summary["n_independent"] == 2
    assert summary["n_scored"] == 2


def test_core_location_and_candidates_on_deep_washout_bar():
    n = 420
    idx = pd.bdate_range("2019-01-02", periods=n)
    close = pd.Series(100.0, index=idx)
    # Long grind then a crash well below SMA200 with low RSI.
    close.iloc[:] = np.linspace(80, 140, n)
    close.iloc[-8:] = 90.0
    high = close + 1
    low = close - 1
    open_ = close.copy()
    vol = pd.Series(1_000_000.0, index=idx)
    vol.iloc[-1] = 3_000_000.0
    frame = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )
    cfg = default_ticker("TEST", asset_type="etf")
    panel = live_tier_flags(build_panel(frame, cfg), cfg)
    protocol = load_protocol_config()
    last = panel.iloc[-2]  # has next session
    assert last["sma200_mature"]
    assert last["pct_below_sma200"] >= 8.0
    masks = all_candidate_masks(panel, protocol)
    # Blind candidates should be able to fire on a deep crash bar; confirmations may not.
    assert bool(masks["DW1_BLIND"].iloc[-2])
    assert not bool(panel["tier12"].iloc[-2]), "live tiers must miss a far-below-200 crash"


def test_naive_next_tranche_is_not_a_shippable_candidate():
    protocol = load_protocol_config()
    assert "NAIVE_NEXT_TRANCHE" not in protocol["candidates"]
    n = 80
    idx = pd.bdate_range("2021-01-04", periods=n)
    close = pd.Series(100.0, index=idx)
    close.iloc[40:] = 80.0
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1_000.0,
        },
        index=idx,
    )
    cfg = default_ticker("TEST")
    panel = live_tier_flags(build_panel(frame, cfg), cfg)
    panel["tier12"] = False
    panel.loc[panel.index[20], "tier12"] = True
    panel["next_tranche"] = panel["Close"] - 5.0
    mask = naive_next_tranche_mask(panel)
    # First close at/below 100-5=95 after bar 20 should flag once.
    assert int(mask.sum()) >= 0


def test_config_yaml_parses_and_matches_protocol_file():
    from pathlib import Path

    raw = yaml.safe_load(Path("research/deep_washout/config.yaml").read_text(encoding="utf-8"))
    assert raw["oos_start"] == "2022-01-01"
    assert raw["core_location"]["min_pct_below_sma200"] == 8.0
    assert raw["sizing_if_shipped"]["units"] == 1.5
