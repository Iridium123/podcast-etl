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

- `test_models.py` -- `slugify`, `episode_json_filename`, `StepStatus`, `Episode`, `Podcast`, `TorrentItem` (dict roundtrips, save/load, GUID filenames, `guid_hash` torrent-item filenames)
- `test_pipeline.py` -- `Pipeline` step execution, skipping already-completed steps, step filters, `deep_merge`
- `test_feed.py` -- `parse_feed` (audio extraction, slug dedup, status preservation, episode image extraction, episode number parsing, `raw_title` capture)
- `test_unit3d_feed.py` -- `parse_unit3d_feed` (torrent enclosure extraction, on-disk state preservation, orphan exclusion, all-on-disk-episode restoration, blacklist)
- `test_torrent_fetch.py` -- `to_rfc2822`, ID3 extraction with fallbacks, episode spawning (slug dedup, collision-suffixed filenames, synthesized download status), three-state fetch machine (blob+local hash, re-add recovery, no-MP3 terminal, partial-spawn idempotency, per-item failure isolation)
- `test_cli.py` -- `parse_date_range`, `reset` command (single feed, --all, cancel, nonexistent, argument validation), `delete` command (config removal, on-disk cleanup, missing-feed exit, cancel)
- `test_service.py` -- service layer: `load_config`, `save_config` (atomic writes), `validate_config`, `get_output_dir`, `find_feed_config`, `find_podcast_dir`, `get_pipeline_steps`, `filter_episodes`, `get_feed_status`, `split_config_fields`, `merge_config_fields`, `get_resolved_config_with_sources`, `reset_feed_data`, `delete_feed`, `source` validation/dispatch, `filter_torrent_items`, fetch-phase ordering in `run_pipeline`
- `test_download_step.py` -- `DownloadStep` filename construction, skip-existing, download
- `test_tag_step.py` -- `TagStep` MP3 tagging, TRCK track number, APIC album art embedding, audio file discovery, error cases
- `test_qbittorrent_client.py` -- `QBittorrentClient` login, has_torrent, add_torrent, is_complete (progress-based), get_files, `get_torrent_client` factory
- `test_unit3d_tracker.py` -- `ModifiedUnit3dTracker` upload, field construction, image handling, cover override precedence
- `test_transcription_detector.py` -- `TranscriptionDetector` whisper API, local transcription, `load_prompt`, `build_llm_client`, `classify` (cached system prompt, client reuse), `AnthropicProvider` (prompt resolution + classify), `resolve_overlaps` (overlap/near-adjacent snapping, containment drop, buffer), `_parse_llm_response`
- `test_detect_ads_step.py` -- `DetectAdsStep` orchestration, config merging, transcript saving/reuse, overlap resolution, standalone labels-file output
- `test_strip_ads_step.py` -- `StripAdsStep` ffmpeg args, idempotency, no-ads passthrough, reading segments from the labels file
- `test_labels.py` -- `Labels`/`Provenance`/`EpisodeRef` to_dict/from_dict, save/load roundtrip, on-disk shape, `AdSegment.notes`
- `test_migrate_labels.py` -- `scripts/migrate_labels.py` migration of embedded segments to label files, dry-run, idempotency, CLI entry
- `test_stage_step.py` -- `StageStep` copy, idempotency, client_path rebasing, strip_ads fallback
- `test_torrent_step.py` -- `TorrentStep` mktorrent args, idempotency, error cases
- `test_seed_step.py` -- `SeedStep` add_torrent, idempotency, client resolution
- `test_upload_step.py` -- `UploadStep` tracker.upload call, tracker resolution, cover image override, error cases
- `test_images.py` -- `download_image` (caching, extension extraction, fallback), `resolve_episode_image` (episode/feed fallback, dedup, error handling), `convert_image` (resize, format conversion, no upscale)
- `test_title_clean.py` -- `strip_date`, `reorder_parts`, `prepend_episode_number`, `sanitize`, `clean_title` (date formats, bracket types, part variants, episode number prepend, filesystem chars, separator collapsing, config flags)
- `test_text.py` -- `clean_description` (HTML, entity-encoded, CDATA, plain text), `contains_blacklisted`, `apply_blacklist`
- `test_poller.py` -- `run_poll_loop` enabled/disabled feed filtering, `last`/`episode_filter` resolution passed to `service.run_pipeline`
- `test_async_poller.py` -- `async_poll_loop`, `PollControl` shutdown/pause/run-now
- `test_web.py` -- web UI routes: smoke test, dashboard, feeds CRUD, defaults editing, config form submission, `source` field, feed-detail torrents table
- `test_log_stream.py` -- `read_new_lines` (offset tracking, partial trailing line, truncation, missing file), `read_tail_lines` (initial dashboard population), `tail_log_events` (async SSE generator emitting HTML-escaped, div-wrapped events for new lines)
- `test_audiobookshelf_step.py` -- `AudiobookshelfStep` copy and scan trigger, optional scan config (skip when unconfigured, partial-config error), audio resolution, config merging, error cases
- `test_integration.py` -- end-to-end: parse real RSS feed, download episode, tag MP3, stage file; torrent fetch phase with a real torf `.torrent` and real mutagen ID3 (marked `integration`)
- `test_integration_torrent.py` -- stage + torrent steps with real disk I/O and mktorrent binary (marked `integration`)

