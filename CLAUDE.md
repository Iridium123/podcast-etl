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
uv run podcast-etl eval --help               # ad-detection eval harness (run from repo root)
uv run podcast-etl eval run                  # run eval matrix (reads eval/eval_config.yaml)
uv run python eval/run.py                    # equivalent standalone runner
docker build --target test -t podcast-etl-test . && docker run --rm podcast-etl-test
```

The `eval` command group (`src/podcast_etl/eval_cli.py`) wraps the `eval/`
harness (which lives at the repo root, not in the installed package). Subcommands:
`label` (generate predicted Labels into a named dataset), `annotate`
(`--blank` or `--bootstrap-from` an existing dataset), `validate` (consistency
check; non-zero exit on errors), `score` (`--predictions`/`--gold` datasets,
`--allowed-annotators` filter), and `run` (full matrix from `eval/eval_config.yaml`,
see `eval/eval_config.yaml.example`). Because `eval/` is outside the installed
package, `eval_cli.py` inserts CWD onto `sys.path` and imports `eval.*` lazily
inside each callback, so the harness must be run from the repository root.

## Tests

Tests live in `tests/` and use pytest:

- `test_models.py` -- `slugify`, `episode_json_filename`, `StepStatus`, `Episode`, `Podcast` (dict roundtrips, save/load, GUID filenames)
- `test_pipeline.py` -- `Pipeline` step execution, skipping already-completed steps, step filters, `deep_merge`
- `test_feed.py` -- `parse_feed` (audio extraction, slug dedup, status preservation, episode image extraction, episode number parsing, `raw_title` capture)
- `test_cli.py` -- `parse_date_range`, `reset` command (single feed, --all, cancel, nonexistent, argument validation), `delete` command (config removal, on-disk cleanup, missing-feed exit, cancel)
- `test_service.py` -- service layer: `load_config`, `save_config` (atomic writes), `validate_config`, `get_output_dir`, `find_feed_config`, `find_podcast_dir`, `get_pipeline_steps`, `filter_episodes`, `get_feed_status`, `split_config_fields`, `merge_config_fields`, `get_resolved_config_with_sources`, `reset_feed_data`, `delete_feed`
- `test_download_step.py` -- `DownloadStep` filename construction, skip-existing, download
- `test_tag_step.py` -- `TagStep` MP3 tagging, TRCK track number, APIC album art embedding, audio file discovery, error cases
- `test_qbittorrent_client.py` -- `QBittorrentClient` login, has_torrent, add_torrent
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
- `test_poller.py` -- `run_poll_loop` enabled/disabled feed filtering, `episode_filter` from feed/defaults config
- `test_async_poller.py` -- `async_poll_loop`, `PollControl` shutdown/pause/run-now
- `test_web.py` -- web UI routes: smoke test, dashboard, feeds CRUD, defaults editing, config form submission
- `test_log_stream.py` -- `read_new_lines` (offset tracking, partial trailing line, truncation, missing file), `read_tail_lines` (initial dashboard population), `tail_log_events` (async SSE generator emitting HTML-escaped, div-wrapped events for new lines)
- `test_audiobookshelf_step.py` -- `AudiobookshelfStep` copy and scan trigger, audio resolution, config merging, error cases
- `test_integration.py` -- end-to-end: parse real RSS feed, download episode, tag MP3, stage file (marked `integration`)
- `test_integration_torrent.py` -- stage + torrent steps with real disk I/O and mktorrent binary (marked `integration`)

Eval harness tests live in `tests/test_eval/`:

- `test_score.py` -- `overlap_fraction_matcher`, `match_segments` (greedy assignment), `score_episode`, `aggregate_scores` (precision/recall/F1, mean/median/p95 of absolute boundary errors), `format_report`
- `test_resolve.py` -- `resolve_episode` (audio/transcript path derivation, error branches: missing podcast/episode/audio/download-status/path)
- `test_datasets.py` -- `episode_key`, `iter_label_files`, `label_file_path`, `load_dataset` (keyed by episode_ref not filename), `resolve_dataset_root` (output alias, explicit path, named dataset)
- `test_label.py` -- `label_episode`, `label_dataset` (transcript reuse: cache/production-disk/fresh transcription), `iter_episode_refs` (scanning + regex filter), `_reuse_production_transcript` (provenance matching), `_classify`, production-seam patching
- `test_annotate.py` -- `create_blank` (empty skeleton, human annotator), `bootstrap_from_dataset` (copy from source dataset, missing-episode error)
- `test_validate.py` -- `validate_labels`, `validate_dataset` (negative timestamps, start>=end, exceeds audio duration, overlap)
- `test_review.py` -- `format_review` (transcript with ad-segment highlighting via U+258C left half block), `review_labels`
- `test_eval_cli.py` -- `podcast-etl eval label` (single podcast, all podcasts, regex filter, config YAML), `eval annotate` (--blank, --bootstrap-from, mutually exclusive error), `eval validate` (OK and errors), `eval score` (--predictions/--gold, --allowed-annotators), `eval run` (matrix from config file)
- `test_run.py` -- `run_eval` (shared transcript cache across configs, allowed_annotators filtering, duplicate-name guard, YAML loading, result JSON written, production transcript reuse when whisper provenance matches)

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
- `web/routes/dashboard.py` -- dashboard page (`GET /`, renders the last 100 log lines inline), poll controls (`POST /poll/{pause,resume,run-now}`), log SSE stream (`GET /log-stream`, served via `web/log_stream.py` which tails the file by byte offset and emits each new line as a `text/event-stream` event)
- `web/routes/feeds.py` -- feed list, detail, add, edit, delete, run, save with diff preview and confirmation
- `web/routes/defaults.py` -- global defaults editing with diff preview and confirmation

Templates use Tailwind CSS (CDN) and HTMX (CDN) -- no JS build step.

### CLI (`cli.py`)

Click commands: `add`, `fetch`, `run`, `reset`, `delete`, `status`, `poll`, `serve`. Calls service layer functions for all business logic.

### Core modules

- `models.py` -- `Podcast`, `Episode`, `StepStatus` dataclasses with `save()`/`load()` methods. `Episode.raw_title` stores the original RSS title before cleaning. `episode_json_filename()` produces stable GUID-based filenames.
- `labels.py` -- `Labels`, `Provenance`, `EpisodeRef` dataclasses. `Labels` is the first-class on-disk ad-label artifact (`save`/`load`), written by `detect_ads` to `output/<slug>/labels/<stem>.json` and read by `strip_ads`.
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
- `detect_ads` -- transcribe via local `faster-whisper` or remote whisper server, classify segments via LLM (Anthropic Claude). Saves transcript for reuse on retry. Writes a `Labels` file to `labels/<stem>.json`; the step result records `labels_path`/`transcript_path`/`whisper`/`llm` (no inline segments).
- `strip_ads` -- remove ad segments via ffmpeg with crossfade; loads segments + audio duration from the `detect_ads` labels file (no embedded-segments fallback)
- `stage` -- copy audio to `torrent_data_dir/`; prefers cleaned audio, falls back to download
- `torrent` -- create `.torrent` via `mktorrent`, extract `info_hash` via `torf`
- `seed` -- add torrent to qBittorrent via Web API
- `upload` -- upload to UNIT3D tracker; uses episode artwork as cover (500x500 JPEG), falls back to `cover_image` config; supports banner images
- `audiobookshelf` -- copy audio to Audiobookshelf library dir and trigger scan

### External integrations

- `clients/qbittorrent.py` -- `QBittorrentClient` implementing `TorrentClient` protocol; session-based auth
- `trackers/unit3d.py` -- `ModifiedUnit3dTracker` implementing `Tracker` protocol; multipart upload to UNIT3D REST API
- `detectors/` -- `AdSegment` dataclass (with optional `notes`), `Detector`/`LLMProvider` protocols, `resolve_overlaps` utility (greedy earliest-start-wins: snaps overlapping/near-adjacent segments — gap ≤ `ADJACENCY_BUFFER_SECONDS`, default 5s — to a contiguous, non-overlapping sequence while keeping each segment distinct; drops fully-contained segments). `transcription.py` owns the production classify code path: `load_prompt(name)` reads `prompts/<name>.txt`; `build_llm_client(llm_config)` makes one reusable client; `classify(transcript, prompt_text, llm_config, client=None)` is the single classify function (prompt sent as a cacheable `ephemeral` system block, transcript as the user message). `TranscriptionDetector` handles whisper + classification; `AnthropicProvider` resolves the prompt name and calls `classify`.

### Config format

The top-level `defaults` block is deep-merged with per-feed overrides via `resolve_feed_config`. Each feed entry supports `name` (short identifier), `enabled` (boolean, default `false`), `last`, `episode_filter`, and any key from `defaults` as an override.

```yaml
poll_interval: 3600

