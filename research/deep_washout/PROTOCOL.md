# Frozen protocol: deep-washout accumulation tier (v1)

**Frozen:** 2026-11-07, before held-out evaluation.
**Protocol ID:** `deep-washout-v1`
**Decision asset:** QTUM. AIPO is a short descriptive sample. SMH and QQQ are robustness proxies only. Proxy or AIPO results cannot justify a production change.

This file is the pre-registered plan. Thresholds live in `config.yaml`. Do not retune after seeing OOS numbers.

## 1. Why a new tier is even a question

Live finbot (main) has two accumulation tiers, not a swing system:

- **Tier 1** needs a *rising* SMA50 and close inside **−1.5% to +3%** of it, daily RSI 35–48, and support within 1× ATR of SMA50 or a confirmed 10/10 swing.
- **Tier 2** needs either (A) a *rising* SMA200 and close inside **−1.0% to +3%** of it, or (B) a close *back above* SMA200 after a ≥5% washout in 60 sessions, plus weekly RSI < 50 or daily RSI < 30, plus support within 1× ATR of SMA200 or a confirmed swing.

Prices far below a mature SMA200 fail both location bands. Path (B) only fires on the reclaim, not while still deep below the 200. Support-to-SMA200 within 1× ATR also fails when the gap is several ATR. `next_tranche` (entry − 2.25× ATR) is printed metadata. **It is not a trigger.** A naive “close is below the last issue’s next_tranche, so buy” rule is rejected a priori and is only scored as a diagnostic.

Motivating tape (independently verified in this run, not used as a scored complete trade): QTUM around 2026-11-06 was reported ~10% below a mature rising SMA200 with a falling SMA50, daily RSI ~32 / weekly ~36, and below the 2026-10-16 2× buy next_tranche 129.33. AIPO was reported ~21% under SMA200. Thesis is pre-accepted multi-year accumulation.

## 2. Data

| Item | Rule |
|---|---|
| Source | Yahoo Finance via `yfinance`, `history(period="max", interval="1d", auto_adjust=True)` |
| Adjustment | Split- and dividend-adjusted total-return-style bars. Unadjusted prices are not used for returns. |
| Corporate actions | Dividend and split counts recorded in provenance; never invented. |
| Missingness | Count of bars vs NYSE business days between first and last bar; gaps listed, not filled. |
| Lookahead | None. Weekly RSI uses completed Friday weeks only. 10/10 swings are known only after `right=10` bars. Prior 5/10-day highs exclude the signal bar. |
| Synthesis | Forbidden. If a ticker has no history, skip it and say so. |
| Live lookback | Scanner fetches `2y`; SMA/RSI/ATR(14)/200 are rolling and match a full-history rolling window once enough bars exist. Maturity is `history_bars >= 350`, same as live. |

Assets:

- **QTUM** — full available history; only series that can ship a tier.
- **AIPO** — short listing; descriptive only.
- **SMH, QQQ** — longer proxies for “is this just one quantum-ETF path?” Not independent target evidence.

## 3. Execution and leakage

- Signal decided at session **close** using that close and prior-only weekly/pivot fields.
- Fill at the **next session open**, plus 5 bps slippage and 5 bps one-way cost (10 bps round-trip if exited in event studies; event studies apply the 5 bps entry cost/slippage to the fill).
- Horizons: **20 / 60 / 120 / 250** trading days from the fill bar to a later **close**.
- **Incomplete forward windows are not scored.** A signal with fewer than H bars after the fill is omitted from horizon H (including the live 2026-11 washout at 20d+).
- Cash yield in portfolio tests is **0** (cash drag is fully penalized).

## 4. Frozen candidate family (four, not a grid)

All four share one **core location** (round numbers from the live −1% SMA200 undershoot gap, not a search):

1. SMA200 mature (`bars >= 350`).
2. Close **≥ 8%** below SMA200.
3. Close **≥ 2.0× ATR(14)** below SMA200.
4. Residual z-score `(close − SMA200) / std(close − SMA200, 200) ≤ −1.5`.

SMA200 **slope is not required**. Requiring a rising 200 would skip long bear washouts; requiring a falling 200 would skip the stated QTUM tape (rising 200, falling 50). Both regimes stay in the family; this is documented as a limitation.

Plus RSI: daily RSI ≤ 35 **or** completed-week RSI ≤ 40.

| ID | Extra hypothesis |
|---|---|
| `DW1_BLIND` | Core + RSI. No turn confirmation. |
| `DW2_DRAWDOWN_BLIND` | DW1 + 52-week drawdown ≥ 15% (close vs rolling 252-day high). |
| `DW3_CONFIRM5_VOL` | DW1 + volume ≥ 1.5× prior 20-day average + close **above the prior 5-day high**. |
| `DW4_CONFIRM10_DIVERGENCE` | DW1 + causal bullish RSI divergence on the two latest *confirmed* 10/10 swing lows (later low is lower, RSI at that low is higher) + close **above the prior 10-day high**. |

No other thresholds, no SMA-length grid, no RSI-band search. Confirmation vs blind is the only timing contrast.

**Causal RSI divergence:** `swing_lows(left=10, right=10)` at center `j` is known at bar `j+10`. Divergence may turn true only on/after that confirmation bar, using RSI at the swing centers (known by then).

