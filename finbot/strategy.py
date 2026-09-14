"""Confluence gates, earnings blackout, and accumulation tranche metadata.

This is a long-term accumulation / buy-tranche scanner, not a swing-entry system.
2R/3R targets are not part of the entry decision or the Issue plan. ATR is used
for suggested tranche spacing and wide invalidation context only.
"""

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
    """Buy-tranche metadata — not a swing stop/target plan."""

    entry: float
    next_tranche: float
    tranche_spacing_atr: float
    invalidation_level: float | None
    invalidation_hint: str


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
        _trend_gate(snapshot, cfg),
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
    slope = "up" if snap.sma_200_slope_up else "down/flat"
    dist = snap.sma_200_distance_pct
    lines = [
        f"<!-- {marker} -->",
        f"# {result.ticker} {result.signal_date.isoformat()}",
        "",
        "**Setup:** long-term accumulation / buy tranche (not a swing trade)",
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
        f"| SMA 200 slope | {slope} |",
        f"| Distance vs SMA 200 | {_pct(dist)} |",
        f"| SMA 200 reclaim | {'yes' if snap.sma_200_reclaim else 'no'} |",
        f"| Daily RSI 14 | {_num(snap.rsi_14)} |",
        f"| Weekly RSI 14 | {_num(snap.rsi_weekly)} |",
        f"| ATR 14 | {_num(snap.atr_14)} |",
        f"| Structural swing support | {_num(snap.swing_support)} |",
        f"| Structural swing resistance | {_num(snap.swing_resistance)} |",
        f"| Next earnings | {result.next_earnings.isoformat() if result.next_earnings else 'n/a'} |",
        f"| Confluence | {'PASS' if result.entry_triggered else 'FAIL'} |",
    ]
    if result.levels is not None:
        lines.append(
            f"| Next tranche ({result.levels.tranche_spacing_atr:g}× ATR below) | "
            f"{_num(result.levels.next_tranche)} |"
        )
        lines.append(f"| Invalidation level | {_num(result.levels.invalidation_level)} |")
        lines.append(f"| Invalidation hint | {result.levels.invalidation_hint} |")
    lines.extend(["", "## Gate states", "", "| Gate | Pass | Detail |", "|---|---|---|"])
    for gate in result.gates:
        lines.append(f"| {gate.name} | {'PASS' if gate.passed else 'FAIL'} | {gate.detail} |")
    return "\n".join(lines) + "\n"


def issue_title(ticker: str, signal_date: date) -> str:
    return f"[ACCUMULATION] {ticker} {signal_date.isoformat()}"


def issue_marker(ticker: str, signal_date: date) -> str:
    return f"finbot-accumulation:{ticker}:{signal_date.isoformat()}"


def _levels(snapshot: Snapshot, cfg: TickerConfig) -> Levels | None:
    if snapshot.atr_14 is None or snapshot.atr_14 <= 0:
        return None
    entry = snapshot.close
    spacing = cfg.tranche_spacing_atr
    next_tranche = entry - spacing * snapshot.atr_14
    invalidation_level: float | None
    if snapshot.sma_200 is not None:
        invalidation_level = snapshot.sma_200
    elif cfg.invalidation_atr_multiple > 0:
        invalidation_level = entry - cfg.invalidation_atr_multiple * snapshot.atr_14
    else:
        invalidation_level = None
    hint = (
        f"Not a swing take-profit plan. Suggested next buy tranche ~{spacing:g}× ATR "
        f"below entry ({_num(next_tranche)})."
    )
    if snapshot.sma_200 is not None:
        hint += (
            f" Wide invalidation context: daily close sustainably below SMA 200 "
            f"({_num(snapshot.sma_200)}), not a 1.5× ATR stop."
        )
    elif invalidation_level is not None:
        hint += (
            f" Wide invalidation context: ~{cfg.invalidation_atr_multiple:g}× ATR "
            f"below entry ({_num(invalidation_level)})."
        )
    return Levels(
        entry=entry,
        next_tranche=next_tranche,
        tranche_spacing_atr=spacing,
        invalidation_level=invalidation_level,
        invalidation_hint=hint,
    )


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