defaults:
  output_dir: ./output
  torrent_data_dir: /torrent-data
  blacklist: ["John Doe"]
  pipeline: [download, tag, detect_ads, strip_ads, stage, torrent, seed, upload]
  title_cleaning: {strip_date: false, reorder_parts: false, prepend_episode_number: false, sanitize: false}
  ad_detection: {whisper: {model: base, language: en}, llm: {provider: anthropic, model: claude-sonnet-4-20250514, prompt: default}, min_confidence: 0.5}
  audiobookshelf: {url: ..., api_key: ..., library_id: ..., dir: /podcasts}
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

### Ad detection eval harness (`eval/`)

Standalone evaluation system for measuring ad detection quality against gold-standard `Labels` files. Decoupled from the main pipeline — imports `podcast_etl` for episode resolution and classification but does not modify pipeline data.

**Design:** a *dataset* is a directory of production-format `Labels` files laid out as `<root>/<podcast-slug>/labels/<stem>.json`. Production's own `output/` directory is a valid dataset (it uses the same layout). Two datasets are matched by `episode_key` (derived from `EpisodeRef` embedded in each file, not from filenames). The eval harness is a thin scorer on top of production: classification uses production's `classify()`, `load_prompt()`, and `build_llm_client()` directly, so eval predictions are faithful to what the pipeline would produce.

