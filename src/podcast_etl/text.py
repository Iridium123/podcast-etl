"""Utilities for cleaning podcast text fields (descriptions, titles, etc.)."""

from __future__ import annotations

import html
import re


def clean_description(raw: str | None) -> str | None:
    """Convert an RSS description from any common format to clean plain text.

    Handles:
    - CDATA-wrapped HTML (<![CDATA[<p>...]])
    - HTML entity-encoded markup (&lt;p&gt;...)
    - Plain text with line breaks
    - Mixed content

    Preserves paragraph structure and spacing but strips tags, links, and
    other rich content.
    """
    if not raw or not raw.strip():
        return None

    text = raw

    # Unwrap CDATA if present (feedparser usually strips this, but be safe)
    text = re.sub(r"<!\[CDATA\[(.*?)]]>", r"\1", text, flags=re.DOTALL)

    # Decode HTML entities first so we can work with actual tags.
    # Two passes to handle double-encoded entities (&amp;lt; -> &lt; -> <).
    text = html.unescape(html.unescape(text))

    # Normalise <br>, <br/>, <br /> to newlines before stripping tags
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Block-level elements get paragraph breaks
    text = re.sub(r"</(?:p|div|blockquote|li|h[1-6])>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<(?:p|div|blockquote|li|h[1-6])\b[^>]*>", "", text, flags=re.IGNORECASE)

    # Strip all remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Collapse runs of whitespace on each line (but keep newlines)
    lines = text.split("\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
    text = "\n".join(lines)

    # Collapse 3+ consecutive newlines to 2 (preserve paragraph breaks)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip() or None


def contains_blacklisted(text: str | None, blacklist: list[str]) -> bool:
    """Return True if text contains any blacklisted string (case-insensitive)."""
    if not text or not blacklist:
        return False
    lower = text.lower()
    return any(entry.lower() in lower for entry in blacklist)


def apply_blacklist(
    text: str | None,
    blacklist: list[str],
) -> str | None:
    """Return None if text contains any blacklisted string, otherwise return text unchanged."""
    if contains_blacklisted(text, blacklist):
        return None
    return text
