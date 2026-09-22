"""Earnings blackout and accumulation confluence-gate tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from finbot.config import default_ticker
from finbot.data import _extract_dates
from finbot.indicators import Snapshot
from finbot.strategy import evaluate, format_report, issue_title


def _passing_snapshot(**overrides) -> Snapshot:
    """IBM-like snapshot that satisfies Tier 2 accumulation defaults (rising 200, weekly RSI)."""
    close = 171.0
    sma_200 = 168.5  # +1.48% — inside the −1% to +3% band
    sma_50 = 169.0
    snap = Snapshot(
        ticker="IBM",
        signal_date=date(2024, 6, 3),
        open=170.0,
        high=172.0,
        low=169.4,
        close=close,
        volume=6_000_000.0,
        sma_20=170.6,
        sma_50=sma_50,
        sma_200=sma_200,
        rsi_14=38.0,
        rsi_14_prev=36.0,
        rsi_weekly=36.0,
        rsi_weekly_prev=38.0,
        rsi_weekly_live=37.0,
        atr_14=2.0,
        volume_avg_20=8_000_000.0,
        volume_multiple=0.75,
        swing_support=170.2,
        swing_resistance=180.0,
        golden_cross_recent=False,
        close_crossed_above_sma50=False,
        sma50_above_sma200=True,
        sma_50_slope_up=True,
        sma_50_distance_pct=(close / sma_50 - 1.0) * 100.0,
        sma_200_slope_up=True,
        sma_200_distance_pct=(close / sma_200 - 1.0) * 100.0,
        sma_200_reclaim=False,
        history_bars=500,
        sma200_is_mature=True,
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
    assert result.signal_tier in ("TIER_1_ROUTINE", "TIER_2_MAJOR")
    assert all(gate.passed for gate in result.gates)
    assert result.levels is not None
    assert result.levels.entry == pytest.approx(171.0)
    assert result.levels.tranche_spacing_atr == pytest.approx(2.25)
    assert result.levels.next_tranche == pytest.approx(171.0 - 2.25 * 2.0)
    assert result.levels.invalidation_level == pytest.approx(171.0 - 3.5 * 2.0)
    assert "weekly close confirmation" in result.levels.invalidation_hint
    markdown = format_report(result)
    assert "**Setup:** long-term accumulation / buy tranche" in markdown
    assert "| Close / entry |" in markdown
    assert "| Next tranche (2.25× ATR below) |" in markdown
    assert "| Invalidation hint |" in markdown
    assert "Target 2:1" not in markdown
    assert "Target 3:1" not in markdown
    assert "| Confluence | PASS |" in markdown
    assert issue_title("IBM", snap.signal_date) == "[ACCUMULATION] IBM 2024-06-03"


def test_tier1_routine_50_sma_dip_triggers_entry():
    cfg = default_ticker()
    # 200 SMA is flat or far away, but 50 SMA is rising and close is at 50 SMA with daily RSI 42
    snap = _passing_snapshot(
        sma_200=150.0,
        sma_200_slope_up=False,
        sma_200_distance_pct=14.0,
        sma_50=170.0,
        sma_50_slope_up=True,
        close=171.0,
        low=169.5,
        sma_50_distance_pct=(171.0 / 170.0 - 1.0) * 100.0,
        rsi_14=42.0,
        rsi_weekly=55.0,  # Fails Tier 2 weekly RSI (<50), but Tier 1 passes!
        swing_support=170.0,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.entry_triggered
    assert result.signal_tier == "TIER_1_ROUTINE"
    assert "Tier 1 (Routine 50 SMA Dip)" in result.tier_detail


def test_etf_earnings_gate_always_passes():
    cfg = default_ticker("QTUM", asset_type="etf")
    snap = _passing_snapshot(ticker="QTUM")
    # Even if today is in the earnings list, ETF ignores it
    result = evaluate(snap, [snap.signal_date], cfg)
    assert result.gate("earnings").passed
    assert "N/A: ETF basket" in result.gate("earnings").detail
    assert result.entry_triggered


@pytest.mark.parametrize("offset", [-5, 0, 5])
def test_earnings_blackout_blocks_inside_window(offset: int):
    cfg = default_ticker("IBM", asset_type="equity")
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

    # Break both Tier 1 and Tier 2 trend
    bad_trend = _passing_snapshot(
        close=150.0,
        low=149.0,
        sma_50=165.0,
        sma_50_slope_up=False,
        sma_50_distance_pct=-9.0,
        sma_200=160.0,
        sma_200_slope_up=False,
        sma_200_reclaim=False,
        sma_200_distance_pct=(150.0 / 160.0 - 1.0) * 100.0,
    )
    res = evaluate(bad_trend, _far_earnings(bad_trend.signal_date), cfg)
    assert not res.entry_triggered
    assert not res.gate("trend").passed


def test_quiet_volume_does_not_block_accumulation():
    cfg = default_ticker()
    snap = _passing_snapshot(volume_multiple=0.25)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("volume").passed
    assert result.entry_triggered


def test_volume_gate_can_still_require_expansion_when_configured():
    cfg = default_ticker(volume_min_multiple=1.2)
    snap = _passing_snapshot(volume_multiple=0.8)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert not result.gate("volume").passed
    assert not result.entry_triggered


def test_weekly_rsi_is_primary_pullback_path():
    cfg = default_ticker()
    # Weekly RSI 45 is < 50.0 (Tier 2 pass), daily RSI 55 is not oversold
    snap = _passing_snapshot(rsi_14=55.0, rsi_14_prev=54.0, rsi_weekly=45.0)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("pullback").passed
    assert result.entry_triggered


def test_daily_rsi_extreme_is_alternate_pullback_path():
    cfg = default_ticker()
    # Weekly RSI 55 (>50.0), but daily RSI 28 (<30.0 extreme)
    snap = _passing_snapshot(rsi_14=28.0, rsi_14_prev=32.0, rsi_weekly=55.0)
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
    assert result.entry_triggered


def test_trend_fails_when_extended_above_moving_averages():
    cfg = default_ticker()
    close = 185.0
    sma_200 = 168.5  # +9.8%
    sma_50 = 170.0   # +8.8%
    snap = _passing_snapshot(
        close=close,
        low=184.0,
        sma_50=sma_50,
        sma_50_slope_up=True,
        sma_50_distance_pct=(close / sma_50 - 1.0) * 100.0,
        sma_200=sma_200,
        sma_200_slope_up=True,
        sma_200_reclaim=False,
        sma_200_distance_pct=(close / sma_200 - 1.0) * 100.0,
        swing_support=184.0,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert not result.gate("trend").passed
    assert not result.entry_triggered


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


def test_calendar_extracts_earnings_not_dividend_dates():
    calendar = {
        "Dividend Date": date(2026, 9, 10),
        "Ex-Dividend Date": date(2026, 8, 10),
        "Earnings Date": [date(2026, 10, 21)],
        "Earnings Average": 2.88,
    }
    assert _extract_dates(calendar) == [date(2026, 10, 21)]
