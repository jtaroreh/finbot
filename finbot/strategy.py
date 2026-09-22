"""Confluence gates, earnings blackout, and accumulation tranche metadata.

This is a long-term accumulation / buy-tranche scanner, not a swing-entry system.
Two-Tier Accumulation Engine:
- Tier 1 (TIER_1_ROUTINE): Routine 50 SMA pullback during healthy uptrends.
- Tier 2 (TIER_2_MAJOR): Major 200 SMA / deep cyclical washout accumulation.
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
    signal_tier: str = "NONE"
    tier_detail: str = ""
    asset_type: str = "equity"

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
    earnings_gate = _earnings_gate(snapshot.signal_date, earnings_dates, cfg)
    volume_gate = _volume_gate(snapshot, cfg)

    # Evaluate Tier 2 (Major Washout)
    t2_trend = _tier2_trend_gate(snapshot, cfg)
    t2_pullback = _tier2_pullback_gate(snapshot, cfg)
    t2_support = _support_gate(snapshot, cfg, active_tier="TIER_2_MAJOR")
    t2_qualifies = cfg.tier2_enabled and t2_trend.passed and t2_pullback.passed and t2_support.passed

    # Evaluate Tier 1 (Routine 50 SMA Dip)
    t1_trend = _tier1_trend_gate(snapshot, cfg)
    t1_pullback = _tier1_pullback_gate(snapshot, cfg)
    t1_support = _support_gate(snapshot, cfg, active_tier="TIER_1_ROUTINE")
    t1_qualifies = cfg.tier1_enabled and t1_trend.passed and t1_pullback.passed and t1_support.passed

    signal_tier: str
    tier_detail: str
    trend_gate: GateResult
    pullback_gate: GateResult
    support_gate: GateResult

    if t2_qualifies:
        signal_tier = "TIER_2_MAJOR"
        tier_detail = f"Tier 2 (Major Washout / 200 SMA): {t2_trend.detail}; {t2_pullback.detail}"
        trend_gate = t2_trend
        pullback_gate = t2_pullback
        support_gate = t2_support
    elif t1_qualifies:
        signal_tier = "TIER_1_ROUTINE"
        tier_detail = f"Tier 1 (Routine 50 SMA Dip): {t1_trend.detail}; {t1_pullback.detail}"
        trend_gate = t1_trend
        pullback_gate = t1_pullback
        support_gate = t1_support
    else:
        signal_tier = "NONE"
        tier_detail = "Neither Tier 1 (50 SMA routine dip) nor Tier 2 (200 SMA major washout) criteria met"
        if t2_trend.passed:
            trend_gate = t2_trend
        elif t1_trend.passed:
            trend_gate = t1_trend
        else:
            trend_gate = t2_trend if snapshot.sma_200 is not None else t1_trend

        if t2_pullback.passed:
            pullback_gate = t2_pullback
        elif t1_pullback.passed:
            pullback_gate = t1_pullback
        else:
            pullback_gate = t2_pullback

        support_gate = _support_gate(snapshot, cfg, active_tier="ANY")

    gates = (
        earnings_gate,
        trend_gate,
        pullback_gate,
        support_gate,
        volume_gate,
    )

    triggered = (signal_tier != "NONE") and earnings_gate.passed and volume_gate.passed
    if not triggered and signal_tier != "NONE":
        signal_tier = "NONE"

    levels = _levels(snapshot, cfg)
    if triggered:
        summary = "ENTRY"
    else:
        failed = [gate.name for gate in gates if not gate.passed]
        if failed:
            summary = f"NO ENTRY ({', '.join(failed)})"
        else:
            summary = "NO ENTRY"

    return ScanResult(
        ticker=snapshot.ticker,
        signal_date=snapshot.signal_date,
        entry_triggered=triggered,
        snapshot=snapshot,
        gates=gates,
        levels=levels,
        next_earnings=next_earnings,
        summary=summary,
        signal_tier=signal_tier,
        tier_detail=tier_detail,
        asset_type=cfg.asset_type,
    )


def format_report(result: ScanResult) -> str:
    snap = result.snapshot
    marker = issue_marker(result.ticker, result.signal_date)
    slope_200 = "up" if snap.sma_200_slope_up else "down/flat"
    slope_50 = "up" if snap.sma_50_slope_up else "down/flat"
    dist_200 = snap.sma_200_distance_pct
    dist_50 = snap.sma_50_distance_pct
    lines = [
        f"<!-- {marker} -->",
        f"# {result.ticker} {result.signal_date.isoformat()}",
        "",
        "**Setup:** long-term accumulation / buy tranche (not a swing trade)",
        f"**Status:** {result.summary}",
        f"**Signal Tier:** {result.signal_tier}",
        f"**Tier Detail:** {result.tier_detail}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Ticker | {result.ticker} |",
        f"| Asset type | {result.asset_type.upper()} |",
        f"| Signal date | {result.signal_date.isoformat()} |",
        f"| Signal tier | {result.signal_tier} |",
        f"| Open | {_num(snap.open)} |",
        f"| High | {_num(snap.high)} |",
        f"| Low | {_num(snap.low)} |",
        f"| Close / entry | {_num(snap.close)} |",
        f"| Volume | {_int(snap.volume)} |",
        f"| 20-day avg volume (prior) | {_int(snap.volume_avg_20)} |",
        f"| Volume multiple | {_num(snap.volume_multiple)} |",
        f"| SMA 20 | {_num(snap.sma_20)} |",
        f"| SMA 50 | {_num(snap.sma_50)} |",
        f"| SMA 50 slope | {slope_50} |",
        f"| Distance vs SMA 50 | {_pct(dist_50)} |",
        f"| SMA 200 | {_num(snap.sma_200)} |",
        f"| SMA 200 slope | {slope_200} |",
        f"| Distance vs SMA 200 | {_pct(dist_200)} |",
        f"| SMA 200 reclaim | {'yes' if snap.sma_200_reclaim else 'no'} |",
        f"| SMA 200 mature | {'yes' if snap.sma200_is_mature else 'no'} |",
        f"| Daily RSI 14 | {_num(snap.rsi_14)} |",
        f"| Weekly RSI 14 (closed) | {_num(snap.rsi_weekly)} |",
        f"| Weekly RSI 14 (live) | {_num(snap.rsi_weekly_live)} |",
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
    if cfg.invalidation_atr_multiple > 0:
        invalidation_level = entry - cfg.invalidation_atr_multiple * snapshot.atr_14
    else:
        invalidation_level = None

    hint = (
        f"Not a swing take-profit plan. Suggested next buy tranche ~{spacing:g}× ATR "
        f"below entry ({_num(next_tranche)})."
    )
    if invalidation_level is not None:
        hint += (
            f" Wide invalidation context: requires weekly close confirmation below "
            f"~{cfg.invalidation_atr_multiple:g}× ATR ({_num(invalidation_level)}), "
            f"well below the {spacing:g}× ATR next buy tranche."
        )
    return Levels(
        entry=entry,
        next_tranche=next_tranche,
        tranche_spacing_atr=spacing,
        invalidation_level=invalidation_level,
        invalidation_hint=hint,
    )


def _earnings_gate(signal_date: date, earnings_dates: list[date], cfg: TickerConfig) -> GateResult:
    if cfg.asset_type.lower() == "etf":
        return GateResult(
            name="earnings",
            passed=True,
            detail="N/A: ETF basket (no single-name earnings event)",
        )
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


def _tier1_trend_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    """Tier 1 trend: upward-sloping 50 SMA and close within [-undershoot, +proximity] of it."""
    if snapshot.sma_50 is None:
        return GateResult("trend", False, "SMA 50 unavailable")
    dist = snapshot.sma_50_distance_pct
    if dist is None:
        dist = ((snapshot.close / snapshot.sma_50) - 1.0) * 100.0

    in_band = -cfg.sma50_undershoot_pct <= dist <= cfg.sma50_proximity_pct
    if snapshot.sma_50_slope_up and in_band:
        return GateResult(
            "trend",
            True,
            (
                f"Tier 1: Upward-sloping SMA 50; close {_num(dist)}% from "
                f"{_num(snapshot.sma_50)} (band "
                f"-{cfg.sma50_undershoot_pct:g}% to +{cfg.sma50_proximity_pct:g}%)"
            ),
        )
    if not snapshot.sma_50_slope_up and in_band:
        return GateResult(
            "trend",
            False,
            (
                f"Tier 1: Close {_num(dist)}% from SMA 50 {_num(snapshot.sma_50)} "
                "but SMA 50 is not upward-sloping"
            ),
        )
    return GateResult(
        "trend",
        False,
        (
            f"Tier 1: Close {_num(snapshot.close)} is {_num(dist)}% from SMA 50 "
            f"{_num(snapshot.sma_50)} (need −{cfg.sma50_undershoot_pct:g}% to "
            f"+{cfg.sma50_proximity_pct:g}% on a rising 50)"
        ),
    )


def _tier1_pullback_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    """Tier 1 pullback: daily RSI in the routine dip band [rsi_daily_dip_min, rsi_daily_dip_max]."""
    daily = snapshot.rsi_14
    if daily is None:
        return GateResult("pullback", False, "Daily RSI unavailable")
    if cfg.rsi_daily_dip_min <= daily <= cfg.rsi_daily_dip_max:
        return GateResult(
            "pullback",
            True,
            (
                f"Tier 1: Daily RSI {_num(daily)} in routine dip band "
                f"[{cfg.rsi_daily_dip_min:g}, {cfg.rsi_daily_dip_max:g}]"
            ),
        )
    return GateResult(
        "pullback",
        False,
        (
            f"Tier 1: Daily RSI {_num(daily)} not in routine dip band "
            f"[{cfg.rsi_daily_dip_min:g}, {cfg.rsi_daily_dip_max:g}]"
        ),
    )


def _tier2_trend_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    """Tier 2 trend: institutional 200 SMA pullback band or deep washout reclaim."""
    if snapshot.sma_200 is None:
        return GateResult("trend", False, "SMA 200 unavailable")

    dist = snapshot.sma_200_distance_pct
    if dist is None:
        dist = ((snapshot.close / snapshot.sma_200) - 1.0) * 100.0
    in_band = -cfg.sma200_undershoot_pct <= dist <= cfg.sma200_proximity_pct

    if snapshot.sma_200_slope_up and in_band:
        return GateResult(
            "trend",
            True,
            (
                f"Tier 2: Upward-sloping SMA 200; close {_num(dist)}% from "
                f"{_num(snapshot.sma_200)} (band "
                f"-{cfg.sma200_undershoot_pct:g}% to +{cfg.sma200_proximity_pct:g}%)"
            ),
        )

    if snapshot.sma_200_reclaim and snapshot.close > snapshot.sma_200:
        return GateResult(
            "trend",
            True,
            (
                f"Tier 2: Reclaiming SMA 200 after washout; close {_num(snapshot.close)} "
                f"vs SMA 200 {_num(snapshot.sma_200)}"
            ),
        )

    if not snapshot.sma_200_slope_up and in_band:
        return GateResult(
            "trend",
            False,
            (
                f"Tier 2: Close {_num(dist)}% from SMA 200 {_num(snapshot.sma_200)} "
                "but the 200 is not upward-sloping and no washout reclaim"
            ),
        )
    return GateResult(
        "trend",
        False,
        (
            f"Tier 2: Close {_num(snapshot.close)} is {_num(dist)}% from SMA 200 "
            f"{_num(snapshot.sma_200)} (need −{cfg.sma200_undershoot_pct:g}% to "
            f"+{cfg.sma200_proximity_pct:g}% on a rising 200, or a washout reclaim)"
        ),
    )


def _tier2_pullback_gate(snapshot: Snapshot, cfg: TickerConfig) -> GateResult:
    """Tier 2 pullback: weekly RSI < rsi_weekly_max or daily RSI < rsi_daily_extreme."""
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
                f"Tier 2: Weekly RSI {_num(weekly)} < {cfg.rsi_weekly_max:g} "
                "(cyclical accumulation zone)"
            ),
        )
    if daily_ok:
        return GateResult(
            "pullback",
            True,
            (
                f"Tier 2: Daily RSI {_num(daily)} < {cfg.rsi_daily_extreme:g} "
                f"(extreme washout); weekly RSI {_num(weekly)}"
            ),
        )
    if weekly is None and daily is None:
        return GateResult("pullback", False, "Weekly and daily RSI unavailable")
    return GateResult(
        "pullback",
        False,
        (
            f"Tier 2: Weekly RSI {_num(weekly)} not < {cfg.rsi_weekly_max:g} and "
            f"daily RSI {_num(daily)} not < {cfg.rsi_daily_extreme:g}"
        ),
    )


def _support_gate(
    snapshot: Snapshot,
    cfg: TickerConfig,
    active_tier: str = "ANY",
) -> GateResult:
    """Structural support: proximity to relevant moving average or swing support within ATR multiple."""
    if snapshot.atr_14 is None or snapshot.atr_14 <= 0:
        return GateResult("support", False, "ATR 14 unavailable")
    tolerance = cfg.support_atr_multiple * snapshot.atr_14

    candidates: list[tuple[str, float]] = []
    if snapshot.swing_support is not None:
        candidates.append(("structural swing support", snapshot.swing_support))

    if active_tier == "TIER_1_ROUTINE":
        if snapshot.sma_50 is not None:
            candidates.append(("SMA 50", snapshot.sma_50))
    elif active_tier == "TIER_2_MAJOR":
        if snapshot.sma_200 is not None:
            candidates.append(("SMA 200", snapshot.sma_200))
    else:
        for label, val in (("SMA 50", snapshot.sma_50), ("SMA 200", snapshot.sma_200)):
            if val is not None:
                candidates.append((label, val))

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
            f"(need ≤ {_num(tolerance)} = {cfg.support_atr_multiple:g}× ATR)"
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
