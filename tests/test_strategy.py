"""Earnings blackout and confluence-gate tests."""

from __future__ import annotations

from datetime import date, timedelta
from dataclasses import replace

import pytest

from finbot.config import default_ticker
from finbot.data import _extract_dates
from finbot.indicators import Snapshot
from finbot.strategy import evaluate, format_report, issue_title


def _passing_snapshot(**overrides) -> Snapshot:
    snap = Snapshot(
        ticker="IBM",
        signal_date=date(2024, 6, 3),
        open=170.0,
        high=172.0,
        low=169.4,
        close=171.0,
        volume=12_000_000.0,
        sma_20=170.6,
        sma_50=168.0,
        sma_200=160.0,
        rsi_14=38.0,
        rsi_14_prev=36.0,
        atr_14=2.0,
        volume_avg_20=8_000_000.0,
        volume_multiple=1.5,
        swing_support=170.2,
        swing_resistance=180.0,
        golden_cross_recent=False,
        close_crossed_above_sma50=False,
        sma50_above_sma200=True,
    )
    return replace(snap, **overrides)


def _far_earnings(signal: date) -> list[date]:
    return [signal + timedelta(days=30)]


def test_all_gates_pass_emits_entry_and_levels():
    cfg = default_ticker()
    snap = _passing_snapshot()
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.entry_triggered
    assert result.summary == "ENTRY"
    assert all(gate.passed for gate in result.gates)
    assert result.levels is not None
    assert result.levels.entry == pytest.approx(171.0)
    assert result.levels.stop == pytest.approx(171.0 - 1.5 * 2.0)
    assert result.levels.targets == (
        (2, 171.0 + 2 * 3.0),
        (3, 171.0 + 3 * 3.0),
    )
    markdown = format_report(result)
    assert "| Close / entry |" in markdown
    assert "| Target 2:1 |" in markdown
    assert "| Target 3:1 |" in markdown
    assert issue_title("IBM", snap.signal_date) == "[ENTRY] IBM 2024-06-03"


@pytest.mark.parametrize("offset", [-5, 0, 5])
def test_earnings_blackout_blocks_inside_window(offset: int):
    cfg = default_ticker()
    snap = _passing_snapshot()
    earnings = [snap.signal_date + timedelta(days=offset)]
    result = evaluate(snap, earnings, cfg)
    assert not result.entry_triggered
    assert not result.gate("earnings").passed
    assert result.levels is None
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
        "trend": _passing_snapshot(close=150.0, low=149.0, sma_200=160.0, golden_cross_recent=False),
        "pullback": _passing_snapshot(rsi_14=55.0, rsi_14_prev=52.0),
        "support": _passing_snapshot(
            swing_support=140.0,
            sma_20=140.0,
            sma_50=138.0,
            sma_200=120.0,
            low=171.0,
        ),
        "volume": _passing_snapshot(volume=8_000_000.0, volume_avg_20=8_000_000.0, volume_multiple=1.0),
    }
    for name, mutated in cases.items():
        result = evaluate(mutated, _far_earnings(mutated.signal_date), cfg)
        assert not result.entry_triggered, name
        assert not result.gate(name).passed, name
        assert result.levels is None, name


def test_rsi_freefall_without_stabilization_fails_pullback():
    cfg = default_ticker()
    snap = _passing_snapshot(rsi_14=20.0, rsi_14_prev=24.0)
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert not result.gate("pullback").passed
    assert not result.entry_triggered
    assert "free-falling" in result.gate("pullback").detail


def test_trend_passes_on_constructive_golden_cross():
    cfg = default_ticker()
    snap = _passing_snapshot(
        close=171.0,
        sma_200=175.0,
        sma_50=168.0,
        golden_cross_recent=True,
        sma50_above_sma200=True,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("trend").passed
    assert result.entry_triggered


def test_trend_passes_on_50_sma_price_cross_with_longer_trend():
    cfg = default_ticker()
    snap = _passing_snapshot(
        close=169.5,
        low=168.8,
        sma_200=175.0,
        sma_50=169.0,
        swing_support=169.0,
        golden_cross_recent=False,
        close_crossed_above_sma50=True,
        sma50_above_sma200=True,
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("trend").passed
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
    )
    result = evaluate(snap, _far_earnings(snap.signal_date), cfg)
    assert result.gate("support").passed
    assert "SMA 50" in result.gate("support").detail


def test_volume_gate_uses_configured_multiple():
    cfg = default_ticker()
    fail = evaluate(
        _passing_snapshot(volume=9_599_999.0, volume_avg_20=8_000_000.0),
        _far_earnings(date(2024, 6, 3)),
        cfg,
    )
    assert not fail.gate("volume").passed
    ok = evaluate(
        _passing_snapshot(volume=9_600_000.0, volume_avg_20=8_000_000.0),
        _far_earnings(date(2024, 6, 3)),
        cfg,
    )
    assert ok.gate("volume").passed


def test_calendar_extracts_earnings_not_dividend_dates():
    calendar = {
        "Dividend Date": date(2026, 9, 10),
        "Ex-Dividend Date": date(2026, 8, 10),
        "Earnings Date": [date(2026, 10, 21)],
        "Earnings Average": 2.88,
    }
    assert _extract_dates(calendar) == [date(2026, 10, 21)]
