"""Run the frozen deep-washout protocol. Does not modify live scanner code."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from finbot.config import default_ticker  # noqa: E402
from finbot.indicators import build_snapshot  # noqa: E402
from finbot.strategy import evaluate  # noqa: E402

from research.deep_washout.features import (  # noqa: E402
    build_panel,
    live_tier_flags,
    parity_sample,
)
from research.deep_washout.load import fetch_history, load_protocol_config  # noqa: E402
from research.deep_washout.portfolio import simulate_dca, simulate_equal_cash  # noqa: E402
from research.deep_washout.signals import (  # noqa: E402
    all_candidate_masks,
    baseline_masks,
    naive_next_tranche_mask,
)
from research.deep_washout.stats import (  # noqa: E402
    fill_and_horizon,
    in_window,
    mark_episodes,
    summarize_events,
)

RESULTS = Path(__file__).resolve().parent / "results"


def _num(value: Any, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "n/a"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return f"{float(value):.{digits}f}"


def _pct(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "n/a"
    return f"{100.0 * float(value):.{digits}f}%"


def _snapshot_dict(ohlcv: pd.DataFrame, symbol: str, asof: str) -> dict[str, Any]:
    cfg = default_ticker(symbol, asset_type="etf")
    cut = ohlcv.loc[: pd.Timestamp(asof)]
    if cut.empty:
        return {"symbol": symbol, "asof_requested": asof, "error": "no bars on or before asof"}
    snap = build_snapshot(cut, cfg)
    result = evaluate(snap, [], cfg)
    return {
        "symbol": symbol,
        "asof_requested": asof,
        "signal_date": snap.signal_date.isoformat(),
        "close": snap.close,
        "sma_50": snap.sma_50,
        "sma_50_slope_up": snap.sma_50_slope_up,
        "sma_200": snap.sma_200,
        "sma_200_slope_up": snap.sma_200_slope_up,
        "sma200_is_mature": snap.sma200_is_mature,
        "dist_sma200_pct": snap.sma_200_distance_pct,
        "rsi_14": snap.rsi_14,
        "rsi_weekly": snap.rsi_weekly,
        "atr_14": snap.atr_14,
        "signal_tier": result.signal_tier,
        "summary": result.summary,
        "next_tranche": result.levels.next_tranche if result.levels else None,
        "history_bars": snap.history_bars,
        "n_bars_used": len(cut),
    }


def asof_snapshot(ohlcv: pd.DataFrame, symbol: str, asof: str) -> dict[str, Any]:
    full = _snapshot_dict(ohlcv, symbol, asof)
    cut = ohlcv.loc[: pd.Timestamp(asof)]
    lookback = cut.iloc[-504:] if len(cut) > 504 else cut
    liveish = _snapshot_dict(lookback, symbol, asof)
    liveish["note"] = "Last 504 sessions only, matching live lookback_period=2y warmup for Wilder RSI."
    return {"full_history": full, "lookback_2y": liveish}


def oct16_next_tranche(ohlcv: pd.DataFrame) -> dict[str, Any] | None:
    cfg = default_ticker("QTUM", asset_type="etf")
    matches = ohlcv[ohlcv.index.normalize() == pd.Timestamp("2026-10-16")]
    if matches.empty:
        return None
    end = matches.index[-1]
    cut = ohlcv.loc[:end]
    lookback = cut.iloc[-504:] if len(cut) > 504 else cut
    snap = build_snapshot(lookback, cfg)
    result = evaluate(snap, [], cfg)
    return {
        "signal_date": snap.signal_date.isoformat(),
        "close": snap.close,
        "signal_tier": result.signal_tier,
        "next_tranche": result.levels.next_tranche if result.levels else None,
        "sma_200": snap.sma_200,
        "n_bars_used": len(lookback),
        "issue_14_next_tranche_printed": 129.33,
    }


def score_mask(
    panel: pd.DataFrame,
    mask: pd.Series,
    protocol: dict[str, Any],
    window_start: str | None,
    window_end: str | None = None,
) -> dict[int, dict[str, Any]]:
    exe = protocol["execution"]
    gap = int(protocol["episode_gap_bars"])
    seed = int(protocol["seed"])
    n_boot = int(protocol["bootstrap_n"])
    ci = float(protocol["ci_level"])
    win = in_window(panel, window_start, window_end)
    signals = mask.fillna(False) & win
    out: dict[int, dict[str, Any]] = {}
    for h in protocol["horizons"]:
        events = fill_and_horizon(
            panel,
            signals,
            int(h),
            slippage_bps=float(exe["slippage_bps"]),
            cost_bps=float(exe["cost_bps_one_way"]),
        )
        events = mark_episodes(events, gap)
        out[int(h)] = summarize_events(
            events,
            horizon=int(h),
            seed=seed,
            n_boot=n_boot,
            ci_level=ci,
            use_episode_first=True,
        )
        out[int(h)]["n_signal_days_in_window"] = int(signals.sum())
    return out


def decide(protocol: dict[str, Any], qtum_oos: dict[str, dict[int, dict[str, Any]]]) -> dict[str, Any]:
    success = protocol["success"]
    min_n = int(success["min_independent_episodes_oos"])
    min_hit = float(success["min_hit_rate_120d"])
    mae_cap = float(success["max_mae_vs_tier2_ratio_120d"])
    reasons: list[str] = []
    qualified: list[str] = []
    details: dict[str, Any] = {}
    baselines = {k: qtum_oos[k] for k in ("MATCHED_REGIME", "TIER12_EXACT", "TIER2_EXACT") if k in qtum_oos}

    for name in protocol["candidates"]:
        row = qtum_oos.get(name, {})
        s120 = row.get(120, {})
        s250 = row.get(250, {})
        n_ind = int(s120.get("n_independent") or 0)
        fail: list[str] = []
        if n_ind < min_n:
            fail.append(f"n_independent_120d={n_ind} < {min_n} (automatic insufficient evidence)")
        mean_dw = s120.get("mean_return")
        mean_mr = (baselines.get("MATCHED_REGIME") or {}).get(120, {}).get("mean_return")
        mean_t12 = (baselines.get("TIER12_EXACT") or {}).get(120, {}).get("mean_return")
        if mean_dw is None:
            fail.append("no complete 120d windows")
        else:
            if mean_mr is not None and not (mean_dw > mean_mr):
                fail.append(f"120d mean {_pct(mean_dw)} not > MATCHED_REGIME {_pct(mean_mr)}")
            if mean_t12 is not None and not (mean_dw > mean_t12):
                fail.append(f"120d mean {_pct(mean_dw)} not > TIER12_EXACT {_pct(mean_t12)}")
            hit = s120.get("hit_rate")
            lo = s120.get("mean_return_ci_lo")
            cond_hit = hit is not None and hit >= min_hit and lo is not None and lo > 0
            cond_edge = lo is not None and mean_mr is not None and lo > mean_mr
            if not (cond_hit or cond_edge):
                fail.append(
                    f"120d hit {_pct(hit)} / CI lo {_pct(lo)} failed "
                    f"(need hit>={_pct(min_hit)} and CI_lo>0, or CI_lo > matched-regime)"
                )
        n250 = int(s250.get("n_independent") or 0)
        if n250 >= min_n:
            m250 = s250.get("mean_return")
            mr250 = (baselines.get("MATCHED_REGIME") or {}).get(250, {}).get("mean_return")
            if m250 is None or (mr250 is not None and not (m250 > mr250)):
                fail.append("250d mean did not beat MATCHED_REGIME with adequate n")
        mae_dw = s120.get("median_mae")
        t2 = (baselines.get("TIER2_EXACT") or {}).get(120, {})
        mae_ref = t2.get("median_mae") if int(t2.get("n_independent") or 0) >= 3 else (
            (baselines.get("MATCHED_REGIME") or {}).get(120, {}).get("median_mae")
        )
        if mae_dw is not None and mae_ref is not None:
            # MAE is negative; "worse" means more negative, i.e. mae_dw < mae_cap * mae_ref
            if mae_dw < mae_cap * mae_ref:
                fail.append(
                    f"median MAE {mae_dw:.4f} worse than {mae_cap:g}× reference {mae_ref:.4f}"
                )
        details[name] = {"n_independent_120d": n_ind, "fail": fail, "pass": not fail}
        if fail:
            reasons.append(f"{name}: " + "; ".join(fail))
        else:
            qualified.append(name)

    ship = len(qualified) == 1 or (len(qualified) > 1)
    chosen = None
    if qualified:
        # At most one: pick the one with highest 120d episode mean among those that passed.
        chosen = max(qualified, key=lambda n: qtum_oos[n][120].get("mean_return") or -np.inf)
        if len(qualified) > 1:
            reasons.append(
                f"multiple candidates passed ({qualified}); conservative pick is {chosen} "
                "(still requires portfolio DCA test)"
            )
    return {
        "ship_tier": False,  # portfolio test applied by caller
        "candidates_passing_event_tests": qualified,
        "chosen_event_candidate": chosen,
        "reasons": reasons,
        "details": details,
        "would_ship_if_portfolio_ok": ship and chosen is not None,
    }


def apply_portfolio_gate(
    protocol: dict[str, Any],
    decision: dict[str, Any],
    port: dict[str, Any],
) -> dict[str, Any]:
    success = protocol["success"]
    chosen = decision.get("chosen_event_candidate")
    if not chosen:
        decision["ship_tier"] = False
        decision["conclusion"] = (
            "Insufficient evidence to ship a new accumulation tier. "
            "Research-only: live Tier 1/2, watchlist, and bots are unchanged."
        )
        return decision
    dw = port.get(chosen) or {}
    dca = port.get("DCA_MONTHLY") or {}
    tv = dw.get("terminal_value")
    dv = dca.get("terminal_value")
    idle = dw.get("avg_idle_cash_pct")
    fail = []
    if tv is None or dv is None or dv == 0:
        fail.append("portfolio terminal value unavailable")
    else:
        edge = tv / dv - 1.0
        if edge < float(success["min_dca_terminal_edge"]):
            fail.append(f"terminal vs DCA edge {edge:.4f} < {success['min_dca_terminal_edge']}")
    if idle is None or idle > float(success["max_avg_idle_cash_pct"]):
        fail.append(f"avg idle cash {idle} > {success['max_avg_idle_cash_pct']}%")
    if fail:
        decision["ship_tier"] = False
        decision["portfolio_fail"] = fail
        decision["conclusion"] = (
            f"{chosen} passed event-study gates but not the equal-cash DCA gate "
            f"({'; '.join(fail)}). Research-only: no live tier change."
        )
        return decision
    decision["ship_tier"] = True
    decision["portfolio_fail"] = []
    decision["conclusion"] = (
        f"{chosen} meets the frozen QTUM OOS success criteria. "
        "A conservative 1.5× tier is justified in the PR body; still not a profit promise."
    )
    return decision


def markdown_table(rows: list[dict[str, Any]]) -> str:
    cols = [
        "asset",
        "role",
        "candidate",
        "window",
        "horizon",
        "n",
        "n_independent",
        "mean_return",
        "mean_return_ci",
        "hit_rate",
        "median_mae",
        "vs_matched_120_note",
    ]
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    lines = [header, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def flatten_rows(
    asset: str,
    role: str,
    window: str,
    scores: dict[str, dict[int, dict[str, Any]]],
    matched_120: float | None,
) -> list[dict[str, Any]]:
    rows = []
    for name, by_h in scores.items():
        for h, stats in by_h.items():
            mean_r = stats.get("mean_return")
            note = ""
            if int(h) == 120 and mean_r is not None and matched_120 is not None:
                note = "beats_matched" if mean_r > matched_120 else "does_not_beat_matched"
            rows.append(
                {
                    "asset": asset,
                    "role": role,
                    "candidate": name,
                    "window": window,
                    "horizon": h,
                    "n": stats.get("n"),
                    "n_independent": stats.get("n_independent"),
                    "mean_return": _pct(mean_r),
                    "mean_return_ci": f"{_pct(stats.get('mean_return_ci_lo'))} .. {_pct(stats.get('mean_return_ci_hi'))}",
                    "hit_rate": _pct(stats.get("hit_rate")),
                    "median_mae": _pct(stats.get("median_mae")),
                    "vs_matched_120_note": note,
                    "mean_return_raw": mean_r,
                    "hit_rate_raw": stats.get("hit_rate"),
                    "median_mae_raw": stats.get("median_mae"),
                    "ci_lo_raw": stats.get("mean_return_ci_lo"),
                    "ci_hi_raw": stats.get("mean_return_ci_hi"),
                    "nw_se": stats.get("nw_se_overlapping"),
                    "n_scored": stats.get("n_scored"),
                    "n_signal_days_in_window": stats.get("n_signal_days_in_window"),
                }
            )
    return rows


def write_report(
    *,
    protocol: dict[str, Any],
    provenance: list[dict[str, Any]],
    asof: dict[str, Any],
    parity: dict[str, Any],
    rows: list[dict[str, Any]],
    decision: dict[str, Any],
    portfolios: dict[str, Any],
    sizing: dict[str, Any],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    table_md = markdown_table(rows)
    (out_dir / "summary_table.md").write_text(table_md + "\n", encoding="utf-8")
    pd.DataFrame(rows).to_csv(out_dir / "summary_table.csv", index=False)
    (out_dir / "provenance.json").write_text(json.dumps(provenance, indent=2, default=str), encoding="utf-8")
    (out_dir / "asof_verification.json").write_text(json.dumps(asof, indent=2, default=str), encoding="utf-8")
    (out_dir / "decision.json").write_text(json.dumps(decision, indent=2, default=str), encoding="utf-8")
    (out_dir / "portfolios.json").write_text(json.dumps(portfolios, indent=2, default=str), encoding="utf-8")
    (out_dir / "parity.json").write_text(json.dumps(parity, indent=2, default=str), encoding="utf-8")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Deep-washout accumulation: held-out results",
        "",
        f"Generated: {generated} UTC. Protocol `{protocol['protocol_id']}` frozen {protocol['frozen_at']}.",
        "This is not financial advice and not a promise of future profit.",
        "",
        "## Conclusion",
        "",
        decision.get("conclusion", ""),
        "",
        f"- Ship new live tier: **{decision.get('ship_tier')}**",
        f"- Event-test candidates: {decision.get('candidates_passing_event_tests')}",
        f"- Chosen (if any): {decision.get('chosen_event_candidate')}",
        "",
        "## Sizing / spacing decision (even if no tier ships)",
        "",
        json.dumps(sizing, indent=2),
        "",
        "## As-of verification (2026-11-06 tape)",
        "",
        "```json",
        json.dumps(asof, indent=2, default=str),
        "```",
        "",
        "## Data provenance",
        "",
        "```json",
        json.dumps(provenance, indent=2, default=str),
        "```",
        "",
        "## Tier 1/2 parity vs `strategy.evaluate`",
        "",
        "```json",
        json.dumps(parity, indent=2, default=str),
        "```",
        "",
        "## Event-study table (complete windows only; episode-first means)",
        "",
        table_md,
        "",
        "## Equal-cash portfolios (QTUM OOS)",
        "",
        "```json",
        json.dumps(portfolios.get("QTUM", portfolios), indent=2, default=str)[:80000],
        "```",
        "",
        "## Limitations",
        "",
        "- QTUM is the only decision series. SMH/QQQ are proxies; AIPO is descriptive.",
        "- Four frozen candidates still create a multiple-testing burden (120d primary).",
        "- Independent n is the binding constraint for rare washouts.",
        "- Yahoo adjusted bars; 0% cash yield; episode gap of 60 days is an approximation.",
        "- Survivor/regime: watchlist names that still exist, tech/semiconductor path.",
        "- Current 2026-11 washout has incomplete 20d+ windows and is not scored at those horizons.",
        "- Proxy success alone cannot justify production change.",
        "",
    ]
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(config_path: str | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    protocol = load_protocol_config(config_path)
    out_dir = Path(out_dir) if out_dir else RESULTS
    exe = protocol["execution"]
    oos_start = str(protocol["oos_start"])
    roles = {
        **{s: "decision" for s in protocol["assets"]["decision"]},
        **{s: "descriptive" for s in protocol["assets"]["descriptive"]},
        **{s: "proxies" for s in protocol["assets"]["proxies"]},
    }
    symbols = list(roles)
    provenance: list[dict[str, Any]] = []
    panels: dict[str, pd.DataFrame] = {}
    asof: dict[str, Any] = {"requested": protocol["asof_check_date"], "snapshots": {}, "oct16_qtum": None}
    parity: dict[str, Any] = {}
    all_rows: list[dict[str, Any]] = []
    qtum_oos_scores: dict[str, dict[int, dict[str, Any]]] = {}
    portfolios: dict[str, Any] = {}

    for symbol in symbols:
        frame, prov = fetch_history(symbol)
        provenance.append({**{k: getattr(prov, k) for k in prov.__dataclass_fields__}})
        if prov.empty or frame.empty:
            continue
        cfg = default_ticker(symbol, asset_type="etf")
        panel = live_tier_flags(build_panel(frame, cfg), cfg)
        panels[symbol] = panel
        asof["snapshots"][symbol] = asof_snapshot(frame, symbol, str(protocol["asof_check_date"]))
        if symbol == "QTUM":
            asof["oct16_qtum"] = oct16_next_tranche(frame)
            parity[symbol] = parity_sample(frame, cfg, panel, n_days=48, seed=int(protocol["seed"]))

        cands = all_candidate_masks(panel, protocol)
        bases = baseline_masks(panel)
        naive = naive_next_tranche_mask(panel)
        masks = {**cands, **bases, "NAIVE_NEXT_TRANCHE_DIAGNOSTIC": naive}

        for window_name, start, end in (
            ("freqcheck_to_2021", None, "2022-01-01"),
            ("oos_from_2022", oos_start, None),
            ("oos_2022_2023", "2022-01-01", "2024-01-01"),
            ("oos_2024_on", "2024-01-01", None),
        ):
            scored = {name: score_mask(panel, mask, protocol, start, end) for name, mask in masks.items()}
            matched_120 = (scored.get("MATCHED_REGIME") or {}).get(120, {}).get("mean_return")
            all_rows.extend(flatten_rows(symbol, roles[symbol], window_name, scored, matched_120))
            if symbol == "QTUM" and window_name == "oos_from_2022":
                qtum_oos_scores = scored

        if symbol == "QTUM":
            port: dict[str, Any] = {}
            port["DCA_MONTHLY"] = simulate_dca(
                panel,
                start=oos_start,
                contribution=float(protocol["portfolio"]["contribution_monthly"]),
                slippage_bps=float(exe["slippage_bps"]),
                cost_bps=float(exe["cost_bps_one_way"]),
            )
            t_units = pd.Series(0.0, index=panel.index)
            t_units = t_units.mask(panel["tier1"], float(protocol["portfolio"]["tier1_units"]))
            t_units = t_units.mask(panel["tier2"], float(protocol["portfolio"]["tier2_units"]))
            port["TIER12_EXACT"] = simulate_equal_cash(
                panel,
                panel["tier12"],
                t_units,
                start=oos_start,
                contribution=float(protocol["portfolio"]["contribution_monthly"]),
                unit_cash=float(protocol["portfolio"]["unit_cash"]),
                slippage_bps=float(exe["slippage_bps"]),
                cost_bps=float(exe["cost_bps_one_way"]),
            )
            for name, mask in cands.items():
                port[name] = simulate_equal_cash(
                    panel,
                    mask,
                    float(protocol["portfolio"]["dw_units"]),
                    start=oos_start,
                    contribution=float(protocol["portfolio"]["contribution_monthly"]),
                    unit_cash=float(protocol["portfolio"]["unit_cash"]),
                    slippage_bps=float(exe["slippage_bps"]),
                    cost_bps=float(exe["cost_bps_one_way"]),
                    spaced=True,
                    spacing_atr=float(protocol["sizing_if_shipped"]["spacing_atr"]),
                    spacing_reset_bars=int(protocol["sizing_if_shipped"]["spacing_reset_bars"]),
                )
                port[f"{name}_UNSPACED"] = simulate_equal_cash(
                    panel,
                    mask,
                    float(protocol["portfolio"]["dw_units"]),
                    start=oos_start,
                    contribution=float(protocol["portfolio"]["contribution_monthly"]),
                    unit_cash=float(protocol["portfolio"]["unit_cash"]),
                    slippage_bps=float(exe["slippage_bps"]),
                    cost_bps=float(exe["cost_bps_one_way"]),
                    spaced=False,
                )
            portfolios["QTUM"] = port

    # Proxy consistency check
    proxy_flag = []
    for name in protocol["candidates"]:
        for proxy in protocol["assets"]["proxies"]:
            sub = [
                r
                for r in all_rows
                if r["asset"] == proxy
                and r["candidate"] == name
                and r["window"] == "oos_from_2022"
                and r["horizon"] == 120
            ]
            mr = [
                r
                for r in all_rows
                if r["asset"] == proxy
                and r["candidate"] == "MATCHED_REGIME"
                and r["window"] == "oos_from_2022"
                and r["horizon"] == 120
            ]
            if sub and mr and sub[0]["mean_return_raw"] is not None and mr[0]["mean_return_raw"] is not None:
                if sub[0]["mean_return_raw"] < mr[0]["mean_return_raw"] - 0.05:
                    proxy_flag.append(f"{proxy}/{name} 120d mean is >5pp below matched-regime")

    decision = decide(protocol, qtum_oos_scores)
    if proxy_flag:
        decision["proxy_flags"] = proxy_flag
        if decision.get("would_ship_if_portfolio_ok") and len(proxy_flag) >= 2:
            decision["candidates_passing_event_tests"] = []
            decision["chosen_event_candidate"] = None
            decision["reasons"].append("both proxies strongly reverse the 120d edge; do not ship")
    decision = apply_portfolio_gate(protocol, decision, portfolios.get("QTUM") or {})

    sizing = dict(protocol["sizing_if_shipped"])
    sizing["applied_to_live"] = bool(decision.get("ship_tier"))
    sizing["recommendation"] = (
        "Do not add a live tier. If a later adequate sample ever qualified, use 1.5× units, "
        "2.25× ATR / 21-bar spacing as a post-buy control (not a trigger), wait for a recent "
        "T2 next_tranche before stacking DW, and cap 3× DW units per 252 sessions."
        if not decision.get("ship_tier")
        else (
            "Ship one conservative 1.5× deep-washout tier. Spacing 2.25× ATR or 21 bars after any "
            "buy is a control, not a trigger. Do not stack on a same-washout T2 until that T2's "
            "next_tranche (or 21 sessions). Cap 3× DW units / 252d."
        )
    )

    write_report(
        protocol=protocol,
        provenance=provenance,
        asof=asof,
        parity=parity,
        rows=all_rows,
        decision=decision,
        portfolios=portfolios,
        sizing=sizing,
        out_dir=out_dir,
    )
    return {"decision": decision, "asof": asof, "parity": parity, "out": str(out_dir)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Frozen deep-washout backtest (research only).")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = run(args.config, Path(args.out) if args.out else None)
    print(json.dumps(result["decision"], indent=2, default=str))
    print("asof", json.dumps(result["asof"], indent=2, default=str)[:4000])
    print("wrote", result["out"])


if __name__ == "__main__":
    main()
