# podcast-etl

A step-based pipeline that ingests podcast RSS feeds, downloads audio, and tracks per-episode processing status for resumability. Optionally packages episodes as torrents, seeds them via qBittorrent, and uploads to a UNIT3D-based tracker for archiving.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- `mktorrent` (system package) — required for the `torrent` step; included in the Docker image

## Setup

```sh
uv sync
cp feeds.yaml.example feeds.yaml
# edit feeds.yaml with your feeds
```

## Usage

### Add a feed

```sh
uv run podcast-etl add "https://example.com/feed.xml"
# with an optional short name and custom pipeline steps
uv run podcast-etl add "https://example.com/feed.xml" --name my-podcast --step download --step tag
```

### Fetch feed metadata

```sh
uv run podcast-etl fetch --all
# by name or URL
uv run podcast-etl fetch --feed my-podcast
uv run podcast-etl fetch --feed "https://example.com/feed.xml"
```

Writes `podcast.json` and per-episode JSON files to `output/<podcast-slug>/`.

### Run the pipeline

```sh
uv run podcast-etl run --all
# by name or URL
uv run podcast-etl run --feed my-podcast
# only run a specific step
uv run podcast-etl run --feed my-podcast --step download
# only process the last N episodes
uv run podcast-etl run --feed my-podcast --last 5
# re-process even if already completed
uv run podcast-etl run --feed my-podcast --overwrite
```

Fetches feeds then runs configured pipeline steps. Episodes that have already completed a step are skipped unless `--overwrite` is passed.

Downloaded audio files are named `YYYY-MM-DD <Episode Title>.mp3` using the episode's release date and a sanitized version of its title. Characters forbidden on Windows/macOS (`/:*?"<>|`) are removed, and `": "` is replaced with `" - "` (e.g. `2024-03-15 Ep 3 - God Picked a Loser.mp3`).

### Reset a feed

```sh
uv run podcast-etl reset --feed my-podcast
# skip confirmation prompt
uv run podcast-etl reset --feed my-podcast --yes
# by URL
uv run podcast-etl reset --feed "https://example.com/feed.xml" --yes
# reset all feeds
uv run podcast-etl reset --all --yes
```

Deletes the feed's entire output directory (podcast.json, episode JSON files, and downloaded audio) so it can be reprocessed from scratch. Prompts for confirmation unless `--yes` / `-y` is passed.

### Check status

```sh
uv run podcast-etl status
# by name or URL
uv run podcast-etl status --feed my-podcast
```

Shows per-episode step completion for all feeds (or a specific feed).

### Long-running poll mode

```sh
uv run podcast-etl poll --interval 3600
```

Fetches and processes all feeds on a loop. Shuts down cleanly on SIGTERM/SIGINT.

## Docker

A pre-built image is published to `ghcr.io/iridium123/podcast-etl:latest` on every push to `main`. The image includes `mktorrent` for torrent creation.

### Docker Compose (recommended)

```sh
docker compose up -d
```

Place your `feeds.yaml` in a `config/` directory alongside `docker-compose.yaml`. Output lands in `./output`.

To use the `stage`/`torrent`/`seed` steps, mount a shared volume between podcast-etl and your qBittorrent container:

```yaml
services:
  podcast-etl:
    image: ghcr.io/iridium123/podcast-etl:latest
    volumes:
      - ./config:/config
      - ./output:/output
      - /path/to/torrent-data:/torrent-data   # shared with qBittorrent
```

### Manual docker run

```sh
docker run -v ./config:/config -v ./output:/output ghcr.io/iridium123/podcast-etl:latest
```

Override the default poll mode to run a one-off command:

```sh
docker run -v ./config:/config -v ./output:/output ghcr.io/iridium123/podcast-etl:latest \
  podcast-etl -c /config/feeds.yaml run --all
```

### Build locally

```sh
docker build -t podcast-etl .
```

## Configuration

Edit `feeds.yaml` to manage feeds and pipeline settings. See `feeds.yaml.example` for a full example.

```yaml
feeds:
  - url: "https://example.com/feed.xml"
    name: my-podcast
    pipeline: [download, tag, stage, torrent, seed, upload]
    client: qbittorrent       # optional; falls back to first configured client
    tracker: unit3d           # optional; falls back to first configured tracker
    category_id: 14           # required for upload step
    type_id: 9                # required for upload step
    cover_image: /config/cover.jpg    # optional
    banner_image: /config/banner.jpg  # optional

settings:
  poll_interval: 3600
  output_dir: ./output
  torrent_data_dir: /torrent-data   # staging dir readable by both app and torrent client
  pipeline: [download, tag]         # default for feeds without their own pipeline

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

## Pipeline Steps

Steps run in the order listed in `pipeline`. Each step requires the previous steps in its chain to have completed.

| Step | Requires | Description |
|------|----------|-------------|
| `download` | — | Fetch audio from RSS `audio_url` → `output/<podcast>/audio/` |
| `tag` | `download` | Write ID3/MP4 metadata (title, artist, date) to the downloaded file |
| `stage` | `download` | Copy audio to `torrent_data_dir/<podcast>/<episode>/` for seeding; computes client-side path |
| `torrent` | `stage` | Create `.torrent` via `mktorrent`; extract `info_hash` via `torf`; output in `output/<podcast>/torrents/` |
| `seed` | `torrent`, `stage` | Add torrent to qBittorrent via Web API with the correct save path |
| `upload` | `torrent` | Upload `.torrent` + metadata to UNIT3D tracker REST API |

## Adding a new pipeline step

1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`)
2. Register it in `cli.py` with `register_step(YourStep())`
3. Add `your_step` to the `pipeline` list in `feeds.yaml` (globally or per-feed)
