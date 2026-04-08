# podcast-etl

A step-based pipeline that ingests podcast RSS feeds, downloads audio, tags MP3s, detects and strips ads, creates torrents, and uploads to a UNIT3D tracker. Manage everything through a browser-based web UI or a full-featured CLI.

## Quick Start (Docker)

```sh
docker compose up -d
```

Open `http://localhost:8000` to access the web UI. Place your `feeds.yaml` in a `config/` directory alongside `docker-compose.yaml`.

For a one-off CLI command instead:

```sh
docker run -v ./config:/config -v ./output:/output ghcr.io/iridium123/podcast-etl:latest \
  podcast-etl -c /config/feeds.yaml run --all
```

## Quick Start (Local)

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and optionally `mktorrent` and `ffmpeg` for the torrent and ad-stripping steps.

```sh
uv sync
cp feeds.yaml.example feeds.yaml   # edit with your feeds
uv run podcast-etl serve            # web UI + poll loop on http://localhost:8000
```

## Web UI

The web UI is a browser-based interface for managing feeds and monitoring the pipeline. It runs a built-in poll loop, so no separate `poll` process is needed.

```sh
uv run podcast-etl serve                 # default port 8000
uv run podcast-etl serve --port 9000     # custom port
```

**Dashboard** (`/`) -- summary counts (active feeds, episodes processed/pending), poll status with pause/resume/run-now controls, and a live log tail.

**Feeds** (`/feeds`) -- list all configured feeds, add new ones, and drill into per-feed detail pages with episode step-completion grids and config editing.

**Feed config editing** (`/feeds/{name}/edit`) -- structured form controls for common fields (name, URL, enabled, pipeline steps, title cleaning, category/type IDs) plus a raw YAML editor for advanced overrides (tracker, ad detection, audiobookshelf). Changes are validated and diffed before saving.

**Defaults** (`/defaults`) -- edit global settings that all feeds inherit.

**Resolved config preview** -- each feed detail page shows the final merged config after `deep_merge(defaults, feed)`, color-coded to show which values come from the feed vs. defaults.

All CLI commands still work alongside the web UI and share the same `feeds.yaml`.

## CLI Reference

### Global options

```sh
uv run podcast-etl -c /path/to/feeds.yaml ...   # custom config path
uv run podcast-etl -v ...                        # verbose (DEBUG) logging
uv run podcast-etl --log-level WARNING ...       # set log level
```

### Add a feed

```sh
uv run podcast-etl add "https://example.com/feed.xml"
uv run podcast-etl add "https://example.com/feed.xml" --name my-podcast --step download --step tag
```

### Fetch feed metadata

```sh
uv run podcast-etl fetch --all
uv run podcast-etl fetch --feed my-podcast
```

### Run the pipeline

```sh
uv run podcast-etl run --all
uv run podcast-etl run --feed my-podcast
uv run podcast-etl run --feed my-podcast --step download       # single step
uv run podcast-etl run --feed my-podcast --last 5              # last N episodes
uv run podcast-etl run --feed my-podcast --filter "Part [0-9]+"  # title regex
uv run podcast-etl run --feed my-podcast --date 2026-03-01     # single date
uv run podcast-etl run --feed my-podcast --date 2026-03-01..2026-03-07  # date range
uv run podcast-etl run --feed my-podcast --date 2026-03-01..   # from date onward
uv run podcast-etl run --feed my-podcast --date ..2026-03-07   # up to date
uv run podcast-etl run --feed my-podcast --overwrite           # re-process completed
```

The `--feed` flag accepts either a feed name or a full URL.

### Reset a feed

```sh
uv run podcast-etl reset --feed my-podcast --yes
uv run podcast-etl reset --all --yes
```

Deletes the feed's output directory so it can be reprocessed from scratch. Prompts for confirmation unless `--yes` is passed.

### Delete a feed

```sh
uv run podcast-etl delete my-podcast
# skip confirmation prompt
uv run podcast-etl delete my-podcast --yes
# by URL
uv run podcast-etl delete "https://example.com/feed.xml" --yes
```

Removes the feed from `feeds.yaml` and deletes its output directory. Prompts for confirmation unless `--yes` / `-y` is passed.

### Check status

```sh
uv run podcast-etl status
uv run podcast-etl status --feed my-podcast
```

### Poll mode (without web UI)

```sh
uv run podcast-etl poll --interval 3600
```

Fetches and processes all enabled feeds on a loop. The `serve` command is preferred since it includes the poll loop plus the web UI.

## Configuration

All configuration lives in `feeds.yaml`. The web UI reads and writes this file directly -- there is no database.

The `defaults` block contains shared config inherited by all feeds. Any key in `defaults` can appear in a feed entry to override it via deep merge, so you only need to specify the keys that differ.

```yaml
poll_interval: 3600

defaults:
  output_dir: ./output
  torrent_data_dir: /torrent-data
  pipeline: [download, tag]
  blacklist:
    - "John Doe"

  title_cleaning:
    strip_date: false
    reorder_parts: false
    prepend_episode_number: false
    sanitize: false

  ad_detection:
    whisper:
      model: base
      language: en
      # url: http://localhost:8080   # optional: remote whisper server
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
    private: true
    source: MyTracker

feeds:
  - url: "https://example.com/feed.xml"
    name: my-podcast
    enabled: true
    last: 5
    episode_filter: "Part [0-9]+"
    pipeline: [download, tag, detect_ads, strip_ads, stage, torrent, seed, upload]
    category_id: 14
    type_id: 9
    cover_image: /config/cover.jpg
    banner_image: /config/banner.jpg
    tracker:
      mod_queue_opt_in: 1
    ad_detection:
      llm:
        model: claude-sonnet-4-20250514
    title_cleaning:
      strip_date: true
      reorder_parts: true
      prepend_episode_number: true
      sanitize: true
```

