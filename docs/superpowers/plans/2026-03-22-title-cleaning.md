# Title Cleaning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable title cleaning rules (strip embedded dates, reorder part numbers) applied at feed parse time, with global and per-feed config.

**Architecture:** New `title_clean.py` module with rule functions and an orchestrator. Integrated into `feed.py`'s `parse_feed()` so cleaned titles propagate to all downstream uses. Config follows existing `merge_config()` pattern for global + per-feed overrides.

**Tech Stack:** Python, regex, pytest

**Spec:** `docs/superpowers/specs/2026-03-22-title-cleaning-design.md`

---

### Task 1: `strip_date` — tests and implementation

**Files:**
- Create: `tests/test_title_clean.py`
- Create: `src/podcast_etl/title_clean.py`

- [ ] **Step 1: Write failing tests for `strip_date`**

In `tests/test_title_clean.py`:

```python
"""Tests for title_clean.py: strip_date, reorder_parts, clean_title."""
from podcast_etl.title_clean import strip_date


class TestStripDate:
    # Parentheses
    def test_numeric_underscore_parens(self):
        assert strip_date("Natasha Lennard (3_19_26)") == "Natasha Lennard"

    def test_numeric_slash_parens(self):
        assert strip_date("Guest Name (03/22/2026)") == "Guest Name"

    def test_numeric_dash_parens(self):
        assert strip_date("Guest Name (3-22-26)") == "Guest Name"

    def test_iso_date_parens(self):
        assert strip_date("Guest Name (2026-03-22)") == "Guest Name"

    def test_month_name_comma_parens(self):
        assert strip_date("Guest Name (March 22, 2026)") == "Guest Name"

    def test_short_month_no_comma_parens(self):
        assert strip_date("Guest Name (Mar 22 2026)") == "Guest Name"

    # Brackets
    def test_numeric_brackets(self):
        assert strip_date("Guest Name [3_19_26]") == "Guest Name"

    # Braces
    def test_numeric_braces(self):
        assert strip_date("Guest Name {3_19_26}") == "Guest Name"

    # Date at start
    def test_date_at_start(self):
        assert strip_date("(3_19_26) Guest Name") == "Guest Name"

    # Date in middle with separators
    def test_date_in_middle(self):
        assert strip_date("Show - (3_19_26) - Guest") == "Show - Guest"

    # Cleanup of trailing separator
    def test_trailing_dash_cleaned(self):
        assert strip_date("Guest Name - (3_19_26)") == "Guest Name"

    # No match cases
    def test_bare_date_not_stripped(self):
        assert strip_date("Guest Name 3_19_26") == "Guest Name 3_19_26"

    def test_no_date_unchanged(self):
        assert strip_date("Just a Normal Title") == "Just a Normal Title"

    def test_empty_string(self):
        assert strip_date("") == ""

    # Safety: don't return empty
    def test_date_only_returns_original(self):
        assert strip_date("(3_19_26)") == "(3_19_26)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_title_clean.py -v`
Expected: ImportError — `title_clean` module does not exist yet.

- [ ] **Step 3: Implement `strip_date`**

Create `src/podcast_etl/title_clean.py`:

```python
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
_BRACKETED_DATE = (
    r"\s*[-–—]*\s*"
    r"(?:"
    rf"\({_DATE_INTERIOR}\)"
    rf"|\[{_DATE_INTERIOR}\]"
    rf"|\{{{_DATE_INTERIOR}\}}"
    r")"
    r"\s*[-–—]*\s*"
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
    result = re.sub(r"^[-–—]\s*", "", result)
    result = re.sub(r"\s*[-–—]$", "", result)
    return result if result else title
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_title_clean.py::TestStripDate -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/title_clean.py tests/test_title_clean.py
git commit -m "Add strip_date title cleaning rule with tests"
```

---

### Task 2: `reorder_parts` — tests and implementation

**Files:**
- Modify: `tests/test_title_clean.py`
- Modify: `src/podcast_etl/title_clean.py`

- [ ] **Step 1: Write failing tests for `reorder_parts`**

Append to `tests/test_title_clean.py`:

