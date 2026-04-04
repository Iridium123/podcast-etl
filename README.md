# podcast-etl

A step-based pipeline that ingests podcast RSS feeds, downloads audio, and tracks per-episode processing status for resumability. Optionally packages episodes as torrents, seeds them via qBittorrent, and uploads to a UNIT3D-based tracker for archiving.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- `mktorrent` (system package) — required for the `torrent` step; included in the Docker image
- `ffmpeg` (system package) — required for the `strip_ads` step; included in the Docker image

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
# only process episodes whose title matches a regex
uv run podcast-etl run --feed my-podcast --filter "Part [0-9]+"
# only process episodes from a specific date
uv run podcast-etl run --feed my-podcast --date 2026-03-01
# episodes in a date range (inclusive)
uv run podcast-etl run --feed my-podcast --date 2026-03-01..2026-03-07
# open-ended: everything from a date onward
uv run podcast-etl run --feed my-podcast --date 2026-03-01..
# open-started: everything up to and including a date
uv run podcast-etl run --feed my-podcast --date ..2026-03-07
# re-process even if already completed
uv run podcast-etl run --feed my-podcast --overwrite
# control log verbosity (-v is shorthand for DEBUG)
uv run podcast-etl -v run --all
uv run podcast-etl --log-level WARNING run --all
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

## Web UI

A browser-based interface for managing feeds and monitoring pipeline status. It runs alongside the integrated poll loop so no separate `poll` process is needed.

### Start the web UI

```sh
uv run podcast-etl serve
# custom port
uv run podcast-etl serve --port 9000
```

Open `http://localhost:8000` in your browser.

### Docker with web UI

Map port 8000 when running the container:

```sh
docker run -p 8000:8000 -v ./config:/config -v ./output:/output ghcr.io/iridium123/podcast-etl:latest
```

The Docker image defaults to `serve` so the web UI and poll loop start automatically.

### What the UI provides

- **Config management** — edit feeds and global defaults via structured forms or raw YAML
- **Status dashboard** — per-feed, per-episode step completion at a glance
- **Poll controls** — pause, resume, or trigger an immediate poll run from the browser
- **Log tail** — live log output streamed to the dashboard

All CLI commands (`run`, `fetch`, `reset`, `status`, etc.) still work alongside the web UI and share the same `feeds.yaml` config file.

## Docker

A pre-built image is published to `ghcr.io/iridium123/podcast-etl:latest` on every push to `main`. The image includes `mktorrent` and `ffmpeg`, and exposes port `8000` for the web UI.

### Docker Compose (recommended)

```sh
docker compose up -d
```

Place your `feeds.yaml` in a `config/` directory alongside `docker-compose.yaml`. Output lands in `./output`. The container starts the web UI and poll loop automatically; open `http://localhost:8000` to access the dashboard.

To use the `stage`/`torrent`/`seed` steps, mount a shared volume between podcast-etl and your qBittorrent container:

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
```

### Manual docker run

```sh
docker run -p 8000:8000 -v ./config:/config -v ./output:/output ghcr.io/iridium123/podcast-etl:latest
```

Override the default serve mode to run a one-off command:

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

The `defaults` block contains shared config inherited by all feeds. Any key in `defaults` can appear in a feed entry to override it — overrides are applied via deep merge, so nested keys like `tracker.mod_queue_opt_in` can be set without repeating the whole block.

```yaml
poll_interval: 3600

defaults:
  output_dir: ./output
  torrent_data_dir: /torrent-data   # staging dir readable by both app and torrent client
  pipeline: [download, tag]         # default for feeds without their own pipeline
  blacklist:                        # strings to reject from descriptions (case-insensitive)
    - "John Doe"                    # any description containing this is blanked to null

  title_cleaning:                   # global title cleaning (default off)
    strip_date: false               # remove bracketed dates from episode titles
    reorder_parts: false            # move (Part N) to front of episode title
    prepend_episode_number: false   # prepend itunes:episode number to title
    sanitize: false                 # replace invalid filesystem chars, normalize separators

  ad_detection:
    whisper:
      model: base                   # faster-whisper model (tiny, base, small, medium, large-v3)
      language: en
      # url: http://localhost:8080  # optional: use remote whisper server instead of local
    llm:
      provider: anthropic           # uses ANTHROPIC_API_KEY env var by default
      model: claude-sonnet-4-20250514
    min_confidence: 0.5

  audiobookshelf:
    url: https://abs.example.com
    api_key: your-api-key
    library_id: lib_abc123          # for triggering library scan
    dir: /podcasts                  # root dir on shared volume; podcast title used as subdir

  client:
    url: http://localhost:8080
    username: admin
    password: secret
    save_path: /data                # path to torrent_data_dir as seen by qBittorrent

  tracker:
    url: https://tracker.example.com
    remember_cookie: "eyJpdi..."    # from browser; OR use username+password below
    # username: your-username       # alternative to remember_cookie (no 2FA support)
    # password: your-password
    announce_url: https://tracker.example.com/announce/your-passkey/announce
    anonymous: 0
    personal_release: 0
    mod_queue_opt_in: 0
    description_suffix: "Uploaded by MyBot"  # optional; appended to episode description on tracker
    private: true                   # optional; sets -p flag in mktorrent (default: true)
    source: MyTracker               # optional; sets -s flag in mktorrent

