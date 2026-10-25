---
id: P-0003
type: playbook
status: active
tags: [session, asia, london, new_york, uae, pakistan, liquidity]
regime: []
symbols: []
condition: "Session-specific behavior rules for crypto markets"
rule: null
effect: null
effect_value: null
action: "Adjust strategy and expectations based on the active trading session"
evidence_trades: []
win_stats: {}
created: "2026-10-25"
last_validated: null
expires: null
---

# Session Behavior Guide

## Timezone Reference

| Location | Timezone | UTC Offset |
|---|---|---|
| UTC | UTC | +0 |
| London | BST (summer) / GMT (winter) | +1 / +0 |
| New York | EDT (summer) / EST (winter) | -4 / -5 |
| UAE (Dubai) | GST | +4 |
| Pakistan (PKT) | PKT | +5 |

All system timestamps are UTC. The Telegram bot displays times in both UTC and PKT.

## Session Windows (UTC)

| Session | Opens (UTC) | Closes (UTC) | Character |
|---|---|---|---|
| Asia | 00:00 | 08:00 | Low liquidity, tight range, fakeouts |
| London | 07:00 | 16:00 | Breakouts and reversals, increasing liquidity |
| New York | 13:00 | 22:00 | Highest volume, trending moves |
| London/NY overlap | 13:00 | 16:00 | Most liquid period of the day |

## Session-Specific Rules

### Asia Session (00:00–08:00 UTC)

- **Low liquidity:** Spreads are wider, order books are thinner
- **Tight range:** Price often consolidates within the previous day's range
- **Fakeouts:** Breakouts from Asia ranges often reverse at London open
- **Rule:** Reduce position confidence by 0.10 during Asia session
- **Avoid:** Breakout trades in Asia — wait for London to confirm
- **Best for:** Range-bound strategies, accumulation at clear levels

### London Open (07:00–09:00 UTC)

- **Breakout/reversal:** London often breaks the Asia range direction OR reverses it
- **Volume spike:** First 30–60 minutes of London are high-activity
- **Rule:** The first 5 minutes of London open (07:00–07:05 UTC) — no new entries;
  wait for initial volatility to settle
- **Best for:** Momentum entries after the first 5-minute bar closes with clear direction

### New York Open (13:00–15:00 UTC)

- **Volume spike:** Largest volume of the day, especially 13:00–14:00 UTC
- **Trend continuation:** NY often continues or confirms the London direction
- **Rule:** The first 5 minutes of NY open (13:00–13:05 UTC) — no new entries
- **Best for:** Trend-following entries aligned with HTF trend and London direction

### London/NY Overlap (13:00–16:00 UTC)

- **Most liquid:** Tightest spreads, best fills, most reliable signals
- **Trend confirmation:** Moves that start here and hold are more likely to be genuine
- **Best for:** Any strategy; highest quality execution window

### Weekend (Friday 22:00 UTC – Sunday 23:00 UTC)

- See `005_funding_time.md` for weekend-specific rules
- **Low liquidity:** Crypto markets are open but institutional participation drops
- **Wider spreads:** May exceed `max_spread_pct` limit in risk.yaml
- **Stop hunts:** Weekend is known for stop-hunt moves that reverse quickly
- **Rule:** No new trades if `features.spread_pct > 0.05%` (half the normal max_spread_pct)

## UAE / PKT Operator Context

The system operator is in UAE (UTC+4) / Pakistan (UTC+5). For operator convenience:

- Asia session starts at 04:00 UAE / 05:00 PKT
- London open is at 11:00 UAE / 12:00 PKT
- NY open is at 17:00 UAE / 18:00 PKT
- NY close is at 02:00 UAE (next day) / 03:00 PKT (next day)

The Telegram bot's daily summary is sent at a time configured for the operator's timezone.