```python
from podcast_etl.title_clean import reorder_parts


class TestReorderParts:
    # Basic reordering
    def test_part_parens(self):
        assert reorder_parts("The Great Episode (Part 1)") == "Part 1 - The Great Episode"

    def test_part_brackets(self):
        assert reorder_parts("The Great Episode [Part 2]") == "Part 2 - The Great Episode"

    def test_part_braces(self):
        assert reorder_parts("The Great Episode {Part 3}") == "Part 3 - The Great Episode"

    # Pt. and Pt variants
    def test_pt_dot(self):
        assert reorder_parts("The Great Episode (Pt. 1)") == "Pt. 1 - The Great Episode"

    def test_pt_no_dot(self):
        assert reorder_parts("The Great Episode (Pt 2)") == "Pt 2 - The Great Episode"

    # Case insensitivity (preserves original case)
    def test_lowercase_part(self):
        assert reorder_parts("The Great Episode (part 1)") == "part 1 - The Great Episode"

    def test_uppercase_part(self):
        assert reorder_parts("The Great Episode (PART 1)") == "PART 1 - The Great Episode"

    # Multi-digit
    def test_multi_digit_part(self):
        assert reorder_parts("The Great Episode (Part 12)") == "Part 12 - The Great Episode"

    # Trailing separator cleanup
    def test_trailing_dash_before_part(self):
        assert reorder_parts("The Great Episode - (Part 1)") == "Part 1 - The Great Episode"

    # No match cases
    def test_bare_part_not_reordered(self):
        assert reorder_parts("The Great Episode Part 1") == "The Great Episode Part 1"

    def test_no_part_unchanged(self):
        assert reorder_parts("Just a Normal Title") == "Just a Normal Title"

    def test_empty_string(self):
        assert reorder_parts("") == ""

    # Only first match moves
    def test_multiple_parts_only_first_moves(self):
        assert reorder_parts("Episode (Part 1) (Part 2)") == "Part 1 - Episode (Part 2)"

    # Part at start — already at front, but brackets should be removed
    def test_part_at_start_parens(self):
        assert reorder_parts("(Part 1) The Great Episode") == "Part 1 - The Great Episode"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_title_clean.py::TestReorderParts -v`
Expected: ImportError — `reorder_parts` not yet defined.

- [ ] **Step 3: Implement `reorder_parts`**

Add to `src/podcast_etl/title_clean.py`:

```python
# Part indicator pattern inside brackets: Part 1, Pt. 2, Pt 3
_PART_INTERIOR = r"(?:(?:Part|Pt)\.?\s*\d+)"

_BRACKETED_PART_RE = re.compile(
    r"\s*[-–—]*\s*"
    r"(?:"
    rf"\(({_PART_INTERIOR})\)"
    rf"|\[({_PART_INTERIOR})\]"
    rf"|\{{({_PART_INTERIOR})\}}"
    r")"
    r"\s*[-–—]*\s*",
    re.IGNORECASE,
)


def reorder_parts(title: str) -> str:
    """Move the first bracketed part indicator to the front of the title.

    Transforms 'Title (Part 1)' to 'Part 1 - Title'. Preserves original
    casing. Only matches Part/Pt./Pt inside (), [], or {}.
    """
    if not title:
        return title
    match = _BRACKETED_PART_RE.search(title)
    if not match:
        return title
    # One of the three capture groups will have the match
    part_text = match.group(1) or match.group(2) or match.group(3)
    remainder = (title[:match.start()] + title[match.end():]).strip()
    # Clean up dangling separators
    remainder = re.sub(r"^[-–—]\s*", "", remainder)
    remainder = re.sub(r"\s*[-–—]$", "", remainder)
    return f"{part_text} - {remainder}" if remainder else part_text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_title_clean.py -v`
Expected: All tests pass (both `TestStripDate` and `TestReorderParts`).

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/title_clean.py tests/test_title_clean.py
git commit -m "Add reorder_parts title cleaning rule with tests"
```

---

### Task 3: `clean_title` orchestrator — tests and implementation

**Files:**
- Modify: `tests/test_title_clean.py`
- Modify: `src/podcast_etl/title_clean.py`

- [ ] **Step 1: Write failing tests for `clean_title`**

Append to `tests/test_title_clean.py`:

```python
from podcast_etl.title_clean import clean_title


