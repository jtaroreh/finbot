"""Confluence gates, earnings blackout, and trade levels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from finbot.config import TickerConfig
from finbot.indicators import Snapshot


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Levels:
    entry: float
    stop: float
    risk: float
    targets: tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class ScanResult:
    ticker: str
    signal_date: date
    entry_triggered: bool
    snapshot: Snapshot
    gates: tuple[GateResult, ...]
    levels: Levels | None
    next_earnings: date | None
    summary: str

    def gate(self, name: str) -> GateResult:
        for item in self.gates:
            if item.name == name:
                return item
        raise KeyError(name)


def evaluate(
    snapshot: Snapshot,
    earnings_dates: list[date],
    cfg: TickerConfig,
) -> ScanResult:
    next_earnings = _next_earnings(snapshot.signal_date, earnings_dates)
    gates = (
        _earnings_gate(snapshot.signal_date, earnings_dates, cfg),
        _trend_gate(snapshot),
        _pullback_gate(snapshot, cfg),
        _support_gate(snapshot, cfg),
        _volume_gate(snapshot, cfg),
    )
    triggered = all(gate.passed for gate in gates)
    levels = _levels(snapshot, cfg)
    summary = "ENTRY" if triggered else "NO ENTRY"
    failed = [gate.name for gate in gates if not gate.passed]
    if failed:
        summary = f"NO ENTRY ({', '.join(failed)})"
    return ScanResult(
        ticker=snapshot.ticker,
        signal_date=snapshot.signal_date,
        entry_triggered=triggered,
        snapshot=snapshot,
        gates=gates,
        levels=levels,
        next_earnings=next_earnings,
        summary=summary,
    )


def format_report(result: ScanResult) -> str:
    snap = result.snapshot
    marker = issue_marker(result.ticker, result.signal_date)
    lines = [
        f"<!-- {marker} -->",
        f"# {result.ticker} {result.signal_date.isoformat()}",
        "",
        f"**Status:** {result.summary}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Ticker | {result.ticker} |",
        f"| Signal date | {result.signal_date.isoformat()} |",
        f"| Open | {_num(snap.open)} |",
        f"| High | {_num(snap.high)} |",
        f"| Low | {_num(snap.low)} |",
        f"| Close / entry | {_num(snap.close)} |",
        f"| Volume | {_int(snap.volume)} |",
        f"| 20-day avg volume (prior) | {_int(snap.volume_avg_20)} |",
        f"| Volume multiple | {_num(snap.volume_multiple)} |",
        f"| SMA 20 | {_num(snap.sma_20)} |",
        f"| SMA 50 | {_num(snap.sma_50)} |",
        f"| SMA 200 | {_num(snap.sma_200)} |",
        f"| RSI 14 | {_num(snap.rsi_14)} |",
        f"| ATR 14 | {_num(snap.atr_14)} |",
        f"| Swing support | {_num(snap.swing_support)} |",
        f"| Swing resistance | {_num(snap.swing_resistance)} |",
        f"| Next earnings | {result.next_earnings.isoformat() if result.next_earnings else 'n/a'} |",
        f"| Confluence | {'PASS' if result.entry_triggered else 'FAIL'} |",
    ]
    if result.levels is not None:
        prefix = "" if result.entry_triggered else "Suggested "
        stop_mult = result.levels.risk / snap.atr_14 if snap.atr_14 else 0.0
        lines.append(f"| {prefix}Stop ({stop_mult:g}× ATR) | {_num(result.levels.stop)} |")
        lines.append(f"| {prefix}Risk (entry − stop) | {_num(result.levels.risk)} |")
        for multiple, price in result.levels.targets:
            lines.append(f"| {prefix}Target {multiple}:1 | {_num(price)} |")
    lines.extend(["", "## Gate states", "", "| Gate | Pass | Detail |", "|---|---|---|"])
    for gate in result.gates:
        lines.append(f"| {gate.name} | {'PASS' if gate.passed else 'FAIL'} | {gate.detail} |")
    return "\n".join(lines) + "\n"


def issue_title(ticker: str, signal_date: date) -> str:
    return f"[ENTRY] {ticker} {signal_date.isoformat()}"


def issue_marker(ticker: str, signal_date: date) -> str:
    return f"finbot-entry:{ticker}:{signal_date.isoformat()}"


def _levels(snapshot: Snapshot, cfg: TickerConfig) -> Levels | None:
    if snapshot.atr_14 is None or snapshot.atr_14 <= 0:
        return None
    entry = snapshot.close
    stop = entry - cfg.stop_atr_multiple * snapshot.atr_14
    risk = entry - stop
    if risk <= 0:
        return None
    targets = tuple((multiple, entry + multiple * risk) for multiple in cfg.target_r_multiples)
    return Levels(entry=entry, stop=stop, risk=risk, targets=targets)


def _earnings_gate(signal_date: date, earnings_dates: list[date], cfg: TickerConfig) -> GateResult:
    window = cfg.earnings_blackout_days
    inside = [
        earn
        for earn in earnings_dates
        if abs((earn - signal_date).days) <= window
    ]
    if inside:
        nearest = min(inside, key=lambda earn: abs((earn - signal_date).days))
        return GateResult(
            name="earnings",
            passed=False,
            detail=(
                f"Blackout: earnings {nearest.isoformat()} within {window} "
                f"calendar days of {signal_date.isoformat()}"
            ),
        )
    nearest = _nearest_earnings(signal_date, earnings_dates)
    if nearest is None:
        return GateResult(
            name="earnings",
            passed=True,
            detail="No earnings dates available; blackout not applied",
        )
    return GateResult(
        name="earnings",
        passed=True,
        detail=f"Nearest earnings {nearest.isoformat()} outside {window}-day window",
    )


def _trend_gate(snapshot: Snapshot) -> GateResult:
    if snapshot.sma_200 is None:
        return GateResult("trend", False, "SMA 200 unavailable")
    if snapshot.close > snapshot.sma_200:
        return GateResult(
            "trend",
            True,
            f"Close {_num(snapshot.close)} above SMA 200 {_num(snapshot.sma_200)}",
        )
    if snapshot.golden_cross_recent and snapshot.sma_50 is not None and snapshot.close > snapshot.sma_50:
        return GateResult(
            "trend",
            True,
            "Constructive 50/200 golden cross; close above SMA 50",
        )
    if snapshot.close_crossed_above_sma50 and snapshot.sma50_above_sma200:
        return GateResult(
            "trend",
            True,
            "Close crossed above SMA 50 with SMA 50 above SMA 200",
        )
    return GateResult(
        "trend",
        False,
        (
            f"Close {_num(snapshot.close)} not above SMA 200 {_num(snapshot.sma_200)} "
            "and no constructive 50 SMA cross"
        ),
    )


def _pullback_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    if snapshot.rsi_14 is None:
        return GateResult("pullback", False, "RSI 14 unavailable")
    rsi = snapshot.rsi_14
    prev = snapshot.rsi_14_prev
    declining = prev is not None and rsi < prev
    if rsi < cfg.rsi_freefall and declining:
        return GateResult(
            "pullback",
            False,
            f"RSI {_num(rsi)} free-falling below {cfg.rsi_freefall} (prev {_num(prev)})",
        )
    if cfg.rsi_pullback_min <= rsi <= cfg.rsi_pullback_max:
        return GateResult(
            "pullback",
            True,
            f"RSI {_num(rsi)} in {cfg.rsi_pullback_min:g}–{cfg.rsi_pullback_max:g} zone",
        )
    if (
        cfg.rsi_freefall <= rsi < cfg.rsi_pullback_min
        and prev is not None
        and rsi >= prev
    ):
        return GateResult(
            "pullback",
            False,
            f"RSI {_num(rsi)} stabilizing but still below {cfg.rsi_pullback_min:g}",
        )
    return GateResult(
        "pullback",
        False,
        f"RSI {_num(rsi)} outside {cfg.rsi_pullback_min:g}–{cfg.rsi_pullback_max:g} pullback zone",
    )


def _support_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    if snapshot.atr_14 is None or snapshot.atr_14 <= 0:
        return GateResult("support", False, "ATR 14 unavailable")
    tolerance = cfg.support_atr_multiple * snapshot.atr_14
    candidates: list[tuple[str, float]] = []
    if snapshot.swing_support is not None:
        candidates.append(("swing support", snapshot.swing_support))
    for label, value in (
        ("SMA 20", snapshot.sma_20),
        ("SMA 50", snapshot.sma_50),
        ("SMA 200", snapshot.sma_200),
    ):
        if value is not None:
            candidates.append((label, value))
    if not candidates:
        return GateResult("support", False, "No support references available")

    best: tuple[str, float, float] | None = None
    for label, level in candidates:
        dist = min(abs(snapshot.close - level), abs(snapshot.low - level))
        if best is None or dist < best[2]:
            best = (label, level, dist)
        if dist <= tolerance and snapshot.close >= level - tolerance:
            atr_mult = dist / snapshot.atr_14
            return GateResult(
                "support",
                True,
                (
                    f"Retest of {label} {_num(level)} "
                    f"(distance {_num(dist)} = {_num(atr_mult)}× ATR)"
                ),
            )

    assert best is not None
    return GateResult(
        "support",
        False,
        (
            f"Nearest {best[0]} {_num(best[1])} is {_num(best[2])} away "
            f"(need ≤ {_num(tolerance)} = {cfg.support_atr_multiple:g}× ATR)"
        ),
    )


def _volume_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    if snapshot.volume_avg_20 is None or snapshot.volume_avg_20 <= 0:
        return GateResult("volume", False, "20-day average volume unavailable")
    multiple = snapshot.volume / snapshot.volume_avg_20
    if multiple >= cfg.volume_min_multiple:
        return GateResult(
            "volume",
            True,
            f"Volume {_int(snapshot.volume)} = {_num(multiple)}× 20-day average",
        )
    return GateResult(
        "volume",
        False,
        (
            f"Volume {_int(snapshot.volume)} = {_num(multiple)}× 20-day average "
            f"(need ≥ {cfg.volume_min_multiple:g}×)"
        ),
    )


def _next_earnings(signal_date: date, earnings_dates: list[date]) -> date | None:
    future = [earn for earn in earnings_dates if earn >= signal_date]
    if future:
        return min(future)
    return _nearest_earnings(signal_date, earnings_dates)


def _nearest_earnings(signal_date: date, earnings_dates: list[date]) -> date | None:
    if not earnings_dates:
        return None
    return min(earnings_dates, key=lambda earn: abs((earn - signal_date).days))


def _num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _int(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{int(round(value)):,}"
