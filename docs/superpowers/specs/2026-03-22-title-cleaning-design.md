# Title Cleaning Feature Design

## Problem

Episode titles from RSS feeds often contain embedded dates and part-number suffixes that cause redundancy or poor sorting:

1. **Embedded dates** — Titles like `"Natasha Lennard (3_19_26)"` are redundant because the pipeline already prepends dates to filenames and upload titles.
2. **Part suffixes** — Titles like `"The Great Episode (Part 1)"` sort poorly when multiple parts are released together. Moving the part indicator to the front fixes sort order.

## Design

### New module: `src/podcast_etl/title_clean.py`

Three public functions:

**`strip_date(title: str) -> str`**

Removes date strings wrapped in bracket separators `()`, `[]`, `{}`. Supported date formats inside brackets:

- Numeric with `/`, `-`, `_` separators: `(3_19_26)`, `(03/22/2026)`, `(3-22-26)`
- ISO: `(2026-03-22)`
- Month name: `(March 22, 2026)`, `(Mar 22 2026)`

Strips the matched group and cleans up adjacent whitespace/dashes. Does **not** match bare dates without surrounding brackets. If stripping the date would leave an empty title, returns the original unchanged.

**`reorder_parts(title: str) -> str`**

Finds the **first** part indicator wrapped in `()`, `[]`, `{}` and moves it to the front. Matches case-insensitively:

- `Part 1`, `Part 12`
- `Pt. 1`, `Pt 2`

Transforms: `"The Great Episode (Part 1)"` -> `"Part 1 - The Great Episode"`

Brackets are removed; output preserves the original casing. Does **not** match bare `Part 1` without brackets. Does **not** handle `Part X of Y`. If multiple bracketed part indicators exist, only the first match is moved; the rest remain in place.

**`clean_title(title: str, config: dict) -> str`**

Orchestrator. Applies enabled rules in order:
1. `strip_date` (if `config.get("strip_date")` is truthy)
2. `reorder_parts` (if `config.get("reorder_parts")` is truthy)

Returns title unchanged if config is empty or both flags are false.

### Integration point: `feed.py`

`parse_feed()` gains a `title_cleaning: dict | None` parameter. After extracting the title from the RSS entry, calls `clean_title(title, title_cleaning_config)` before slug generation and episode construction. The cleaned title propagates to all downstream uses (filenames, ID3 tags, tracker uploads).

The `title_cleaning` config dict is resolved by the caller (`cli.py`) by merging global `settings.title_cleaning` with per-feed `title_cleaning` via `merge_config()`.

All callers of `parse_feed()` must pass the merged config: `cli.py` (for `run`/`fetch` commands) and `poller.py` (for `poll` mode).

### Slug stability note

Enabling title cleaning on a feed with existing run history will change episode slugs (e.g. `"natasha-lennard-3_19_26"` becomes `"natasha-lennard"`). Step status is preserved because episodes are keyed by `guid`, not slug. However, old episode JSON files under the previous filename will remain on disk as orphans. This is acceptable — a one-time manual cleanup if desired.

### Config

```yaml
# Global (default off)
settings:
  title_cleaning:
    strip_date: false
    reorder_parts: false

# Per-feed override
feeds:
  - url: https://example.com/rss
    title_cleaning:
      strip_date: true
      reorder_parts: true
```

Both flags default to `false`. Per-feed values override global values via existing `merge_config()` shallow-merge pattern.

### Validation

`cli.py`'s `_validate_feed_overrides()` adds `title_cleaning` to the list of sections checked for type compatibility (alongside `ad_detection`, `audiobookshelf`).

### Tests: `tests/test_title_clean.py`

Dedicated test file covering:

- **`strip_date`**: each bracket type `()[]{}`, each date format (numeric separators, ISO, month name), dates at start/middle/end of title, cleanup of leftover separators, no-match passthrough, no bare dates without brackets
- **`reorder_parts`**: `Part`, `Pt.`, `Pt` variants, each bracket type, case insensitivity, no-match passthrough, no bare part numbers without brackets
- **`clean_title`**: config flags control which rules apply, both off by default, both enabled together, rules compose correctly (strip date then reorder parts)

### Files changed

- **New:** `src/podcast_etl/title_clean.py`
- **New:** `tests/test_title_clean.py`
- **Modified:** `src/podcast_etl/feed.py` — add `title_cleaning` param, call `clean_title()`
- **Modified:** `src/podcast_etl/cli.py` — resolve and pass `title_cleaning` config, add validation
- **Modified:** `src/podcast_etl/poller.py` — resolve and pass `title_cleaning` config
- **Modified:** `tests/test_feed.py` — test that title cleaning is applied during feed parsing
- **Modified:** `tests/test_cli.py` — test validation of `title_cleaning` config
- **Modified:** `CLAUDE.md` — document new config section
- **Modified:** `README.md` — document new config section
