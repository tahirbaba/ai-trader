"""Guard: .env.example must never carry real secret values.

Only TRADING_MODE, EXCHANGE_NAME and DATABASE_URL may have a value; every other
variable must be an empty placeholder. Failure messages name the offending
variables but never print their values.
"""

from __future__ import annotations

import re
from pathlib import Path

ENV_EXAMPLE = Path(__file__).parent.parent / ".env.example"

ALLOWED_VALUES = {
    "TRADING_MODE": "paper",
    "EXCHANGE_NAME": "bybit",
    "DATABASE_URL": "sqlite:///./trader.db",
}

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _parse() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ASSIGNMENT.match(line)
        assert m, f".env.example has an unparseable line: {line.split('=', 1)[0]!r}"
        values[m.group(1)] = m.group(2).strip().strip("\"'")
    return values


def test_only_allowed_variables_have_values() -> None:
    offenders = sorted(k for k, v in _parse().items() if k not in ALLOWED_VALUES and v)
    assert not offenders, f".env.example must not contain values for: {offenders}"


def test_allowed_variables_have_safe_defaults() -> None:
    values = _parse()
    for key, expected in ALLOWED_VALUES.items():
        assert values.get(key) == expected, f"{key} must be {expected!r} in .env.example"
