#!/usr/bin/env bash
# PreToolUse hook — blocks Edit, Write, MultiEdit, NotebookEdit on protected files.
# Claude Code sends hook input as JSON on stdin with the structure:
#   {"tool_name": "<name>", "tool_input": {"file_path": "...", ...}}
# The file_path is under tool_input, NOT at the top level.
#
# Exit codes (Claude Code PreToolUse contract):
#   0 = allow
#   2 = BLOCK (reason on stderr is shown to Claude)
# Any other non-zero code is a NON-blocking error and the tool still runs,
# so every block path and every failure path below must exit 2 (fail-closed).

block() {
  echo "BLOCKED: $1" >&2
  exit 2
}

# Any unexpected failure in this script must also block.
trap 'block "hook error (fail-closed)."' ERR

INPUT=$(cat) || block "could not read hook input (fail-closed)."
[[ -n "$INPUT" ]] || block "empty hook input (fail-closed)."

# Extract file_path from tool_input (nested key). Exit 3 on malformed input.
FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(3)
if not isinstance(d, dict) or not isinstance(d.get('tool_input'), dict):
    sys.exit(3)
ti = d['tool_input']
# Edit/Write/MultiEdit: file_path; NotebookEdit: notebook_path (older: path)
p = ti.get('file_path') or ti.get('notebook_path') or ti.get('path')
if not isinstance(p, str) or not p:
    sys.exit(3)
print(os.path.normpath(p))
" 2>/dev/null) || block "malformed hook input or missing file path (fail-closed)."

# Files that require explicit owner instruction to modify
PROTECTED=(
  ".env"
  "config/risk.yaml"
  "config/live_approval.yaml"
  ".claude/settings.json"
  ".claude/settings.local.json"
  ".claude/hooks/block_sensitive.sh"
  ".claude/hooks/block_bash_writes.py"
)

for p in "${PROTECTED[@]}"; do
  if [[ "$FILE_PATH" == *"$p" ]]; then
    block "'$p' is a protected file. Requires explicit owner instruction."
  fi
done

exit 0