def _trend_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    """SMA 200 is the primary accumulation filter.

    Pass if either:

    (A) Pullback to the institutional 200 SMA: the 200 is upward-sloping
        (SMA200[t] > SMA200[t - sma200_slope_lookback]) and close is inside
        [-sma200_undershoot_pct, +sma200_proximity_pct] percent of it
        (default −1% to +3%). Sitting on the average (0%) counts.

    (B) Reclaim after a deep washout: close is back above the 200 SMA, at least
        one bar in sma200_washout_lookback was >= sma200_washout_pct below it,
        and price traded at or below the 200 within sma200_reclaim_recent_bars.

    Golden-cross / 50 SMA continuation paths are not a trend pass.
    """
    if snapshot.sma_200 is None:
        return GateResult("trend", False, "SMA 200 unavailable")

    dist = snapshot.sma_200_distance_pct
    if dist is None:
        dist = (snapshot.close / snapshot.sma_200 - 1.0) * 100.0
    in_band = -cfg.sma200_undershoot_pct <= dist <= cfg.sma200_proximity_pct

    if snapshot.sma_200_slope_up and in_band:
        return GateResult(
            "trend",
            True,
            (
                f"Upward-sloping SMA 200; close {_num(dist)}% from "
                f"{_num(snapshot.sma_200)} (band "
                f"-{cfg.sma200_undershoot_pct:g}% to +{cfg.sma200_proximity_pct:g}%)"
            ),
        )

    if snapshot.sma_200_reclaim and snapshot.close > snapshot.sma_200:
        return GateResult(
            "trend",
            True,
            (
                f"Reclaiming SMA 200 after washout; close {_num(snapshot.close)} "
                f"vs SMA 200 {_num(snapshot.sma_200)}"
            ),
        )

    if not snapshot.sma_200_slope_up and in_band:
        return GateResult(
            "trend",
            False,
            (
                f"Close {_num(dist)}% from SMA 200 {_num(snapshot.sma_200)} "
                "but the 200 is not upward-sloping and no washout reclaim"
            ),
        )
    return GateResult(
        "trend",
        False,
        (
            f"Close {_num(snapshot.close)} is {_num(dist)}% from SMA 200 "
            f"{_num(snapshot.sma_200)} (need −{cfg.sma200_undershoot_pct:g}% to "
            f"+{cfg.sma200_proximity_pct:g}% on a rising 200, or a washout reclaim)"
        ),
    )


def _pullback_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    """Cyclical oversold: weekly RSI below rsi_weekly_max (primary), or daily RSI extreme."""
    weekly = snapshot.rsi_weekly
    daily = snapshot.rsi_14
    weekly_on = cfg.rsi_weekly_max > 0
    daily_on = cfg.rsi_daily_extreme > 0
    weekly_ok = weekly_on and weekly is not None and weekly < cfg.rsi_weekly_max
    daily_ok = daily_on and daily is not None and daily < cfg.rsi_daily_extreme

    if weekly_ok:
        return GateResult(
            "pullback",
            True,
            (
                f"Weekly RSI {_num(weekly)} < {cfg.rsi_weekly_max:g} "
                "(cyclical accumulation zone)"
            ),
        )
    if daily_ok:
        return GateResult(
            "pullback",
            True,
            (
                f"Daily RSI {_num(daily)} < {cfg.rsi_daily_extreme:g} "
                f"(extreme washout); weekly RSI {_num(weekly)}"
            ),
        )
    if weekly is None and daily is None:
        return GateResult("pullback", False, "Weekly and daily RSI unavailable")
    return GateResult(
        "pullback",
        False,
        (
            f"Weekly RSI {_num(weekly)} not < {cfg.rsi_weekly_max:g} and "
            f"daily RSI {_num(daily)} not < {cfg.rsi_daily_extreme:g}"
        ),
    )


def _support_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    """Structural support: 10/10 swing low, SMA 200 (percentage band or ATR), or SMA 50."""
    if snapshot.atr_14 is None or snapshot.atr_14 <= 0:
        return GateResult("support", False, "ATR 14 unavailable")
    tolerance = cfg.support_atr_multiple * snapshot.atr_14

    if snapshot.sma_200 is not None:
        dist_pct = abs(snapshot.close / snapshot.sma_200 - 1.0) * 100.0
        band = max(cfg.sma200_proximity_pct, cfg.sma200_undershoot_pct)
        if dist_pct <= band and snapshot.close >= snapshot.sma_200 * (1 - cfg.sma200_undershoot_pct / 100.0):
            return GateResult(
                "support",
                True,
                (
                    f"At institutional SMA 200 {_num(snapshot.sma_200)} "
                    f"(distance {_num(dist_pct)}%)"
                ),
            )

    candidates: list[tuple[str, float]] = []
    if snapshot.swing_support is not None:
        candidates.append(("structural swing support", snapshot.swing_support))
    for label, value in (("SMA 50", snapshot.sma_50), ("SMA 200", snapshot.sma_200)):
        if value is not None:
            candidates.append((label, value))
    if not candidates:
        return GateResult("support", False, "No structural support references available")

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
            f"(need ≤ {_num(tolerance)} = {cfg.support_atr_multiple:g}× ATR "
            f"or within {cfg.sma200_proximity_pct:g}% of SMA 200)"
        ),
    )


def _volume_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    """Volume expansion is optional. Default 0 disables the 1.2× swing requirement."""
    multiple = snapshot.volume_multiple
    if multiple is None and snapshot.volume_avg_20 not in (None, 0):
        multiple = snapshot.volume / snapshot.volume_avg_20

    if cfg.volume_min_multiple <= 0:
        if multiple is None:
            return GateResult(
                "volume",
                True,
                "Volume expansion not required for accumulation",
            )
        return GateResult(
            "volume",
            True,
            (
                f"Volume expansion not required for accumulation; "
                f"actual {_num(multiple)}× 20-day average"
            ),
        )

    if snapshot.volume_avg_20 is None or snapshot.volume_avg_20 <= 0 or multiple is None:
        return GateResult("volume", False, "20-day average volume unavailable")
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


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def _int(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{int(round(value)):,}"
