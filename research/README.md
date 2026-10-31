# Research: finbot tranche triggers vs monthly DCA

`tranche_backtest.py` replays the live Tier 1 / Tier 2 rules from
`config/watchlist.yaml` day by day (causal: confirmed swing lows, closed weekly
RSI, earnings blackout for single names) and compares them with buying on the
first trading day of each month, with the same $1,000/month going into both.

```
pip install -r requirements.txt
python research/tranche_backtest.py      # writes research/out/results.json
```

It also runs a rolling walk-forward on the Tier 1 RSI band and SMA length
(IBM: 8y train / 3y test; QTUM: 3y / 18m). Not imported by the live scanner.
Results as of 2026-10-30 data are summarized in the PR description.

Parity check: on 240 IBM/QTUM sessions (the last 60 signal days each plus 60
random days), the backtest's tier matched `strategy.evaluate` run on a 2-year
slice ending that day in every case.
