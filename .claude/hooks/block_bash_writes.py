#!/usr/bin/env python3
"""
PreToolUse hook — blocks Bash commands that write, move, delete, or overwrite
protected files: .env, config/risk.yaml, config/live_approval.yaml,
.claude/settings.json, .claude/settings.local.json, and the hook scripts themselves.

Detected patterns (case-sensitive paths, anchored to filename):
  sed -i / sed --in-place  targeting a protected path
  > or >> redirect         targeting a protected path
  tee                      targeting a protected path
  mv / cp                  where the destination is a protected path
  rm / unlink              targeting a protected path
  git checkout [--] <path> targeting a protected path
  git restore <path>       targeting a protected path
  python/python3/perl/node one-liners writing to a protected path
  cat / printf / echo with redirect

Exit codes (Claude Code PreToolUse contract):
  0 = allow
  2 = BLOCK (reason on stderr is shown to Claude)
Any other non-zero code is a NON-blocking error and the tool still runs, so
every block path and every malformed-input path exits 2 (fail-closed).
"""

from __future__ import annotations

import json
import re
import sys
from typing import NoReturn

PROTECTED = [
    r"\.env(?!\w)",
    r"config/risk\.yaml",
    r"config/live_approval\.yaml",
    r"\.claude/settings\.json",
    r"\.claude/settings\.local\.json",
    r"\.claude/hooks/block_sensitive\.sh",
    r"\.claude/hooks/block_bash_writes\.py",
]

# Build a single alternation pattern for "is this string a protected path?"
_PROTECTED_PAT = re.compile(
    r"(?:^|[\s'\"/])(" + "|".join(PROTECTED) + r")(?:$|[\s'\"<>|]|$)",
)


def _mentions_protected(text: str) -> str | None:
    """Return the matched protected path if found, else None."""
    m = _PROTECTED_PAT.search(text)
    return m.group(1) if m else None


# Patterns for dangerous write/mutate operations.
# Each is (regex_on_command, description).
_DANGER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # sed -i or sed --in-place
    (re.compile(r"\bsed\s+(-\w*i\w*|--in-place)"), "sed -i (in-place edit)"),
    # output redirect  >  or  >>  (but not >&2 or 2>&1 — stderr redirects are safe)
    (re.compile(r"(?<![12&])\s*>{1,2}\s*(?!&)"), "output redirect (> or >>)"),
    # tee (always writes to a file)
    (re.compile(r"\btee\b"), "tee"),
    # mv / cp with protected destination (checked separately)
    (re.compile(r"\b(mv|cp)\b"), "mv/cp"),
    # rm / unlink
    (re.compile(r"\b(rm|unlink)\b"), "rm/unlink"),
    # git checkout [--] <path> or git restore <path>
    (re.compile(r"\bgit\s+(checkout|restore)\b"), "git checkout/restore"),
    # python/python3 -c with write-targeting expressions
    (re.compile(r"\bpython3?\s+(-c|[^-].*open\()"), "python one-liner"),
    # perl -i or perl -e with write-targeting
    (re.compile(r"\bperl\s+(-i|-e)"), "perl one-liner"),
    # node/nodejs -e one-liners
    (re.compile(r"\bnode\s+-e\b"), "node one-liner"),
    # cat with redirect (already caught by redirect pattern, but explicit for clarity)
    # printf is already caught by redirect; echo too
]


def check_command(command: str) -> str | None:
    """
    Return a block reason string if the command is dangerous, else None.
    """
    for pat, desc in _DANGER_PATTERNS:
        if pat.search(command):
            protected = _mentions_protected(command)
            if protected:
                return f"{desc} targeting protected file '{protected}'"
    return None


def _block(reason: str) -> NoReturn:
    print(f"BLOCKED: {reason}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        _block("malformed hook input (fail-closed).")

    if not isinstance(data, dict):
        _block("malformed hook input (fail-closed).")

    tool_name = data.get("tool_name") or data.get("tool")
    if not isinstance(tool_name, str) or not tool_name:
        _block("missing tool_name in hook input (fail-closed).")
    if tool_name != "Bash":
        sys.exit(0)  # matcher misroute — not a Bash call, nothing to guard

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        _block("missing tool_input in hook input (fail-closed).")

    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        _block("missing or empty command in hook input (fail-closed).")

    reason = check_command(command)
    if reason:
        _block(f"{reason}. Modifying protected files requires explicit owner instruction.")

    sys.exit(0)


if __name__ == "__main__":
    main()