**After making changes**, run tests and check whether new behaviour should be tested. Always update `README.md` and `CLAUDE.md` to reflect any changes to CLI commands, pipeline steps, architecture, or configuration.

## Architecture

### Overview

The system has three entry points that share a common service layer:

- **Web UI** (`cli.py serve` -> `web/`) -- FastAPI server with Jinja2/HTMX templates, runs an async poll loop as a background task. This is the primary mode in Docker.
- **CLI** (`cli.py`) -- Click commands for scripting and one-off runs. Thin wrapper over the service layer.
- **Poll mode** (`cli.py poll`) -- standalone synchronous poll loop without the web UI.

All three read and write `feeds.yaml` as the single source of truth. Episode state is persisted as JSON on disk (`output/<podcast-slug>/episodes/<date>-<slug>-<guid-hash>.json`), making the pipeline resumable -- re-runs skip already-completed steps.

Feeds have a `source` (default `rss`; also `unit3d` for UNIT3D tracker RSS whose enclosures are `.torrent` files). For torrent-source feeds, a **fetch phase** (`torrent_fetch.py`) runs before the pipeline: it downloads `.torrent` blobs, hands them to qBittorrent, and once complete spawns one Episode per MP3 with a synthesized `download` StepStatus -- so every pipeline step works unchanged. Torrent-item state lives in `output/<podcast-slug>/torrents/<guid-hash>.json`; blobs are kept in `torrent_files/`. Fetching is implied by the source and is NOT a pipeline step.

### Service layer (`service.py`)

