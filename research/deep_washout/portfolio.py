"""Equal-contribution portfolio vs monthly DCA, with cash drag and costs."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def month_starts(index: pd.DatetimeIndex) -> np.ndarray:
    periods = index.to_period("M")
    return np.r_[True, periods[1:] != periods[:-1]]


def simulate_equal_cash(
    panel: pd.DataFrame,
    buy_mask: pd.Series,
    units_on_signal: pd.Series | float,
    *,
    start: str,
    contribution: float,
    unit_cash: float,
    slippage_bps: float,
    cost_bps: float,
    spaced: bool = False,
    spacing_atr: float = 2.25,
    spacing_reset_bars: int = 21,
) -> dict[str, Any]:
    """Credit `contribution` on each month-start bar in-window; spend on signals.

    Fill uses next session's open (decision at close). If the month-start bar
    is also a signal, the contribution is available before the next-open fill
    on the following session.
    """
    idx = panel.index
    start_ts = pd.Timestamp(start)
    close = panel["Close"].to_numpy(dtype=float)
    nxt = panel["next_open"].to_numpy(dtype=float)
    atr = panel["atr_14"].to_numpy(dtype=float)
    first = month_starts(idx)
    signal = buy_mask.reindex(idx).fillna(False).to_numpy(dtype=bool)
    if np.isscalar(units_on_signal):
        units = np.full(len(idx), float(units_on_signal))
    else:
        units = units_on_signal.reindex(idx).fillna(0.0).to_numpy(dtype=float)
    slip = 1.0 + (slippage_bps + cost_bps) / 10_000.0

    cash = 0.0
    shares = 0.0
    contributed = 0.0
    deployed = 0.0
    n_buys = 0
    last_buy_i = -10**9
    last_level = np.inf
    idle: list[float] = []
    invested: list[float] = []
    in_window = False

    pending_spend = 0.0  # filled at today's open (from yesterday's signal)

    for i in range(len(idx)):
        ts = idx[i]
        px = close[i]
        if ts >= start_ts:
            in_window = True
        if pending_spend > 0 and in_window:
            fill = nxt[i - 1] * slip if i > 0 else float("nan")
            if np.isfinite(fill) and fill > 0 and cash > 0:
                spend = min(cash, pending_spend)
                shares += spend / fill
                cash -= spend
                deployed += spend
                n_buys += 1
                last_buy_i = i - 1
                if np.isfinite(atr[i - 1]):
                    last_level = close[i - 1] - spacing_atr * atr[i - 1]
            pending_spend = 0.0
        if in_window and first[i]:
            contributed += contribution
            cash += contribution
        if in_window and signal[i] and units[i] > 0:
            allowed = True
            if spaced:
                allowed = (px <= last_level) or (i - last_buy_i >= spacing_reset_bars)
            if allowed:
                pending_spend = unit_cash * float(units[i])
        if in_window and contributed > 0 and np.isfinite(px):
            equity = shares * px + cash
            idle.append(cash / contributed)
            invested.append((shares * px) / equity if equity else 0.0)

    if pending_spend > 0 and contributed > 0:
        # no next open — leave as cash (incomplete, not filled)
        pass

    end_px = next((close[j] for j in range(len(close) - 1, -1, -1) if np.isfinite(close[j])), float("nan"))
    terminal = shares * end_px + cash if np.isfinite(end_px) else float("nan")
    return {
        "start": start,
        "end": str(idx[-1].date()) if len(idx) else None,
        "contributed": contributed,
        "terminal_value": terminal,
        "cash_left": cash,
        "shares": shares,
        "deployed": deployed,
        "n_buys": n_buys,
        "avg_idle_cash_pct": float(np.mean(idle) * 100.0) if idle else None,
        "avg_invested_pct": float(np.mean(invested) * 100.0) if invested else None,
        "terminal_vs_contributed": (terminal / contributed - 1.0) if contributed else None,
    }


def simulate_dca(
    panel: pd.DataFrame,
    *,
    start: str,
    contribution: float,
    slippage_bps: float,
    cost_bps: float,
) -> dict[str, Any]:
    idx = panel.index
    start_ts = pd.Timestamp(start)
    close = panel["Close"].to_numpy(dtype=float)
    first = month_starts(idx)
    slip = 1.0 + (slippage_bps + cost_bps) / 10_000.0
    shares = 0.0
    contributed = 0.0
    n_buys = 0
    invested: list[float] = []
    in_window = False
    for i in range(len(idx)):
        if idx[i] >= start_ts:
            in_window = True
        px = close[i]
        if in_window and first[i] and np.isfinite(px) and px > 0:
            fill = px * slip  # month-start: buy at that session close (schedule, not a scan)
            contributed += contribution
            shares += contribution / fill
            n_buys += 1
        if in_window and contributed > 0 and np.isfinite(px):
            invested.append(1.0)
    end_px = next((close[j] for j in range(len(close) - 1, -1, -1) if np.isfinite(close[j])), float("nan"))
    terminal = shares * end_px if np.isfinite(end_px) else float("nan")
    return {
        "start": start,
        "end": str(idx[-1].date()) if len(idx) else None,
        "contributed": contributed,
        "terminal_value": terminal,
        "cash_left": 0.0,
        "shares": shares,
        "deployed": contributed,
        "n_buys": n_buys,
        "avg_idle_cash_pct": 0.0,
        "avg_invested_pct": 100.0 if invested else None,
        "terminal_vs_contributed": (terminal / contributed - 1.0) if contributed else None,
        "note": "DCA spends the month-start credit at that session close (scheduled, fully invested).",
    }
