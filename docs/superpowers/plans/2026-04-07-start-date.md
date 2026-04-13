# `start_date` config field — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-feed and defaults `start_date` config field that filters out any episode published before that date, working alongside (not instead of) `last: N` for feed-migration scenarios.

**Architecture:** Extend `service.filter_episodes` with a `start_date: date | None` parameter applied as a floor after `last`/`date_range` and before the regex filter. Plumb the value from resolved config through `service.run_pipeline`, `poller.run_poll_loop`, and `poller.async_poll_loop` via a `_coerce_start_date` helper that normalizes `None` / `date` / ISO-string inputs. `validate_config` rejects wrong types / bad strings. The web UI promotes `start_date` to a structured `<input type="date">` by adding a `date_fields` parameter to `form_helpers.parse_form_section`.

**Tech Stack:** Python 3.12, `datetime.date`, `email.utils.parsedate_to_datetime`, PyYAML, Click, FastAPI, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-04-07-start-date-design.md`

---

## File map

Modified:

- `src/podcast_etl/service.py` — new `_coerce_start_date` helper; `filter_episodes` signature + body; `validate_config` body; `run_pipeline` body; `KNOWN_FEED_FIELDS` + `KNOWN_DEFAULTS_FIELDS`
- `src/podcast_etl/poller.py` — `run_poll_loop` and `async_poll_loop` both read `start_date` from `resolved` and pass to `filter_episodes`
- `src/podcast_etl/web/form_helpers.py` — new `apply_date_field`; `parse_form_section` gets a `date_fields` parameter
- `src/podcast_etl/web/routes/feeds.py` — `_parse_feed_form` passes `date_fields=["start_date"]`
- `src/podcast_etl/web/routes/defaults.py` — `_parse_defaults_form` passes `date_fields=["start_date"]`
- `src/podcast_etl/web/templates/feeds/form.html` — new date input in General section
- `src/podcast_etl/web/templates/defaults/edit.html` — new date input in General section
- `tests/test_service.py` — new `filter_episodes` tests, new `validate_config` tests, new `_coerce_start_date` tests
- `tests/test_form_helpers.py` — new `apply_date_field` tests, new `parse_form_section` `date_fields` tests
- `README.md` — config example + field narrative
- `CLAUDE.md` — feed-field list + `filter_episodes` parameter note

No new files.

---

## Task 1: `_coerce_start_date` helper (TDD)

**Why first:** Every later task depends on a single normalizer that accepts `None`, `datetime.date`, or ISO-8601 string. Starting here means validation and plumbing can both call the same helper without duplicating parsing logic.

**Files:**
- Modify: `src/podcast_etl/service.py` (new private helper near the top, after imports, before `load_config`)
- Test: `tests/test_service.py` (new section `# _coerce_start_date` before the `# filter_episodes` section)

- [ ] **Step 1.1: Write the failing tests**

Add this section to `tests/test_service.py` *before* the `# filter_episodes` section (roughly before line 293). Also add `_coerce_start_date` to the `from podcast_etl.service import (...)` block at the top of the file.

```python
# ---------------------------------------------------------------------------
# _coerce_start_date
# ---------------------------------------------------------------------------

def test_coerce_start_date_none_returns_none():
    from podcast_etl.service import _coerce_start_date
    assert _coerce_start_date(None) is None


def test_coerce_start_date_date_instance_returns_same():
    from podcast_etl.service import _coerce_start_date
    d = date(2026, 4, 7)
    assert _coerce_start_date(d) is d


def test_coerce_start_date_iso_string_parses():
    from podcast_etl.service import _coerce_start_date
    assert _coerce_start_date("2026-04-07") == date(2026, 4, 7)


def test_coerce_start_date_invalid_string_raises():
    from podcast_etl.service import _coerce_start_date
    with pytest.raises(ValueError, match="not a valid ISO date"):
        _coerce_start_date("not-a-date")


def test_coerce_start_date_wrong_type_raises():
    from podcast_etl.service import _coerce_start_date
    with pytest.raises(TypeError, match="must be a date"):
        _coerce_start_date(42)
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_service.py::test_coerce_start_date_none_returns_none -v`
Expected: FAIL with `ImportError: cannot import name '_coerce_start_date' from 'podcast_etl.service'`

- [ ] **Step 1.3: Write the helper**

In `src/podcast_etl/service.py`, just after the existing imports (around line 35 after `logger = logging.getLogger(__name__)`), add:

```python
def _coerce_start_date(value: object) -> date | None:
    """Normalize a raw config value into ``date | None``.

    Accepts ``None``, a ``datetime.date`` instance (PyYAML parses bare ISO
    dates into one), or an ISO-8601 string (e.g. from the web UI). Raises
    ``ValueError`` on an unparseable string and ``TypeError`` on any other
    type. This is the single place the rest of the codebase converts raw
    config values into the typed floor that ``filter_episodes`` expects.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"start_date {value!r} is not a valid ISO date") from exc
    raise TypeError(f"start_date must be a date or ISO string, got {type(value).__name__}")
```

