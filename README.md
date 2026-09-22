# finbot

Finbot is a **two-tier accumulation engine** designed for disciplined long-term accumulation of high-conviction holdings (**IBM**, **QTUM**, and **AIPO**). It is **not** a short-term swing-trading system: there are no 1.5× ATR stops and no 2R/3R profit targets. Instead, it identifies optimal technical confluence windows for adding buy tranches into structural pullbacks when the underlying fundamental thesis is already established.

The scanner runs daily post-market close on weekdays (21:15 UTC / 4:15pm US Eastern), operating via a dual path:

1. **Daily Webhook Snapshot** — On every scan, POSTs full ticker JSON (gates, technical indicators, asset archetype, earnings status, tranche levels) to Grok Bot webhooks so the AI can evaluate contextual market conditions.
2. **GitHub Issue on Accumulation Trigger** — Automatically opens one GitHub Issue per ticker only when technical confluence passes (`signal=ENTRY`, `confluence=true`), tagged with the specific trigger tier (`TIER_1_ROUTINE` or `TIER_2_MAJOR`).

The default watchlist consists of:
- **IBM** (`asset_type: equity`) — Core enterprise AI/hybrid cloud dividend compounder. Subject to single-name earnings blackouts.
- **QTUM** (`asset_type: etf`) — Defiance Quantum ETF (~70 equal-weighted tech/quantum holdings). Earnings blackout marked N/A.
- **AIPO** (`asset_type: etf`) — Defiance AI & Power Infrastructure ETF (grid, nuclear, data center utilities). Surfaces indicator maturity status.

This repository publishes scanner output; it is not financial advice.

## Two-Tier Accumulation Model

Waiting for a deep 200 SMA crash during secular bull markets risks complete cash drag and opportunity cost. Conversely, buying indiscriminately risks catching local tops. Finbot addresses this with a two-tier framework:

### Tier 1: Routine Dip (`TIER_1_ROUTINE`)
- **Philosophy:** Standard institutional accumulation into minor consolidations during established uptrends.
- **Trend Requirement:** 50 SMA is upward-sloping (`SMA50[t] > SMA50[t - 10]`) and close is within **−1.5% to +3.0%** of the 50 SMA.
- **Pullback Requirement:** Daily RSI 14 pulled back into the **35 to 48** consolidation zone.
- **Support Requirement:** Close or Low within 1.0× ATR of the 50 SMA or confirmed 10/10 structural fractal swing support.
- **Frequency:** Typically triggers 2 to 4 times per year during normal bull market consolidation.
- **Sizing:** Standard accumulation tranche (e.g. 1× unit).

### Tier 2: Major Washout (`TIER_2_MAJOR`)
- **Philosophy:** High-conviction accumulation during broad market corrections, cyclical de-risking, or macro washouts.
- **Trend Requirement (either):**
  - **(A) Rising 200 SMA Pullback:** Upward-sloping 200 SMA (`SMA200[t] > SMA200[t - 20]`) and close within **−1.0% to +3.0%** of the 200 SMA.
  - **(B) Washout Reclaim:** Close back above 200 SMA after having dropped ≥ 5% below it within the past 60 sessions.
- **Pullback Requirement:** Weekly RSI (closed bars) **< 50.0** or Daily RSI **< 30.0** (extreme oversold).
- **Support Requirement:** Close or Low within 1.0× ATR of the 200 SMA or confirmed structural swing support.
- **Frequency:** Typically triggers 1 time every 1 to 2 years.
- **Sizing:** Heavy accumulation tranche (e.g. 2× unit).

## Tranche Spacing & Structural Invalidation

Every signal computes coherent buy-tranche geometry:
- **Entry Level:** Signal closing price.
- **Next Buy Tranche:** `Entry - 2.25 × ATR(14)` (~4% to 7% below entry, staging dry powder for further consolidation).
- **Structural Invalidation Level:** `Entry - 3.5 × ATR(14)` (placed well below the next buy tranche). Invalidation explicitly requires a confirmed weekly close below this level, avoiding premature shakeouts from 1-day intraday wicks.

## Asset Archetypes

- **Single Equities (`asset_type: equity`):** IBM is evaluated against a 5-day calendar earnings blackout window to prevent buying ahead of binary earnings announcements.
- **Thematic ETFs (`asset_type: etf`):** QTUM and AIPO hold diversified baskets and do not report single-company corporate earnings. The earnings gate passes cleanly with `detail: N/A: ETF basket`. AIPO's indicator maturity is reported in the snapshot (`sma200_is_mature`), allowing Tier 1 (50 SMA) signals to operate reliably while the 200 SMA continues to mature.

## Running Locally

