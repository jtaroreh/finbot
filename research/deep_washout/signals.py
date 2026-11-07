"""Frozen candidate masks. Thresholds come from config.yaml only."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def core_location_mask(panel: pd.DataFrame, core: dict[str, Any]) -> pd.Series:
    ok = pd.Series(True, index=panel.index)
    if core.get("require_sma200_mature", True):
        ok &= panel["sma200_mature"].fillna(False)
    if core.get("require_sma200_rising", False):
        ok &= panel["sma_200_slope_up"].fillna(False)
    ok &= panel["sma_200"].notna()
    ok &= panel["atr_14"].notna()
    ok &= panel["z_sma200"].notna()
    ok &= panel["pct_below_sma200"] >= float(core["min_pct_below_sma200"])
    ok &= panel["atr_below_sma200"] >= float(core["min_atr_below_sma200"])
    ok &= panel["z_sma200"] <= float(core["max_zscore"])
    return ok.fillna(False)


def rsi_mask(panel: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    daily = panel["rsi_14"] <= float(spec["rsi_daily_max"])
    weekly = panel["rsi_weekly"] <= float(spec["rsi_weekly_max"])
    combine = str(spec.get("rsi_combine", "or")).lower()
    if combine == "and":
        return (daily & weekly).fillna(False)
    return (daily | weekly).fillna(False)


def confirmation_mask(panel: pd.DataFrame, kind: str) -> pd.Series:
    if kind in (None, "", "none"):
        return pd.Series(True, index=panel.index)
    if kind == "prior_high_5":
        return panel["close_above_prior_high_5"].fillna(False)
    if kind == "prior_high_10":
        return panel["close_above_prior_high_10"].fillna(False)
    raise ValueError(f"Unknown confirmation: {kind}")


def candidate_mask(panel: pd.DataFrame, core: dict[str, Any], spec: dict[str, Any]) -> pd.Series:
    ok = core_location_mask(panel, core) & rsi_mask(panel, spec)
    ok &= confirmation_mask(panel, str(spec.get("confirmation") or "none"))
    vol_min = float(spec.get("volume_min_multiple") or 0.0)
    if vol_min > 0:
        ok &= panel["vol_mult"] >= vol_min
    dd = float(spec.get("min_drawdown_52w_pct") or 0.0)
    if dd > 0:
        ok &= panel["drawdown_52w_pct"] <= -dd
    if spec.get("require_divergence"):
        ok &= panel["rsi_div_causal"].fillna(False)
    ok &= panel["has_next_session"].fillna(False)
    return ok.fillna(False)


def all_candidate_masks(panel: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, pd.Series]:
    core = protocol["core_location"]
    out: dict[str, pd.Series] = {}
    for name, spec in protocol["candidates"].items():
        out[name] = candidate_mask(panel, core, spec)
    return out


def baseline_masks(panel: pd.DataFrame) -> dict[str, pd.Series]:
    mature = panel["sma200_mature"].fillna(False) & panel["sma_200"].notna()
    has_fill = panel["has_next_session"].fillna(False)
    matched = mature & (panel["Close"] < panel["sma_200"]) & (~panel["sma_50_slope_up"].fillna(False))
    return {
        "UNCONDITIONAL": mature & has_fill,
        "MATCHED_REGIME": matched.fillna(False) & has_fill,
        "TIER1_EXACT": panel["tier1"].fillna(False) & has_fill,
        "TIER2_EXACT": panel["tier2"].fillna(False) & has_fill,
        "TIER12_EXACT": panel["tier12"].fillna(False) & has_fill,
    }


def naive_next_tranche_mask(panel: pd.DataFrame) -> pd.Series:
    """Diagnostic: first close at/below the latest T1/T2 next_tranche. Not a candidate."""
    n = len(panel)
    flags = np.zeros(n, dtype=bool)
    pending: float | None = None
    used = False
    close = panel["Close"].to_numpy(dtype=float)
    nxt = panel["next_tranche"].to_numpy(dtype=float)
    t12 = panel["tier12"].to_numpy(dtype=bool)
    for i in range(n):
        if t12[i] and np.isfinite(nxt[i]):
            pending = float(nxt[i])
            used = False
            continue
        if pending is None or used:
            continue
        if np.isfinite(close[i]) and close[i] <= pending:
            flags[i] = True
            used = True
    out = pd.Series(flags, index=panel.index)
    return out & panel["has_next_session"].fillna(False)