**Rejected diagnostic (not shippable):** first close at or below the latest live Tier 1/2 `next_tranche` (2.25× ATR below that entry). Spacing is not a trigger.

## 5. Baselines (same cash rules)

- `UNCONDITIONAL` — every mature-SMA200 session (event study: every such day is an “entry”).
- `MATCHED_REGIME` — mature SMA200, close below SMA200, SMA50 not rising (the “deep tape” without the 8%/z/RSI extras).
- `TIER1_EXACT` / `TIER2_EXACT` / `TIER12_EXACT` — live `strategy.evaluate` rules (vectorized, parity-checked against `evaluate` on sampled days).
- `DCA_MONTHLY` — buy on the first trading day of each month with the same contribution stream.

Portfolio comparisons: **$1,000** credited on the first session of each month. DCA spends it immediately. Signal strategies spend `units × $1,000` per fill, capped by cash, leftover cash idle at 0% yield. Live sizing: T1 = 1×, T2 = 2×, DW = 1.5×. Report average percent invested, idle cash, costs, and terminal value. Equal contributions; no borrowing.

## 6. Metrics

For each asset × candidate/baseline × horizon, on complete windows only:

- `n` — overlapping signal days with a complete H-day window after fill.
- `n_independent` — clusters: a new episode starts when ≥ 60 trading days have passed since the previous episode’s first signal.
- Mean / median **forward total return** (fill → close H days later).
- **Hit rate** — fraction with return > 0.
- **MAE** — max adverse excursion: `min(low from fill bar through exit bar) / fill − 1`.
- Uncertainty: (a) episode-level bootstrap 95% CI (`seed=42`, 1000 draws) on mean return and hit rate; (b) Newey–West SE on overlapping returns with lag = H.

Primary decision numbers use **episode-first** signals (one row per independent episode) so overlapping washout days are not counted as independent wins.

## 7. Walk-forward

- Warmup until SMA200 maturity.
- **Frequency-check (not for shipping):** mature bars through 2021-12-31. May be shown; may not change `config.yaml`.
- **Held-out OOS (shipping decision):** calendar `oos_start = 2022-01-01` through as-of. Includes 2022 as unseen because no IS search was run.
- Additional stability cuts at `2024-01-01` (early OOS vs late OOS). A candidate that only works in one subwindow does not ship.

## 8. Sample adequacy and success (all required on QTUM OOS)

A new live tier ships only if **one** frozen candidate meets **all** of the following on QTUM held-out data. Multiple-testing: four candidates; we may ship at most one; 120d is the primary horizon (4 comparisons, not 16 cells).

1. `n_independent` ≥ 6 at 120d on QTUM OOS. Below that: automatic **insufficient evidence**.
2. Episode-mean 120d return **greater than** `MATCHED_REGIME` and **greater than** `TIER12_EXACT` on the same OOS window (each baseline’s own episode-means).
3. If ≥ 6 independent complete 250d episodes exist, episode-mean 250d return also greater than `MATCHED_REGIME`. If 250d n_ind < 6, 250d is reported and does not rescue a failed 120d, nor does it alone fail a pass that lacks 250d n.
4. 120d hit rate ≥ 55% **and** bootstrap CI lower bound on episode-mean 120d return > 0, **or** that CI lower bound > `MATCHED_REGIME` mean.
5. Median 120d MAE not worse than 1.5× `TIER2_EXACT` median MAE (if T2 has ≥ 3 independent OOS episodes; otherwise compare to `MATCHED_REGIME` MAE).
6. Equal-contribution portfolio terminal value ≥ DCA + 1% relative, and average idle cash ≤ 50% of contributed capital.
7. SMH/QQQ must not show a large opposite 120d edge (proxy mean 120d episode return below matched-regime by more than 5 percentage points on both proxies). Proxies still cannot justify shipping if QTUM fails.
8. AIPO never counts toward shipping.

If none qualify: **research-only** PR. No `strategy.py`, `config/watchlist.yaml`, notifier, workflow, or threshold edits.

## 9. Sizing / spacing (even if no tier ships)

If a future sample ever qualified, conservative defaults would be:

- **1.5×** unit (between T1 1× and T2 2×): rarer than T1, still-below-200 is riskier than a T2 reclaim.
- After **any** buy (T1, T2, or DW), a further DW fill waits until close ≤ last fill − **2.25× ATR** or **21** sessions. That wait is **not** an entry trigger.
- Do not add DW in the same washout as a recent T2 until that T2’s printed next_tranche is reached (or 21 sessions).
- Cap **3×** DW units per 252 sessions.

This research does not trade, merge, change secrets, or modify bots.

## 10. Limitations (pre-declared)

- One decision name (QTUM), short life, one 2022-style semiconductor/tech regime plus a 2025–26 tape. Survivor bias: we study names that still exist and are on the watchlist.
- Four candidates still imply a multiple-testing burden.
- Adjusted Yahoo bars can differ slightly from unadjusted prints; as-of checks use the same adjusted series as the backtest.
- 0% cash yield overstates cash drag vs T-bills and is conservative for “does timing beat DCA?”
- Event-study returns overlap; episode clustering is an approximation of independent washouts, not a structural break model.
- No claim of future profit.
