---
id: P-0005
type: playbook
status: active
tags: [funding, funding_settlement, timing, perpetuals]
regime: []
symbols: []
condition: "Funding settlement windows — avoid new entries near settlement if funding is against your side"
rule: null
effect: null
effect_value: null
action: "Check funding direction and proximity to settlement before entering perpetuals"
evidence_trades: []
win_stats: {}
created: "2026-10-25"
last_validated: null
expires: null
---

# Funding Settlement Behavior

## Funding Settlement Schedule

Most major exchanges (Bybit, Binance, OKX) settle perpetual futures funding every 8 hours:
- **00:00 UTC** — midnight settlement
- **08:00 UTC** — morning settlement
- **16:00 UTC** — afternoon settlement

At each settlement, positions pay or receive funding based on the current funding rate and
the direction of their position (long vs. short).

## The Risk: Pre-Settlement Squeeze

In the minutes before funding settlement, traders with positions that are paying funding
may close their positions to avoid the payment. This creates predictable price pressure:

- **Positive funding (longs pay shorts):** Before settlement, some longs close → brief price
  drop. After settlement resets, the pressure relieves.
- **Negative funding (shorts pay longs):** Before settlement, some shorts close → brief price
  rise. After settlement resets, the pressure relieves.

This pattern is well-known and often "faded" by experienced traders — but for small positions
it creates noise that can stop out trades for no fundamental reason.

## Rules

### Rule 1: Avoid New Entries Near Funding Settlement

The `avoid_funding_window_minutes` setting in `risk.yaml` (default: 10) defines a window
before each funding settlement time. The Risk Gate blocks new entries if:
- Current time is within `avoid_funding_window_minutes` of the next funding settlement, AND
- The current funding rate is against your proposed side

Example: If funding is positive (longs pay) and you want to go long, avoid opening within
10 minutes of 00:00, 08:00, or 16:00 UTC.

### Rule 2: Post-Settlement Entry Window

The 5–15 minutes after funding settlement is often a better entry window:
- Squeeze pressure has relieved
- The funding rate has reset for the next 8-hour period
- Volume often picks up as new positions are established

### Rule 3: Funding Settlement Timing by Timezone

For the operator's reference:

| Settlement (UTC) | UAE (UTC+4) | PKT (UTC+5) |
|---|---|---|
| 00:00 UTC | 04:00 | 05:00 |
| 08:00 UTC | 12:00 | 13:00 |
| 16:00 UTC | 20:00 | 21:00 |

### Rule 4: Funding Rate at Extremes Near Settlement

If funding_z > 2.0 or funding_z < -2.0 AND you are within 30 minutes of settlement:
- The squeeze risk is elevated
- If your side is paying (positive funding + long, or negative funding + short):
  consider waiting for settlement to pass before entering

### Rule 5: Funding as a Trend Indicator (Post-Settlement)

After settlement resets, the new funding rate direction can signal the market's short-term
bias:
- Funding quickly returns to positive after reset → bullish bias, longs are willing to pay
- Funding quickly returns to negative after reset → bearish bias, shorts are willing to pay
- Funding stays near zero → neutral/indecisive; wait for other confluences

This is an informational note, not a hard rule. The `features.funding_z` captures this.
