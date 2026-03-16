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
- `test_pipeline.py` — `Pipeline` step execution, skipping already-completed steps, step filters, `deep_merge`
- `test_feed.py` — `parse_feed` (audio extraction, slug dedup, status preservation)
- `test_cli.py` — `load_config`, `save_config`, `get_output_dir`, `find_feed_config`, `get_pipeline_steps`, `filter_episodes`, `validate_config`
- `test_download_step.py` — `DownloadStep` filename construction, skip-existing, download
- `test_tag_step.py` — `TagStep` MP3 tagging, audio file discovery, error cases
- `test_qbittorrent_client.py` — `QBittorrentClient` login, has_torrent, add_torrent
- `test_unit3d_tracker.py` — `ModifiedUnit3dTracker` upload, field construction, image handling
- `test_transcription_detector.py` — `TranscriptionDetector` whisper API, local transcription, `AnthropicProvider` LLM calls, `merge_segments`, `_parse_llm_response`
- `test_detect_ads_step.py` — `DetectAdsStep` orchestration, config merging, transcript saving/reuse, segment merging
- `test_strip_ads_step.py` — `StripAdsStep` ffmpeg args, idempotency, no-ads passthrough
- `test_stage_step.py` — `StageStep` copy, idempotency, client_path rebasing, strip_ads fallback
- `test_torrent_step.py` — `TorrentStep` mktorrent args, idempotency, error cases
- `test_seed_step.py` — `SeedStep` add_torrent, idempotency, client resolution
- `test_upload_step.py` — `UploadStep` tracker.upload call, tracker resolution, error cases
- `test_title_clean.py` — `strip_date`, `reorder_parts`, `clean_title` (date formats, bracket types, part variants, config flags)
- `test_text.py` — `clean_description` (HTML, entity-encoded, CDATA, plain text), `contains_blacklisted`, `apply_blacklist`
- `test_poller.py` — `run_poll_loop` enabled/disabled feed filtering
- `test_audiobookshelf_step.py` — `AudiobookshelfStep` copy and scan trigger, audio resolution, config merging, error cases
- `test_rate_limiter.py` — `RateLimiter` sleep behavior, `get_rate_limiter` registry

**After making changes**, run tests and check whether new behaviour should be tested. Always update `README.md` and `CLAUDE.md` to reflect any changes to CLI commands, pipeline steps, architecture, or configuration — do not skip this.

## Architecture

The pipeline is step-based and resumable. Each episode tracks its own completion state per step, stored as JSON on disk, so re-runs skip already-processed episodes.

**Data flow:**
1. `feed.py` — fetches RSS via `feedparser`, parses into `Podcast`/`Episode` models, merges existing on-disk step status to preserve progress
2. `models.py` — `Podcast`, `Episode`, `StepStatus` dataclasses with `save()`/`load()` methods; persisted to `output/<podcast-slug>/podcast.json` and `output/<podcast-slug>/episodes/<ep-slug>.json`
3. `pipeline.py` — `Pipeline` runs registered `Step` instances over episodes, skipping any where `episode.status[step.name]` is already set; writes status back to disk after each step; `PipelineContext` carries `output_dir`, `podcast`, and a single resolved config dict produced by `resolve_feed_config` (deep-merging `defaults` with per-feed overrides via `deep_merge`)
4. `cli.py` — Click commands (`add`, `fetch`, `run`, `reset`, `status`, `poll`); registers built-in steps at import time via `register_step()`; `find_feed_config(config, identifier)` resolves a feed by name or URL
5. `poller.py` — long-running loop that reloads config each cycle and handles SIGTERM/SIGINT gracefully; uses per-feed `pipeline` list when set
6. `clients/qbittorrent.py` — `QBittorrentClient` implementing `TorrentClient` protocol; session-based auth, `has_torrent`, `add_torrent`
7. `trackers/unit3d.py` — `ModifiedUnit3dTracker` implementing `Tracker` protocol; multipart upload to UNIT3D REST API
8. `text.py` — `clean_description` (HTML/entity/CDATA → plain text), `apply_blacklist` / `contains_blacklisted` for rejecting text containing configured strings
9. `title_clean.py` — `strip_date` (remove bracketed dates), `reorder_parts` (move part indicators to front), `clean_title` (orchestrator applying enabled rules based on config)
10. `detectors/__init__.py` — `AdSegment` dataclass, `Detector` and `LLMProvider` protocols, `merge_segments` utility
11. `detectors/transcription.py` — `TranscriptionDetector` (whisper transcription + LLM classification); supports local transcription via `faster-whisper` (default) or remote via OpenAI-compatible API; `AnthropicProvider` for Claude API