Key config behaviors:

- **`enabled`** defaults to `false`. Only `true` feeds are processed during poll/serve. Explicit `--feed` runs ignore this flag.
- **`last`** and **`episode_filter`** limit which episodes are processed during poll. They can also appear in `defaults`.
- **Per-feed overrides** are deep-merged with `defaults`, so `tracker: {mod_queue_opt_in: 1}` only overrides that one key.

### Title Cleaning

Optional rules applied at feed parse time. All off by default; enable globally or per-feed.

- **`strip_date`** -- removes dates in brackets: `(3/19/26)`, `[2026-03-22]`, `(March 22, 2026)`, etc.
- **`reorder_parts`** -- moves `(Part N)` after the common series prefix so multi-part same-day episodes sort correctly.
- **`prepend_episode_number`** -- prepends `itunes:episode` number: `"Rise of the Mongols"` becomes `"123 - Rise of the Mongols"`.
- **`sanitize`** -- replaces filesystem-invalid characters with `_`, collapses separator sequences to ` - `.

Changing title cleaning rules changes episode slugs and filenames. Use `reset` to start fresh if enabling mid-stream.

### Tracker Cookie

To get the `remember_cookie` value: log in to the tracker in your browser, open DevTools, go to Application then Cookies, and copy the value of `remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d`. This works with 2FA-enabled accounts.

## Pipeline Steps

Steps run in the order listed in `pipeline`. Each step's result is stored per-episode, so re-runs skip completed work.

| Step | Requires | Description |
|------|----------|-------------|
| `download` | -- | Fetch audio from RSS `audio_url` |
| `tag` | `download` | Write ID3 metadata (title, artist, date, TRCK track number, APIC album art) |
| `detect_ads` | `download` | Transcribe via faster-whisper, classify ad segments via LLM |
| `strip_ads` | `detect_ads` | Remove ad segments via ffmpeg with crossfade |
| `stage` | `download` | Copy audio to `torrent_data_dir/`; prefers cleaned audio if available |
| `torrent` | `stage` | Create `.torrent` via `mktorrent` |
| `seed` | `torrent` | Add torrent to qBittorrent via Web API |
| `upload` | `torrent` | Upload `.torrent` + metadata to UNIT3D tracker |
| `audiobookshelf` | `download` | Copy audio to Audiobookshelf library and trigger scan |

## Docker

A pre-built image is published to `ghcr.io/iridium123/podcast-etl:latest` on every push to `main`. It includes `mktorrent` and `ffmpeg` and defaults to `serve` mode (web UI + poll loop on port 8000).

### Docker Compose (recommended)

```yaml
services:
  podcast-etl:
    image: ghcr.io/iridium123/podcast-etl:latest
    ports:
      - "8000:8000"
    volumes:
      - ./config:/config
      - ./output:/output
      - /path/to/torrent-data:/torrent-data   # shared with qBittorrent
    environment:
      - TZ=Etc/UTC
```

### Build locally

```sh
docker build -t podcast-etl .
```

### Run tests in Docker

```sh
docker build --target test -t podcast-etl-test . && docker run --rm podcast-etl-test
```

## Adding a Pipeline Step

1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`)
2. Register it in `service.py` with `register_step(YourStep())`
3. Add `your_step` to the `pipeline` list in `feeds.yaml`

<details>
<summary>Category IDs</summary>

| ID | Category |
|----|----------|
| 3 | Alternative and Unexplained |
| 4 | Arts and Culture |
| 5 | Advice and Relationships |
| 6 | Comedy |
| 7 | Education and Learning |
| 8 | Environment and Nature |
| 9 | Drama and Fiction |
| 10 | Film and TV |
| 11 | Fitness and Health |
| 12 | Food and Drink |
| 13 | Horror and Science Fiction |
| 14 | History |
| 15 | Hobbies, Travel and Leisure |
| 16 | Kids and Family |
| 17 | Money and Business |
| 18 | Music |
| 19 | News and Politics |
| 20 | Pop Culture and Fashion |
| 21 | Religion and Spirituality |
| 22 | Self-Help |
| 23 | Serious Discussion and Debate |
| 24 | Science and Engineering |
| 25 | Social Issues and Journalism |
| 26 | Sport |
| 27 | Technology and Computing |
| 28 | True Crime |
| 29 | The Podcast |
| 31 | Human Interest |
| 32 | Warfare and Military |
| 33 | Video Games |
| 34 | Tabletop Games |
| 35 | Social Science |
| 36 | Survival and Adventure |

</details>

<details>
<summary>Type IDs</summary>

| ID | Type |
|----|------|
| 7 | Audio - Patreon |
| 8 | Video - Patreon |
| 9 | Audio - Free |
| 10 | Other |
| 11 | Video - Free |
| 12 | Audio - Nebula |
| 13 | Video - Nebula |
| 14 | Audio - Premium |
| 15 | Video - Premium |

</details>
