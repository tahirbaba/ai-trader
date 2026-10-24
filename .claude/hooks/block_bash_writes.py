#!/usr/bin/env python3
"""
PreToolUse hook — blocks Bash commands that write, move, delete, or overwrite
protected files: .env, config/risk.yaml, config/live_approval.yaml,
.claude/settings.json, .claude/settings.local.json.

Detected patterns (case-sensitive paths, anchored to filename):
  sed -i / sed --in-place  targeting a protected path
  > or >> redirect         targeting a protected path
  tee                      targeting a protected path
  mv / cp                  where the destination is a protected path
  rm / unlink              targeting a protected path
  git checkout -- <path>   targeting a protected path
  git restore <path>       targeting a protected path

Exit 1 (with message on stderr) to block; exit 0 to allow.
"""

from __future__ import annotations

import json
import re
import sys

PROTECTED = [
    r"\.env",
    r"config/risk\.yaml",
    r"config/live_approval\.yaml",
    r"\.claude/settings\.json",
    r"\.claude/settings\.local\.json",
]

# Build a single alternation pattern for "is this string a protected path?"
_PROTECTED_PAT = re.compile(
    r"(?:^|[\s'\"/])(" + "|".join(PROTECTED) + r")(?:$|[\s'\"])",
)


def _mentions_protected(text: str) -> str | None:
    """Return the matched protected path if found, else None."""
    m = _PROTECTED_PAT.search(text)
    return m.group(1) if m else None


# Patterns for dangerous operations.
# Each is (regex_on_command, description).
_DANGER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # sed -i or sed --in-place
    (re.compile(r"\bsed\s+(-\w*i\w*|--in-place)"), "sed -i (in-place edit)"),
    # output redirect  >  or  >>  (but not >&2 or 2>&1 — stderr redirects are safe)
    (re.compile(r"(?<![12&])\s*>{1,2}\s*(?!&)"), "output redirect (> or >>)"),
    # tee
    (re.compile(r"\btee\b"), "tee"),
    # mv / cp with protected destination (checked separately in handler)
    (re.compile(r"\b(mv|cp)\b"), "mv/cp"),
    # rm / unlink
    (re.compile(r"\b(rm|unlink)\b"), "rm/unlink"),
    # git checkout [--] <path> or git restore <path>
    # Use a lookahead so we don't require a word-boundary after non-word chars like --
    (re.compile(r"\bgit\s+(checkout|restore)\b"), "git checkout/restore"),
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


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)  # not our input format — allow

    tool_name = data.get("tool_name", "") or data.get("tool", "")
    if tool_name != "Bash":
        sys.exit(0)

    command: str = (data.get("tool_input") or {}).get("command", "")
    if not command:
        sys.exit(0)

    reason = check_command(command)
    if reason:
        print(
            f"BLOCKED: {reason}. " "Modifying protected files requires explicit owner instruction.",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