Note: `date` is already imported at the top of `service.py` via `from datetime import date`.

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `uv run pytest tests/test_service.py -k coerce_start_date -v`
Expected: all 5 tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add src/podcast_etl/service.py tests/test_service.py
git commit -m "feat: add _coerce_start_date helper for config normalization"
```

---

## Task 2: Extend `filter_episodes` with `start_date` parameter (TDD)

**Why second:** The core filter logic is the load-bearing piece — every other change just passes data to it.

**Files:**
- Modify: `src/podcast_etl/service.py:191-221` (`filter_episodes` signature and body)
- Test: `tests/test_service.py` (new tests in the `# filter_episodes` section)

- [ ] **Step 2.1: Write the failing tests**

Append these tests inside the existing `# filter_episodes` section in `tests/test_service.py` (after `test_filter_episodes_regex_skips_none_title` at line 386):

```python
def test_filter_episodes_start_date_floor():
    result = filter_episodes(_EPISODES, start_date=date(2026, 3, 3))
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b", "Ep 4"]


def test_filter_episodes_start_date_keeps_equal():
    """Boundary date is included (< not <=)."""
    result = filter_episodes(_EPISODES, start_date=date(2026, 3, 4))
    assert [e.title for e in result] == ["Ep 4"]


def test_filter_episodes_start_date_skips_no_published():
    episodes = [
        _ep("No date"),
        _ep("Has date", "Wed, 04 Mar 2026 00:00:00 +0000"),
    ]
    result = filter_episodes(episodes, start_date=date(2026, 3, 1))
    assert [e.title for e in result] == ["Has date"]


def test_filter_episodes_start_date_skips_unparseable_date():
    episodes = [
        _ep("Bad date", "not a real date"),
        _ep("Good date", "Wed, 04 Mar 2026 00:00:00 +0000"),
    ]
    result = filter_episodes(episodes, start_date=date(2026, 3, 1))
    assert [e.title for e in result] == ["Good date"]


def test_filter_episodes_start_date_combined_with_last():
    """The migration scenario: last N, floored by start_date.

    With last=5 (all 5 episodes) and start_date=2026-03-03, we expect
    only the 3 episodes at or after that date — last and start_date stack.
    """
    result = filter_episodes(_EPISODES, last=5, start_date=date(2026, 3, 3))
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b", "Ep 4"]


def test_filter_episodes_start_date_combined_with_date_range():
    """start_date intersects with date_range — both apply."""
    result = filter_episodes(
        _EPISODES,
        date_range=(date(2026, 3, 1), date(2026, 3, 4)),
        start_date=date(2026, 3, 3),
    )
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b", "Ep 4"]


def test_filter_episodes_start_date_combined_with_episode_filter():
    """regex still applies on top of start_date."""
    result = filter_episodes(
        _EPISODES,
        start_date=date(2026, 3, 3),
        episode_filter=r"Ep 3",
    )
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b"]
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_service.py -k filter_episodes_start_date -v`
Expected: all 7 tests FAIL with `TypeError: filter_episodes() got an unexpected keyword argument 'start_date'`.

- [ ] **Step 2.3: Modify `filter_episodes`**

Replace the existing `filter_episodes` function in `src/podcast_etl/service.py` (lines 191-221) with:

```python
def filter_episodes(
    episodes: list[Episode],
    last: int | None = None,
    date_range: tuple[date | None, date | None] | None = None,
    episode_filter: str | None = None,
    start_date: date | None = None,
) -> list[Episode]:
    """Filter episodes by count, publication date range, start-date floor, and/or title regex."""
    if last is not None:
        result = episodes[:last]
    elif date_range is not None:
        start, end = date_range
        result = []
        for ep in episodes:
            if ep.published is None:
                continue
            try:
                pub_date = parsedate_to_datetime(ep.published).date()
            except Exception:
                logger.warning("Skipping episode %r: unable to parse published date %r", ep.title, ep.published)
                continue
            if start is not None and pub_date < start:
                continue
            if end is not None and pub_date > end:
                continue
            result.append(ep)
    else:
        result = episodes
    if start_date is not None:
        floored: list[Episode] = []
        for ep in result:
            if ep.published is None:
                continue
            try:
                pub_date = parsedate_to_datetime(ep.published).date()
            except Exception:
                logger.warning("Skipping episode %r: unable to parse published date %r", ep.title, ep.published)
                continue
            if pub_date < start_date:
                continue
            floored.append(ep)
        result = floored
    if episode_filter is not None:
        pattern = re.compile(episode_filter)
        result = [ep for ep in result if ep.title and pattern.search(ep.title)]
    return result
```

- [ ] **Step 2.4: Run full filter test suite to verify**

Run: `uv run pytest tests/test_service.py -k filter_episodes -v`
Expected: all `filter_episodes` tests PASS (both existing and the 7 new).

- [ ] **Step 2.5: Commit**

```bash
git add src/podcast_etl/service.py tests/test_service.py
git commit -m "feat: add start_date floor to filter_episodes"
```

