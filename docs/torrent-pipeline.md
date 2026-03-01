# Plan: Torrent Packaging, Seeding, and Tracker Upload Pipeline

## Context

To archive podcast episodes, we need to package them as torrents, add them to a local torrent client for seeding, and upload them to a UNIT3D-based tracker. This extends the existing step-based pipeline by adding four new steps: `stage`, `torrent`, `seed`, and `upload`. Each step is a natural checkpoint using the existing `StepStatus` persistence mechanism — no new checkpointing infrastructure is needed.

A key constraint: the torrent client (qBittorrent) must be able to access the seeded files, but it runs in a separate Docker container with its own volume mount. The `torrent_data_dir` setting provides the path **as seen by this app**, and a separate `save_path` in the client config provides the same path **as seen by qBittorrent** (so the client's save-path is set correctly).

**Note on torrent creation tool:** We'll use `mktorrent` (a C CLI tool called via subprocess) for creating .torrent files, as it's well-established and battle-tested. For reading back the created `.torrent` to extract the `info_hash`, we'll use the `torf` library in read-only mode. If `torf` is undesirable, this can be replaced with `bencode3` + `hashlib.sha1`.

## New Configuration (feeds.yaml additions)

```yaml
settings:
  output_dir: ./output                # existing — download/processing data
  torrent_data_dir: /torrent-data     # new — staging dir accessible by both apps

  clients:
    qbittorrent:
      url: http://localhost:8080
      username: admin
      password: secret
      save_path: /data               # path to torrent_data_dir as seen by qBittorrent

  trackers:
    unit3d:
      url: https://tracker.example.com
      api_key: your-key
      announce_url: https://tracker.example.com/announce/your-passkey/announce
      # no category_id or type_id here — feeds must configure these explicitly
      anonymous: 0
      personal_release: 0
      mod_queue_opt_in: 0
```

Per-feed config specifies which client/tracker to use. `category_id` and `type_id` are **required** per feed when using the upload step:
```yaml
feeds:
  - url: ...
    pipeline: [download, tag, stage, torrent, seed, upload]
    client: qbittorrent              # optional; falls back to first configured client
    tracker: unit3d                  # optional; falls back to first configured tracker
    category_id: 14                  # required per feed for upload step
    type_id: 9                       # required per feed for upload step
    cover_image: /config/cover.jpg   # optional; same image used for all episodes in this feed
    banner_image: /config/banner.jpg # optional; same image used for all episodes in this feed
```

## Directory Layout

```
output/<podcast-slug>/
  audio/                          # existing — downloaded audio files (untouched)
  torrents/                       # new — .torrent files, one per episode
    <episode-slug>.torrent

/torrent-data/<podcast-slug>/<episode-slug>/
  2024-01-15 Episode Title.mp3   # same filename as audio/; seeded by qBittorrent
```

## New Steps

### 1. `stage` (`src/podcast_etl/steps/stage.py`)

Copies the episode audio file from `output_dir` to `torrent_data_dir/<podcast-slug>/<episode-slug>/`. The filename is taken directly from the download step result — no renaming.

- Source: `episode.status["download"].result["path"]` (relative to `podcast_dir`, e.g. `audio/2024-01-15 Episode Title.mp3`)
- Destination: `torrent_data_dir / podcast.slug / episode.slug / <same-filename>`
- Uses `shutil.copy2` to preserve metadata
- Idempotent: skips copy if destination file already exists
- Returns:
  - `local_path`: absolute path to the copied file (as seen by this app)
  - `client_path`: same path but rebased onto `save_path` (as seen by qBittorrent)
  - `episode_dir`: parent directory of `local_path`

### 2. `torrent` (`src/podcast_etl/steps/torrent.py`)

Creates a `.torrent` file targeting the **specific audio file** (not the directory) using `mktorrent`. Reads the result back with `torf` to extract `info_hash`.

- Source: `stage` result's `local_path` (the audio file itself)
- Output: `output/<podcast>/torrents/<episode-slug>.torrent`
- `mktorrent` call: `mktorrent -a <announce_url> -o <output_path> -c "<title> — <podcast>" <local_path>`
- Private flag `-p` included if tracker requires it (configurable)
- Idempotent: skips creation if `.torrent` file already exists
- Returns: `{"torrent_path": "...", "info_hash": "<sha1-hex>"}`

### 3. `seed` (`src/podcast_etl/steps/seed.py`)

Adds the torrent to qBittorrent via its Web API using `httpx`. Sets the save path to the **client-side episode directory** so qBittorrent seeds without re-downloading.

- Authenticates via cookie session (`POST /api/v2/auth/login`)
- Idempotent: checks if hash already in client (`GET /api/v2/torrents/info?hashes=<hash>`) before adding
- Adds torrent: `POST /api/v2/torrents/add` with .torrent file bytes and `savepath` = `client_path`'s parent dir
- Returns: `{"client": "qbittorrent", "hash": "<info_hash>"}`

### 4. `upload` (`src/podcast_etl/steps/upload.py`)

Uploads the torrent and metadata to a UNIT3D tracker via its REST API. `category_id` and `type_id` are read from feed config and are required.

API: `POST <tracker_url>/api/torrents/upload` with `Authorization: Bearer <api_key>`

Multipart form fields:
- `torrent`: the .torrent file
- `name`: `"<Podcast Title> - <Episode Title> (<YYYY-MM-DD>)"`
- `description`: episode description from RSS
- `category_id`: **required**, from feed config
- `type_id`: **required**, from feed config
- `imdb`, `tvdb`, `tmdb`, `mal`, `igdb`: hardcoded `"0"` for podcasts
- `stream`, `sd`, `anonymous`, `personal_release`, `mod_queue_opt_in`: from tracker config (default `"0"`)
- `cover_image` / `banner_image`: from feed config if provided

Idempotent: if `torrent_id` already present in step result, skip upload.
Returns: `{"tracker": "unit3d", "torrent_id": 123, "url": "..."}`

## Client & Tracker Protocols (extensibility)

**`src/podcast_etl/clients/__init__.py`**:
```python
class TorrentClient(Protocol):
    def add_torrent(self, torrent_path: Path, save_path: str) -> str: ...  # returns info_hash
    def has_torrent(self, info_hash: str) -> bool: ...
```
`QBittorrentClient` in `src/podcast_etl/clients/qbittorrent.py` implements it.

**`src/podcast_etl/trackers/__init__.py`**:
```python
class Tracker(Protocol):
    def upload(self, torrent_path: Path, episode: Episode, podcast: Podcast, feed_config: dict) -> dict: ...
```
`ModifiedUnit3dTracker` in `src/podcast_etl/trackers/unit3d.py` implements it.

## Docker Changes

**`Dockerfile`** — install `mktorrent` in the final stage:

```dockerfile
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

COPY src/ src/
RUN uv sync --no-dev

FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    mktorrent \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app
VOLUME ["/config", "/output", "/torrent-data"]

CMD ["podcast-etl", "-c", "/config/feeds.yaml", "poll"]
```

**`docker-compose.yaml`** — add torrent-data volume:

```yaml
services:
  podcast-etl:
    image: ghcr.io/iridium123/podcast-etl:latest
    restart: unless-stopped
    volumes:
      - ./config:/config
      - ./output:/output
      - /path/to/torrent-data:/torrent-data   # shared with qBittorrent container
    environment:
      - TZ=America/Los_Angeles
```

`torrent_data_dir` in `feeds.yaml` → `/torrent-data`. qBittorrent's `save_path` → whatever path qBittorrent sees for the same host directory.

## New Dependencies

- `torf` (Python) — read .torrent files to extract `info_hash`
- `mktorrent` (system) — installed in Docker image via `apt-get`
- `httpx` — already in use

## Files to Create

- `src/podcast_etl/steps/stage.py`
- `src/podcast_etl/steps/torrent.py`
- `src/podcast_etl/steps/seed.py`
- `src/podcast_etl/steps/upload.py`
- `src/podcast_etl/clients/__init__.py`
- `src/podcast_etl/clients/qbittorrent.py`
- `src/podcast_etl/trackers/__init__.py`
- `src/podcast_etl/trackers/unit3d.py`
- `tests/test_stage_step.py`
- `tests/test_torrent_step.py`
- `tests/test_seed_step.py`
- `tests/test_upload_step.py`

## Files to Modify

- `src/podcast_etl/cli.py` — register the four new steps
- `pyproject.toml` — add `torf` dependency
- `feeds.yaml.example` — add `clients`/`trackers` config, new step names, image fields
- `Dockerfile` — install `mktorrent`; add `/torrent-data` to `VOLUME`
- `docker-compose.yaml` — add torrent-data volume mount
- `README.md` + `CLAUDE.md` — document new steps, config, architecture

---

## Implementation Todo List

### Phase 1: Infrastructure & Dependencies ✅
- [x] Add `torf` to `pyproject.toml` and run `uv sync`
- [x] Update `Dockerfile`: add `apt-get install mktorrent` layer, add `/torrent-data` to `VOLUME`
- [x] Update `docker-compose.yaml`: add `/torrent-data` volume mount
- [x] Create `src/podcast_etl/clients/` package with `__init__.py` (TorrentClient protocol)
- [x] Create `src/podcast_etl/trackers/` package with `__init__.py` (Tracker protocol)

### Phase 2: qBittorrent Client ✅
- [x] Implement `QBittorrentClient` in `src/podcast_etl/clients/qbittorrent.py`
  - `_login()` — POST `/api/v2/auth/login`, store session cookie
  - `has_torrent(info_hash)` — GET `/api/v2/torrents/info?hashes=<hash>`, return bool
  - `add_torrent(torrent_path, save_path)` — POST `/api/v2/torrents/add` with file + savepath
- [x] Write `tests/test_qbittorrent_client.py` (mock httpx; test login, add, dedup)

### Phase 3: UNIT3D Tracker Client ✅
- [x] Implement `ModifiedUnit3dTracker` in `src/podcast_etl/trackers/unit3d.py`
  - `upload(torrent_path, episode, podcast, feed_config)` — POST to `/api/torrents/upload`
  - Build multipart form from episode metadata + feed config fields
  - Handle `cover_image` / `banner_image` if provided in feed config
- [x] Write `tests/test_unit3d_tracker.py` (mock httpx; test field construction, image upload, error cases)

### Phase 4: `stage` Step ✅
- [x] Add `feed_config` field to `PipelineContext` in `pipeline.py` (backward-compatible default)
- [x] Pass `feed_config` into `PipelineContext` in `cli.py:run_pipeline`
- [x] Implement `StageStep` in `src/podcast_etl/steps/stage.py`
  - Resolve source path from `episode.status["download"].result["path"]`
  - Compute destination under `torrent_data_dir / podcast.slug / episode.slug /`
  - Compute `client_path` by rebasing onto qBittorrent `save_path`
  - Skip if destination file already exists
- [x] Write `tests/test_stage_step.py` (mock filesystem; test copy, idempotency, missing download status)

### Phase 5: `torrent` Step ✅
- [x] Implement `TorrentStep` in `src/podcast_etl/steps/torrent.py`
  - Resolve audio file path from stage result
  - Create `output/<podcast>/torrents/` directory
  - Call `mktorrent` via `subprocess.run` targeting the audio file
  - Read created `.torrent` with `torf.Torrent.read()` to extract `info_hash`
  - Skip if `.torrent` already exists
- [x] Write `tests/test_torrent_step.py` (mock `subprocess.run`; mock `torf.Torrent.read`; test flags, idempotency, subprocess failure)

### Phase 6: `seed` Step
- [ ] Implement `SeedStep` in `src/podcast_etl/steps/seed.py`
  - Instantiate `QBittorrentClient` from `context.config["settings"]["clients"]["qbittorrent"]`
  - Use `info_hash` from torrent step result
  - Skip if `has_torrent()` returns True
  - Call `add_torrent()` with .torrent path and client-side episode dir
- [ ] Write `tests/test_seed_step.py` (mock QBittorrentClient; test skip-if-exists, success, client error)

### Phase 7: `upload` Step
- [ ] Implement `UploadStep` in `src/podcast_etl/steps/upload.py`
  - Instantiate `ModifiedUnit3dTracker` from tracker config
  - Validate `category_id` and `type_id` are present in feed config (raise clear error if missing)
  - Skip if `torrent_id` already in step status result
  - Call `tracker.upload()`
- [ ] Write `tests/test_upload_step.py` (mock ModifiedUnit3dTracker; test skip, missing category_id error, success)

### Phase 8: Registration & Config
- [ ] Register all four steps in `src/podcast_etl/cli.py`
- [ ] Update `feeds.yaml.example` with full clients/trackers config blocks and example feed pipeline
- [ ] Update `README.md` and `CLAUDE.md` with new steps, config fields, and Docker setup

### Phase 9: Verification
- [ ] Run `uv run pytest tests/ -v` — all tests pass
- [ ] Integration test `stage` step on a real episode (verify file appears in torrent_data_dir with correct name)
- [ ] Integration test `torrent` step (verify .torrent created and info_hash returned)
- [ ] Integration test `seed` step (verify torrent visible in qBittorrent)
- [ ] Integration test `upload` step (verify torrent appears on tracker)
