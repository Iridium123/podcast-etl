# podcast-etl

A step-based pipeline that ingests podcast RSS feeds, downloads audio, and tracks per-episode processing status for resumability.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Setup

```sh
uv sync
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

This writes `podcast.json` and per-episode JSON files to `output/<podcast-slug>/`.

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

Fetches feeds then runs configured pipeline steps (downloads audio by default). Episodes that have already been processed are skipped unless `--overwrite` is passed.

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

Shows per-episode step completion for all feeds.

### Long-running poll mode

```sh
uv run podcast-etl poll --interval 3600
```

Fetches and processes all feeds on a loop. Shuts down cleanly on SIGTERM/SIGINT.

## Docker

A pre-built image is published to `ghcr.io/iridium123/podcast-etl:latest` on every push to `main`.

### Docker Compose (recommended)

```sh
docker compose up -d
```

Place your `feeds.yaml` in a `config/` directory alongside `docker-compose.yaml`. Output lands in `./output`.

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

Edit `feeds.yaml` to manage feeds and pipeline settings:

```yaml
feeds:
  - url: "https://example.com/feed.xml"
    name: my-podcast       # optional; enables --feed my-podcast
    pipeline:              # optional; overrides settings.pipeline for this feed
      - download
      - tag
settings:
  poll_interval: 3600
  output_dir: ./output
  pipeline:                # default for feeds without their own pipeline
    - download
    - tag
```

Feeds without a `name` or `pipeline` continue to work — both fields are optional.

## Adding a new pipeline step

1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol
2. Register it in `cli.py` with `register_step(YourStep())`
3. Add `your_step` to the `pipeline` list in `feeds.yaml` (globally or per-feed)
