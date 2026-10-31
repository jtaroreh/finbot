"""One-off research: do finbot's accumulation triggers buy cheaper than monthly DCA?

Reuses finbot's own config (config/watchlist.yaml) and indicator functions so the
rules match the live scanner. Nothing here is imported by the live scan.

Rules replayed day by day, causally (only data known at that close):
  TIER_2_MAJOR (2 units): 200 SMA trend gate (rising 200 and close in
      [-1%, +3%], or washout reclaim) AND weekly RSI(14, closed weeks) < 50 or
      daily RSI < 30, AND support (swing low or 200 SMA within 1x ATR).
  TIER_1_ROUTINE (1 unit): rising 50 SMA (vs 10 bars ago), close in
      [-1.5%, +3%] of it, daily RSI in [35, 48], support (swing low or 50 SMA
      within 1x ATR). Tier 2 wins when both qualify.
  Earnings blackout (+/-5 calendar days) for single names (IBM, SPCX), not ETFs.
  Volume gate is off in config (volume_min_multiple = 0).
  Swing lows are 10/10 fractals and only count once confirmed (10 bars later).

Live finbot has no cooldown: every qualifying session is an alert. The
"literal" variant buys on every alert. The "spaced" variant is an assumption:
after a buy, the next one waits until close <= last entry - 2.25x ATR (the
next-tranche level the report prints) or 21 sessions have passed.

Money: both strategies get the same $1,000 on the first trading day of every
month. DCA buys immediately. Finbot holds it as cash and spends 1 unit
($1,000) per Tier 1 alert and 2 units per Tier 2 alert, capped by cash on hand.

Usage:  python research/tranche_backtest.py [--out research/out]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finbot.config import TickerConfig, load_config  # noqa: E402
from finbot.data import _extract_dates, _normalize_ohlcv  # noqa: E402
from finbot.indicators import atr, rsi, sma, swing_lows, weekly_close  # noqa: E402

MONTHLY = 1000.0
UNIT = 1000.0
RESET_BARS = 21
IBM_START = "2000-01-01"  # warmup; earnings history from yfinance starts 2002


@dataclass
class Signals:
    tier: np.ndarray  # 0 none, 1, 2
    close: np.ndarray
    atr: np.ndarray
    index: pd.DatetimeIndex


def load(symbol: str) -> pd.DataFrame:
    frame = yf.Ticker(symbol).history(period="max", interval="1d", auto_adjust=True)
    frame = _normalize_ohlcv(frame)
    if symbol == "IBM":
        frame = frame.loc[IBM_START:]
    return frame


def earnings_history(symbol: str, asset_type: str) -> list:
    """Live finbot asks for 16 dates; the backtest needs the full history."""
    if asset_type == "etf":
        return []
    try:
        return sorted(set(_extract_dates(yf.Ticker(symbol).get_earnings_dates(limit=100))))  # Yahoo caps at 100
    except Exception:  # noqa: BLE001
        return []


def signals(df: pd.DataFrame, cfg: TickerConfig, earnings: list) -> Signals:
    c, h, l = df["Close"], df["High"], df["Low"]
    a = atr(h, l, c, cfg.atr_period)
    r = rsi(c, cfg.rsi_period)
    s50 = sma(c, cfg.sma_mid)
    s200 = sma(c, cfg.sma_slow)

    # weekly RSI on completed weeks only (same as complete_only=True live)
    wk = rsi(weekly_close(c), cfg.rsi_period)
    wd = np.asarray(df.index.weekday)
    shift = np.where(wd == 4, 0, (4 - wd) % 7 - 7)  # Friday: this week; else last Friday
    labels = df.index.normalize() + pd.to_timedelta(shift, unit="D")
    wrsi = pd.Series(wk.reindex(labels).to_numpy(), index=df.index)

    swing = swing_lows(l, cfg.swing_left, cfg.swing_right)
    swing_known = swing.shift(cfg.swing_right).ffill()  # confirmed 10 bars later

    def support(level: pd.Series) -> pd.Series:
        ok = pd.Series(False, index=df.index)
        for lv in (swing_known, level):
            dist = np.minimum((c - lv).abs(), (l - lv).abs())
            ok |= (dist <= cfg.support_atr_multiple * a) & (c >= lv - cfg.support_atr_multiple * a)
        return ok.fillna(False)

    # Tier 1
    d50 = (c / s50 - 1) * 100
    up50 = s50 > s50.shift(cfg.sma50_slope_lookback)
    t1 = (
        cfg.tier1_enabled
        & up50
        & d50.between(-cfg.sma50_undershoot_pct, cfg.sma50_proximity_pct)
        & r.between(cfg.rsi_daily_dip_min, cfg.rsi_daily_dip_max)
        & support(s50)
    )

    # Tier 2
    d200 = (c / s200 - 1) * 100
    up200 = s200 > s200.shift(cfg.sma200_slope_lookback)
    band = d200.between(-cfg.sma200_undershoot_pct, cfg.sma200_proximity_pct)
    washed = (d200 <= -cfg.sma200_washout_pct).astype(float).rolling(
        cfg.sma200_washout_lookback, min_periods=1).max() > 0
    below = (c <= s200).astype(float).shift(1).rolling(
        cfg.sma200_reclaim_recent_bars, min_periods=1).max() > 0
    reclaim = (c > s200) & washed & below
    trend2 = (up200 & band) | reclaim
    pull2 = (wrsi < cfg.rsi_weekly_max) | (r < cfg.rsi_daily_extreme)
    t2 = cfg.tier2_enabled & s200.notna() & trend2 & pull2 & support(s200)

    ok_earn = np.ones(len(df), dtype=bool)
    if cfg.asset_type != "etf" and earnings:
        days = np.array([d.toordinal() for d in df.index.date])
        e = np.array([d.toordinal() for d in earnings])
        ok_earn = np.abs(days[:, None] - e[None, :]).min(axis=1) > cfg.earnings_blackout_days

    tier = np.where(t2.fillna(False).to_numpy(), 2, np.where(t1.fillna(False).to_numpy(), 1, 0))
    tier = np.where(ok_earn, tier, 0)
    return Signals(tier, c.to_numpy(), a.to_numpy(), df.index)


def month_starts(idx: pd.DatetimeIndex) -> np.ndarray:
    m = idx.to_period("M")
    first = np.r_[True, m[1:] != m[:-1]]
    return first


def simulate(sig: Signals, start: int, end: int, spaced: bool) -> dict:
    """Run finbot and DCA on bars [start, end)."""
    idx, close, a = sig.index, sig.close, sig.atr
    first = month_starts(idx)
    cash = contributed = 0.0
    fb_sh = dca_sh = 0.0
    t1 = t2 = 0
    last_i, last_level = -10**9, np.inf
    idle_frac, fb_ratio, dca_ratio, dates, fb_cost, dca_cost = [], [], [], [], [], []
    deployed = 0.0
    for i in range(start, end):
        p = close[i]
        if first[i]:
            contributed += MONTHLY
            cash += MONTHLY
            dca_sh += MONTHLY / p
        t = sig.tier[i]
        if t and cash > 0:
            allowed = (not spaced) or p <= last_level or (i - last_i) >= RESET_BARS
            if allowed:
                spend = min(cash, UNIT * t)
                fb_sh += spend / p
                cash -= spend
                deployed += spend
                t1 += t == 1
                t2 += t == 2
                last_i = i
                last_level = p - 2.25 * a[i] if not np.isnan(a[i]) else -np.inf
        if contributed:
            idle_frac.append(cash / contributed)
            fb_ratio.append((fb_sh * p + cash) / contributed)
            dca_ratio.append(dca_sh * p / contributed)
            dates.append(idx[i])
            fb_cost.append(deployed / fb_sh if fb_sh else np.nan)
            dca_cost.append(contributed / dca_sh)
    p_end = close[end - 1]
    fb_val_idle = fb_sh * p_end + cash
    fb_val_dep = (fb_sh + cash / p_end) * p_end
    dca_val = dca_sh * p_end

    def mdd(x: list) -> float:
        s = pd.Series(x)
        return float((s / s.cummax() - 1).min() * 100) if len(s) else float("nan")

    return {
        "start": str(idx[start].date()),
        "end": str(idx[end - 1].date()),
        "contributed": contributed,
        "dca_avg_cost": contributed / dca_sh if dca_sh else None,
        "fb_avg_cost": deployed / fb_sh if fb_sh else None,
        "fb_avg_cost_end_deployed": (deployed + cash) / (fb_sh + cash / p_end) if contributed else None,
        "fb_value_idle": fb_val_idle,
        "fb_value_end_deployed": fb_val_dep,
        "dca_value": dca_val,
        "idle_pct": float(np.mean(idle_frac) * 100) if idle_frac else None,
        "cash_left_at_end": cash,
        "tier1_tranches": int(t1),
        "tier2_tranches": int(t2),
        "fb_max_dd_pct": mdd(fb_ratio),
        "dca_max_dd_pct": mdd(dca_ratio),
        "series": {
            "dates": [str(d.date()) for d in dates],
            "fb_ratio": fb_ratio,
            "dca_ratio": dca_ratio,
            "fb_cost": fb_cost,
            "dca_cost": dca_cost,
        },
    }


def window_start(sig: Signals, cfg: TickerConfig, earliest: str | None) -> int:
    """First month-start bar once the rules can run.

    Tickers with 2+ years of history wait for a sloped 200 SMA (Tier 2 live);
    younger listings start once the 50 SMA slope exists, as live finbot would.
    """
    if len(sig.index) > 504:
        need = cfg.sma_slow + cfg.sma200_slope_lookback
    else:
        need = cfg.sma_mid + cfg.sma50_slope_lookback
    first = month_starts(sig.index)
    for i in range(need, len(sig.index)):
        if first[i] and (earliest is None or sig.index[i] >= pd.Timestamp(earliest)):
            return i
    return len(sig.index)


# ---------------------------------------------------------------- walk-forward
GRID = {
    "rsi_daily_dip_min": [30.0, 35.0, 40.0],
    "rsi_daily_dip_max": [45.0, 48.0, 52.0, 55.0],
    "sma_mid": [20, 50, 100],
}


def edge(r: dict) -> float:
    """End value with leftover cash deployed at the end, vs DCA, in %."""
    return (r["fb_value_end_deployed"] / r["dca_value"] - 1) * 100


def walk_forward(df, cfg, earnings, earliest, train_months, test_months) -> list[dict]:
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    sigs = {}
    for cmb in combos:
        key = tuple(cmb.values())
        sigs[key] = signals(df, replace(cfg, **cmb), earnings)
    base_key = (cfg.rsi_daily_dip_min, cfg.rsi_daily_dip_max, cfg.sma_mid)
    any_sig = sigs[base_key]
    idx = any_sig.index
    s0 = window_start(any_sig, replace(cfg, sma_mid=max(GRID["sma_mid"])), earliest)
    first = month_starts(idx)

    def bar_at(ts) -> int:
        j = int(idx.searchsorted(ts))
        while j < len(idx) and not first[j]:
            j += 1
        return j

    folds = []
    t0 = idx[s0]
    while True:
        tr_end = t0 + pd.DateOffset(months=train_months)
        te_end = tr_end + pd.DateOffset(months=test_months)
        a, b = bar_at(t0), bar_at(tr_end)
        c = min(bar_at(te_end), len(idx))
        if b >= len(idx) or c - b < 120:
            break
        scores = {k: edge(simulate(s, a, b, spaced=False)) for k, s in sigs.items()}
        best = max(scores, key=scores.get)
        tests = {k: edge(simulate(s, b, c, spaced=False)) for k, s in sigs.items()}
        rank = sorted(tests.values(), reverse=True).index(tests[base_key]) + 1
        folds.append({
            "train": f"{idx[a].date()} to {idx[b - 1].date()}",
            "test": f"{idx[b].date()} to {idx[c - 1].date()}",
            "best_params": dict(zip(GRID, best)),
            "best_train_edge": scores[best],
            "current_train_edge": scores[base_key],
            "tuned_test_edge": tests[best],
            "current_test_edge": tests[base_key],
            "current_test_rank": f"{rank}/{len(tests)}",
            "median_test_edge": float(np.median(list(tests.values()))),
        })
        t0 = t0 + pd.DateOffset(months=test_months)
    return folds


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    config = load_config()

    results, wf = {}, {}
    for cfg in config.tickers:
        sym = cfg.symbol
        df = load(sym)
        earnings = earnings_history(sym, cfg.asset_type)
        earliest = None
        if cfg.asset_type != "etf" and earnings:
            earliest = str(min(earnings) + pd.Timedelta(days=14))
        sig = signals(df, cfg, earnings)
        s0 = window_start(sig, cfg, earliest)
        entry = {
            "bars": len(df),
            "first_bar": str(df.index[0].date()),
            "sma200_bars_available": int(np.isfinite(sma(df["Close"], cfg.sma_slow)).sum()),
            "earnings_dates_used": len(earnings),
        }
        if s0 >= len(df) - 1:
            entry["note"] = "not enough history for the 50 SMA slope"
        else:
            entry["literal"] = simulate(sig, s0, len(df), spaced=False)
            entry["spaced"] = simulate(sig, s0, len(df), spaced=True)
        results[sym] = entry
        if sym == "IBM":
            wf[sym] = walk_forward(df, cfg, earnings, earliest, train_months=96, test_months=36)
        elif sym == "QTUM":
            wf[sym] = walk_forward(df, cfg, earnings, earliest, train_months=36, test_months=18)
        entry["signal_days"] = {"tier1": int((sig.tier[s0:] == 1).sum()), "tier2": int((sig.tier[s0:] == 2).sum()),
                                "sessions": int(len(df) - s0)}
        entry["first_earnings"] = str(min(earnings)) if earnings else None
        for v in ("literal", "spaced"):
            print(sym, v, json.dumps({k: (round(x, 2) if isinstance(x, float) else x)
                                      for k, x in entry.get(v, {}).items() if k != "series"}))
        print(sym, entry["signal_days"], entry["first_earnings"], entry["earnings_dates_used"])

    (out / "results.json").write_text(json.dumps({"results": results, "walk_forward": wf}, default=float, indent=1))
    print(json.dumps(wf, indent=1, default=float))


if __name__ == "__main__":
    main()
