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

**After making changes**, check whether tests need updating and whether new behaviour should be tested. Also check whether `CLAUDE.md` itself needs updating (e.g. new steps, changed commands, architectural changes).

## Architecture

The pipeline is step-based and resumable. Each episode tracks its own completion state per step, stored as JSON on disk, so re-runs skip already-processed episodes.

**Data flow:**
1. `feed.py` — fetches RSS via `feedparser`, parses into `Podcast`/`Episode` models, merges existing on-disk step status to preserve progress
2. `models.py` — `Podcast`, `Episode`, `StepStatus` dataclasses with `save()`/`load()` methods; persisted to `output/<podcast-slug>/podcast.json` and `output/<podcast-slug>/episodes/<ep-slug>.json`
3. `pipeline.py` — `Pipeline` runs registered `Step` instances over episodes, skipping any where `episode.status[step.name]` is already set; writes status back to disk after each step
4. `cli.py` — Click commands (`add`, `fetch`, `run`, `status`, `poll`); registers built-in steps at import time via `register_step()`
5. `poller.py` — long-running loop that reloads config each cycle and handles SIGTERM/SIGINT gracefully

**Adding a new pipeline step:**
1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`)
2. Register it in `cli.py`: `register_step(YourStep())`
3. Add `your_step` to `pipeline` list in `feeds.yaml`

**Key note on logging:** `cli.py` disables all logging at module import (before dependencies load) to suppress pyenv hashlib errors, then re-enables it in `setup_logging()`. New code that runs before `setup_logging()` will not produce log output.
