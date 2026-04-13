# `start_date` config field — design

## Problem

When migrating a feed to a new host or onboarding a feed where you only care
about new episodes going forward, there is no way to tell the pipeline "ignore
anything older than this date." The only floor today is `last: N`, which
selects the N newest episodes irrespective of when they were published, and
the CLI `--date START..END` flag, which is one-shot and lives in the command
line, not in config.

The user's exact scenario: migrate a feed to a new host, set `last: 10` to
keep the working set small, but also pin a `start_date` of "today" so the new
host doesn't re-download the older episodes that are still well within the
last-10 window.

## Goals

- Add a per-feed `start_date` config field that filters out any episode
  published before that date.
- Make `start_date` work *together* with `last`, not as an alternative to it.
- Keep the existing `last`, `--date`, `episode_filter`, and other filters
  working unchanged.
- Persist `start_date` through the same surfaces as other feed config (CLI
  config, web UI form, web UI YAML editor, defaults inheritance).

## Non-goals

- Auto-setting `start_date` when a feed is added (no magic in `podcast-etl
  add` or in the web UI add form).
- Any new CLI flag — `start_date` is read from the resolved config, exactly
  like `last`.
- An "end date" companion field. The existing `--date` range covers ad-hoc
  bounded reprocessing.
- Migrating existing on-disk episodes. `start_date` filters at *episode
  selection* time, not at retention time. Episodes already on disk that
  predate `start_date` are not deleted.

## Design

### Config schema

`start_date` is a new optional field allowed in:

- a feed entry under `feeds:`
- the top-level `defaults:` block

Inheritance is the same as every other field: `defaults.start_date` is
merged with the feed entry via `resolve_feed_config`, with the per-feed
value winning. A global default is unusual but not nonsensical — keeping
both surfaces consistent matches the rest of the config and avoids
explaining a special case.

Format: an ISO date (`YYYY-MM-DD`). YAML's `safe_load` will parse a bare
`2026-04-07` as a `datetime.date` instance. We also accept the string form
so values written via the web UI or quoted by hand still load.

Example:

```yaml
defaults:
  output_dir: ./output

feeds:
  - url: https://example.com/rss
    name: my-podcast
    enabled: true
    last: 10
    start_date: 2026-04-07     # never download anything older than today
```

### `filter_episodes` change (`service.py:191`)

New signature:

```python
def filter_episodes(
    episodes: list[Episode],
    last: int | None = None,
    date_range: tuple[date | None, date | None] | None = None,
    episode_filter: str | None = None,
    start_date: date | None = None,
) -> list[Episode]:
```

Order of operations:

1. Apply `last` **or** `date_range` (mutually exclusive — unchanged).
2. **NEW:** Apply `start_date` as a floor. For each remaining episode:
   - If `episode.published is None`, **drop** it (matches the existing
     `date_range` branch in `service.py:204`).
   - Parse `episode.published` via `parsedate_to_datetime(...).date()`. On
     parse failure, log a warning and drop (again, matches the existing
     branch).
   - Drop if `pub_date < start_date`.
3. Apply `episode_filter` regex (unchanged).

The `start_date` floor is always applied if set, regardless of whether `last`
or `date_range` was used. This is what makes the user's "stack with
`last`" scenario work, and it also means a CLI `--date 2025-01-01..2025-06-01`
on a feed with `start_date: 2026-04-01` correctly returns an empty result —
a clear signal that the two requirements are contradictory.

### Plumbing the value

The resolved config flows through three call sites today; all three need to
read `start_date` and pass it to `filter_episodes`.

- **`service.run_pipeline`** (`service.py:232`): pull
  `start_date = _coerce_start_date(resolved_config.get("start_date"))` and
  pass it to `filter_episodes`.
- **`poller.run_poll_loop`** (`poller.py:83`): same.
- **`poller.async_poll_loop`** (`poller.py:162`): same.

A small helper, `_coerce_start_date(value) -> date | None`, lives in
`service.py` and accepts `None`, `date`, or ISO `str`. It is the single
place that normalizes the value before it reaches `filter_episodes`. This
keeps `filter_episodes` strictly typed (`date | None`) while letting raw
config values flow through unchanged.

The CLI `run` command does **not** get a new flag. Users opt in by editing
config (CLI YAML or web UI). The existing `--date` flag still works and
stacks with `start_date` per the rule above.

### Validation (`service.validate_config`)

For each feed and for `defaults`, if `start_date` is present:

- accept a `date` instance as-is
- accept a `str` and try `date.fromisoformat(...)` — append a clear error if
  parsing fails (`Feed 'foo': start_date 'not-a-date' is not a valid ISO date`)