---

## Task 3: Validate `start_date` in `validate_config` (TDD)

**Files:**
- Modify: `src/podcast_etl/service.py:67-93` (`validate_config` body)
- Test: `tests/test_service.py` (new tests in the `# validate_config` section)

- [ ] **Step 3.1: Write the failing tests**

Append these tests inside the existing `# validate_config` section in `tests/test_service.py` (after `test_validate_config_passes_valid_title_cleaning` at line 450):

```python
def test_validate_config_accepts_start_date_as_date_instance():
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": date(2026, 4, 7)}],
    }
    validate_config(config)  # should not raise


def test_validate_config_accepts_start_date_as_iso_string():
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": "2026-04-07"}],
    }
    validate_config(config)  # should not raise


def test_validate_config_rejects_start_date_unparseable_string():
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": "not-a-date"}],
    }
    with pytest.raises(SystemExit, match="not a valid ISO date"):
        validate_config(config)


def test_validate_config_rejects_start_date_wrong_type():
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": 42}],
    }
    with pytest.raises(SystemExit, match="start_date must be a date"):
        validate_config(config)


def test_validate_config_accepts_start_date_in_defaults():
    config = {
        "feeds": [{"url": "https://example.com/rss"}],
        "defaults": {"start_date": "2026-04-07"},
    }
    validate_config(config)  # should not raise


def test_validate_config_rejects_bad_start_date_in_defaults():
    config = {
        "feeds": [{"url": "https://example.com/rss"}],
        "defaults": {"start_date": "garbage"},
    }
    with pytest.raises(SystemExit, match="not a valid ISO date"):
        validate_config(config)
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_service.py -k "validate_config and start_date" -v`
Expected: the two "rejects" tests FAIL (they currently pass because validate_config ignores unknown fields); the "accepts" tests PASS by accident. To be precise:
- `test_validate_config_accepts_start_date_as_date_instance` — PASS (unknown field ignored)
- `test_validate_config_accepts_start_date_as_iso_string` — PASS
- `test_validate_config_rejects_start_date_unparseable_string` — **FAIL** (no SystemExit)
- `test_validate_config_rejects_start_date_wrong_type` — **FAIL**
- `test_validate_config_accepts_start_date_in_defaults` — PASS
- `test_validate_config_rejects_bad_start_date_in_defaults` — **FAIL**

- [ ] **Step 3.3: Modify `validate_config`**

Replace `validate_config` in `src/podcast_etl/service.py` (lines 67-93) with the expanded version below. The change: after the existing pipeline/type-mismatch checks for feeds, validate `start_date`. After the defaults pipeline check, validate `defaults.start_date`.

```python
def validate_config(config: dict) -> None:
    """Validate config structure and catch common errors early."""
    defaults = config.get("defaults", {})
    feeds = config.get("feeds", [])
    errors: list[str] = []

    for i, feed in enumerate(feeds):
        feed_label = feed.get("name") or feed.get("url") or f"feeds[{i}]"

        if not feed.get("url"):
            errors.append(f"Feed {feed_label!r}: missing 'url'")

        for step_name in feed.get("pipeline", []):
            if step_name not in STEP_REGISTRY:
                errors.append(f"Feed {feed_label!r}: unknown pipeline step {step_name!r}")

        try:
            deep_merge(defaults, feed)
        except TypeError as exc:
            errors.append(f"Feed {feed_label!r}: {exc}")

        if "start_date" in feed:
            try:
                _coerce_start_date(feed["start_date"])
            except (TypeError, ValueError) as exc:
                errors.append(f"Feed {feed_label!r}: {exc}")

    for step_name in defaults.get("pipeline", []):
        if step_name not in STEP_REGISTRY:
            errors.append(f"defaults.pipeline: unknown step {step_name!r}")

    if "start_date" in defaults:
        try:
            _coerce_start_date(defaults["start_date"])
        except (TypeError, ValueError) as exc:
            errors.append(f"defaults: {exc}")

    if errors:
        raise SystemExit("Config validation failed:\n  " + "\n  ".join(errors))
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `uv run pytest tests/test_service.py -k validate_config -v`
Expected: all `validate_config` tests PASS (existing 8 + new 6 = 14).

- [ ] **Step 3.5: Commit**

```bash
git add src/podcast_etl/service.py tests/test_service.py
git commit -m "feat: validate start_date in validate_config"
```

---

## Task 4: Plumb `start_date` through `run_pipeline`

**Why no unit test:** `run_pipeline` is a thin wrapper that wires `filter_episodes` into the `Pipeline` class. The change is a single argument pass-through; the behavior is already covered by Task 2's filter tests. We verify the wiring at the end via the full test suite.

**Files:**
- Modify: `src/podcast_etl/service.py:232-248` (`run_pipeline` body)

- [ ] **Step 4.1: Modify `run_pipeline`**

Replace the `run_pipeline` function in `src/podcast_etl/service.py` (lines 232-248) with:

```python
def run_pipeline(
    podcast: Podcast,
    output_dir: Path,
    resolved_config: dict,
    step_filter: str | None = None,
    last: int | None = None,
    date_range: tuple[date | None, date | None] | None = None,
    episode_filter: str | None = None,
    overwrite: bool = False,
) -> None:
    step_names = get_pipeline_steps(resolved_config)
    steps = [get_step(name) for name in step_names]
    context = PipelineContext(output_dir=output_dir, podcast=podcast, config=resolved_config, overwrite=overwrite)
    pipeline = Pipeline(steps=steps, context=context)
    ep_filter = episode_filter if episode_filter is not None else resolved_config.get("episode_filter")
    start_date = _coerce_start_date(resolved_config.get("start_date"))
    episodes = filter_episodes(
        podcast.episodes,
        last=last,
        date_range=date_range,
        episode_filter=ep_filter,
        start_date=start_date,
    )
    pipeline.run(episodes, step_filter=step_filter, overwrite=overwrite)
