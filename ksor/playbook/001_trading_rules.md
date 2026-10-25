---
id: P-0001
type: playbook
status: active
tags: [macro_blackout, htf_trend, discipline, invalidation]
regime: []
symbols: []
condition: "Core trading rules that apply to every trade decision"
rule: null
effect: null
effect_value: null
action: "Follow all rules before entering any trade"
evidence_trades: []
win_stats: {}
created: "2026-10-25"
last_validated: null
expires: null
---

# Core Trading Rules

## 1. Never Trade Into High-Impact Macro Events

Do not open a new position within 30 minutes before or after any high-impact macro event:
- FOMC (Federal Open Market Committee) decisions and minutes
- US CPI (Consumer Price Index)
- US NFP (Non-Farm Payrolls)
- ECB rate decisions
- BoE rate decisions

The `MacroCalendar.is_blackout(now)` check enforces this automatically. If the check is
unavailable or stale, default to NO_TRADE near the top of every hour on event days.

## 2. Respect the Higher-Timeframe (HTF) Trend

Before entering, check the 4H and 1D trend direction:
- In a strong HTF uptrend: prefer longs, avoid counter-trend shorts
- In a strong HTF downtrend: prefer shorts, avoid counter-trend longs
- If HTF is ambiguous (ranging): reduce position confidence

The `features.htf_aligned` flag in the FeaturePack encodes whether the proposed side
agrees with the 4H trend. A trade against HTF requires an explicit override with reasoning.

## 3. No Revenge Trading

After a loss, do not immediately re-enter to recover the loss. Mandatory cooldown applies
(`cooldown_after_loss_minutes` in risk.yaml). Signs of revenge trading:
- Entering a new trade within the cooldown period after a loss
- Increasing size after a loss
- Entering in the opposite direction immediately after a stopped-out trade

The system enforces the cooldown at the Risk Gate level.

## 4. Define Invalidation Before Entry

Every trade must have a clearly defined invalidation level BEFORE entry:
- Where exactly is the thesis wrong?
- What price/structure change would negate the setup?
- The stop-loss must be placed at or beyond the invalidation level

The `DecisionAgent` output schema requires an `invalidation` field. A trade without a
stated invalidation is rejected by the Risk Gate.

## 5. Plan the Trade, Trade the Plan

Once a trade is entered:
- Do not move the stop-loss further from entry ("widening the stop")
- The `PositionReviewAgent` can only output HOLD, CLOSE, or TIGHTEN_STOP
- Take-profit levels are set at entry; do not move them further out in hope

## 6. Minimum Confluence

Do not trade with fewer than 3 independent signals agreeing. The deterministic
`confluence_score` (from `features/confluence.py`) must be >= `min_confluence_score`
from risk.yaml. The LLM's own claimed confluence is not trusted for this check.
