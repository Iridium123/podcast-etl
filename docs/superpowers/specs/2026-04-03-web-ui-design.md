# Web UI Design

## Problem

The `feeds.yaml` config file is getting unwieldy. Nested per-feed overrides, deep-merged defaults, and a growing number of settings make manual YAML editing error-prone. The CLI works well for scripting and one-off runs, but day-to-day config management needs a better interface.

## Goals

- Web UI for config management (primary) and status visibility (secondary)
- Single long-running process that serves the UI and runs the poll loop
- CLI preserved for scripting and one-off use
- Single-user homelab target — no auth
- `feeds.yaml` remains the single source of truth — no database

## Approach

FastAPI + Jinja2 templates + HTMX. Pure Python, no JS build step. The poll loop runs as an async background task in the FastAPI server process.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Server                        │
│                  (single process)                        │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Web Routes  │  │  Poll Loop   │  │  CLI (click)  │  │
│  │  (HTMX)     │  │  (bg task)   │  │  (separate    │  │
│  │              │  │              │  │   process)    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │          │
│         └─────────┬───────┴──────────────────┘          │
│                   │                                     │
│         ┌─────────▼─────────┐                           │
│         │   Service Layer   │  ← extracted from cli.py  │
│         └─────────┬─────────┘                           │
│                   │                                     │
│  ┌────────┬───────┼───────┬──────────┬──────────┐      │
│  │ models │ feed  │pipeline│  steps   │ poller   │      │
│  └────────┴───────┴───────┴──────────┴──────────┘      │
│                (existing modules)                        │
└─────────────────────────────────────────────────────────┘
                        │
                   ┌────▼────┐
                   │  Disk   │
                   │ YAML +  │
                   │  JSON   │
                   └─────────┘
