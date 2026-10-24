"""Phase 2B: News collector + input sanitizer.

InputSanitizer functions are importable standalone.
NewsCollector polls free RSS feeds and deduplicates items.
"""

from __future__ import annotations

import datetime
import hashlib
import html
import logging
import re
from typing import Any

import feedparser

from trader.data.collector import CollectorResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feed URLs (no auth required)
# ---------------------------------------------------------------------------
_FEED_URLS = [
    "https://cryptopanic.com/news/rss/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

# ---------------------------------------------------------------------------
# Injection detection phrases
# ---------------------------------------------------------------------------
_INJECTION_PHRASES: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all instructions",
    "disregard",
    "new instructions:",
    "system prompt",
    "you are now",
    "override",
    "[system]",
    "[user]",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Sanitizer — standalone importable functions
# ---------------------------------------------------------------------------


def sanitize_text(text: str, max_length: int = 2000) -> str:
    """Strip HTML tags, collapse whitespace, truncate to *max_length*.

    Steps:
    1. Unescape HTML entities (e.g. ``&amp;`` → ``&``).
    2. Strip remaining HTML tags.
    3. Collapse whitespace.
    4. Truncate and append ``"..."`` if longer than *max_length*.
    """
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_length:
        text = text[:max_length] + "..."
    return text


def detect_injection(text: str) -> bool:
    """Return True if *text* contains any known injection phrase (case-insensitive)."""
    lower = text.lower()
    return any(phrase in lower for phrase in _INJECTION_PHRASES)


def sanitize_news_item(title: str, body: str) -> tuple[str, str, bool]:
    """Sanitize a news item's title and body.

    Returns
    -------
    (clean_title, clean_body, injection_flag)
        * clean_title: sanitised, max 200 chars
        * clean_body: sanitised, max 2000 chars
        * injection_flag: True if injection detected in either field
    """
    clean_title = sanitize_text(title, max_length=200)
    clean_body = sanitize_text(body, max_length=2000)
    injection_flag = detect_injection(clean_title) or detect_injection(clean_body)
    return clean_title, clean_body, injection_flag


# ---------------------------------------------------------------------------
# NewsCollector
# ---------------------------------------------------------------------------


def _dedupe_hash(source: str, link: str, published_iso: str) -> str:
    raw = f"{source}|{link}|{published_iso}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _parsed_to_iso(published_parsed: Any) -> str:
    """Convert feedparser's time struct to an ISO-8601 string."""
    if published_parsed is None:
        return "unknown"
    try:
        dt = datetime.datetime(*published_parsed[:6])
        return dt.isoformat()
    except Exception:  # noqa: BLE001
        return "unknown"


class NewsCollector:
    """Poll free RSS feeds and return sanitised news items.

    Parameters
    ----------
    symbols:
        Trading symbols of interest (currently unused for filtering).
    rest_client:
        Unused; kept for interface parity.
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        rest_client: Any = None,
    ) -> None:
        self._symbols = symbols or []
        self._rest = rest_client
        self._seen_hashes: set[str] = set()

    def collect(self) -> CollectorResult:
        """Fetch and return news items."""
        now = datetime.datetime.utcnow()
        items: list[dict[str, Any]] = []
        quality_flags: list[str] = []

        try:
            feed = self._fetch_feed()
        except Exception as exc:  # noqa: BLE001
            logger.warning("News feed error: %s", exc)
            return CollectorResult(
                symbol="NEWS",
                ts=now,
                data={"items": [], "count": 0, "injection_count": 0},
                quality_flags=["news_feed_error"],
            )

        for entry in feed.entries:
            source = "CryptoPanic"
            link: str = getattr(entry, "link", "") or ""
            published_parsed = getattr(entry, "published_parsed", None)
            published_iso = _parsed_to_iso(published_parsed)

            h = _dedupe_hash(source, link, published_iso)
            if h in self._seen_hashes:
                continue
            self._seen_hashes.add(h)

            title_raw: str = getattr(entry, "title", "") or ""
            summary_raw: str = getattr(entry, "summary", "") or ""

            clean_title, clean_summary, injection_flag = sanitize_news_item(title_raw, summary_raw)

            ts_published: datetime.datetime
            if published_parsed:
                try:
                    ts_published = datetime.datetime(*published_parsed[:6])
                except Exception:  # noqa: BLE001
                    ts_published = now
            else:
                ts_published = now

            items.append(
                {
                    "title": clean_title,
                    "url": link,
                    "source": source,
                    "ts_published": ts_published,
                    "summary": clean_summary,
                    "injection_flag": injection_flag,
                    "dedupe_hash": h,
                }
            )

        injection_count = sum(1 for item in items if item["injection_flag"])

        return CollectorResult(
            symbol="NEWS",
            ts=now,
            data={"items": items, "count": len(items), "injection_count": injection_count},
            quality_flags=quality_flags,
        )

    def _fetch_feed(self) -> Any:
        """Try each feed URL in order; return the first successful parse."""
        last_exc: Exception | None = None
        for url in _FEED_URLS:
            try:
                feed = feedparser.parse(url)
                return feed
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Feed URL %s failed: %s", url, exc)
        if last_exc:
            raise last_exc
        raise RuntimeError("No feed URLs configured")

    def backfill(self, limit: int) -> list[CollectorResult]:
        """Not supported; returns empty list."""
        return []
