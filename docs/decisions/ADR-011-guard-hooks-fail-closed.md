# ADR-011: Guard Hooks Block With Exit 2 and Fail Closed

**Date**: 2026-10-27
**Status**: Accepted
**Phase**: fix (between Phase 8 and Phase 9)

## Context

`.claude/hooks/block_sensitive.sh` (Edit/Write/MultiEdit/NotebookEdit) and
`.claude/hooks/block_bash_writes.py` (Bash) protect `.env`, `config/risk.yaml`,
`config/live_approval.yaml`, `.claude/settings*.json` and the hook scripts.

Both hooks signalled a block with **exit 1**. In Claude Code a PreToolUse hook
only blocks with **exit 2** (or JSON `permissionDecision: "deny"`); any other
non-zero code is a non-blocking error and the tool call still runs. The guards
were therefore advisory only. Malformed input was also allowed (fail-open).

## Decision

1. Every block path exits **2** with a `BLOCKED: <reason>` message on stderr.
2. Fail-closed: unreadable/empty stdin, invalid JSON, a non-object payload, a
   missing `tool_input`, or a missing/non-string `file_path` / `command` all
   exit 2. `block_sensitive.sh` also traps unexpected script errors to exit 2.
3. `block_sensitive.sh` normalises the path (`os.path.normpath`) and reads
   `notebook_path` for NotebookEdit.
4. Tests assert `returncode == 2` exactly (never `!= 0`), and cover the
   fail-closed paths for both hooks.

## Consequences

- Protected files can no longer be changed by Claude through Edit/Write or the
  detected Bash write patterns; the owner edits them by hand.
- The Bash guard is pattern-based and conservative: commands that merely
  *mention* a protected path alongside a write-like token (e.g. a heredoc
  containing `.env`) are blocked as false positives. This is accepted.
- Hook scripts are re-executed on every tool call, so changes take effect
  without a session restart (verified live).
