#!/usr/bin/env bash
# PreToolUse hook — blocks Edit, Write, MultiEdit, NotebookEdit on protected files.
# Claude Code sends hook input as JSON on stdin with the structure:
#   {"tool_name": "<name>", "tool_input": {"file_path": "...", ...}}
# The file_path is under tool_input, NOT at the top level.
set -e

INPUT=$(cat)

# Extract file_path from tool_input (nested key)
FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input') or {}
    # Edit/Write: file_path; MultiEdit: file_path; NotebookEdit: path
    print(ti.get('file_path') or ti.get('path') or '')
except Exception:
    print('')
" 2>/dev/null || true)

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
    echo "BLOCKED: '$p' is a protected file. Requires explicit owner instruction." >&2
    exit 1
  fi
done
