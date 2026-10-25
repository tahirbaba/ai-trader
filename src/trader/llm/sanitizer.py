"""Phase 6: Input sanitizer for all external text fed to LLM prompts.

All external text (news titles, bodies, macro event names) MUST be passed through
sanitize_for_prompt() before being included in any prompt string.
The sanitizer wraps content in clearly-delimited data blocks so LLM system prompts
can instruct the model to treat it as DATA, never as instructions.
"""

from __future__ import annotations

import html
import re
import unicodedata

# ---------------------------------------------------------------------------
# HTML tag stripping
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Unescape HTML entities, then strip HTML tags."""
    text = html.unescape(text)
    text = _HTML_TAG_RE.sub(" ", text)
    return text


def _collapse_whitespace(text: str) -> str:
    """Collapse multiple whitespace characters into a single space and strip ends."""
    return re.sub(r"\s+", " ", text).strip()


def _normalize_unicode(text: str) -> str:
    """NFKC-normalize to prevent Unicode homoglyph attacks."""
    return unicodedata.normalize("NFKC", text)


def sanitize_for_prompt(text: str, max_length: int = 500, label: str = "data") -> str:
    """Strip markup, truncate, wrap in a clearly-delimited data block.

    Output format:
    <{label}>
    {cleaned_text}
    </{label}>

    Strips HTML, normalizes Unicode, collapses whitespace, truncates to max_length chars.
    Raises nothing — always returns a safe string.

    Parameters
    ----------
    text:       Raw external text (news title, body, event name, etc.)
    max_length: Maximum character length of the cleaned text body.
    label:      XML-like tag label for the data block (default "data").

    Returns
    -------
    str: Wrapped, sanitized data block ready for prompt inclusion.
    """
    try:
        cleaned = _normalize_unicode(text)
        cleaned = _strip_html(cleaned)
        cleaned = _collapse_whitespace(cleaned)

        if len(cleaned) > max_length:
            cleaned = cleaned[:max_length]

        return f"<{label}>\n{cleaned}\n</{label}>"
    except Exception:
        # Never raise — return empty data block on any error
        return f"<{label}>\n</{label}>"
