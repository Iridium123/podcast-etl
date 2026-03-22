"""Title cleaning rules for podcast episode titles."""
from __future__ import annotations

import re

# Date patterns (used inside bracket groups)
_MONTH_NAMES = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
)

# Numeric dates: M/D/YY, MM/DD/YYYY, etc. with /, -, _ separators
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
    """Remove bracketed date strings from a title.

    Only matches dates inside (), [], or {}. Cleans up adjacent
    whitespace and dashes. Returns the original if stripping would
    leave an empty result.
    """
    if not title:
        return title
    result = _BRACKETED_DATE_RE.sub(" ", title).strip()
    # Clean up leftover dangling separators at start/end
    result = re.sub(r"^[-\u2013\u2014]\s*", "", result)
    result = re.sub(r"\s*[-\u2013\u2014]$", "", result)
    return result if result else title
