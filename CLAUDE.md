# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
uv sync                                      # install dependencies
uv run podcast-etl --help                    # CLI entry point
uv run podcast-etl serve                     # web UI + poll loop on port 8000
uv run podcast-etl serve --port 9000         # custom port
uv run podcast-etl -v run --all              # run pipeline (verbose)
uv run podcast-etl --log-level WARNING run --all
uv run pytest tests/ -v                      # unit tests only
uv run pytest tests/ -v -m ''               # all tests (including integration)
docker build --target test -t podcast-etl-test . && docker run --rm podcast-etl-test
```

## Tests

Tests live in `tests/` and use pytest:

- `test_models.py` -- `slugify`, `episode_json_filename`, `StepStatus`, `Episode`, `Podcast` (dict roundtrips, save/load, GUID filenames)
- `test_pipeline.py` -- `Pipeline` step execution, skipping already-completed steps, step filters, `deep_merge`
- `test_feed.py` -- `parse_feed` (audio extraction, slug dedup, status preservation, episode image extraction, episode number parsing, `raw_title` capture)
- `test_cli.py` -- `parse_date_range`, `reset` command (single feed, --all, cancel, nonexistent, argument validation), `delete` command (config removal, on-disk cleanup, missing-feed exit, cancel)
- `test_service.py` -- service layer: `load_config`, `save_config` (atomic writes), `validate_config` (incl. start_date), `get_output_dir`, `find_feed_config`, `find_podcast_dir`, `get_pipeline_steps`, `filter_episodes` (incl. start_date floor), `_coerce_start_date`, `get_feed_status`, `split_config_fields`, `merge_config_fields`, `get_resolved_config_with_sources`, `reset_feed_data`, `delete_feed`
- `test_download_step.py` -- `DownloadStep` filename construction, skip-existing, download
- `test_tag_step.py` -- `TagStep` MP3 tagging, TRCK track number, APIC album art embedding, audio file discovery, error cases
- `test_qbittorrent_client.py` -- `QBittorrentClient` login, has_torrent, add_torrent
- `test_unit3d_tracker.py` -- `ModifiedUnit3dTracker` upload, field construction, image handling, cover override precedence
- `test_transcription_detector.py` -- `TranscriptionDetector` whisper API, local transcription, `AnthropicProvider` LLM calls, `merge_segments`, `_parse_llm_response`
- `test_detect_ads_step.py` -- `DetectAdsStep` orchestration, config merging, transcript saving/reuse, segment merging
- `test_strip_ads_step.py` -- `StripAdsStep` ffmpeg args, idempotency, no-ads passthrough
- `test_stage_step.py` -- `StageStep` copy, idempotency, client_path rebasing, strip_ads fallback
- `test_torrent_step.py` -- `TorrentStep` mktorrent args, idempotency, error cases
- `test_seed_step.py` -- `SeedStep` add_torrent, idempotency, client resolution
- `test_upload_step.py` -- `UploadStep` tracker.upload call, tracker resolution, cover image override, error cases
- `test_images.py` -- `download_image` (caching, extension extraction, fallback), `resolve_episode_image` (episode/feed fallback, dedup, error handling), `convert_image` (resize, format conversion, no upscale)
- `test_title_clean.py` -- `strip_date`, `reorder_parts`, `prepend_episode_number`, `sanitize`, `clean_title` (date formats, bracket types, part variants, episode number prepend, filesystem chars, separator collapsing, config flags)
- `test_text.py` -- `clean_description` (HTML, entity-encoded, CDATA, plain text), `contains_blacklisted`, `apply_blacklist`
- `test_poller.py` -- `run_poll_loop` enabled/disabled feed filtering, `episode_filter` from feed/defaults config
- `test_async_poller.py` -- `async_poll_loop`, `PollControl` shutdown/pause/run-now
- `test_web.py` -- web UI routes: smoke test, dashboard, feeds CRUD, defaults editing, config form submission
- `test_audiobookshelf_step.py` -- `AudiobookshelfStep` copy and scan trigger, audio resolution, config merging, error cases
- `test_integration.py` -- end-to-end: parse real RSS feed, download episode, tag MP3, stage file (marked `integration`)
- `test_integration_torrent.py` -- stage + torrent steps with real disk I/O and mktorrent binary (marked `integration`)

**After making changes**, run tests and check whether new behaviour should be tested. Always update `README.md` and `CLAUDE.md` to reflect any changes to CLI commands, pipeline steps, architecture, or configuration.

## Architecture

### Overview

The system has three entry points that share a common service layer:

- **Web UI** (`cli.py serve` -> `web/`) -- FastAPI server with Jinja2/HTMX templates, runs an async poll loop as a background task. This is the primary mode in Docker.
- **CLI** (`cli.py`) -- Click commands for scripting and one-off runs. Thin wrapper over the service layer.
- **Poll mode** (`cli.py poll`) -- standalone synchronous poll loop without the web UI.

All three read and write `feeds.yaml` as the single source of truth. Episode state is persisted as JSON on disk (`output/<podcast-slug>/episodes/<date>-<slug>-<guid-hash>.json`), making the pipeline resumable -- re-runs skip already-completed steps.

### Service layer (`service.py`)

Orchestration logic shared by CLI and web routes. Registers all built-in pipeline steps at import time. Key functions: `load_config`, `save_config` (atomic via temp file + rename), `validate_config`, `find_feed_config`, `find_podcast_dir`, `fetch_feed`, `run_pipeline`, `get_feed_status`, `filter_episodes`, `reset_feed_data` (delete a podcast's output directory), `delete_feed` (remove from config + delete data). Also provides `split_config_fields`/`merge_config_fields` for the web UI's form/YAML split editing, and `get_resolved_config_with_sources` for the resolved config preview with source attribution.

### Web UI (`web/`)

FastAPI app factory in `web/__init__.py`. The `create_app(config_path)` function sets up routes and starts an async poll loop (`async_poll_loop`) as a lifespan background task, controlled via a `PollControl` dataclass (pause/resume/run-now/shutdown).

Routes:
- `web/routes/dashboard.py` -- dashboard page (`GET /`), poll controls (`POST /poll/{pause,resume,run-now}`), log tail (`GET /log-tail`)
- `web/routes/feeds.py` -- feed list, detail, add, edit, delete, run, save with diff preview and confirmation
- `web/routes/defaults.py` -- global defaults editing with diff preview and confirmation

Templates use Tailwind CSS (CDN) and HTMX (CDN) -- no JS build step.

### CLI (`cli.py`)

Click commands: `add`, `fetch`, `run`, `reset`, `delete`, `status`, `poll`, `serve`. Calls service layer functions for all business logic.

### Core modules

- `models.py` -- `Podcast`, `Episode`, `StepStatus` dataclasses with `save()`/`load()` methods. `Episode.raw_title` stores the original RSS title before cleaning. `episode_json_filename()` produces stable GUID-based filenames.
- `feed.py` -- fetches RSS via `feedparser`, parses into models, merges existing on-disk step status to preserve progress. Parses `itunes:episode` into `Episode.episode_number` and `itunes:image` into `Episode.image_url`.
- `pipeline.py` -- `Pipeline` runs registered `Step` instances over episodes, skipping completed ones. `PipelineContext` carries `output_dir`, `podcast`, and resolved config. `deep_merge` and `resolve_feed_config` handle config inheritance.
- `poller.py` -- synchronous `run_poll_loop` (for standalone `poll` command) and async `async_poll_loop` (for `serve` command). Both reload config each cycle. `PollControl` dataclass provides pause/resume/run-now/shutdown via asyncio events.
- `title_clean.py` -- `clean_title` orchestrates: `strip_date` -> `reorder_parts` -> `prepend_episode_number` -> `sanitize`.
- `text.py` -- `clean_description` (HTML/entity/CDATA to plain text), `apply_blacklist`/`contains_blacklisted`.
- `images.py` -- `download_image` (caching), `resolve_episode_image` (episode/feed fallback, dedup), `convert_image` (Pillow resize + JPEG).

### Pipeline steps (`steps/`)

Each step implements the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`):

