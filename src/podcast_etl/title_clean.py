"""Title cleaning rules for podcast episode titles."""
from __future__ import annotations

import re

# Date patterns (used inside bracket groups)
_MONTH_NAMES = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
)

# Numeric dates: M/D/YY, MM/DD/YYYY, etc. with /, -, _ separators.
# This is intentionally loose — it may match non-date sequences like (1/2/34).
# Acceptable trade-off for podcast titles where such patterns are rare.
_NUMERIC_DATE = r"\d{1,2}[/_-]\d{1,2}[/_-]\d{2,4}"
# ISO dates: YYYY-MM-DD
_ISO_DATE = r"\d{4}-\d{2}-\d{2}"
# Month name dates: March 22, 2026 or Mar 22 2026
_MONTH_DATE = _MONTH_NAMES + r"\s+\d{1,2},?\s+\d{4}"

_DATE_INTERIOR = rf"(?:{_NUMERIC_DATE}|{_ISO_DATE}|{_MONTH_DATE})"

# Bracketed date with optional surrounding whitespace/dashes
# Only consume leading separator (dash before bracket) to avoid eating
# a trailing separator that belongs to subsequent content.
_BRACKETED_DATE = (
    r"\s*[-\u2013\u2014]*\s*"
    r"(?:"
    rf"\({_DATE_INTERIOR}\)"
    rf"|\[{_DATE_INTERIOR}\]"
    rf"|\{{{_DATE_INTERIOR}\}}"
    r")"
    r"\s*"
)

_BRACKETED_DATE_RE = re.compile(_BRACKETED_DATE)


def strip_date(title: str) -> str:
    """Remove all bracketed date strings from a title.

    Only matches dates inside (), [], or {}. Removes every match
    (titles with multiple bracketed dates get all of them stripped).
    Cleans up adjacent whitespace and dashes. Returns the original
    if stripping would leave an empty result.
    """
    if not title:
        return title
    # Replace with space (not empty) so surrounding words don't merge;
    # the regex's \s* already consumes adjacent whitespace.
    result = _BRACKETED_DATE_RE.sub(" ", title).strip()
    # Clean up leftover dangling separators at start/end
    result = re.sub(r"^[-\u2013\u2014]\s*", "", result)
    result = re.sub(r"\s*[-\u2013\u2014]$", "", result)
    return result if result else title


# Part indicator pattern inside brackets: Part 1, Pt. 2, Pt 3
_PART_INTERIOR = r"(?:(?:Part|Pt)\.?\s*\d+)"

_BRACKETED_PART_RE = re.compile(
    r"\s*[-\u2013\u2014]*\s*"
    r"(?:"
    rf"\(({_PART_INTERIOR})\)"
    rf"|\[({_PART_INTERIOR})\]"
    rf"|\{{({_PART_INTERIOR})\}}"
    r")"
    r"\s*",
    re.IGNORECASE,
)


def reorder_parts(title: str) -> str:
    """Move the first bracketed part indicator to the front of the title.

    Transforms 'Title (Part 1)' to 'Part 1 - Title'. Only moves the
    first match — additional part indicators stay in place. Preserves
    original casing. Only matches Part/Pt./Pt inside (), [], or {}.
    """
    if not title:
        return title
    match = _BRACKETED_PART_RE.search(title)
    if not match:
        return title
    # One of the three capture groups will have the match
    part_text = match.group(1) or match.group(2) or match.group(3)
    before = title[:match.start()]
    after = title[match.end():]
    remainder = (before + " " + after).strip() if before.strip() and after.strip() else (before + after).strip()
    # Clean up dangling separators
    remainder = re.sub(r"^[-\u2013\u2014]\s*", "", remainder)
    remainder = re.sub(r"\s*[-\u2013\u2014]$", "", remainder)
    return f"{part_text} - {remainder}" if remainder else part_text


def clean_title(title: str, config: dict | None) -> str:
    """Apply enabled title cleaning rules based on config flags.

    Rules are applied in order: strip_date first, then reorder_parts.
    """
    if not config:
        return title
    if config.get("strip_date"):
        title = strip_date(title)
    if config.get("reorder_parts"):
        title = reorder_parts(title)
    return title