```

- [ ] **Step 4.2: Run service test suite to verify nothing regressed**

Run: `uv run pytest tests/test_service.py -v`
Expected: all tests PASS (no new ones yet; existing suite should still be green).

- [ ] **Step 4.3: Commit**

```bash
git add src/podcast_etl/service.py
git commit -m "feat: pull start_date from resolved config in run_pipeline"
```

---

## Task 5: Plumb `start_date` through pollers

**Files:**
- Modify: `src/podcast_etl/poller.py:81-83` (`run_poll_loop` filter call)
- Modify: `src/podcast_etl/poller.py:160-162` (`async_poll_loop` filter call)

- [ ] **Step 5.1: Update `run_poll_loop`**

In `src/podcast_etl/poller.py`, replace lines 81-83 (inside the `for feed_entry in feeds` loop, just after `episodes = filter_episodes(...)`) to pull `start_date` from resolved config and pass it. The current code is:

```python
                    last = resolved.get("last")
                    episode_filter = resolved.get("episode_filter")
                    episodes = filter_episodes(podcast.episodes, last=last, episode_filter=episode_filter)
```

Replace with:

```python
                    last = resolved.get("last")
                    episode_filter = resolved.get("episode_filter")
                    start_date = _coerce_start_date(resolved.get("start_date"))
                    episodes = filter_episodes(
                        podcast.episodes,
                        last=last,
                        episode_filter=episode_filter,
                        start_date=start_date,
                    )
```

Also update the import at the top of `poller.py` (currently line 12):

```python
from podcast_etl.service import filter_episodes, validate_config
```

to:

```python
from podcast_etl.service import _coerce_start_date, filter_episodes, validate_config
```

- [ ] **Step 5.2: Update `async_poll_loop`**

In the same file, replace lines 160-162 (the same pattern inside `async_poll_loop`) with the matching update:

```python
                        last = resolved.get("last")
                        episode_filter = resolved.get("episode_filter")
                        start_date = _coerce_start_date(resolved.get("start_date"))
                        episodes = filter_episodes(
                            podcast.episodes,
                            last=last,
                            episode_filter=episode_filter,
                            start_date=start_date,
                        )
```

- [ ] **Step 5.3: Run poller tests to verify no regression**

Run: `uv run pytest tests/test_poller.py tests/test_async_poller.py -v`
Expected: all existing poller tests PASS. The pollers mock `Pipeline.run` and `parse_feed`, so the new pass-through is exercised but not asserted directly — Task 2's filter tests are the authoritative coverage.

- [ ] **Step 5.4: Commit**

```bash
git add src/podcast_etl/poller.py
git commit -m "feat: pull start_date from resolved config in pollers"
```

---

## Task 6: Add `start_date` to `KNOWN_FEED_FIELDS` / `KNOWN_DEFAULTS_FIELDS`

**Why this early:** Once `start_date` is in the known-fields set, `split_config_fields` will pull it out of the YAML-textarea payload so the structured form input (added in Task 9) can own the field. Doing this before the form helper changes avoids an awkward middle state where the value appears twice.

**Files:**
- Modify: `src/podcast_etl/service.py:306-315` (`KNOWN_*_FIELDS` sets)

- [ ] **Step 6.1: Update the sets**

In `src/podcast_etl/service.py`, replace:

```python
KNOWN_FEED_FIELDS = {
    "url", "name", "enabled", "last", "episode_filter",
    "category_id", "type_id", "pipeline", "title_cleaning",
    "title_override",
}

KNOWN_DEFAULTS_FIELDS = {
    "output_dir", "pipeline", "title_cleaning",
    "blacklist", "torrent_data_dir",
}
```

with:

```python
KNOWN_FEED_FIELDS = {
    "url", "name", "enabled", "last", "episode_filter",
    "category_id", "type_id", "pipeline", "title_cleaning",
    "title_override", "start_date",
}