```

### Service layer (`service.py`)

Extracted from `cli.py` orchestration logic. Both CLI and web routes call these functions:

- `load_config(config_path) -> dict`
- `save_config(config, config_path)`
- `validate_config(config)`
- `get_output_dir(config) -> Path`
- `find_feed_config(config, identifier) -> dict | None`
- `fetch_feed(url, output_dir, resolved_config) -> Podcast`
- `run_pipeline(podcast, output_dir, resolved_config, **filters)`
- `get_feed_status(output_dir, config) -> list[dict]` — new; returns per-feed episode/step completion data for the dashboard
- `get_resolved_config(defaults, feed) -> tuple[dict, dict]` — new; returns `(resolved_config, source_map)` where `source_map` indicates which keys came from defaults vs feed overrides

### Poller changes (`poller.py`)

The existing `run_poll_loop` is a synchronous blocking loop with `time.sleep(1)` increments and `signal.signal()` for shutdown. It also imports `filter_episodes` and `validate_config` from `cli.py`. Changes needed:

- **Imports**: Update to import from `service.py` instead of `cli.py` (for `filter_episodes`, `validate_config`)
- **Async version**: Add `async_poll_loop()` that replaces `time.sleep()` with `asyncio.sleep()` and uses an `asyncio.Event` for shutdown instead of signal handlers. The existing synchronous `run_poll_loop` remains for standalone `podcast-etl poll` use.
- **Shared state**: The async poll loop accepts a `PollControl` object (simple dataclass with `paused: bool`, `run_now: asyncio.Event`, `shutdown: asyncio.Event`) that the web routes can mutate to pause/resume and trigger immediate runs. Single-user, single-process — no locks needed beyond the event primitives.

### CLI changes (`cli.py`)

Becomes a thin wrapper. Click commands keep argument parsing and output formatting but call `service.py` functions. New `serve` command added:

```
podcast-etl serve --config feeds.yaml --port 8000
```

Starts uvicorn with the FastAPI app. Binds to `0.0.0.0` by default for Docker use.

### Web module (`web/`)

FastAPI app with Jinja2 templates and HTMX for dynamic behavior.

## Pages

### Dashboard (`GET /`)

- Summary counts: active feeds, episodes processed, episodes pending
- Poll status: running/paused, last cycle time, next cycle time
- Controls: pause/resume poll, trigger immediate run

### Feeds list (`GET /feeds`)

- All configured feeds with name, enabled status, episode count summary
- Per-feed actions: Run, Edit
- Add Feed button

### Feed detail (`GET /feeds/{name}`)

- Feed metadata (name, URL, enabled status)
- Episode step-completion grid (title x pipeline steps, done/pending)
- Config editing (see below)
- Resolved config preview (see below)

### Defaults (`GET /defaults`)

- Structured forms for common global settings
- Raw YAML editor for advanced settings
- Same form/YAML split as feed config

## Config Editing

### Structured forms + raw YAML

Each feed's config (and the global defaults) is split into two buckets:

**Known fields** — rendered as form controls:
- `url`, `name`, `enabled`, `last`, `episode_filter`
- `category_id`, `type_id`
- `pipeline` (toggle chips for each registered step)
- `title_cleaning` (checkboxes for each flag)
- `cover_image`, `banner_image`

**Everything else** — shown in a raw YAML editor:
- `tracker` overrides, `ad_detection` overrides, `audiobookshelf` overrides, any future keys
- Only shows feed-level overrides, not inherited defaults

The list of known fields is a single list in the code. Promoting a field from raw YAML to a form control means adding it to that list and writing the template markup — the raw YAML editor automatically stops showing it.

### Save flow

1. Merge form field values with raw YAML editor contents into a single feed dict
2. Run `validate_config()` — reject with error message if invalid
3. Write to `feeds.yaml`
4. Poller picks up changes on next cycle (it already reloads config each cycle)

### YAML as single source of truth

- Web UI reads `feeds.yaml`, presents forms, writes changes back
- Hand-edits to the YAML file are picked up on next page load
- No database, no shadow state

## Resolved Config Preview

Collapsible read-only section on the feed detail page showing the final merged config after `deep_merge(defaults, feed)`:

- Uses `yaml.dump(default_flow_style=False)` for pretty-printed output
- Color-codes which values come from the feed (override) vs. defaults (inherited)
- Calls the same `deep_merge` function the pipeline uses, so what you see is what runs
- Updates after saving edits (HTMX re-renders the preview)

## File Structure

```
src/podcast_etl/
  service.py              ← NEW: orchestration logic extracted from cli.py
  cli.py                  ← CHANGED: thin wrapper calling service.py
  web/
    __init__.py           ← NEW: FastAPI app factory
    routes/
      __init__.py
      dashboard.py        ← NEW: GET /
      feeds.py            ← NEW: GET/POST /feeds, /feeds/{name}
      defaults.py         ← NEW: GET/POST /defaults
    templates/
      base.html           ← NEW: layout with nav, HTMX + Tailwind CDN
      dashboard.html
      feeds/
        list.html
        detail.html
        form.html         ← feed config form (structured fields)
        yaml_editor.html  ← raw YAML partial
        resolved.html     ← merged config preview partial
      defaults/
        edit.html
        yaml_editor.html
    static/               ← NEW: CSS overrides, favicon (minimal)
  models.py               ← unchanged
  feed.py                 ← unchanged
  pipeline.py             ← unchanged
  poller.py               ← CHANGED: imports from service.py, new async_poll_loop
  steps/                  ← unchanged
  clients/                ← unchanged
  trackers/               ← unchanged
  detectors/              ← unchanged
```

## New Dependencies

- `fastapi` — web framework
- `uvicorn` — ASGI server
- `jinja2` — templates (FastAPI uses it natively)

HTMX and Tailwind are CDN script tags in `base.html`. No JS build tooling.

## Docker Changes

- `CMD` switches to `podcast-etl serve`
- Same three volumes (`/config`, `/output`, `/torrent-data`)
- Expose port 8000
- `podcast-etl serve` replaces `podcast-etl poll` as the long-running process

## What Doesn't Change

- All existing CLI commands work exactly as before
- `podcast-etl poll` still works standalone if the web UI isn't needed
- All existing modules (models, feed, pipeline, steps, clients, trackers, detectors) are unchanged
- `poller.py` gets updated imports and a new async variant, but the synchronous `run_poll_loop` still works for standalone `poll` use
- All existing tests pass (some `test_cli.py` tests move to `test_service.py`, `test_poller.py` updated for new imports)

## Tests

- **`test_service.py`** — tests for the extracted service layer functions; mostly migrated from `test_cli.py` orchestration tests
- **`test_web.py`** — FastAPI TestClient tests for each route (GET/POST), form submission, YAML editing, validation error display, resolved config preview
- **`test_cli.py`** — updated to test that CLI commands call service layer correctly; existing tests adjusted for the extraction
