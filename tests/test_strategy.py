"""Earnings blackout and accumulation confluence-gate tests."""

from __future__ import annotations

from datetime import date, timedelta
from dataclasses import replace

import pytest

from finbot.config import default_ticker
from finbot.data import _extract_dates
from finbot.indicators import Snapshot
from finbot.strategy import evaluate, format_report, issue_title


def _passing_snapshot(**overrides) -> Snapshot:
    """IBM-like snapshot that satisfies accumulation defaults (rising 200, weekly RSI)."""
    close = 171.0
    sma_200 = 168.5  # +1.48% — inside the −1% to +3% band
    snap = Snapshot(
        ticker="IBM",
        signal_date=date(2024, 6, 3),
        open=170.0,
        high=172.0,
        low=169.4,
        close=close,
        volume=6_000_000.0,
        sma_20=170.6,
        sma_50=169.0,
        sma_200=sma_200,
        rsi_14=38.0,
        rsi_14_prev=36.0,
        rsi_weekly=36.0,
        rsi_weekly_prev=38.0,
        atr_14=2.0,
        volume_avg_20=8_000_000.0,
        volume_multiple=0.75,
        swing_support=170.2,
        swing_resistance=180.0,
        golden_cross_recent=False,
        close_crossed_above_sma50=False,
        sma50_above_sma200=True,
        sma_200_slope_up=True,
        sma_200_distance_pct=(close / sma_200 - 1.0) * 100.0,
        sma_200_reclaim=False,
    )
    return replace(snap, **overrides)


def _far_earnings(signal: date) -> list[date]:
    return [signal + timedelta(days=30)]


def test_all_gates_pass_emits_entry_and_tranche_metadata():
    cfg = default_ticker()
    snap = _passing_snapshot()
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.entry_triggered
    assert result.summary == "ENTRY"
    assert all(gate.passed for gate in result.gates)
    assert result.levels is not None
    assert result.levels.entry == pytest.approx(171.0)
    assert result.levels.tranche_spacing_atr == pytest.approx(2.25)
    assert result.levels.next_tranche == pytest.approx(171.0 - 2.25 * 2.0)
    assert result.levels.invalidation_level == pytest.approx(168.5)
    assert "not a 1.5" in result.levels.invalidation_hint
    markdown = format_report(result)
    assert "**Setup:** long-term accumulation / buy tranche" in markdown
    assert "| Close / entry |" in markdown
    assert "| Next tranche (2.25× ATR below) |" in markdown
    assert "| Invalidation hint |" in markdown
    assert "Target 2:1" not in markdown
    assert "Target 3:1" not in markdown
    assert "| Confluence | PASS |" in markdown
    assert issue_title("IBM", snap.signal_date) == "[ACCUMULATION] IBM 2024-06-03"


@pytest.mark.parametrize("offset", [-5, 0, 5])
def test_earnings_blackout_blocks_inside_window(offset: int):
    cfg = default_ticker()
    snap = _passing_snapshot()
    earnings = [snap.signal_date + timedelta(days=offset)]
    result = evaluate(snap, earnings, cfg)
    assert not result.entry_triggered
    assert not result.gate("earnings").passed
    assert result.levels is not None
    assert "earnings" in result.summary


@pytest.mark.parametrize("offset", [-6, 6, 20])
def test_earnings_outside_window_allows_entry(offset: int):
    cfg = default_ticker()
    snap = _passing_snapshot()
    result = evaluate(snap, [snap.signal_date + timedelta(days=offset)], cfg)
    assert result.gate("earnings").passed
    assert result.entry_triggered


def test_missing_earnings_does_not_block():
    cfg = default_ticker()
    snap = _passing_snapshot()
    result = evaluate(snap, [], cfg)
    assert result.gate("earnings").passed
    assert result.entry_triggered


def test_confluence_requires_every_gate():
    cfg = default_ticker()
    snap = _passing_snapshot()
    base = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert base.entry_triggered

    cases = {
        "trend": _passing_snapshot(
            close=150.0,
            low=149.0,
            sma_200=160.0,
            sma_200_slope_up=False,
            sma_200_reclaim=False,
            sma_200_distance_pct=(150.0 / 160.0 - 1.0) * 100.0,
        ),
        "pullback": _passing_snapshot(rsi_14=55.0, rsi_14_prev=52.0, rsi_weekly=55.0),
        "support": _passing_snapshot(
            swing_support=140.0,
            sma_20=140.0,
            sma_50=138.0,
            sma_200=120.0,
            low=171.0,
            sma_200_distance_pct=(171.0 / 120.0 - 1.0) * 100.0,
            sma_200_slope_up=True,
            rsi_weekly=36.0,
        ),
    }
    for name, mutated in cases.items():
        result = evaluate(mutated, _far_earnings(mutated.signal_date), cfg)
        assert not result.entry_triggered, name
        assert not result.gate(name).passed, name
        assert result.levels is not None, name


def test_quiet_volume_does_not_block_accumulation():
    cfg = default_ticker()
    snap = _passing_snapshot(volume=2_000_000.0, volume_avg_20=8_000_000.0, volume_multiple=0.25)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("volume").passed
    assert result.entry_triggered
    assert "not required" in result.gate("volume").detail