feeds:
  - url: "https://example.com/feed.xml"
    name: my-podcast
    enabled: true                 # optional; must be true to run during poll (default: false)
    last: 5                       # optional; only process N most recent episodes during poll
    episode_filter: "Part [0-9]+" # optional; regex — only process episodes whose title matches
    pipeline: [download, tag, detect_ads, strip_ads, stage, torrent, seed, upload]
    category_id: 14               # required for upload step (see ID tables below)
    type_id: 9                    # required for upload step (see ID tables below)
    cover_image: /config/cover.jpg    # optional; uploaded as torrent cover (1:1 aspect ratio, JPEG)
    banner_image: /config/banner.jpg  # optional; uploaded as torrent banner (16:9 aspect ratio, JPEG)
    tracker:                          # optional per-feed tracker overrides (deep-merged)
      mod_queue_opt_in: 1
      description_suffix: "Per-feed suffix"
    ad_detection:                     # optional per-feed overrides (deep-merged)
      llm:
        model: claude-sonnet-4-20250514
    title_cleaning:                   # optional per-feed title cleaning
      strip_date: true                # remove bracketed dates from titles
      reorder_parts: true             # move (Part N) to front of title
      prepend_episode_number: true    # prepend itunes:episode number to title
      sanitize: true                  # replace invalid filesystem chars, normalize separators
```

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

### Title Cleaning

Optional rules to clean episode titles at feed parse time. All rules are off by default and can be enabled globally in `defaults.title_cleaning` or per-feed in `title_cleaning`. Per-feed values override global values.

**Note:** Enabling or disabling title cleaning rules changes episode slugs and filenames. Step status is preserved via GUID, but enabling a rule mid-stream will cause episodes to be re-processed under the new filename. Use `reset` to start fresh if needed.

**`strip_date`** — Removes dates wrapped in brackets `()`, `[]`, or `{}` from episode titles. Useful when the pipeline already prepends dates to filenames and upload titles. Supported formats: `(3_19_26)`, `(03/22/2026)`, `(2026-03-22)`, `(March 22, 2026)`, etc. Bare dates without brackets are not affected.

**`reorder_parts`** — Reorders part indicators like `(Part 1)`, `(Pt. 2)`, `[Pt 3]` so multi-part episodes released on the same day sort correctly. Uses same-day sibling episodes from the RSS feed to find a common series prefix and inserts the part number after it. For example, `"World War II - D-Day (Part 3)"` becomes `"World War II - Part 3 - D-Day"`. If the common prefix is too short (< 5 chars), the part is prepended instead. Only triggers when multiple same-day episodes have part indicators; solo episodes are left unchanged. Only matches parts inside brackets; bare `Part 1` is not affected.

**`prepend_episode_number`** — Prepends the `itunes:episode` number to the episode title in the format `{number} - {title}`. For example, episode 123 with title `"Rise of the Mongols"` becomes `"123 - Rise of the Mongols"`. Runs after `reorder_parts`, so with parts reordered: `"123 - Part 3 - Rise of the Mongols"`. Only applies when the RSS entry has a numeric `itunes:episode` value; non-numeric values (e.g. `"bonus"`) are ignored.

**`sanitize`** — Replaces characters that are invalid on any of macOS, Windows, or Linux filesystems (`\ / : * ? " < > |` and control characters) with `_`, then collapses any sequence of underscores, whitespace, and dashes into a single ` - `. Cleans up double-dash artifacts from other rules (e.g. `"Show - - Part 3"` → `"Show - Part 3"`) and makes titles like `"Title: Subtitle"` filesystem-safe (`"Title - Subtitle"`). Runs after all other title cleaning rules.

To get the `remember_cookie` value: log in to the tracker in your browser, then open DevTools → Application → Cookies → copy the value of `remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d`. This works with 2FA-enabled accounts. The cookie is long-lived but will eventually expire, requiring a fresh copy.

## Pipeline Steps

Steps run in the order listed in `pipeline`. Each step requires the previous steps in its chain to have completed.

| Step | Requires | Description |
|------|----------|-------------|
| `download` | — | Fetch audio from RSS `audio_url` → `output/<podcast>/audio/` |
| `tag` | `download` | Write ID3 metadata (title, artist, date, track number) to the downloaded MP3 file; writes track number from `itunes:episode` when available; embeds episode artwork as album art when available (falls back to feed image) |
| `detect_ads` | `download` | Transcribe audio via local faster-whisper (or remote server), classify ad segments via LLM; saves transcript and reuses on retry |
| `strip_ads` | `detect_ads`, `download` | Remove detected ad segments from audio via ffmpeg; output in `output/<podcast>/cleaned/` |
| `stage` | `download` (or `strip_ads`) | Copy audio to `torrent_data_dir/` for seeding; prefers cleaned audio if available |
| `torrent` | `stage` | Create `.torrent` via `mktorrent`; extract `info_hash` via `torf`; output in `output/<podcast>/torrents/` |
| `seed` | `torrent`, `stage` | Add torrent to qBittorrent via Web API with the correct save path |
| `upload` | `torrent` | Upload `.torrent` + metadata to UNIT3D tracker via web form; uses episode artwork as cover when available, falls back to `cover_image` config; supports banner images |
| `audiobookshelf` | `download` (or `strip_ads`) | Copy audio into `audiobookshelf.dir/<podcast title>/` and trigger library scan; uses `title_override` if set |

## Adding a new pipeline step

1. Create `src/podcast_etl/steps/your_step.py` implementing the `Step` protocol (`name: str`, `process(episode, context) -> StepResult`)
2. Register it in `cli.py` with `register_step(YourStep())`
3. Add `your_step` to the `pipeline` list in `feeds.yaml` (globally or per-feed)