**Modules:**
- `eval/datasets.py` -- `load_dataset`, `iter_label_files`, `label_file_path`, `episode_key`, `resolve_dataset_root` (resolves `"output"` alias, explicit path, or named dataset under `eval/datasets/`)
- `eval/label.py` -- `label_episode`, `label_dataset`, `iter_episode_refs` — run production detection logic to generate `Labels` files into a dataset root; reuses production transcripts when whisper provenance matches, then falls back to in-memory cache, then fresh transcription
- `eval/annotate.py` -- `create_blank` (empty skeleton with `annotator="human"`), `bootstrap_from_dataset` (copy a Labels from a source dataset as a hand-edit starting point)
- `eval/validate.py` -- `validate_labels`, `validate_dataset` (negative timestamps, start >= end, exceeds audio duration, overlaps)
- `eval/review.py` -- `format_review` (transcript display with ad-segment highlights via U+258C left half block), `review_labels`
- `eval/score.py` -- `overlap_fraction_matcher`, `match_segments` (greedy assignment), `score_episode`, `aggregate_scores` (precision/recall/F1, mean/median/p95 of absolute boundary errors), `format_report`
- `eval/resolve.py` -- `resolve_episode` finds audio/transcript paths on disk from an `EpisodeRef`
- `eval/run.py` -- matrix runner: load YAML, label each config's predictions into `eval/datasets/_runs/<name>`, score vs gold, print comparison table

**CLI (`podcast-etl eval`):** Five subcommands, all must be run from the repo root:
- `label DATASET_NAME [--podcast SLUG] [--episodes REGEX] [--config YAML]` — generate predicted Labels
- `annotate PODCAST EPISODE_STEM [--dataset gold] [--blank | --bootstrap-from SRC]` — create/seed a gold annotation
- `validate DATASET_NAME` — consistency check; non-zero exit on errors
- `score --predictions DS [--predictions DS ...] --gold DS [--allowed-annotators A ...]` — score predictions vs gold
- `run [--config eval/eval_config.yaml]` — run the full matrix

Common options: `--output-dir` (default `./output`), `--datasets-dir` (default `eval/datasets`), `--results-dir` (default `eval/results`).

**Transcript reuse:** Before transcribing, `label.py` checks `episode.status['detect_ads'].result['whisper']`; if it matches the eval's normalized whisper config (via `normalize_whisper_config`), the on-disk production transcript is reused. A single in-memory cache is shared across all configs in a run, so identical whisper settings transcribe each episode only once.

**Annotator filtering:** `allowed_annotators` (YAML field in `eval_config.yaml`, default `["human"]`) controls which gold annotations are scored. This prevents circular eval when scoring against model-bootstrapped labels.

**Bundled dataset:** `eval/datasets/sonnet-4-6-bootstrap/` is a committed dataset of 3 money-stuff episodes labelled by `claude-sonnet-4-6`. Per-run predictions land in `eval/datasets/_runs/` and result JSON in `eval/results/` (both gitignored).

### Gotchas

**Logging disable hack:** `cli.py` disables all logging at module import (`logging.disable(logging.ERROR)`) before dependencies load, to suppress pyenv hashlib blake2 errors. It re-enables logging in `setup_logging()`. Any code that runs before `setup_logging()` will not produce log output.

**Web UI form/YAML split:** The sets `KNOWN_FEED_FIELDS` and `KNOWN_DEFAULTS_FIELDS` in `service.py` control which config keys get structured form controls vs. raw YAML editing. Promoting a field means adding it to the set and writing the template markup.

**Eval pythonpath:** The `eval/` package lives at the project root, not under `src/`. `pyproject.toml` sets `pythonpath = ["."]` in `[tool.pytest.ini_options]` so `from eval.<module> import ...` works in pytest. The `podcast-etl eval` console entry point inserts CWD onto `sys.path` at runtime (via `_ensure_cwd_importable()`), so the command must be run from the repo root — running it from any other directory will fail with an import error.