def test_volume_gate_can_still_require_expansion_when_configured():
    cfg = default_ticker(volume_min_multiple=1.2)
    fail = evaluate(
        _passing_snapshot(
            volume=9_599_999.0,
            volume_avg_20=8_000_000.0,
            volume_multiple=9_599_999.0 / 8_000_000.0,
        ),
        _far_earnings(date(2024, 6, 3)),
        cfg,
    )
    assert not fail.gate("volume").passed
    ok = evaluate(
        _passing_snapshot(volume=9_600_000.0, volume_avg_20=8_000_000.0, volume_multiple=1.2),
        _far_earnings(date(2024, 6, 3)),
        cfg,
    )
    assert ok.gate("volume").passed


def test_weekly_rsi_is_primary_pullback_path():
    cfg = default_ticker()
    snap = _passing_snapshot(rsi_weekly=39.9, rsi_14=60.0)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("pullback").passed
    assert "Weekly RSI" in result.gate("pullback").detail
    assert result.entry_triggered


def test_daily_rsi_extreme_is_alternate_pullback_path():
    cfg = default_ticker()
    snap = _passing_snapshot(rsi_weekly=52.0, rsi_14=24.0)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("pullback").passed
    assert "Daily RSI" in result.gate("pullback").detail
    assert result.entry_triggered


def test_old_daily_rsi_30_45_zone_is_not_enough():
    cfg = default_ticker()
    snap = _passing_snapshot(rsi_weekly=52.0, rsi_14=38.0, rsi_14_prev=36.0)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert not result.gate("pullback").passed
    assert not result.entry_triggered


def test_daily_rsi_extreme_is_not_a_freefall_fail():
    cfg = default_ticker()
    snap = _passing_snapshot(rsi_14=20.0, rsi_14_prev=24.0, rsi_weekly=50.0)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("pullback").passed
    assert result.entry_triggered


def test_trend_passes_on_rising_200_within_3_percent():
    cfg = default_ticker()
    close = 171.0
    sma_200 = 168.0  # +1.79%
    snap = _passing_snapshot(
        close=close,
        sma_200=sma_200,
        sma_200_slope_up=True,
        sma_200_reclaim=False,
        sma_200_distance_pct=(close / sma_200 - 1.0) * 100.0,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("trend").passed
    assert "Upward-sloping SMA 200" in result.gate("trend").detail
    assert result.entry_triggered


def test_trend_fails_when_extended_above_rising_200():
    cfg = default_ticker()
    close = 180.0
    sma_200 = 168.5  # +6.8%
    snap = _passing_snapshot(
        close=close,
        low=179.0,
        sma_200=sma_200,
        sma_200_slope_up=True,
        sma_200_reclaim=False,
        sma_200_distance_pct=(close / sma_200 - 1.0) * 100.0,
        swing_support=179.0,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert not result.gate("trend").passed
    assert not result.entry_triggered


def test_trend_fails_near_falling_200_without_reclaim():
    cfg = default_ticker()
    snap = _passing_snapshot(sma_200_slope_up=False, sma_200_reclaim=False)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert not result.gate("trend").passed
    assert "not upward-sloping" in result.gate("trend").detail


def test_trend_passes_on_reclaim_after_washout():
    cfg = default_ticker()
    close = 171.0
    sma_200 = 170.0
    snap = _passing_snapshot(
        close=close,
        low=169.5,
        sma_200=sma_200,
        sma_200_slope_up=False,
        sma_200_reclaim=True,
        sma_200_distance_pct=(close / sma_200 - 1.0) * 100.0,
        swing_support=170.0,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("trend").passed
    assert "Reclaiming SMA 200" in result.gate("trend").detail
    assert result.entry_triggered


def test_golden_cross_below_200_is_not_a_trend_pass():
    cfg = default_ticker()
    snap = _passing_snapshot(
        close=171.0,
        sma_200=175.0,
        sma_50=168.0,
        golden_cross_recent=True,
        sma50_above_sma200=True,
        sma_200_slope_up=True,
        sma_200_reclaim=False,
        sma_200_distance_pct=(171.0 / 175.0 - 1.0) * 100.0,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert not result.gate("trend").passed
    assert not result.entry_triggered


def test_support_can_use_key_moving_average():
    cfg = default_ticker()
    snap = _passing_snapshot(
        swing_support=140.0,
        sma_20=140.0,
        sma_50=170.8,
        sma_200=120.0,
        low=170.5,
        close=171.0,
        atr_14=2.0,
        sma_200_distance_pct=(171.0 / 120.0 - 1.0) * 100.0,
        sma_200_slope_up=True,
        sma_200_reclaim=False,
        rsi_weekly=36.0,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("support").passed
    assert "SMA 50" in result.gate("support").detail


def test_support_passes_when_close_is_in_sma200_band():
    cfg = default_ticker()
    snap = _passing_snapshot(swing_support=140.0, sma_50=140.0)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("support").passed
    assert "SMA 200" in result.gate("support").detail


def test_calendar_extracts_earnings_not_dividend_dates():
    calendar = {
        "Dividend Date": date(2026, 9, 10),
        "Ex-Dividend Date": date(2026, 8, 10),
        "Earnings Date": [date(2026, 10, 21)],
        "Earnings Average": 2.88,
    }
    assert _extract_dates(calendar) == [date(2026, 10, 21)]