class TestCleanTitle:
    def test_empty_config_no_change(self):
        assert clean_title("Title (3_19_26)", {}) == "Title (3_19_26)"

    def test_none_config_no_change(self):
        assert clean_title("Title (3_19_26)", None) == "Title (3_19_26)"

    def test_both_false_no_change(self):
        assert clean_title("Title (3_19_26)", {"strip_date": False, "reorder_parts": False}) == "Title (3_19_26)"

    def test_strip_date_only(self):
        assert clean_title("Guest (3_19_26)", {"strip_date": True}) == "Guest"

    def test_reorder_parts_only(self):
        assert clean_title("Episode (Part 1)", {"reorder_parts": True}) == "Part 1 - Episode"

    def test_both_enabled(self):
        assert clean_title("Episode (Part 1) (3_19_26)", {"strip_date": True, "reorder_parts": True}) == "Part 1 - Episode"

    def test_both_enabled_reverse_order_in_title(self):
        assert clean_title("Episode (3_19_26) (Part 1)", {"strip_date": True, "reorder_parts": True}) == "Part 1 - Episode"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_title_clean.py::TestCleanTitle -v`
Expected: ImportError — `clean_title` not yet defined.

- [ ] **Step 3: Implement `clean_title`**

Add to `src/podcast_etl/title_clean.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_title_clean.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/title_clean.py tests/test_title_clean.py
git commit -m "Add clean_title orchestrator with tests"
```

---

### Task 4: Integrate into `feed.py`

**Files:**
- Modify: `src/podcast_etl/feed.py` (lines 14-18, 58)
- Modify: `tests/test_feed.py`

- [ ] **Step 1: Write failing tests for title cleaning in `parse_feed`**

Add to `tests/test_feed.py`, at the bottom, a new section:

```python
# ---------------------------------------------------------------------------
# Title cleaning
# ---------------------------------------------------------------------------

