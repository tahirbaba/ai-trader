---
id: P-0002
type: playbook
status: active
tags: [funding, open_interest, squeeze, derivatives]
regime: []
symbols: []
condition: "Funding rate and open interest awareness before entering perpetual futures"
rule: null
effect: null
effect_value: null
action: "Check funding z-score and OI-price divergence before every perpetual futures entry"
evidence_trades: []
win_stats: {}
created: "2026-10-25"
last_validated: null
expires: null
---

# Funding Rate and Open Interest Awareness

## Funding Rate Z-Score

The funding z-score (`features.funding_z`) measures how extreme the current funding rate
is relative to its recent history. Interpretation:

| funding_z | Condition | Implication |
|---|---|---|
| > +2.0 | Strongly positive funding | Longs are paying shorts; long squeeze risk |
| +1.0 to +2.0 | Elevated positive | Mild long-side crowding |
| -1.0 to +1.0 | Neutral | Normal; no funding-driven caution |
| -1.0 to -2.0 | Elevated negative | Mild short-side crowding |
| < -2.0 | Strongly negative funding | Shorts are paying longs; short squeeze risk |

### Rules

**Wanting to SHORT when funding_z < -2.0:**
Extra caution required. Strongly negative funding means shorts are already crowded and
paying longs. Any upside move can trigger a violent short squeeze. Reduce confidence by
at least 0.20 or avoid entirely.

**Wanting to LONG when funding_z > +2.0:**
Extra caution required. Strongly positive funding means longs are crowded and paying shorts.
Any downside move can trigger a long squeeze. Reduce confidence by at least 0.20 or avoid.

## Open Interest (OI) — Price Divergence

OI-price divergence (`features.oi_price_divergence`) signals a weakening move:

- **Price rising + OI falling:** Longs are closing, not new buyers entering. Move is
  likely to reverse. Avoid new longs.
- **Price falling + OI falling:** Shorts are covering, not new sellers entering. Move is
  likely to reverse. Avoid new shorts.
- **Price rising + OI rising:** New longs entering, genuine bullish conviction. Valid long.
- **Price falling + OI rising:** New shorts entering, genuine bearish conviction. Valid short.

Check `features.oi_price_divergence` (positive = divergence detected, negative = confirming).

## Basis Z-Score

The basis z-score (`features.basis_z`) measures perp premium vs. spot:
- High positive basis (perp > spot) = market is bullish; normal for bull markets
- High negative basis (backwardation) = market stress or bearish dominance

Extreme basis (|basis_z| > 2) warrants extra caution regardless of direction.

## Liquidation Clusters

If liquidation cluster data is available:
- Large long liquidation cluster above current price = potential target for shorts
- Large short liquidation cluster below current price = potential target for longs
- Trading INTO a liquidation cluster (e.g., longing into a long liq. cluster) = stop hunt risk
