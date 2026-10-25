# Phase 5 — Risk Gate Specification

## Overview

The Risk Gate is the final code-based safety layer between a brain Decision and the Executor.
It enforces all hard risk limits defined in `config/risk.yaml` and emits structured verdicts.

## Architecture

```
Decision → LessonFilter → RiskGate → Executor
```

The LLM never sees exchange APIs. The RiskGate is pure Python, deterministic, and has no I/O
during evaluation (all state is passed in as arguments).

## Components

### `src/trader/risk/models.py`
Pydantic data models for RiskGate I/O. No SQLAlchemy.

- `DecisionInput` — brain output (action, symbol, prices, confidence)
- `AccountState` — equity, daily/weekly loss, position counts
- `MarketState` — live price, spread, ATR, funding, data freshness
- `LessonCheckInput` — LessonFilter result passed in
- `RiskVerdict` — approved flag, position size, failure reasons

### `src/trader/risk/sizing.py`
Pure-function position sizing and liquidation math.

- `compute_size()` — max qty from loss cap and margin cap, rounded down to lot_size
- `compute_liquidation_price()` — isolated-margin liq price (Bybit formula)
- `check_risk_reward()` — reward-to-risk ratio after fees

### `src/trader/risk/gate.py`
`RiskGate.evaluate()` — runs 27 checks in order, returns `RiskVerdict`.

### `src/trader/execution/layer2_check.py`
Independent cap re-check in the Executor path. Raises `CapBreachError` if limits breached.

### `src/trader/risk/recorder.py`
DSOR write helpers: `record_risk_check()`, `record_risk_event()`.

## Risk Checks (in order)

| # | Name | Rule |
|---|------|------|
| 1 | kill_switch | system_state.kill_switch = on |
| 2 | mode_check | LIVE requires live_approval.yaml |
| 3 | action_filter | HOLD/NO_TRADE → no order |
| 4 | symbol_allowed | symbol in allowed_symbols |
| 5 | lesson_filter_blocked | lesson_check.blocked |
| 6 | clock_drift | clock_drift_ms > max |
| 7 | data_freshness | data_age_seconds > max |
| 8 | spread_check | spread_pct > max |
| 9 | macro_blackout | is_macro_blackout |
| 10 | exchange_blackout | is_exchange_blackout |
| 11 | equity_floor | equity < floor |
| 12 | daily_loss_limit | daily_loss >= limit |
| 13 | weekly_loss_limit | weekly_loss >= limit |
| 14 | consecutive_losses | consecutive_losses >= max |
| 15 | cooldown | last_loss within cooldown window |
| 16 | max_open_positions | open_positions >= max |
| 17 | max_trades_today | trades_today >= max |
| 18 | llm_budget | llm_cost >= budget |
| 19 | confidence | effective_confidence < min |
| 20 | confluence | confluence_score < min |
| 21 | require_sl | stop_loss == 0 or wrong side |
| 22 | stop_atr_range | stop distance outside ATR bounds |
| 23 | funding_window | funding event within window and against position |
| 24 | sizing | compute_size() raises SizingError |
| 25 | liq_check | liq_distance < liq_buffer * stop_distance |
| 26 | rr_check | RR after fees < min_risk_reward |
| 27 | entry_drift | price moved too far since brain snapshot |

## Sizing Math

```
stop_distance = abs(entry - stop)
stop_distance_with_slippage = stop_distance * (1 + slippage_buffer_pct)
entry_fee = entry * qty * taker_fee_rate
exit_fee = stop * qty * taker_fee_rate
loss_at_stop = qty * stop_distance_with_slippage + entry_fee + exit_fee

max_qty_from_loss = max_loss_usd / (stop_distance_with_slippage + entry * taker_fee_rate + stop * taker_fee_rate)
max_qty_from_margin = max_margin_usd * max_leverage / entry
raw_qty = min(max_qty_from_loss, max_qty_from_margin)
qty = floor(raw_qty / lot_size) * lot_size
```

## Layer 2 Independence

`layer2_check.py` is intentionally independent of `gate.py`. It recomputes limits from
scratch using only `sizing.py`. This prevents a single code path failure from bypassing
both checks.