def test_parse_feed_applies_strip_date():
    entry = _Entry(title="Guest Name (3_19_26)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"strip_date": True})
    assert podcast.episodes[0].title == "Guest Name"


def test_parse_feed_applies_reorder_parts():
    entry = _Entry(title="The Show (Part 1)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"reorder_parts": True})
    assert podcast.episodes[0].title == "Part 1 - The Show"


def test_parse_feed_no_title_cleaning_by_default():
    entry = _Entry(title="Guest Name (3_19_26)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")
    assert podcast.episodes[0].title == "Guest Name (3_19_26)"


def test_parse_feed_title_cleaning_affects_slug():
    """Cleaned titles should produce slugs from the cleaned version."""
    entry = _Entry(title="Guest Name (3_19_26)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"strip_date": True})
    assert podcast.episodes[0].slug == "guest-name"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_feed.py::test_parse_feed_applies_strip_date -v`
Expected: TypeError — `parse_feed()` got unexpected keyword argument `title_cleaning`.

- [ ] **Step 3: Add `title_cleaning` parameter to `parse_feed`**

In `src/podcast_etl/feed.py`:

1. Add import at line 9 (after the `text` import):
```python
from podcast_etl.title_clean import clean_title
```

2. Add parameter to `parse_feed` signature (line 14-17):
```python
def parse_feed(
    url: str,
    output_dir: Path | None = None,
    blacklist: list[str] | None = None,
    title_cleaning: dict | None = None,
) -> Podcast:
```

3. After `title = entry.get("title", "Untitled")` on line 58, add:
```python
        title = clean_title(title, title_cleaning)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_feed.py -v`
Expected: All pass (new and existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/feed.py tests/test_feed.py
git commit -m "Integrate title cleaning into parse_feed"
```

---

### Task 5: Wire config in `cli.py` and `poller.py`

**Files:**
- Modify: `src/podcast_etl/cli.py` (lines 105, 160-165, 295-298, 338-341)
- Modify: `src/podcast_etl/poller.py` (line 61)

- [ ] **Step 1: Add `resolve_title_cleaning` helper to `cli.py`**

Add after `get_pipeline_steps` (after line 147 in `cli.py`):

```python
def resolve_title_cleaning(config: dict, feed_config: dict | None = None) -> dict | None:
    """Merge global and per-feed title_cleaning config."""
    global_cfg = config.get("settings", {}).get("title_cleaning", {})
    feed_cfg = (feed_config or {}).get("title_cleaning", {})
    if not global_cfg and not feed_cfg:
        return None
    return merge_config(global_cfg, feed_cfg) if global_cfg and feed_cfg else (feed_cfg or global_cfg)
```

- [ ] **Step 2: Update `fetch_feed` to accept and pass `title_cleaning`**

Modify `fetch_feed` (line 160-167):

```python
def fetch_feed(
    url: str,
    output_dir: Path,
    blacklist: list[str] | None = None,
    title_cleaning: dict | None = None,
) -> Podcast:
    podcast = parse_feed(url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
    podcast.save(output_dir)
    return podcast
```

- [ ] **Step 3: Pass `title_cleaning` in `fetch` and `run` commands**

In the `fetch` command, **replace** the loop body (lines 296-299) with:
```python
    blacklist = config.get("settings", {}).get("blacklist", [])
    for url in urls:
        fc = find_feed_config(config, url)
        title_cleaning = resolve_title_cleaning(config, fc)
        click.echo(f"Fetching {url}...")
        podcast = fetch_feed(url, output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
        click.echo(f"  {podcast.title}: {len(podcast.episodes)} episodes")
```

In the `run` command, **replace** the loop body (lines 339-343) with:
```python
    blacklist = config.get("settings", {}).get("blacklist", [])
    for url, feed_config in feeds_to_run:
        title_cleaning = resolve_title_cleaning(config, feed_config)
        click.echo(f"Processing {url}...")
        podcast = fetch_feed(url, output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
        click.echo(f"  {podcast.title}: {len(podcast.episodes)} episodes")
        run_pipeline(podcast, output_dir, config, feed_config=feed_config, step_filter=step_filter, last=last, date_range=date_range, overwrite=overwrite)
```

- [ ] **Step 4: Pass `title_cleaning` in `poller.py`**

In `src/podcast_etl/poller.py`, add import at the top (after the existing `pipeline` import):
```python
from podcast_etl.cli import resolve_title_cleaning
```

Then around line 60-61, **replace** the `parse_feed` call with:
```python
                    blacklist = config.get("settings", {}).get("blacklist", [])
                    title_cleaning = resolve_title_cleaning(config, feed_config)
                    podcast = parse_feed(url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
```

- [ ] **Step 5: Run all tests to verify nothing is broken**

Run: `uv run pytest tests/ -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/podcast_etl/cli.py src/podcast_etl/poller.py
git commit -m "Wire title_cleaning config through cli and poller"
```

---

### Task 6: Add config validation

**Files:**
- Modify: `src/podcast_etl/cli.py` (line 105)
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test for validation**

Add to `tests/test_cli.py`:

```python
def test_validate_config_catches_title_cleaning_type_mismatch():
    config = {
        "feeds": [{"url": "https://example.com/rss", "title_cleaning": {"strip_date": {"nested": "bad"}}}],
        "settings": {"title_cleaning": {"strip_date": True}},
    }
    with pytest.raises(SystemExit, match="title_cleaning"):
        validate_config(config)


def test_validate_config_passes_valid_title_cleaning():
    config = {
        "feeds": [{"url": "https://example.com/rss", "title_cleaning": {"strip_date": True}}],
        "settings": {"title_cleaning": {"reorder_parts": True}},
    }
    validate_config(config)  # should not raise
```

- [ ] **Step 2: Run tests to verify the type mismatch test fails**

Run: `uv run pytest tests/test_cli.py::test_validate_config_catches_title_cleaning_type_mismatch -v`
Expected: FAIL — no SystemExit raised because `title_cleaning` is not yet validated.

- [ ] **Step 3: Add `title_cleaning` to validated sections**

In `src/podcast_etl/cli.py`, `_validate_feed_overrides` function (line 105), change:

```python
    for section in ("ad_detection", "audiobookshelf"):
```

to:

```python
    for section in ("ad_detection", "audiobookshelf", "title_cleaning"):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/cli.py tests/test_cli.py
git commit -m "Add title_cleaning to config validation"
```

---

### Task 7: Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md**

Add `title_cleaning` to the feed config YAML example (in the `feeds.yaml` section), and add a line to the test file listing for `test_title_clean.py`.

In the feed config example, add under the feed entry:
```yaml
    title_cleaning:                     # optional per-feed title cleaning
      strip_date: true                  # remove bracketed dates from titles
      reorder_parts: true               # move (Part N) to front of title
```

In the settings section:
```yaml
  title_cleaning:                     # global title cleaning (default off)
    strip_date: false
    reorder_parts: false
```

Add to the test file listing:
```
- `test_title_clean.py` — `strip_date`, `reorder_parts`, `clean_title` (date formats, bracket types, part variants, config flags)
```

Add to **Data flow** item 8 (or as a new item):
```
- `title_clean.py` — `strip_date` (remove bracketed dates), `reorder_parts` (move part indicators to front), `clean_title` (orchestrator applying enabled rules)
```

- [ ] **Step 2: Update README.md**

Add a `Title Cleaning` subsection under the configuration section documenting the config shape, examples, and behavior.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "Document title_cleaning config in CLAUDE.md and README.md"
```

---

### Task 8: Run full test suite and verify

- [ ] **Step 1: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All pass, no regressions.

- [ ] **Step 2: Verify with a manual smoke test**

Run: `uv run podcast-etl --help`
Expected: CLI loads without errors.