- `download` -- fetch audio from RSS `audio_url`
- `tag` -- ID3 metadata, TRCK track number from `episode_number`, APIC album art (episode image -> feed image fallback, 600x600 JPEG)
- `detect_ads` -- transcribe via local `faster-whisper` or remote whisper server, classify segments via LLM (Anthropic Claude). Saves transcript for reuse on retry.
- `strip_ads` -- remove ad segments via ffmpeg with crossfade
- `stage` -- copy audio to `torrent_data_dir/`; prefers cleaned audio, falls back to download
- `torrent` -- create `.torrent` via `mktorrent`, extract `info_hash` via `torf`
- `seed` -- add torrent to qBittorrent via Web API
- `upload` -- upload to UNIT3D tracker; uses episode artwork as cover (500x500 JPEG), falls back to `cover_image` config; supports banner images
- `audiobookshelf` -- copy audio to Audiobookshelf library dir and trigger scan

### External integrations

- `clients/qbittorrent.py` -- `QBittorrentClient` implementing `TorrentClient` protocol; session-based auth
- `trackers/unit3d.py` -- `ModifiedUnit3dTracker` implementing `Tracker` protocol; multipart upload to UNIT3D REST API
- `detectors/` -- `AdSegment` dataclass, `Detector`/`LLMProvider` protocols, `merge_segments` utility. `TranscriptionDetector` handles whisper + LLM classification; `AnthropicProvider` for Claude API.