KNOWN_DEFAULTS_FIELDS = {
    "output_dir", "pipeline", "title_cleaning",
    "blacklist", "torrent_data_dir", "start_date",
}
```

- [ ] **Step 6.2: Run service test suite**

Run: `uv run pytest tests/test_service.py -v`
Expected: all PASS.

- [ ] **Step 6.3: Commit**

```bash
git add src/podcast_etl/service.py
git commit -m "feat: promote start_date to KNOWN_FEED_FIELDS / KNOWN_DEFAULTS_FIELDS"
```

---

## Task 7: `apply_date_field` helper + `date_fields` in `parse_form_section` (TDD)

**Files:**
- Modify: `src/podcast_etl/web/form_helpers.py` (new `apply_date_field` function, new `date_fields` parameter on `parse_form_section`)
- Test: `tests/test_form_helpers.py` (new `apply_date_field` section + new `parse_form_section` date tests)

- [ ] **Step 7.1: Write the failing tests**

Add this to `tests/test_form_helpers.py`. Put the `apply_date_field` section just before the `# apply_pipeline` section (around line 137), and the extra `parse_form_section` tests at the end of the existing `# parse_form_section` section:

```python
# ---------------------------------------------------------------------------
# apply_date_field
# ---------------------------------------------------------------------------

def test_apply_date_field_sets_valid_iso():
    from podcast_etl.web.form_helpers import apply_date_field
    base = {}
    apply_date_field(base, "start_date", "2026-04-07")
    assert base == {"start_date": "2026-04-07"}


def test_apply_date_field_strips_whitespace():
    from podcast_etl.web.form_helpers import apply_date_field
    base = {}
    apply_date_field(base, "start_date", "  2026-04-07  ")
    assert base == {"start_date": "2026-04-07"}


def test_apply_date_field_deletes_when_cleared():
    from podcast_etl.web.form_helpers import apply_date_field
    base = {"start_date": "2026-04-07"}
    apply_date_field(base, "start_date", "")
    assert base == {}


def test_apply_date_field_deletes_when_whitespace():
    from podcast_etl.web.form_helpers import apply_date_field
    base = {"start_date": "2026-04-07"}
    apply_date_field(base, "start_date", "   ")
    assert base == {}


def test_apply_date_field_raises_on_invalid_string():
    """Bad input must surface as a ValueError so the route can render
    the form error instead of storing garbage in YAML."""
    from podcast_etl.web.form_helpers import apply_date_field
    base = {}
    with pytest.raises(ValueError, match="start_date"):
        apply_date_field(base, "start_date", "not-a-date")
    assert base == {}


def test_apply_date_field_empty_on_empty_base_noop():
    from podcast_etl.web.form_helpers import apply_date_field
    base = {}
    apply_date_field(base, "start_date", "")
    assert base == {}
```

And add these to the `# parse_form_section` section (after `test_parse_form_section_clears_fields_not_in_form` around line 426):

```python
def test_parse_form_section_date_field_valid():
    form = {"extra_yaml": "", "start_date": "2026-04-07"}
    base, error = parse_form_section(
        form, [], "Feed",
        date_fields=["start_date"],
    )
    assert error is None
    assert base == {"start_date": "2026-04-07"}


def test_parse_form_section_date_field_empty_clears():
    form = {"extra_yaml": "start_date: 2026-01-01\n"}
    base, error = parse_form_section(
        form, [], "Feed",
        date_fields=["start_date"],
    )
    assert error is None
    assert "start_date" not in base


def test_parse_form_section_bad_date_returns_error():
    form = {"extra_yaml": "", "start_date": "garbage"}
    base, error = parse_form_section(
        form, [], "Feed",
        date_fields=["start_date"],
    )
    assert base == {}
    assert error is not None
    assert "start_date" in error
```

- [ ] **Step 7.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_form_helpers.py -k "apply_date_field or date_field" -v`
Expected: all 9 tests FAIL with `ImportError: cannot import name 'apply_date_field'` or `TypeError: parse_form_section() got an unexpected keyword argument 'date_fields'`.

- [ ] **Step 7.3: Add `apply_date_field`**

In `src/podcast_etl/web/form_helpers.py`, add this function just after `apply_int_field` (after line 94). Also add `from datetime import date` to the imports at the top if not already present.

```python
def apply_date_field(base: dict, key: str, value: str) -> None:
    """Set ``base[key]`` to a validated ISO date string, or delete when cleared.

    The value is parsed with ``date.fromisoformat`` to reject malformed input
    at the form boundary (rather than letting a bad string land in YAML and
    crash ``filter_episodes`` later). On success the parsed date is stored
    as an ISO string so the YAML output stays stable regardless of whether
    the source was a web form or a hand-edited quoted string.

    Raises ``ValueError`` with the field name when the input is present but
    not a valid ISO date.
    """
    stripped = value.strip()
    if not stripped:
        if key in base:
            del base[key]
        return
    try:
        parsed = date.fromisoformat(stripped)
    except ValueError:
        raise ValueError(f"{key}: must be a valid ISO date (YYYY-MM-DD)")
    base[key] = parsed.isoformat()
