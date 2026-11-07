# Deep-washout accumulation: held-out results

**Protocol:** `deep-washout-v1`, frozen 2026-11-07 in `research/deep_washout/PROTOCOL.md` and `config.yaml` **before** OOS evaluation.
**Generated from:** Yahoo `history(period=max, interval=1d, auto_adjust=True)` fetched 2026-11-07T18:37–18:38Z, last bar 2026-11-06.
**Live scanner:** unchanged. No new tier, threshold, webhook, or bot edit.

This is research, not financial advice, and not a promise of future profit.

## Conclusion

**Insufficient evidence to ship a deep-washout accumulation tier.**

On QTUM held-out data (calendar OOS from 2022-01-01), every frozen candidate fails the pre-registered bar:

- Independent 120d episodes: DW1/DW2 = **3**, DW3 = **0**, DW4 = **1** (need ≥ 6).
- Episode-mean 120d return for DW1/DW2 is **1.17%**, below MATCHED_REGIME **21.49%** and live TIER12 **14.60%**.
- Bootstrap CI lower bound on that mean is **−8.72%** (not > 0 and not > matched-regime).
- Median 120d MAE **−16.0%** is worse than 1.5× live TIER2 MAE (**−5.0%**).
- Equal-cash portfolio: DW1 ends at **$82k** vs DCA **$120k** on the same $59k contributions, with **~57%** average idle cash (cap was 50%).

AIPO cannot help: SMA200 is still immature (325 bars). SMH/QQQ DW1 120d point estimates are not a large reversal versus their matched regimes, and **cannot justify production** under the frozen protocol (and their independent n is also < 6).

## As-of verification (relayed 2026-11-06 tape)

Yahoo adjusted bars, `strategy.evaluate` on history through that session. Relayed figures in parentheses.

