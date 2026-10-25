# Mistake Taxonomy

All post-trade mistakes are classified using the types below. A trade may have multiple
mistake types. Classification is done by the post-trade reviewer (Phase 8) and stored in
the `reviews` table under `mistake_types_json`.

| ID | Type | Description |
|---|---|---|
| 1 | `bad_thesis` | The core directional or structural thesis was wrong from the start — price action contradicted the premise. |
| 2 | `bad_timing` | The thesis was eventually correct but the entry was too early or too late, causing the stop to be hit before the move played out. |
| 3 | `ignored_higher_timeframe` | Entered against the clear 4H or 1D trend without adequate justification; `features.htf_aligned` was False. |
| 4 | `ignored_news_or_calendar` | Entered during or near a high-impact macro event (FOMC, CPI, NFP, etc.); `MacroCalendar.is_blackout` was True or should have been. |
| 5 | `ignored_existing_lesson` | An active lesson rule matched the setup but was overridden or ignored; `lesson_checks.blocked` should have been True. |
| 6 | `funding_oi_squeeze_missed` | Failed to account for extreme funding rate or OI-price divergence that indicated squeeze risk; see playbook/002_funding_oi.md. |
| 7 | `stop_too_tight` | Stop-loss was placed closer than the noise level (< `min_stop_atr_multiple` × ATR), causing a premature exit before the move developed. |
| 8 | `stop_too_wide` | Stop-loss was so wide that the R:R was below the minimum, or the potential loss exceeded the per-trade loss cap. |
| 9 | `overtrading` | Entered when no clear setup was present (chasing, boredom trading); confluence score was below `min_confluence_score`. |
| 10 | `revenge_trade` | Entered to recover a recent loss, violating the cooldown rule; entry was within `cooldown_after_loss_minutes` of a loss. |
| 11 | `execution_slippage` | Fill price was significantly worse than the decision price due to slippage, spread, or order type mismatch; beyond the `slippage_buffer_pct`. |
| 12 | `data_issue` | Decision was made on stale, missing, or incorrect data; `data_quality_flags` were set or data age exceeded `max_data_age_seconds`. |
| 13 | `prompt_injection_suspected` | The LLM's reasoning contained text or instructions that appeared to originate from external untrusted data (news, alerts, web); the `injection_flag` was set or the decision contradicted all code-computed signals. |
| 14 | `random_variance` | All rules were followed correctly; the outcome was unfavorable due to normal market randomness. This is NOT a learning signal — no corrective lesson should be created. |

## Usage Notes

- `random_variance` alone → classify as `good_decision_bad_outcome`; no corrective lesson.
- `prompt_injection_suspected` → escalate to owner review; flag in `risk_events`.
- Multiple types: list all that apply. Common combinations:
  - `bad_timing` + `ignored_higher_timeframe`
  - `overtrading` + `stop_too_tight`
  - `funding_oi_squeeze_missed` + `ignored_existing_lesson`
- The repeat-mistake rate metric (Phase 8) tracks how often a mistake type recurs when an
  active lesson existed for it. Goal: trend to zero.
