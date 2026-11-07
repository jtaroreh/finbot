"""Event-study metrics with complete-window rule, episodes, bootstrap, Newey–West."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def fill_and_horizon(
    panel: pd.DataFrame,
    signal: pd.Series,
    horizon: int,
    slippage_bps: float,
    cost_bps: float,
) -> pd.DataFrame:
    """One row per signal with a complete H-day window after the fill bar.

    Signal at close i; fill at Open[i+1] * (1 + slippage + cost).
    Exit at Close[fill_idx + horizon]. MAE uses lows from fill_idx through exit.
    """
    idx = panel.index
    close = panel["Close"].to_numpy(dtype=float)
    low = panel["Low"].to_numpy(dtype=float)
    nxt = panel["next_open"].to_numpy(dtype=float)
    flags = signal.fillna(False).to_numpy(dtype=bool)
    slip = 1.0 + (slippage_bps + cost_bps) / 10_000.0
    rows: list[dict[str, Any]] = []
    n = len(panel)
    for i in np.flatnonzero(flags):
        fill_i = int(i) + 1
        exit_i = fill_i + int(horizon)
        if fill_i >= n or exit_i >= n:
            continue
        fill = nxt[i] * slip
        if not np.isfinite(fill) or fill <= 0:
            continue
        if not np.isfinite(close[exit_i]):
            continue
        window_low = np.nanmin(low[fill_i : exit_i + 1])
        fwd = close[exit_i] / fill - 1.0
        mae = window_low / fill - 1.0
        rows.append(
            {
                "signal_date": str(idx[i].date()),
                "fill_date": str(idx[fill_i].date()),
                "exit_date": str(idx[exit_i].date()),
                "signal_i": int(i),
                "fill_i": fill_i,
                "exit_i": exit_i,
                "fill": float(fill),
                "fwd_return": float(fwd),
                "mae": float(mae),
                "hit": bool(fwd > 0.0),
            }
        )
    return pd.DataFrame(rows)


def mark_episodes(events: pd.DataFrame, gap_bars: int) -> pd.DataFrame:
    if events.empty:
        events = events.copy()
        events["episode_id"] = pd.Series(dtype=int)
        events["episode_first"] = pd.Series(dtype=bool)
        return events
    out = events.sort_values("signal_i").copy()
    episode = []
    first = []
    last_start = None
    eid = -1
    for sig_i in out["signal_i"].to_numpy():
        if last_start is None or int(sig_i) - int(last_start) >= int(gap_bars):
            eid += 1
            last_start = int(sig_i)
            first.append(True)
        else:
            first.append(False)
        episode.append(eid)
    out["episode_id"] = episode
    out["episode_first"] = first
    return out


def newey_west_se(values: np.ndarray, lag: int) -> float:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return float("nan")
    xc = x - x.mean()
    gamma0 = float(np.dot(xc, xc) / n)
    var = gamma0
    l = min(int(lag), n - 1)
    for k in range(1, l + 1):
        w = 1.0 - k / (l + 1)
        g = float(np.dot(xc[k:], xc[:-k]) / n)
        var += 2.0 * w * g
    return float(np.sqrt(max(var, 0.0) / n))


def bootstrap_ci(
    values: np.ndarray,
    *,
    seed: int,
    n_boot: int,
    ci_level: float,
    statistic: str = "mean",
) -> tuple[float, float, float]:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot, dtype=float)
    n = len(x)
    for b in range(n_boot):
        draw = rng.choice(x, size=n, replace=True)
        if statistic == "hit":
            stats[b] = float((draw > 0).mean())
        else:
            stats[b] = float(draw.mean())
    alpha = (1.0 - ci_level) / 2.0
    lo, hi = np.quantile(stats, [alpha, 1.0 - alpha])
    point = float((x > 0).mean()) if statistic == "hit" else float(x.mean())
    return point, float(lo), float(hi)


def summarize_events(
    events: pd.DataFrame,
    *,
    horizon: int,
    seed: int,
    n_boot: int,
    ci_level: float,
    use_episode_first: bool,
) -> dict[str, Any]:
    empty = {
        "n": 0,
        "n_independent": 0,
        "mean_return": None,
        "median_return": None,
        "hit_rate": None,
        "median_mae": None,
        "mean_mae": None,
        "mean_return_ci_lo": None,
        "mean_return_ci_hi": None,
        "hit_rate_ci_lo": None,
        "hit_rate_ci_hi": None,
        "nw_se_overlapping": None,
        "complete_windows_only": True,
        "horizon": horizon,
    }
    if events is None or events.empty:
        return empty
    n_ind = int(events["episode_id"].nunique()) if "episode_id" in events else 0
    work = events[events["episode_first"]] if use_episode_first and "episode_first" in events else events
    if work.empty:
        empty["n"] = int(len(events))
        empty["n_independent"] = n_ind
        return empty
    rets = work["fwd_return"].to_numpy(dtype=float)
    mae = work["mae"].to_numpy(dtype=float)
    mean_r, lo, hi = bootstrap_ci(rets, seed=seed, n_boot=n_boot, ci_level=ci_level, statistic="mean")
    hit, hlo, hhi = bootstrap_ci(rets, seed=seed, n_boot=n_boot, ci_level=ci_level, statistic="hit")
    ov = events["fwd_return"].to_numpy(dtype=float)
    return {
        "n": int(len(events)),
        "n_independent": n_ind,
        "n_scored": int(len(work)),
        "mean_return": mean_r,
        "median_return": float(np.median(rets)),
        "hit_rate": hit,
        "median_mae": float(np.median(mae)),
        "mean_mae": float(np.mean(mae)),
        "mean_return_ci_lo": lo,
        "mean_return_ci_hi": hi,
        "hit_rate_ci_lo": hlo,
        "hit_rate_ci_hi": hhi,
        "nw_se_overlapping": newey_west_se(ov, lag=horizon),
        "complete_windows_only": True,
        "horizon": horizon,
        "scoring": "episode_first" if use_episode_first else "overlapping",
    }


def in_window(panel: pd.DataFrame, start: str | None, end: str | None = None) -> pd.Series:
    ok = pd.Series(True, index=panel.index)
    if start:
        ok &= panel.index >= pd.Timestamp(start)
    if end:
        ok &= panel.index < pd.Timestamp(end)
    return ok