```bash
# Set up environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run dry-run scan (prints markdown report, gate states, and webhook JSON)
python -m finbot.cli --dry-run

# Run dry-run for a single ticker
python -m finbot.cli --dry-run --ticker IBM

# Alternate config file
python -m finbot.cli --dry-run --config config/watchlist.yaml
```

Run test suite:
```bash
pytest
```

## GitHub Actions Automation

`.github/workflows/daily_scan.yml` is scheduled as standard repository automation:
- **Schedule:** `15 21 * * 1-5` (weekdays at 21:15 UTC / 4:15pm US Eastern), executing after the official New York cash equity session closes and settlement prices are finalized.
- **Workflow Dispatch:** Can also be triggered manually via GitHub's UI.
- **Permissions:** `contents: read` and `issues: write`.

On every successful scan, the action POSTs the JSON snapshot to configured Grok Bot webhooks. On verified accumulation confluence (`signal=ENTRY`), it creates an Issue titled `[ACCUMULATION] TICKER YYYY-MM-DD`.

### Secrets Configuration

Configure secrets under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Description |
|---|---|
| `FINBOT_GROK_WEBHOOK_URL_IBM` | Webhook URL for IBM routine |
| `FINBOT_GROK_WEBHOOK_SECRET_IBM` | Bearer token for IBM routine |
| `FINBOT_GROK_WEBHOOK_URL_QTUM` | Webhook URL for theme ETF routine |
| `FINBOT_GROK_WEBHOOK_SECRET_QTUM` | Bearer token for theme ETF routine |
| `FINBOT_GROK_WEBHOOK_URL_AIPO` | Same URL as QTUM |
| `FINBOT_GROK_WEBHOOK_SECRET_AIPO` | Same token as QTUM |

A bare `FINBOT_GROK_WEBHOOK_URL` / `FINBOT_GROK_WEBHOOK_SECRET` functions as a single-bot fallback.

## Grok Bot Instructions & Integration

Finbot POSTs one JSON snapshot per ticker to Grok Bot routines on every scan:

```json
{
  "ticker": "IBM",
  "asset_type": "equity",
  "signal": "ENTRY",
  "signal_tier": "TIER_1_ROUTINE",
  "tier_detail": "Tier 1 (Routine 50 SMA Dip): Upward-sloping SMA 50...",
  "confluence": true,
  "thesis": "long_term_accumulation",
  "signal_date": "2026-09-22",
  "summary": "ENTRY",
  "entry": 231.87,
  "next_tranche": 215.31,
  "tranche_spacing_atr": 2.25,
  "invalidation_level": 206.11,
  "invalidation_hint": "Wide invalidation context: requires weekly close confirmation...",
  "gates": { ... },
  "indicators": {
    "sma_50": 229.31,
    "sma_50_slope_up": true,
    "sma_200": 255.92,
    "sma200_is_mature": true,
    "rsi_14": 46.19,
    "rsi_weekly": 45.70,
    "atr_14": 7.36
  }
}
```

### Recommended Grok Bot Prompt / Routine Setup

In your Grok Bot Routine instruction panel, use the following guidance to interpret incoming Finbot webhooks:

> **Role & Objective:**
> You are a disciplined long-term capital allocation advisor. You receive daily technical snapshots from Finbot for high-conviction holdings (IBM, QTUM, AIPO). Your goal is to evaluate each snapshot and advise whether to execute an accumulation buy tranche.
>
> **Rules of Interpretation:**
> 1. **Conviction is Pre-Established:** The fundamental long-term thesis for these assets is accepted. Do not reject buy tranches based on short-term news sentiment or market noise.
> 2. **Signal Tier Sizing:**
>    - `signal_tier == "TIER_1_ROUTINE"`: Routine 50 SMA dip during an ongoing uptrend. Recommend adding **1× standard accumulation tranche**.
>    - `signal_tier == "TIER_2_MAJOR"`: Major market correction or 200 SMA washout. Recommend adding **2× heavy accumulation tranche**.
>    - `signal_tier == "NONE"`: Market is extended or between dip zones. Recommend holding dry powder (`NO_ENTRY`).
> 3. **Tranche Awareness:** Check `next_tranche` (~2.25× ATR below entry). If the user recently bought a tranche and price has not pulled back to `next_tranche`, advise patience.
> 4. **Earnings Consideration:** If `asset_type == "equity"` and `gates.earnings.passed == false`, confirm that the purchase should wait until after the corporate earnings event. For ETFs, ignore single-name earnings dates.
> 5. **Output Format:** Provide a concise 3-point briefing:
>    - **Signal & Tier:** [ENTRY / NO ENTRY, Tier 1 or Tier 2]
>    - **Technical Context:** [Current price vs 50/200 SMA, RSI status]
>    - **Recommended Action:** [Execute Tranche 1 / Tranche 2 / Wait for Next Tranche]
