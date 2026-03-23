"""Title cleaning rules for podcast episode titles."""
from __future__ import annotations

import re
from typing import Any

from podcast_etl.models import format_date

# Characters invalid on macOS, Windows, or Linux filesystems (plus colon)
_INVALID_FS_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# Any sequence of underscores/whitespace/dashes containing at least one underscore or dash
_SEPARATOR_COLLAPSE_RE = re.compile(r'[\s_-]*[_-][\s_-]*')

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


# Minimum common prefix length to insert part after prefix rather than prepend.
# Below this threshold, the prefix is likely a short word (e.g. "The") rather
# than a meaningful series name.
_MIN_PREFIX_LEN = 5


def _extract_part(title: str) -> tuple[str, str] | None:
    """Extract a bracketed part indicator and return (part_text, remainder).

    Returns None if no bracketed part indicator is found.
    """
    match = _BRACKETED_PART_RE.search(title)
    if not match:
        return None
    part_text = match.group(1) or match.group(2) or match.group(3)
    before = title[:match.start()]
    after = title[match.end():]
    remainder = (before + " " + after).strip() if before.strip() and after.strip() else (before + after).strip()
    remainder = re.sub(r"^[-\u2013\u2014]\s*", "", remainder)
    remainder = re.sub(r"\s*[-\u2013\u2014]$", "", remainder)
    return part_text, remainder


def _common_prefix(strings: list[str]) -> str:
    """Return the longest common prefix of a list of strings."""
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _normalize_date(published: str | None) -> str | None:
    """Normalize a published date string to YYYY-MM-DD for grouping."""
    return format_date(published)


def reorder_parts(title: str, published: str | None = None, all_entries: list[Any] | None = None) -> str:
    """Reorder a bracketed part indicator using same-day sibling context.

    When sibling entries from the same publish date are available, computes
    the longest common prefix across all sibling titles (after stripping
    part indicators) and inserts the part number after the prefix:
    'World War II - D-Day (Part 3)' -> 'World War II - Part 3 - D-Day'

    If the common prefix is shorter than 5 characters, falls back to
    prepending: 'Title (Part 1)' -> 'Part 1 - Title'

    If no same-day siblings have part indicators, the title is returned
    unchanged. Only matches Part/Pt./Pt inside (), [], or {}.
    """
    if not title:
        return title
    extracted = _extract_part(title)
    if not extracted:
        return title
    part_text, remainder = extracted

    # Find same-day siblings with part indicators
    siblings: list[str] = []
    if published and all_entries:
        my_date = _normalize_date(published)
        if my_date:
            for entry in all_entries:
                entry_date = _normalize_date(entry.get("published"))
                if entry_date != my_date:
                    continue
                entry_title = entry.get("title", "")
                entry_extracted = _extract_part(entry_title)
                if entry_extracted:
                    siblings.append(entry_extracted[1])  # remainder after stripping part

    # No siblings — leave unchanged
    if len(siblings) < 2:
        return title

    # Compute common prefix across all sibling remainders, snapped to
    # the last word boundary so we don't split mid-word.
    raw_prefix = _common_prefix(siblings)
    # Snap to last space or separator boundary
    boundary = max(raw_prefix.rfind(" "), raw_prefix.rfind("-"), raw_prefix.rfind("\u2013"), raw_prefix.rfind("\u2014"))
    prefix = raw_prefix[:boundary].rstrip() if boundary > 0 else ""
    # Clean trailing separators from prefix
    prefix = re.sub(r"\s*[-\u2013\u2014]\s*$", "", prefix).rstrip()

    if len(prefix) >= _MIN_PREFIX_LEN:
        # Insert part after common prefix
        suffix = remainder[len(prefix):].strip()
        suffix = re.sub(r"^[-\u2013\u2014]\s*", "", suffix)
        if suffix:
            return f"{prefix} - {part_text} - {suffix}"
        else:
            return f"{prefix} - {part_text}"
    else:
        # Short prefix — prepend part to front
        return f"{part_text} - {remainder}" if remainder else part_text


def sanitize(title: str) -> str:
    """Replace filesystem-invalid characters and normalize separators.

    Replaces characters that are invalid on macOS, Windows, or Linux
    (plus colon) with underscores, then collapses any mix of underscores,
    whitespace, and dashes into a single ' - '.
    """
    if not title:
        return title
    result = _INVALID_FS_CHARS_RE.sub('_', title)
    result = _SEPARATOR_COLLAPSE_RE.sub(' - ', result)
    result = result.strip()
    result = re.sub(r'^[\s_-]+', '', result)
    result = re.sub(r'[\s_-]+$', '', result)
    return result if result else title


def clean_title(
    title: str,
    config: dict | None,
    published: str | None = None,
    all_entries: list[Any] | None = None,
) -> str:
    """Apply enabled title cleaning rules based on config flags.

    Rules are applied in order: strip_date first, then reorder_parts.
    When reorder_parts is enabled, *published* and *all_entries* provide
    same-day sibling context for intelligent part reordering.
    """
    if not config:
        return title
    if config.get("strip_date"):
        title = strip_date(title)
    if config.get("reorder_parts"):
        # Note: all_entries still contain original titles (before strip_date).
        # This is fine because reorder_parts strips part indicators first, and
        # dates typically appear after the episode-specific suffix.
        title = reorder_parts(title, published=published, all_entries=all_entries)
    if config.get("sanitize"):
        title = sanitize(title)
    return title
