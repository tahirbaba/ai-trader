# ADR-006: KSOR Schema and Lesson Rule DSL

**Date:** 2026-10-25
**Status:** Accepted
**Phase:** 4

## Context

The agent needs a Knowledge Source of Record (KSOR) that it reads before decisions and writes
to after learning. Lessons must be enforceable in code (not only in prompts) to prevent
repeat mistakes. The rule evaluation must be safe — no `eval`, `exec`, or dynamic imports.

## Decisions

### 1. Markdown + YAML frontmatter as the storage format

**Decision:** Use `.md` files with YAML frontmatter parsed by `python-frontmatter`.

**Rationale:**
- Human-readable and human-editable (owner can review/approve lessons via Telegram + git)
- Git-versioned: every change is auditable with commit history
- Frontmatter provides structured metadata; body provides human-readable explanation
- `python-frontmatter` is a mature library with good YAML parsing

**Alternatives rejected:**
- Pure JSON/YAML files: less readable for human review
- Database-only: loses git history and human editability
- SQLite-only: cannot be reviewed as text in PRs

### 2. Safe rule DSL (no eval)

**Decision:** A small recursive DSL with `all`/`any`/`not` wrappers and `{field, op, value}`
leaf clauses. Fields are dot-path keys into the context dict. Evaluated by a hand-written
interpreter in `rule_engine.py`.

**Rationale:**
- No `eval`, `exec`, or `importlib` — avoids arbitrary code execution
- DSL is expressive enough for the required rules (threshold checks, equality, set membership)
- Unknown op raises `RuleEngineError` (fail-loud); unknown field returns `False` (fail-safe)
- Simple enough to exhaustively test

**Supported ops:** `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`, `not_in`
**Wrappers:** `all`, `any`, `not`

### 3. SQLite FTS5 for the search index

**Decision:** SQLite FTS5 full-text index over entry text + tags, stored in the same DB.

**Rationale:**
- Zero additional infrastructure (already using SQLite)
- FTS5 is fast enough for the expected number of lessons (<1000)
- Can be upgraded to pgvector/embeddings later without changing the retriever interface

### 4. Tighten-only lesson effects

**Decision:** Allowed effects are `block`, `confidence_penalty`, `require_extra_confirmation`
only. Any other effect (including anything that could raise size/leverage/relax a limit) is
rejected at load time with a warning.

**Rationale:**
- Lessons can only make the system more conservative, never more aggressive
- Prevents the learning loop from inadvertently weakening safety constraints
- Enforced at load time (not at evaluation time) so bad lessons are caught early

### 5. LessonWriter as the sole writer of /ksor/lessons/

**Decision:** `LessonWriter` is the only code path that creates or modifies files under
`/ksor/lessons/`. Every write produces a local git commit. Path guard raises `ValueError`
if the output path is outside the lessons directory.

**Rationale:**
- Single point of control for lesson files → easier to audit
- Git commit per write → every lesson creation/modification is traceable
- Path guard prevents directory traversal attacks

### 6. ksor_root injected via constructor

**Decision:** `LessonWriter` (and `sync.py`) accept `ksor_root` as a constructor parameter,
defaulting to the repo's `/ksor` directory.

**Rationale:**
- Enables tests to use a temp directory (no real filesystem side effects)
- Follows the dependency injection pattern used elsewhere in the project

## Consequences

- `python-frontmatter>=1.1` added as a project dependency
- `KsorEntry` Pydantic model is the canonical in-memory representation of any KSOR entry
- `deprecated` lessons are never returned from the retriever
- `candidate` lessons require explicit opt-in (`include_candidates=True`)
