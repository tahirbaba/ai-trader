# Phase 4: KSOR (Knowledge Source of Record) — Specification

## Goal

Deliver a versioned, searchable knowledge base that the agent reads before every decision and
writes to after learning. Lessons with machine-checkable rules are enforced in code via
`LessonFilter` — not only as prompt text.

## Acceptance Criteria

1. **KsorEntry** Pydantic model parses a `.md` file with YAML frontmatter; all fields default
   gracefully on missing data; `body` captures the markdown text after the frontmatter.

2. **Loader** (`src/trader/ksor/loader.py`) walks `/ksor/**/*.md`, parses frontmatter with
   `python-frontmatter`, returns a list of `KsorEntry` objects. Gracefully skips files that
   fail parsing (logs a warning).

3. **Rule DSL** (`src/trader/ksor/rule_engine.py`) evaluates `evaluate_rule(rule, context)`
   with:
   - Supported ops: `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`, `not_in`
   - Wrappers: `all` (all clauses true), `any` (at least one true), `not` (inversion)
   - Dot-path field lookup into nested dicts (`decision.side`, `features.funding_z`)
   - Unknown field → returns `False` (never raises)
   - Unknown op → raises `RuleEngineError`
   - No `eval`, `exec`, or `importlib` anywhere

4. **Indexer** (`src/trader/ksor/indexer.py`) builds/updates an SQLite FTS5 index over
   entry text + tags. The index file is created at a configurable path (default: `trader.db`
   or in-memory for tests).

5. **Retriever** (`src/trader/ksor/retriever.py`) implements
   `retrieve(regime, symbol, side, tags, k, include_candidates=False)`:
   - Returns at most `k` relevant `active` entries (playbook + lessons)
   - `deprecated` entries never returned
   - `candidate` entries returned only if `include_candidates=True`
   - Empty results are a valid return (not an error)

6. **LessonWriter** (`src/trader/ksor/lesson_writer.py`) is the only component that may
   create or modify files under `/ksor/lessons/`:
   - Path guard: raises `ValueError` if the computed output path is outside the `ksor_root`
     lessons directory
   - Every write produces a git commit (local repo only, not pushed) with a descriptive
     message and linked trade IDs
   - Updates the `lessons_index` DSOR table after writing

7. **LessonFilter** (`src/trader/risk/lesson_filter.py`) implements
   `evaluate(decision_dict, feature_dict) -> LessonFilterResult`:
   - Loads all active lessons, runs `rule_engine.evaluate_rule` on each
   - Collects `block`, `confidence_penalty`, and `require_extra_confirmation` effects
   - Any lesson whose `effect` is not in that set is rejected at load time with a warning
   - A lesson with a "loosening" effect (raises size/leverage/relaxes a limit) is rejected
   - Stores a `lesson_checks` row

8. **Sync** (`src/trader/ksor/sync.py`) implements
   `sync_lessons_index(session, ksor_root)`:
   - Walks `/ksor/lessons/*.md`, upserts `lessons_index` rows
   - Files no longer present → row status set to `deprecated`

9. **Seed content** committed under `/ksor/`:
   - `playbook/001_trading_rules.md` through `005_funding_time.md`
   - `mistakes/TAXONOMY.md` with all 14 mistake types
   - `lessons/L-0001_no_short_strong_negative_funding.md`

10. **Tests** (flat `tests/` directory):
    - `test_ksor_loader.py`
    - `test_ksor_rule_engine.py`
    - `test_ksor_retriever.py`
    - `test_lesson_filter.py`
    - `test_lesson_writer.py`
    - `test_ksor_sync.py`

## Non-Goals (Phase 4)

- Embedding-based search (deferred to a future phase)
- Remote git push for lesson commits
- Lesson lifecycle automation (Phase 8)
- Integration with the LLM brain (Phase 6)