```

- [ ] **Step 7.4: Extend `parse_form_section`**

In the same file, update `parse_form_section` (lines 106-143) to accept `date_fields` and call `apply_date_field` for each one. Replace the function with:

```python
def parse_form_section(
    form_data: Any,
    all_steps: list[str],
    what: str,
    *,
    text_fields: Sequence[str] = (),
    int_fields: Sequence[str] = (),
    bool_fields: Sequence[str] = (),
    date_fields: Sequence[str] = (),
) -> tuple[dict, str | None]:
    """Parse a config section from form data.

    Both the feed edit form and the defaults edit form follow the same
    pattern: a full-YAML textarea provides the base, structured form
    fields overlay on top, and pipeline/title_cleaning are handled
    uniformly. The only thing that differs is which fields each form has.

    Returns ``(merged_dict, None)`` on success or ``({}, error)`` on
    invalid YAML or a bad typed field.
    """
    extra_yaml = str(form_data.get("extra_yaml", ""))
    base, error = parse_yaml_base(extra_yaml, what)
    if error:
        return {}, error

    for field in text_fields:
        apply_text_field(base, field, str(form_data.get(field, "")))
    for field in int_fields:
        try:
            apply_int_field(base, field, str(form_data.get(field, "")))
        except ValueError as exc:
            return {}, str(exc)
    for field in bool_fields:
        apply_bool_field(base, field, str(form_data.get(field, "")))
    for field in date_fields:
        try:
            apply_date_field(base, field, str(form_data.get(field, "")))
        except ValueError as exc:
            return {}, str(exc)

    apply_pipeline(base, parse_pipeline_checkboxes(form_data, all_steps))
    apply_title_cleaning(base, parse_title_cleaning_checkboxes(form_data))

    return base, None
```

- [ ] **Step 7.5: Run tests to verify they pass**

Run: `uv run pytest tests/test_form_helpers.py -v`
Expected: all form helper tests PASS.

- [ ] **Step 7.6: Commit**

```bash
git add src/podcast_etl/web/form_helpers.py tests/test_form_helpers.py
git commit -m "feat: add apply_date_field and date_fields support to parse_form_section"
```

---

## Task 8: Wire `date_fields=["start_date"]` into feed and defaults routes

**Files:**
- Modify: `src/podcast_etl/web/routes/feeds.py:320-333` (`_parse_feed_form`)
- Modify: `src/podcast_etl/web/routes/defaults.py:45-69` (`_parse_defaults_form`)

- [ ] **Step 8.1: Update `_parse_feed_form`**

In `src/podcast_etl/web/routes/feeds.py`, replace the `_parse_feed_form` function (lines 320-333):

```python
def _parse_feed_form(form_data, all_steps: list[str]) -> tuple[dict, str | None]:
    """Parse feed edit form data into (updated_feed_dict, error_str_or_None).

    The full-feed YAML textarea is the base; form fields overlay on top (form fields win).
    Returns (feed_dict, None) on success, or ({}, error_message) on parse failure.
    """
    return parse_form_section(
        form_data,
        all_steps,
        "Full feed YAML",
        text_fields=["url", "name", "title_override", "episode_filter"],
        int_fields=["last", "category_id", "type_id"],
        bool_fields=["enabled"],
        date_fields=["start_date"],
    )
```

- [ ] **Step 8.2: Update `_parse_defaults_form`**

In `src/podcast_etl/web/routes/defaults.py`, update the `parse_form_section` call inside `_parse_defaults_form` (around line 51-56). Replace:

```python
    base, error = parse_form_section(
        form_data,
        all_steps,
        "Full defaults YAML",
        text_fields=["output_dir", "torrent_data_dir"],
    )
```

with:

```python
    base, error = parse_form_section(
        form_data,
        all_steps,
        "Full defaults YAML",
        text_fields=["output_dir", "torrent_data_dir"],
        date_fields=["start_date"],
    )
```

- [ ] **Step 8.3: Run web tests to verify no regression**

Run: `uv run pytest tests/test_web.py tests/test_form_helpers.py -v`
Expected: all PASS.

- [ ] **Step 8.4: Commit**

```bash
git add src/podcast_etl/web/routes/feeds.py src/podcast_etl/web/routes/defaults.py
git commit -m "feat: wire start_date into feed and defaults form parsing"
```

---

## Task 9: Add `start_date` date input to `feeds/form.html`

**Files:**
- Modify: `src/podcast_etl/web/templates/feeds/form.html` (General section)

- [ ] **Step 9.1: Add the input markup**

In `src/podcast_etl/web/templates/feeds/form.html`, find the "Last N episodes" block (around lines 44-50):

```html
      <div>
        <label class="block text-sm text-gray-400 mb-1" for="last">Last N episodes</label>
        <input id="last" name="last" type="number" min="1"
               value="{{ feed.last or '' }}"
               placeholder="(all)"
               class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500">
      </div>