| field | QTUM measured | relayed | match |
|---|---:|---:|---|
| close | 123.37 | 123.37 | yes |
| SMA200 | 137.54 | 137.54 | yes |
| dist vs SMA200 | **−10.30%** | ~−10.3% | yes |
| SMA200 slope | rising, mature | mature rising | yes |
| SMA50 | 144.60, **not** rising | falling | yes |
| daily RSI 14 | 32.14 | ~32 | yes |
| weekly RSI (completed weeks) | 36.47 (2y lookback 36.46) | ~36 | yes |
| live signal | **NONE** (trend, support fail) | T1/T2 miss | yes |
| 2026-10-16 T2 next_tranche | **129.33** (close 137.14, TIER_2_MAJOR) | 129.33 | yes (issue #14) |
| vs that next_tranche | 123.37 is **below** 129.33 | below | yes |

AIPO 2026-11-06: close 22.48, SMA200 28.54, **−21.23%** vs SMA200, SMA50 falling, SMA200 still **immature** (`history_bars=325 < 350`). Live signal NONE (trend, support).

Live T1 needs a rising 50 inside −1.5%/+3%. Live T2 needs a rising-200 band of −1%/+3% **or a reclaim back above the 200**, plus support within 1× ATR of the 200. A print ~10% (QTUM) or ~21% (AIPO) below the 200 fails both location and the T2 support gate. `next_tranche` is printed metadata. **It is not a trigger**; the naive “buy because we are below last issue’s next_tranche” rule was rejected a priori and scored only as a diagnostic.

The 2026-11 washout has **no complete 20d+ forward window** as of 2026-11-06, so it is **not scored** at those horizons.

## Data provenance

| symbol | first bar | last bar | n | weekday gaps | dividends | splits |
|---|---|---|---:|---:|---:|---:|
| QTUM | 2018-09-05 | 2026-11-06 | 2055 | 78 | 32 | 0 |
| AIPO | 2025-07-25 | 2026-11-06 | 325 | 11 | 1 | 0 |
| SMH | 2000-06-05 | 2026-11-06 | 6647 | 248 | 14 | 1 |
| QQQ | 1999-03-10 | 2026-11-06 | 6960 | 258 | 90 | 1 |

Source: `yfinance.Ticker.history(period="max", interval="1d", auto_adjust=True)`. Closes are split/dividend adjusted. Gaps vs weekdays are NYSE holidays / closures, **not filled**. No bars were synthesized. JSON: `provenance.json`.

Live scanner fetches `2y`; SMA200/ATR location match a full-history rolling window. Wilder RSI has long memory; a 2y slice vs full history moved QTUM weekly RSI by ~0.01 on 2026-11-06.

**Tier 1/2 parity:** vectorized replay vs `strategy.evaluate` on 48 random QTUM sessions: **48/48 match** (`parity.json`).

## Frozen family (not a search grid)

Shared core: mature SMA200, close ≥ 8% and ≥ 2× ATR below it, z-score vs 200 ≤ −1.5, and daily RSI ≤ 35 **or** completed-week RSI ≤ 40.

| ID | extra |
|---|---|
| DW1_BLIND | none (buy the washout at the close) |
| DW2_DRAWDOWN_BLIND | 52-week drawdown ≥ 15% |
| DW3_CONFIRM5_VOL | volume ≥ 1.5× prior 20d + close above prior 5-day high |
| DW4_CONFIRM10_DIVERGENCE | causal 10/10 RSI divergence + close above prior 10-day high |

Execution: decision at close, fill next open. Weekly RSI uses completed Fridays only. Swings are known only after `right=10` bars.

## Event-study results

Primary decision rows: [key_oos_120d.md](key_oos_120d.md). Full asset × candidate × window × horizon grid: [summary_table.csv](summary_table.csv).

On QTUM OOS, DW1 and DW2 produced the **same** 83 overlapping / 3 independent episodes: every core-location washout also had a ≥ 15% 52-week drawdown. DW3 never confirmed with volume. DW4 fired once.

Subwindows: those three independent QTUM DW episodes all sit in **2022–2023**. The 2024-on window has **zero** complete DW events (the 2026-11 tape is still open). A candidate that cannot even be measured in the late window cannot ship.

Live **TIER2_EXACT** on QTUM OOS 120d (n_ind=6, mean 30.0%, MAE −5.0%) is the better *existing* location (tag/reclaim of the 200), not a reason to add a still-below-200 tier. That is a side observation; T2 thresholds were not retuned.

The rejected **NAIVE_NEXT_TRANCHE** diagnostic had n_ind=7 and a 120d mean near matched-regime. It is still **not** a candidate: Joel rejected spacing-as-trigger, 2022–2023 subwindow was poor, and the protocol forbids promoting diagnostics after seeing OOS.

## Sizing / spacing (even though no tier ships)

If a later sample ever met the frozen bar, conservative defaults would be:

- **1.5×** unit (between T1 1× and T2 2×).
- After **any** buy, a further DW fill waits until close ≤ last fill − **2.25× ATR** or **21** sessions. That wait is a **risk control, not a trigger**.
- Do not stack DW on a same-washout T2 until that T2’s printed next_tranche (or 21 sessions).
- Cap **3×** DW units per 252 sessions.

**Applied to live code: no.** Dry powder while QTUM sits under the 200 remains a human/Grok-bot decision from the daily snapshot (`signal_tier=NONE`), not a new scanner tier.

## Limitations

- One decision name (QTUM), ~8 years, one 2022 semiconductor/tech bear plus a 2025–26 tape. Watchlist survivor bias.
- Four candidates × four horizons is a multiple-testing burden; 120d was pre-declared primary (four comparisons, not sixteen cells).
- Independent n uses a 60-day cluster, not a structural-break model. Overlapping raw n is reported separately; Newey–West SEs are in the CSV.
- MATCHED_REGIME includes many “already bouncing but still below the 200 with a falling 50” days; deep 8%+ days are a harsher subset. That comparison was frozen on purpose.
- 0% cash yield overstates idle-cash penalty vs T-bills and is conservative for “does timing beat DCA?”
- Yahoo adjusted prints; 78 QTUM weekday gaps are holidays, not interpolated.
- Thresholds are round numbers motivated by the live −1% SMA200 undershoot gap and the qualitative 2026 tape. They were not fit on 2022, but the tape description is inside the OOS calendar; the current event is unscored at 20d+.
- Proxy success alone cannot justify production. AIPO cannot either.

## How to reproduce

```bash
pip install -r requirements.txt
python -m pytest tests/test_deep_washout.py -q
python research/deep_washout/run.py
```

Offline causal tests (weekly RSI complete-weeks, swing confirmation delay, prior 5/10-day highs, next-open fill, incomplete-window drop, episode clustering) live in `tests/test_deep_washout.py`. Existing live-scanner tests remain green (83 passed on this branch).