**Feed config (`feeds.yaml`):** The top-level `defaults` block contains shared config (output dirs, pipeline, client, tracker, ad detection, etc.). Any key in `defaults` can appear in a feed entry to override it — overrides are applied via deep merge, so nested keys like `tracker.mod_queue_opt_in` can be set without repeating the whole `tracker` block. Each feed entry supports optional `name` (short identifier) and `enabled` (boolean, defaults to `false` — must be set to `true` for poll to process it). The `--feed` flag on `run`/`fetch`/`status` accepts either a name or a full URL; `enabled: false` only affects `poll` mode, not explicit `--feed` runs.

```yaml
poll_interval: 3600

defaults:
  output_dir: ./output
  torrent_data_dir: /torrent-data
  blacklist:
    - "John Doe"
  pipeline: [download, tag, detect_ads, strip_ads, stage, torrent, seed, upload]
  title_cleaning:
    strip_date: false
    reorder_parts: false
  ad_detection:
    whisper:
      model: base
      language: en
    llm:
      provider: anthropic
      model: claude-sonnet-4-20250514
    min_confidence: 0.5
  audiobookshelf:
    url: https://abs.example.com
    api_key: your-api-key
    library_id: lib_abc123
    dir: /podcasts
  client:
    url: http://localhost:8080
    username: admin
    password: secret
    save_path: /data
  tracker:
    url: https://tracker.example.com
    remember_cookie: "eyJpdi..."
    announce_url: https://tracker.example.com/announce/your-passkey/announce
    anonymous: 0
    personal_release: 0
    mod_queue_opt_in: 0
    description_suffix: "Uploaded by MyBot"

feeds:
  - url: https://example.com/rss
    name: my-podcast
    enabled: true                 # optional; must be true to run during poll (default: false)
    last: 5                       # optional; only process N most recent episodes during poll
    pipeline: [download, tag, detect_ads, strip_ads, stage, torrent, seed, upload]
    category_id: 14               # required for upload step (see README.md for full ID tables)
    type_id: 9                    # required for upload step (see README.md for full ID tables)
    cover_image: /config/cover.jpg   # optional; uploaded as torrent cover (1:1, JPEG)
    banner_image: /config/banner.jpg # optional; uploaded as torrent banner (16:9, JPEG)
    tracker:                         # optional per-feed tracker overrides (deep-merged)
      mod_queue_opt_in: 1
      description_suffix: "Per-feed suffix"
    ad_detection:                    # optional per-feed overrides (deep-merged)
      llm:
        model: claude-sonnet-4-20250514
    audiobookshelf:                  # optional per-feed overrides (deep-merged)
      library_id: lib_override
    title_cleaning:                  # optional per-feed title cleaning
      strip_date: true               # remove bracketed dates from titles
      reorder_parts: true            # move (Part N) to front of title
```

**Pipeline steps:**
- `download` — fetch audio file from RSS `audio_url`, save to `output/<podcast>/audio/`
- `tag` — write ID3 metadata (title, artist, date) to the downloaded MP3 file
- `detect_ads` — transcribe audio via local `faster-whisper` (default) or remote whisper server, then classify ad segments via LLM (Anthropic Claude); saves transcript to `output/<podcast>/transcripts/` and reuses it on retry to avoid re-transcribing
- `strip_ads` — remove detected ad segments from audio via ffmpeg with crossfade at splice points; output in `output/<podcast>/cleaned/`
- `stage` — copy audio to `torrent_data_dir/` for seeding; prefers cleaned audio from `strip_ads` if available, falls back to `download`; computes both local and qBittorrent-side paths
- `torrent` — create `.torrent` file via `mktorrent` CLI; extracts `info_hash` via `torf`; output in `output/<podcast>/torrents/`
- `seed` — add torrent to qBittorrent via Web API; sets `save_path` to client-side episode directory
- `upload` — upload `.torrent` + metadata to UNIT3D tracker via web form (login → CSRF token → POST); supports `torrent-cover` and `torrent-banner` image uploads; requires `category_id` and `type_id` in feed config
- `audiobookshelf` — copy audio into `audiobookshelf.dir/<podcast title>/` (using `title_override` if set) and trigger a library scan; prefers cleaned audio from `strip_ads`, falls back to `download`; requires `audiobookshelf` config in `defaults` (url, api_key, library_id, dir); supports per-feed overrides

**Adding a new pipeline step:**
1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`)
2. Register it in `cli.py`: `register_step(YourStep())`
3. Add `your_step` to `pipeline` list in `feeds.yaml` (globally or per-feed)

**Docker:** The final image installs `mktorrent` and `ffmpeg` via `apt-get`. Three volumes: `/config` (YAML config), `/output` (download/processing data), `/torrent-data` (staging dir shared with qBittorrent container).

**Key note on logging:** `cli.py` disables all logging at module import (before dependencies load) to suppress pyenv hashlib errors, then re-enables it in `setup_logging()`. New code that runs before `setup_logging()` will not produce log output.