### Config format

The top-level `defaults` block is deep-merged with per-feed overrides via `resolve_feed_config`. Each feed entry supports `name` (short identifier), `enabled` (boolean, default `false`), `last`, `start_date` (ISO date floor; stacks with `last`), `episode_filter`, and any key from `defaults` as an override.

```yaml
poll_interval: 3600

defaults:
  output_dir: ./output
  torrent_data_dir: /torrent-data
  blacklist: ["John Doe"]
  pipeline: [download, tag, detect_ads, strip_ads, stage, torrent, seed, upload]
  title_cleaning: {strip_date: false, reorder_parts: false, prepend_episode_number: false, sanitize: false}
  ad_detection: {whisper: {model: base, language: en}, llm: {provider: anthropic, model: claude-sonnet-4-20250514}, min_confidence: 0.5}
  audiobookshelf: {url: ..., api_key: ..., library_id: ..., dir: /podcasts}
  client: {url: ..., username: ..., password: ..., save_path: /data}
  tracker: {url: ..., remember_cookie: ..., announce_url: ..., anonymous: 0, personal_release: 0, mod_queue_opt_in: 0}

feeds:
  - url: https://example.com/rss
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
    tracker: {mod_queue_opt_in: 1}          # deep-merged with defaults.tracker
    ad_detection: {llm: {model: ...}}       # deep-merged with defaults.ad_detection
    title_cleaning: {strip_date: true}      # per-feed override
```

### Docker

The final image installs `mktorrent` and `ffmpeg` via `apt-get` and exposes port `8000`. Three volumes: `/config` (YAML config), `/output` (download/processing data), `/torrent-data` (staging dir shared with qBittorrent container). The default entrypoint runs `serve` (web UI + integrated poll loop).

### Adding a new pipeline step

1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol
2. Register it in `service.py`: `register_step(YourStep())`
3. Add `your_step` to `pipeline` list in `feeds.yaml`

### Gotchas

**Logging disable hack:** `cli.py` disables all logging at module import (`logging.disable(logging.ERROR)`) before dependencies load, to suppress pyenv hashlib blake2 errors. It re-enables logging in `setup_logging()`. Any code that runs before `setup_logging()` will not produce log output.

**Web UI form/YAML split:** The sets `KNOWN_FEED_FIELDS` and `KNOWN_DEFAULTS_FIELDS` in `service.py` control which config keys get structured form controls vs. raw YAML editing. Promoting a field means adding it to the set and writing the template markup.
