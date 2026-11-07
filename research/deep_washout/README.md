# Deep-washout accumulation research

Frozen protocol: [`PROTOCOL.md`](PROTOCOL.md) and [`config.yaml`](config.yaml), dated **2026-11-07**, before held-out evaluation.

This package is **not** imported by `finbot.cli` or the daily scan. It does not trade, change secrets, or modify live tiers unless a separate, explicit production change is made after the success criteria pass.

## Run

```bash
pip install -r requirements.txt
python -m pytest tests/test_deep_washout.py tests/test_strategy.py tests/test_indicators.py -q
python research/deep_washout/run.py
```

Writes `research/deep_washout/results/` (`REPORT.md`, `summary_table.csv`, provenance, as-of verification). Raw Yahoo caches under `cache/` are gitignored and never synthesized.

## Decision rule (short)

Ship a new live tier only if one frozen candidate beats `MATCHED_REGIME` and live Tier 1/2 on **QTUM held-out** data with ≥ 6 independent 120d episodes and the equal-cash DCA gate. AIPO is descriptive. SMH/QQQ are proxies and cannot justify production. Otherwise the PR is research-only.
