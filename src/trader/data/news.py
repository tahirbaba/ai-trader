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
import unicodedata
from typing import Any

import feedparser
import requests

from trader.data.collector import CollectorResult

_REQUEST_TIMEOUT = 10

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feed URLs (no auth required)
# Decision-path feeds: real-time RSS only. NEWS_API_KEY (NewsAPI.org, ~24h delayed)
# must NOT be used in live/paper decision path — research only.
# ---------------------------------------------------------------------------
_FEED_URLS: list[tuple[str, str]] = [
    ("https://cryptopanic.com/news/rss/", "CryptoPanic"),
    ("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk"),
    # Federal Reserve press releases (authoritative; low volume)
    ("https://www.federalreserve.gov/feeds/press_monetary.xml", "FederalReserve"),
    # The Block and Decrypt — crypto-native news
    ("https://www.theblock.co/rss/all", "TheBlock"),
    ("https://decrypt.co/feed", "Decrypt"),
]

# Maximum item age for decision-path use. Older items are still returned in
# data but flagged as context_only so the Brain treats them as background.
_MAX_DECISION_AGE_MINUTES = 60

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
    """Return True if *text* contains any known injection phrase (case-insensitive).

    Applies NFKC normalization before matching to catch homoglyph substitutions
    (e.g. Cyrillic 'і' in place of Latin 'i').
    """
    normalized = unicodedata.normalize("NFKC", text).lower()
    return any(phrase in normalized for phrase in _INJECTION_PHRASES)


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
        now = datetime.datetime.now(datetime.UTC)
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
            source = feed._source_label
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
                    ts_published = datetime.datetime(*published_parsed[:6]).replace(
                        tzinfo=datetime.UTC
                    )
                except Exception:  # noqa: BLE001
                    ts_published = now
            else:
                ts_published = now

            age_minutes = (now - ts_published).total_seconds() / 60.0
            context_only = age_minutes > _MAX_DECISION_AGE_MINUTES

            items.append(
                {
                    "title": clean_title,
                    "url": link,
                    "source": source,
                    "ts_published": ts_published,
                    "age_minutes": round(age_minutes, 1),
                    "context_only": context_only,
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
        """Try each feed URL in order; return the first successful parse.

        Uses ``requests`` to pre-fetch the content with an explicit timeout,
        then passes the raw bytes to ``feedparser.parse`` to avoid feedparser's
        internal unbounded HTTP call.
        """
        last_exc: Exception | None = None
        for url, source_label in _FEED_URLS:
            try:
                resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                # Attach source label so the caller knows which feed was used
                feed._source_label = source_label
                # Treat bozo (malformed feed) as a non-fatal warning
                if getattr(feed, "bozo", False):
                    logger.warning(
                        "Feed %s is malformed (bozo): %s",
                        url,
                        getattr(feed, "bozo_exception", "unknown"),
                    )
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


# ---------------------------------------------------------------------------
# No-chase helper — pure function, no I/O
# ---------------------------------------------------------------------------

# High-impact keywords in news titles that may cause a directional price move.
_BULLISH_KEYWORDS = frozenset(
    ["rally", "surge", "breakout", "approved", "adoption", "bullish", "buy", "long"]
)
_BEARISH_KEYWORDS = frozenset(
    ["crash", "ban", "hack", "fraud", "bearish", "sell", "short", "delisted", "seized"]
)


def compute_news_chase_block(
    items: list[dict[str, Any]],
    price_now: float,
    price_at_signal: float,
    atr: float | None,
    no_chase_atr_multiple: float = 1.5,
    max_item_age_minutes: float = 60.0,
) -> tuple[bool, str]:
    """Return (block, direction) if a recent item arrived and price already chased.

    The check is:
    1. At least one non-injection item exists within max_item_age_minutes.
    2. abs(price_now - price_at_signal) > no_chase_atr_multiple * atr.
    3. Direction of the move determines which side to block.

    Parameters
    ----------
    items:
        News items from NewsCollector.collect().
    price_now:
        Current live price.
    price_at_signal:
        Snapshot price or price at feature-pack build time (reference point).
    atr:
        Current ATR-14 (same unit as price).
    no_chase_atr_multiple:
        Block new entries when abs(move) > this multiple × ATR.
    max_item_age_minutes:
        Only consider items this fresh.

    Returns
    -------
    (True, "long")   — price surged; block new longs (already chased up).
    (True, "short")  — price dropped; block new shorts (already chased down).
    (False, "")      — no chase detected.
    """
    if atr is None or atr <= 0 or price_now <= 0 or price_at_signal <= 0:
        return False, ""

    price_delta = price_now - price_at_signal
    if abs(price_delta) <= no_chase_atr_multiple * atr:
        return False, ""

    # Price moved enough — check if any fresh non-injection item exists.
    has_fresh_item = any(
        not item.get("injection_flag")
        and item.get("age_minutes", float("inf")) <= max_item_age_minutes
        for item in items
    )
    if not has_fresh_item:
        return False, ""

    # Block the direction of the move.
    if price_delta > 0:
        return True, "long"  # price surged; block new longs
    return True, "short"  # price fell; block new shorts