Orchestration logic shared by CLI and web routes. Registers all built-in pipeline steps at import time. Key functions: `load_config`, `save_config` (atomic via temp file + rename), `validate_config`, `find_feed_config`, `find_podcast_dir`, `fetch_feed`, `run_pipeline`, `get_feed_status`, `filter_episodes`, `reset_feed_data` (delete a podcast's output directory), `delete_feed` (remove from config + delete data). Also provides `split_config_fields`/`merge_config_fields` for the web UI's form/YAML split editing, and `get_resolved_config_with_sources` for the resolved config preview with source attribution.

### Web UI (`web/`)

FastAPI app factory in `web/__init__.py`. The `create_app(config_path)` function sets up routes and starts an async poll loop (`async_poll_loop`) as a lifespan background task, controlled via a `PollControl` dataclass (pause/resume/run-now/shutdown).

Routes:
- `web/routes/dashboard.py` -- dashboard page (`GET /`, renders the last 100 log lines inline), poll controls (`POST /poll/{pause,resume,run-now}`), log SSE stream (`GET /log-stream`, served via `web/log_stream.py` which tails the file by byte offset and emits each new line as a `text/event-stream` event)
- `web/routes/feeds.py` -- feed list, detail, add, edit, delete, run, save with diff preview and confirmation
- `web/routes/defaults.py` -- global defaults editing with diff preview and confirmation

Templates use Tailwind CSS (CDN) and HTMX (CDN) -- no JS build step.

### CLI (`cli.py`)

Click commands: `add`, `fetch`, `run`, `reset`, `delete`, `status`, `poll`, `serve`. Calls service layer functions for all business logic.

### Core modules

- `models.py` -- `Podcast`, `Episode`, `StepStatus`, `TorrentItem` dataclasses with `save()`/`load()` methods. `Episode.raw_title` stores the original RSS title before cleaning. `episode_json_filename()` produces stable GUID-based filenames. `TorrentItem` lifecycle state derives from its fields (no `info_hash` = blob not fetched; `info_hash` without `fetched_at` = downloading; `fetched_at` = done); `Podcast.torrent_items` is populated only for torrent-source feeds.
- `labels.py` -- `Labels`, `Provenance`, `EpisodeRef` dataclasses. `Labels` is the first-class on-disk ad-label artifact (`save`/`load`), written by `detect_ads` to `output/<slug>/labels/<stem>.json` and read by `strip_ads`.
- `feed.py` -- fetches RSS via `feedparser`, parses into models, merges existing on-disk step status to preserve progress. Parses `itunes:episode` into `Episode.episode_number` and `itunes:image` into `Episode.image_url`.
- `unit3d_feed.py` -- UNIT3D tracker RSS parser. Torrent enclosures become `TorrentItem`s (on-disk state merged; items missing from the feed become orphans and are dropped -- tracker-deletion abandonment). Loads ALL on-disk episodes into `Podcast.episodes` (torrent-spawned episodes are never feed-present, unlike `parse_feed`).
- `torrent_fetch.py` -- fetch phase for torrent-source feeds. Three-state machine per item, one advance per poll cycle: (1) fetch blob + compute info hash locally via torf (crash-safe), (2) ensure torrent in client (re-adds from stored blob if deleted -- deletion in qBittorrent is the supported retry gesture) then wait for completion, (3) spawn episodes: ID3 metadata with filename/RSS/mtime fallbacks, dates normalized to RFC 2822 (TagStep requires it), deterministic collision-suffixed filenames, synthesized `download` StepStatus. No-MP3 torrents are terminal. Per-item failures are logged and skipped.
- `pipeline.py` -- `Pipeline` runs registered `Step` instances over episodes, skipping completed ones. `PipelineContext` carries `output_dir`, `podcast`, and resolved config. `deep_merge` and `resolve_feed_config` handle config inheritance.
- `poller.py` -- synchronous `run_poll_loop` (for standalone `poll` command) and async `async_poll_loop` (for `serve` command). Both reload config each cycle and delegate fetch+pipeline to `service.fetch_feed`/`service.run_pipeline`. `PollControl` dataclass provides pause/resume/run-now/shutdown via asyncio events.
- `title_clean.py` -- `clean_title` orchestrates: `strip_date` -> `reorder_parts` -> `prepend_episode_number` -> `sanitize`.
- `text.py` -- `clean_description` (HTML/entity/CDATA to plain text), `apply_blacklist`/`contains_blacklisted`.
- `images.py` -- `download_image` (caching), `resolve_episode_image` (episode/feed fallback, dedup), `convert_image` (Pillow resize + JPEG).

### Pipeline steps (`steps/`)

Each step implements the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`):

- `download` -- fetch audio from RSS `audio_url`
- `tag` -- ID3 metadata, TRCK track number from `episode_number`, APIC album art (episode image -> feed image fallback, 600x600 JPEG)
- `detect_ads` -- transcribe via local `faster-whisper` or remote whisper server, classify segments via LLM (Anthropic Claude). Saves transcript for reuse on retry. Writes a `Labels` file to `labels/<stem>.json`; the step result records `labels_path`/`transcript_path`/`whisper`/`llm` (no inline segments).
- `strip_ads` -- remove ad segments via ffmpeg with crossfade; loads segments + audio duration from the `detect_ads` labels file (no embedded-segments fallback)
- `stage` -- copy audio to `torrent_data_dir/`; prefers cleaned audio, falls back to download
- `torrent` -- create `.torrent` via `mktorrent`, extract `info_hash` via `torf`
- `seed` -- add torrent to qBittorrent via Web API
- `upload` -- upload to UNIT3D tracker; uses episode artwork as cover (500x500 JPEG), falls back to `cover_image` config; supports banner images
- `audiobookshelf` -- copy audio to Audiobookshelf library dir; only `dir` is required. If `url`/`api_key`/`library_id` are all set, triggers a library scan via the ABS API after each copy; if all are absent, skips the scan (logged) and relies on ABS's folder watcher. A partial set of scan keys is a config error.

### External integrations

- `clients/` -- `TorrentClient` protocol (`add_torrent`, `has_torrent`, `is_complete`, `get_files`), `TorrentFileInfo`, and the shared `get_torrent_client` factory. `clients/qbittorrent.py` implements the protocol with session-based auth; `is_complete` checks `progress == 1` (never state names -- qBt renamed them across versions)
- `trackers/unit3d.py` -- `ModifiedUnit3dTracker` implementing `Tracker` protocol; multipart upload to UNIT3D REST API
- `detectors/` -- `AdSegment` dataclass (with optional `notes`), `Detector`/`LLMProvider` protocols, `resolve_overlaps` utility (greedy earliest-start-wins: snaps overlapping/near-adjacent segments — gap ≤ `ADJACENCY_BUFFER_SECONDS`, default 5s — to a contiguous, non-overlapping sequence while keeping each segment distinct; drops fully-contained segments). `transcription.py` owns the production classify code path: `load_prompt(name)` reads `prompts/<name>.txt`; `build_llm_client(llm_config)` makes one reusable client; `classify(transcript, prompt_text, llm_config, client=None)` is the single classify function (prompt sent as a cacheable `ephemeral` system block, transcript as the user message). `TranscriptionDetector` handles whisper + classification; `AnthropicProvider` resolves the prompt name and calls `classify`.

### Config format

The top-level `defaults` block is deep-merged with per-feed overrides via `resolve_feed_config`. Each feed entry supports `name` (short identifier), `enabled` (boolean, default `false`), `last`, `episode_filter`, and any key from `defaults` as an override.

Each feed also supports `source` (`rss` default, or `unit3d` for torrent-source feeds; validated against known values). For `source: unit3d` feeds, `episode_filter`/`last` select *torrents* (by raw RSS title), and the `pipeline` list contains only episode steps -- fetching is implied by the source.

```yaml
poll_interval: 3600

defaults:
  output_dir: ./output
  torrent_data_dir: /torrent-data
  blacklist: ["John Doe"]
  pipeline: [download, tag, detect_ads, strip_ads, stage, torrent, seed, upload]
  title_cleaning: {strip_date: false, reorder_parts: false, prepend_episode_number: false, sanitize: false}
  ad_detection: {whisper: {model: base, language: en}, llm: {provider: anthropic, model: claude-sonnet-4-20250514, prompt: default}, min_confidence: 0.5}
  audiobookshelf: {dir: /podcasts}  # url/api_key/library_id optional (all-or-none): set to trigger API scan after copy
  client: {url: ..., username: ..., password: ..., save_path: /data}
  tracker: {url: ..., remember_cookie: ..., announce_url: ..., anonymous: 0, personal_release: 0, mod_queue_opt_in: 0}

feeds:
  - url: https://example.com/rss
    name: my-podcast
    enabled: true
    last: 5
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

The final image installs `mktorrent` and `ffmpeg` via `apt-get` and exposes port `8000`. Three volumes: `/config` (YAML config), `/output` (download/processing data), `/torrent-data` (staging dir shared with qBittorrent container). The default entrypoint runs `serve` (web UI + integrated poll loop). The `prompts/` directory (ad-detection prompts, resolved relative to the `/app` working directory) and `scripts/` (maintenance scripts such as `migrate_labels.py`, runnable against the live `/output` volume) are copied into the image.

### Adding a new pipeline step

1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol
2. Register it in `service.py`: `register_step(YourStep())`
3. Add `your_step` to `pipeline` list in `feeds.yaml`

### Gotchas

**Logging disable hack:** `cli.py` disables all logging at module import (`logging.disable(logging.ERROR)`) before dependencies load, to suppress pyenv hashlib blake2 errors. It re-enables logging in `setup_logging()`. Any code that runs before `setup_logging()` will not produce log output.

**Web UI form/YAML split:** The sets `KNOWN_FEED_FIELDS` and `KNOWN_DEFAULTS_FIELDS` in `service.py` control which config keys get structured form controls vs. raw YAML editing. Promoting a field means adding it to the set and writing the template markup.
