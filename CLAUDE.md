# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
uv sync                          # install dependencies
uv run podcast-etl --help        # CLI entry point
uv run podcast-etl -v run --all              # run pipeline with verbose (DEBUG) logging
uv run podcast-etl --log-level WARNING run --all  # suppress INFO messages
uv run pytest tests/ -v          # run tests
```

## Tests

Tests live in `tests/` and use pytest:

- `test_models.py` — `slugify`, `StepStatus`, `Episode`, `Podcast` (dict roundtrips, save/load)
- `test_pipeline.py` — `Pipeline` step execution, skipping already-completed steps, step filters
- `test_feed.py` — `parse_feed` (audio extraction, slug dedup, status preservation)
- `test_cli.py` — `load_config`, `save_config`, `get_output_dir`, `find_feed_config`, `get_pipeline_steps`
- `test_download_step.py` — `DownloadStep` filename construction, extension extraction, skip-existing, download
- `test_tag_step.py` — `TagStep` MP3/MP4 tagging, audio file discovery, error cases
- `test_qbittorrent_client.py` — `QBittorrentClient` login, has_torrent, add_torrent
- `test_unit3d_tracker.py` — `ModifiedUnit3dTracker` upload, field construction, image handling
- `test_stage_step.py` — `StageStep` copy, idempotency, client_path rebasing
- `test_torrent_step.py` — `TorrentStep` mktorrent args, idempotency, error cases
- `test_seed_step.py` — `SeedStep` add_torrent, idempotency, client resolution
- `test_upload_step.py` — `UploadStep` tracker.upload call, tracker resolution, error cases

**After making changes**, run tests and check whether new behaviour should be tested. Always update `README.md` and `CLAUDE.md` to reflect any changes to CLI commands, pipeline steps, architecture, or configuration — do not skip this.

## Architecture

The pipeline is step-based and resumable. Each episode tracks its own completion state per step, stored as JSON on disk, so re-runs skip already-processed episodes.

**Data flow:**
1. `feed.py` — fetches RSS via `feedparser`, parses into `Podcast`/`Episode` models, merges existing on-disk step status to preserve progress
2. `models.py` — `Podcast`, `Episode`, `StepStatus` dataclasses with `save()`/`load()` methods; persisted to `output/<podcast-slug>/podcast.json` and `output/<podcast-slug>/episodes/<ep-slug>.json`
3. `pipeline.py` — `Pipeline` runs registered `Step` instances over episodes, skipping any where `episode.status[step.name]` is already set; writes status back to disk after each step; `PipelineContext` carries `output_dir`, `podcast`, `config` (full YAML), and `feed_config` (per-feed overrides)
4. `cli.py` — Click commands (`add`, `fetch`, `run`, `reset`, `status`, `poll`); registers built-in steps at import time via `register_step()`; `find_feed_config(config, identifier)` resolves a feed by name or URL
5. `poller.py` — long-running loop that reloads config each cycle and handles SIGTERM/SIGINT gracefully; uses per-feed `pipeline` list when set
6. `clients/qbittorrent.py` — `QBittorrentClient` implementing `TorrentClient` protocol; session-based auth, `has_torrent`, `add_torrent`
7. `trackers/unit3d.py` — `ModifiedUnit3dTracker` implementing `Tracker` protocol; multipart upload to UNIT3D REST API

**Feed config (`feeds.yaml`):** Each feed entry supports optional `name` (short identifier) and `pipeline` (list of step names). If `pipeline` is omitted, the feed uses `settings.pipeline` as the default. The `--feed` flag on `run`/`fetch`/`status` accepts either a name or a full URL.

```yaml
feeds:
  - url: https://example.com/rss
    name: my-podcast
    pipeline: [download, tag, stage, torrent, seed, upload]
    client: qbittorrent       # optional; falls back to first configured client
    tracker: unit3d           # optional; falls back to first configured tracker
    category_id: 14           # required for upload step
    type_id: 9                # required for upload step
    cover_image: /config/cover.jpg   # optional; passed to tracker
    banner_image: /config/banner.jpg # optional; passed to tracker

settings:
  output_dir: ./output
  torrent_data_dir: /torrent-data   # staging dir readable by both app and torrent client

  clients:
    qbittorrent:
      url: http://localhost:8080
      username: admin
      password: secret
      save_path: /data        # path to torrent_data_dir as seen by qBittorrent

  trackers:
    unit3d:
      url: https://tracker.example.com
      api_key: your-api-key
      announce_url: https://tracker.example.com/announce/your-passkey/announce
      anonymous: 0
      personal_release: 0
      mod_queue_opt_in: 0
```

**Pipeline steps:**
- `download` — fetch audio file from RSS `audio_url`, save to `output/<podcast>/audio/`
- `tag` — write ID3/MP4 metadata (title, artist, date) to the downloaded file
- `stage` — copy audio to `torrent_data_dir/<podcast>/<episode>/` for seeding; computes both local and qBittorrent-side paths
- `torrent` — create `.torrent` file via `mktorrent` CLI; extracts `info_hash` via `torf`; output in `output/<podcast>/torrents/`
- `seed` — add torrent to qBittorrent via Web API; sets `save_path` to client-side episode directory
- `upload` — upload `.torrent` + metadata to UNIT3D tracker via REST API; requires `category_id` and `type_id` in feed config

**Adding a new pipeline step:**
1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`)
2. Register it in `cli.py`: `register_step(YourStep())`
3. Add `your_step` to `pipeline` list in `feeds.yaml` (globally or per-feed)

**Docker:** The final image installs `mktorrent` via `apt-get`. Three volumes: `/config` (YAML config), `/output` (download/processing data), `/torrent-data` (staging dir shared with qBittorrent container).

**Key note on logging:** `cli.py` disables all logging at module import (before dependencies load) to suppress pyenv hashlib errors, then re-enables it in `setup_logging()`. New code that runs before `setup_logging()` will not produce log output.
