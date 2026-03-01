# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
uv sync                          # install dependencies
uv run podcast-etl --help        # CLI entry point
uv run podcast-etl -v run --all  # run pipeline with verbose logging
uv run pytest tests/ -v          # run tests
```

## Tests

Tests live in `tests/` and use pytest. Three test files cover the core modules:

- `test_models.py` — `slugify`, `StepStatus`, `Episode`, `Podcast` (dict roundtrips, save/load)
- `test_pipeline.py` — `Pipeline` step execution, skipping already-completed steps, step filters
- `test_tag_step.py` — `TagStep` MP3/MP4 tagging, audio file discovery, error cases

**After making changes**, run tests and check whether new behaviour should be tested. Always update `README.md` and `CLAUDE.md` to reflect any changes to CLI commands, pipeline steps, architecture, or configuration — do not skip this.

## Architecture

The pipeline is step-based and resumable. Each episode tracks its own completion state per step, stored as JSON on disk, so re-runs skip already-processed episodes.

**Data flow:**
1. `feed.py` — fetches RSS via `feedparser`, parses into `Podcast`/`Episode` models, merges existing on-disk step status to preserve progress
2. `models.py` — `Podcast`, `Episode`, `StepStatus` dataclasses with `save()`/`load()` methods; persisted to `output/<podcast-slug>/podcast.json` and `output/<podcast-slug>/episodes/<ep-slug>.json`
3. `pipeline.py` — `Pipeline` runs registered `Step` instances over episodes, skipping any where `episode.status[step.name]` is already set; writes status back to disk after each step
4. `cli.py` — Click commands (`add`, `fetch`, `run`, `reset`, `status`, `poll`); registers built-in steps at import time via `register_step()`; `find_feed_config(config, identifier)` resolves a feed by name or URL
5. `poller.py` — long-running loop that reloads config each cycle and handles SIGTERM/SIGINT gracefully; uses per-feed `pipeline` list when set

**Feed config (`feeds.yaml`):** Each feed entry supports optional `name` (short identifier) and `pipeline` (list of step names). If `pipeline` is omitted, the feed uses `settings.pipeline` as the default. The `--feed` flag on `run`/`fetch`/`status` accepts either a name or a full URL.

```yaml
feeds:
  - url: https://example.com/rss
    name: my-podcast          # optional; enables --feed my-podcast
    pipeline:                 # optional; overrides settings.pipeline for this feed
      - download
      - tag
settings:
  pipeline:
    - download
    - tag
```

**Adding a new pipeline step:**
1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`)
2. Register it in `cli.py`: `register_step(YourStep())`
3. Add `your_step` to `pipeline` list in `feeds.yaml` (globally or per-feed)

**Key note on logging:** `cli.py` disables all logging at module import (before dependencies load) to suppress pyenv hashlib errors, then re-enables it in `setup_logging()`. New code that runs before `setup_logging()` will not produce log output.
