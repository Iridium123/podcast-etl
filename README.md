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
```

### Fetch feed metadata

```sh
uv run podcast-etl fetch --all
# or a specific feed
uv run podcast-etl fetch --feed "https://example.com/feed.xml"
```

This writes `podcast.json` and per-episode JSON files to `output/<podcast-slug>/`.

### Run the pipeline

```sh
uv run podcast-etl run --all
# or run a specific step only
uv run podcast-etl run --all --step download
```

Fetches feeds then runs configured pipeline steps (downloads audio by default). Episodes that have already been processed are skipped.

### Check status

```sh
uv run podcast-etl status
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
settings:
  poll_interval: 3600
  output_dir: ./output
  pipeline:
    - download
```

## Adding a new pipeline step

1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol
2. Register it in `cli.py` with `register_step(YourStep())`
3. Add `your_step` to the `pipeline` list in `feeds.yaml`
