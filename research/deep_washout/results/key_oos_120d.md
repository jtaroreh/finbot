# Key table: 120-day episode-first OOS (`oos_from_2022`)

Complete forward windows only. Fill = next open + 5 bps slip + 5 bps cost. Independent episodes use a 60-session gap. Means and CIs are on **episode-first** rows (overlapping washout days are not treated as independent).

## QTUM (decision asset)

| candidate | n (overlap) | n_independent | mean return | 95% bootstrap CI | hit rate | median MAE |
|---|---:|---:|---:|---|---:|---:|
| DW1_BLIND | 83 | **3** | 1.17% | −8.72% .. 11.74% | 66.67% | −16.02% |
| DW2_DRAWDOWN_BLIND | 83 | **3** | 1.17% | −8.72% .. 11.74% | 66.67% | −16.02% |
| DW3_CONFIRM5_VOL | 0 | **0** | n/a | n/a | n/a | n/a |
| DW4_CONFIRM10_DIVERGENCE | 1 | **1** | 15.27% | 15.27% .. 15.27% | 100.00% | −1.58% |
| UNCONDITIONAL | 1096 | 19 | 12.43% | 3.71% .. 19.68% | 84.21% | −10.91% |
| MATCHED_REGIME | 223 | 8 | 21.49% | −1.06% .. 41.91% | 62.50% | −5.14% |
| TIER1_EXACT | 41 | 11 | 17.65% | 5.91% .. 27.77% | 90.91% | −11.96% |
| TIER2_EXACT | 49 | 6 | 30.02% | 11.29% .. 46.90% | 83.33% | −5.02% |
| TIER12_EXACT | 90 | 13 | 14.60% | 2.90% .. 24.59% | 84.62% | −11.96% |
| NAIVE_NEXT_TRANCHE (diagnostic only) | 10 | 7 | 21.76% | 5.15% .. 35.01% | 85.71% | −8.77% |

Frozen bar: `n_independent ≥ 6` and 120d mean **greater than** MATCHED_REGIME and TIER12. **No DW candidate clears it.** DW1/DW2 are identical on this sample (every core-location washout also had 52-week drawdown ≥ 15%). The 2026-11 tape has no complete 20d+ window yet and is not in these rows.

## SMH / QQQ (proxies only — cannot ship a tier)

| asset | candidate | n | n_independent | mean return | 95% CI | hit rate | median MAE | vs MATCHED_REGIME |
|---|---|---:|---:|---:|---|---:|---:|---|
| SMH | DW1_BLIND | 73 | 4 | 22.72% | −3.40% .. 53.62% | 75.00% | −19.19% | 13.37% matched |
| SMH | TIER12_EXACT | 77 | 13 | 22.01% | 6.21% .. 37.12% | 76.92% | −9.27% | — |
| QQQ | DW1_BLIND | 76 | 5 | 16.89% | 2.02% .. 31.77% | 80.00% | −8.48% | 6.72% matched |
| QQQ | TIER12_EXACT | 85 | 13 | 7.25% | −2.29% .. 15.31% | 69.23% | −8.39% | — |

Proxy DW1 point estimates are not worse than matched-regime, but independent n is still < 6 and the protocol forbids proxy-only production changes.

## AIPO (descriptive only)

Listed 2025-07-25; 325 bars as of 2026-11-06; SMA200 **immature**. Frozen DW core requires 350-bar maturity, so DW n = 0. Current close 22.48 vs SMA200 28.54 (**−21.23%**). Not scored as target evidence.

## QTUM equal-cash portfolios (OOS, $1,000/month, $59,000 contributed)

| strategy | terminal | vs contributed | n buys | avg idle cash | avg invested |
|---|---:|---:|---:|---:|---:|
| DCA_MONTHLY | $119,639 | +102.8% | 59 | 0.0% | 100% |
| TIER12_EXACT (1×/2×) | $120,429 | +104.1% | 51 | 12.2% | 88.5% |
| DW1_BLIND spaced 1.5× | $82,012 | +39.0% | 9 | 56.5% | 54.6% |
| DW3_CONFIRM5_VOL | $59,000 | 0% | 0 | 100% | 0% |
| DW4_CONFIRM10_DIVERGENCE | $62,575 | +6.1% | 1 | 95.6% | 7.6% |

Cash yield = 0. DW cash drag is large because signals are rare. Full grid: `summary_table.csv`.
