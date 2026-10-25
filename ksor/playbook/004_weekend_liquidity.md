---
id: P-0004
type: playbook
status: active
tags: [weekend, liquidity, spread, stop_hunt]
regime: []
symbols: []
condition: "Weekend trading — low liquidity, wider spreads, stop hunt risk"
rule: null
effect: null
effect_value: null
action: "Apply weekend rules: check spread, reduce confidence, avoid new positions on wide spread"
evidence_trades: []
win_stats: {}
created: "2026-10-25"
last_validated: null
expires: null
---

# Weekend Liquidity Rules

## When Does "Weekend" Apply?

Weekend period: **Friday 22:00 UTC through Sunday 23:00 UTC**.

CME BTC futures and traditional equity futures are closed over the weekend. This removes
a significant source of institutional flow from crypto markets, leading to:

- Lower overall liquidity
- Wider bid-ask spreads
- Thinner order books
- Higher susceptibility to stop-hunt moves

## Rules

### Rule 1: Spread Check

If `features.spread_pct > 0.05%` during the weekend window, do not open new positions.
The Risk Gate enforces `max_spread_pct: 0.1%` at all times; the weekend threshold is
half of that (0.05%) because weekend spread spikes are common and often not worth the cost.

### Rule 2: Confidence Reduction

During the weekend window, reduce confidence by 0.10 on any new entry signal. The lower
liquidity means signal quality is degraded even when spreads are within limits.

### Rule 3: Stop Hunts

Weekend markets are known for sharp, short-lived moves that sweep liquidity levels and
then reverse. This is sometimes called "stop hunting" — the price moves far enough to
trigger stops, then reverses.

Implication:
- Do not place stops at obvious round numbers or obvious swing points on weekends
- Use a slightly wider stop (within ATR limits) to account for noise
- Prefer to wait for Monday's liquidity restoration before entering new positions

### Rule 4: CME Gap Risk

When CME futures markets (BTC, equity indices) close on Friday and re-open Sunday evening
(US time), there is often a "gap" between Friday's close and Sunday's open.

- A large gap up → crypto longs that were entered over the weekend may gap through TP
- A large gap down → crypto shorts may get a boost, but longs face sudden loss
- Monitor the CME gap feature when available (`features.cme_gap_pct`)

### Rule 5: No New Positions on Major Events

If a major macro event (FOMC, CPI, NFP) falls on a Monday, treat the entire weekend as a
pre-event blackout. Do not open new positions that would be held through the event open.

## Weekend Closure Reminder

Despite 24/7 crypto trading, the operator (UAE/PKT timezone) should be aware:
- Low-quality signals and stop hunts are more common Friday night–Sunday
- Consider pausing the system or reducing max_trades_per_day to 2 for weekend periods
- Any open position entering the weekend should have wider-than-normal stop consideration