- reject any other type (`Feed 'foo': start_date must be a date`)

Validation happens once at config load (CLI startup, poll loop reload, web
UI save), so a bad value is rejected before any pipeline code sees it.

### Web UI

Add `"start_date"` to `KNOWN_FEED_FIELDS` and `KNOWN_DEFAULTS_FIELDS` in
`service.py:306-315` so the field is rendered as a structured form control
rather than dropping into the raw YAML editor.

Form input: HTML `<input type="date">`. Browsers serialize this as an empty
string when cleared and as `YYYY-MM-DD` when set, which both round-trip
cleanly through `parse_form_section` — I add a small `date_fields` parameter
to `form_helpers.parse_form_section` and a matching `apply_date_field`
helper that mirrors `apply_text_field` (empty input deletes the key,
populated input is validated as ISO and stored as a `str`). Storing as a
string is fine: validation accepts both string and `date`, and YAML round-
trips it as a string.

Two templates change:

- `feeds/form.html` — add a date input next to "Last N episodes" in the
  General section.
- `defaults/edit.html` — add a date input in the General section.

Both templates must render the current value back into the input as an ISO
string (handling both `date` and `str` forms, since YAML parses bare dates as
`date` instances but web-UI saves as `str`). This mirrors how `last` renders
`value="{{ feed.last or '' }}"` — without it, submitting the form after
editing only the YAML textarea would wipe the field via the empty-form-
overlay rule. A tiny Jinja helper or inline `isoformat() if hasattr(...) else
str(...)` works; exact expression is an implementation detail.

Both routes (`feeds._parse_feed_form`, `defaults._parse_defaults_form`)
get a `date_fields=["start_date"]` argument when calling `parse_form_section`.

No new web UI smoke test (per user request — the existing form helper tests
already cover the `parse_form_section` codepath, and a date field is just
another text-like field with strict parsing).

### Tests

New tests in `tests/test_service.py` (filter section near
`test_filter_episodes_*`):

- `test_filter_episodes_start_date_floor` — drops older, keeps newer
- `test_filter_episodes_start_date_keeps_equal` — boundary date is kept
  (`<` not `<=`)
- `test_filter_episodes_start_date_skips_no_published` — undated episodes
  excluded
- `test_filter_episodes_start_date_skips_unparseable_date` — bad
  `published` is dropped with a warning
- `test_filter_episodes_start_date_combined_with_last` — the migration
  scenario; `last: 5` + `start_date` returns the intersection
- `test_filter_episodes_start_date_combined_with_date_range` — both apply
- `test_filter_episodes_start_date_combined_with_episode_filter` — regex
  still applies on top

New tests in `tests/test_service.py` (validate section):

- `test_validate_config_accepts_start_date_as_date_instance`
- `test_validate_config_accepts_start_date_as_iso_string`
- `test_validate_config_rejects_start_date_unparseable_string`
- `test_validate_config_rejects_start_date_wrong_type` (e.g. int)
- `test_validate_config_accepts_start_date_in_defaults`

### Docs

- `README.md` — add `start_date` to the feed config example and the
  field-list narrative.
- `CLAUDE.md` — same: add to the per-feed fields list (currently lines
  describing `last`, `episode_filter`) and mention in the `service.py`
  / `filter_episodes` lines so future contributors see it.

## Out-of-scope simplifications considered and rejected

- **Add a CLI `--start-date` flag.** The user explicitly framed this as a
  config-level change, and the existing `--date` flag already covers ad-hoc
  CLI use. Adding a third date-ish flag would muddy the surface.
- **Auto-set `start_date` to today in `podcast-etl add`.** Magic at
  feed-creation time is hard to discover and surprises users who add a feed
  meaning to backfill it. Users can set it explicitly when they need it.
- **Make `start_date` and `last` mutually exclusive.** This is the opposite
  of what the user wants. The whole motivation is to combine them.

## Verification plan

1. `uv run pytest tests/test_service.py -v` — all new tests pass, all
   existing tests still pass.
2. `uv run pytest tests/ -v` — full unit suite green.
3. Manual: add a `start_date` to a feed in `feeds.yaml`, run
   `uv run podcast-etl run --feed <name>`, verify only episodes on or after
   that date are processed.
4. Manual: open the web UI feed edit form, set `start_date`, save, reopen,
   confirm the value round-trips.
5. Manual: set an invalid `start_date` in the YAML editor textarea
   (`start_date: not-a-date`) and confirm validation rejects it on save.