```

Immediately after that `</div>`, insert:

```html
      <div>
        <label class="block text-sm text-gray-400 mb-1" for="start_date">Start date</label>
        <input id="start_date" name="start_date" type="date"
               value="{% if feed.start_date %}{{ feed.start_date if feed.start_date is string else feed.start_date.isoformat() }}{% endif %}"
               class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500">
      </div>
```

The `{% if ... %}` guard handles both cases: when `feed.start_date` is a `datetime.date` (bare YAML in `feeds.yaml`) we call `.isoformat()`; when it's a string (saved via the web form, which stores ISO) we render it directly. An absent or empty field yields an empty `value`. Jinja's `is string` test is the idiomatic way to discriminate.

- [ ] **Step 9.2: Manually verify the template loads**

Start the web UI:

```bash
uv run podcast-etl serve --port 8765 &
SERVE_PID=$!
sleep 2
curl -s http://localhost:8765/feeds/add > /tmp/add.html
kill $SERVE_PID
wait 2>/dev/null
grep -c 'name="start_date"' /tmp/add.html
```

Expected: `1` (the input is rendered in the add-feed form).

- [ ] **Step 9.3: Commit**

```bash
git add src/podcast_etl/web/templates/feeds/form.html
git commit -m "feat: add start_date date input to feeds form"
```

---

## Task 10: Add `start_date` date input to `defaults/edit.html`

**Files:**
- Modify: `src/podcast_etl/web/templates/defaults/edit.html` (General section)

- [ ] **Step 10.1: Add the input markup**

In `src/podcast_etl/web/templates/defaults/edit.html`, find the General section grid (around lines 20-46). After the "Poll interval" block (around lines 38-44), insert a new grid cell:

```html
      <div>
        <label class="block text-sm text-gray-400 mb-1" for="start_date">Start date</label>
        <input id="start_date" name="start_date" type="date"
               value="{% if defaults.get('start_date') %}{{ defaults['start_date'] if defaults['start_date'] is string else defaults['start_date'].isoformat() }}{% endif %}"
               class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500">
      </div>
```

So the General section's grid contains: output_dir, torrent_data_dir, poll_interval, start_date (4 cells in the 2-column grid).

- [ ] **Step 10.2: Manually verify the template loads**

```bash
uv run podcast-etl serve --port 8765 &
SERVE_PID=$!
sleep 2
curl -s http://localhost:8765/defaults > /tmp/defaults.html
kill $SERVE_PID
wait 2>/dev/null
grep -c 'name="start_date"' /tmp/defaults.html
```

Expected: `1`.

- [ ] **Step 10.3: Commit**

```bash
git add src/podcast_etl/web/templates/defaults/edit.html
git commit -m "feat: add start_date date input to defaults form"
```

---

## Task 11: Update `README.md` and `CLAUDE.md`

**Files:**
- Modify: `README.md` (config example + field narrative)
- Modify: `CLAUDE.md` (feed-field list + `filter_episodes` note)

- [ ] **Step 11.1: Update README config example**

In `README.md`, find the feed example block (around lines 183-203). Replace the feed entry that starts with `- url: "https://example.com/feed.xml"` so it includes `start_date`:

```yaml
feeds:
  - url: "https://example.com/feed.xml"
    name: my-podcast
    enabled: true
    last: 5
    start_date: 2026-04-07
    episode_filter: "Part [0-9]+"
    pipeline: [download, tag, detect_ads, strip_ads, stage, torrent, seed, upload]
    category_id: 14
    type_id: 9
    cover_image: /config/cover.jpg
    banner_image: /config/banner.jpg
    tracker:
      mod_queue_opt_in: 1
    ad_detection:
      llm:
        model: claude-sonnet-4-20250514
    title_cleaning:
      strip_date: true
      reorder_parts: true
      prepend_episode_number: true
      sanitize: true
```

- [ ] **Step 11.2: Update README field narrative**

In `README.md`, find the "Key config behaviors" bullet list (around lines 206-210). Replace the `last`/`episode_filter` bullet with two bullets:

```markdown
- **`enabled`** defaults to `false`. Only `true` feeds are processed during poll/serve. Explicit `--feed` runs ignore this flag.
- **`last`**, **`start_date`**, and **`episode_filter`** limit which episodes are processed during poll. `last` and `start_date` stack — e.g. `last: 10` + `start_date: 2026-04-07` means "the 10 newest episodes, minus any published before 2026-04-07." Useful when migrating a feed to a new host: you keep the working set small with `last`, but the start date prevents re-downloading older episodes. All three can also appear in `defaults`.
- **Per-feed overrides** are deep-merged with `defaults`, so `tracker: {mod_queue_opt_in: 1}` only overrides that one key.
```

- [ ] **Step 11.3: Update CLAUDE.md feed-field list**

In `CLAUDE.md`, find the "Config format" section (around lines 169-175). Replace:

```markdown
The top-level `defaults` block is deep-merged with per-feed overrides via `resolve_feed_config`. Each feed entry supports `name` (short identifier), `enabled` (boolean, default `false`), `last`, `episode_filter`, and any key from `defaults` as an override.
```

with:

```markdown
The top-level `defaults` block is deep-merged with per-feed overrides via `resolve_feed_config`. Each feed entry supports `name` (short identifier), `enabled` (boolean, default `false`), `last`, `start_date` (ISO date floor; stacks with `last`), `episode_filter`, and any key from `defaults` as an override.
```

Also, in the same section's YAML example (around lines 190-205), add `start_date: 2026-04-07` to the example feed entry so future contributors see it:

```yaml
feeds:
  - url: https://example.com/rss
    name: my-podcast
    enabled: true
    last: 5
    start_date: 2026-04-07
    episode_filter: "Part [0-9]+"
    ...
```

- [ ] **Step 11.4: Update CLAUDE.md test description**

In `CLAUDE.md`, find the `test_service.py` bullet (around line 27):

```markdown
- `test_service.py` -- service layer: `load_config`, `save_config` (atomic writes), `validate_config`, `get_output_dir`, `find_feed_config`, `find_podcast_dir`, `get_pipeline_steps`, `filter_episodes`, `get_feed_status`, `split_config_fields`, `merge_config_fields`, `get_resolved_config_with_sources`, `reset_feed_data`, `delete_feed`
```

Replace with:

```markdown
- `test_service.py` -- service layer: `load_config`, `save_config` (atomic writes), `validate_config` (incl. start_date), `get_output_dir`, `find_feed_config`, `find_podcast_dir`, `get_pipeline_steps`, `filter_episodes` (incl. start_date floor), `_coerce_start_date`, `get_feed_status`, `split_config_fields`, `merge_config_fields`, `get_resolved_config_with_sources`, `reset_feed_data`, `delete_feed`
```

- [ ] **Step 11.5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document start_date config field"
```

---

## Task 12: Full verification

- [ ] **Step 12.1: Run the complete unit test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests PASS. Baseline before this work was 622 passed; after this plan, expect 622 + 7 (filter) + 6 (validate) + 5 (coerce) + 6 (apply_date_field) + 3 (parse_form_section date) = **649 passed**.

- [ ] **Step 12.2: Manual: CLI run with `start_date`**

Add a `start_date` to a test feed in your local `feeds.yaml` (or a scratch copy):

```yaml
feeds:
  - url: https://example.com/rss
    name: test-feed
    enabled: true
    last: 10
    start_date: 2026-04-01
```

Run: `uv run podcast-etl -v run --feed test-feed`
Expected: the log shows only episodes published on or after 2026-04-01 being processed. Episodes older than that are not in the iteration.

- [ ] **Step 12.3: Manual: web UI form round-trip**

Start the server, open `http://localhost:8000/feeds/<feed-name>/edit`, set the `Start date` field, submit, confirm the preview diff, and reopen the edit form. The date should persist.

Also open `http://localhost:8000/defaults`, set a `Start date`, save, and confirm it round-trips.

- [ ] **Step 12.4: Manual: invalid start_date rejection**

In the web UI feed edit form, open the "Full feed config (YAML)" textarea and add:

```yaml
start_date: not-a-date
```

Click Save. Expected: the form re-renders with an error like `Feed 'test-feed': start_date 'not-a-date' is not a valid ISO date` or `start_date: must be a valid ISO date (YYYY-MM-DD)` (depending on which layer catches it first — both are acceptable user-facing messages).

- [ ] **Step 12.5: Final commit (if any stray changes)**

```bash
git status
```

Expected: clean working tree. If anything's left over, commit it with an appropriate message.

---

## Self-review notes

**Spec coverage:**
- Config schema (per-feed + defaults) → Tasks 1, 3, 6, 9, 10, 11
- `filter_episodes` signature + order of operations → Task 2
- `_coerce_start_date` helper → Task 1
- Plumbing (`run_pipeline`, `run_poll_loop`, `async_poll_loop`) → Tasks 4, 5
- Validation → Task 3
- Web UI (`KNOWN_*_FIELDS`, form helper, routes, templates) → Tasks 6, 7, 8, 9, 10
- Test coverage (filter + validate) → Tasks 2, 3
- Docs → Task 11
- Manual verification plan → Task 12

No gaps; every spec section maps to at least one task.

**Type consistency:**
- `_coerce_start_date(value: object) -> date | None` — referenced and called consistently in Tasks 1, 3, 4, 5
- `filter_episodes(..., start_date: date | None = None)` — referenced consistently in Tasks 2, 4, 5
- `apply_date_field(base: dict, key: str, value: str)` stores the field as `str` (ISO isoformat); `_coerce_start_date` later parses that string back to `date`. The web form flow is: form string → YAML string → `_coerce_start_date` → `date` → `filter_episodes`. The CLI YAML flow is: bare YAML date → `datetime.date` instance → `_coerce_start_date` (identity) → `filter_episodes`. Both paths converge on the same typed boundary at `filter_episodes`.

**Placeholder scan:** No TBD, TODO, "similar to", or hand-waving. Each step has exact file paths, complete code blocks, and expected command output.
