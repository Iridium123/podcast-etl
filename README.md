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
uv run podcast-etl serve                 # default port 8000, binds 127.0.0.1
uv run podcast-etl serve --port 9000     # custom port
uv run podcast-etl serve --host 0.0.0.0  # opt in to LAN access (see warning below)
```

> **Security:** the web UI has **no authentication**. Anyone who can reach the
> port can read your `feeds.yaml` (including tracker cookies, qBittorrent
> password, and API keys), modify config, and trigger pipeline runs. The
> defaults bind to host loopback only — both the native `serve` command and
> the recommended Docker compose snippet below. Only expose the UI on a
> trusted network or behind a reverse proxy that adds authentication.

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
      prompt: default          # name of a file in prompts/<name>.txt
    min_confidence: 0.5

  audiobookshelf:
    dir: /podcasts             # required: library dir to copy audio into
    # Optional: set all three to trigger an API scan after each copy.
    # Omit them if Audiobookshelf's folder watcher picks up new files
    # (it does for local mounts; it is unreliable on network/FUSE mounts
    # such as Unraid /mnt/user shares).
    url: https://abs.example.com
    api_key: your-api-key
    library_id: lib_abc123

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
- **`source`** defaults to `rss`. Set `source: unit3d` for torrent-source feeds (see below).
- **Per-feed overrides** are deep-merged with `defaults`, so `tracker: {mod_queue_opt_in: 1}` only overrides that one key.

### Torrent-Source Feeds (UNIT3D)

Besides normal podcast RSS, a feed can ingest from a UNIT3D tracker RSS whose
enclosures are `.torrent` files. Audio arrives via qBittorrent; once on disk,
episodes flow through the regular pipeline (tag, strip ads, Audiobookshelf,
re-upload to a *different* tracker, any combination).

```yaml
feeds:
  - url: https://tracker.example.com/rss/98.yourrsskey
    name: archived-show
    source: unit3d
    enabled: true
    episode_filter: "^The Daily - "     # applies to torrent titles
    pipeline: [tag, audiobookshelf]     # episode steps only; fetching is implied by source
```

How it works, per poll cycle:

1. Each RSS entry becomes a torrent item (visible on the feed detail page).
   The `.torrent` blob is downloaded and kept under `torrent_files/`, and the
   torrent is added to qBittorrent (the `client.save_path` config).
2. While qBittorrent downloads, the item shows as "downloading" and is
   re-checked each cycle.
3. On completion, every MP3 inside the torrent becomes an episode: metadata
   from ID3 tags (dates fall back to a date parsed from the filename, e.g.
   `Show - 2025.10.02 - Title.mp3`, then the RSS item, then file mtime;
   titles fall back to filename/RSS), audio copied into the
   podcast's `audio/` dir, then the configured pipeline runs as usual.

Operational notes:

- The feed's `pipeline` must **not** include `download` (validation error —
  audio arrives via the torrent client). Since the defaults pipeline usually
  starts with `download`, give unit3d feeds their own `pipeline` override.
- The ETL process reads downloaded files by mapping the client's
  `client.save_path` onto its own `torrent_data_dir` — the two must be mounts
  of the same volume (same convention the `stage`/`seed` steps already use).
- **Torrents you already seed** (added to qBittorrent before the feed) keep
  their original save location, which is typically outside `client.save_path`;
  their paths pass through unmapped. If the ETL container can't read such a
  path, the item is skipped with an error naming the missing path — mount
  that location into the container (at the same path qBittorrent reports)
  and it will pick up on the next cycle.
- **Retry a dead torrent** by deleting it (with data) in qBittorrent — the
  next poll re-adds it from the stored blob and starts over.
- **Abandon a torrent** by excluding it with `episode_filter` (or wait for it
  to leave the tracker feed). Torrents that disappear from the feed are
  abandoned automatically; already-fetched episodes are kept and finished.
- `episode_filter`/`last` select *torrents*; once a torrent is included all
  its MP3s become episodes. A narrowed run (`last`/`episode_filter` set)
  pipelines only the selected torrents' episodes, so `--overwrite` can't
  re-process the back-catalog. In-flight torrents that fall out of the `last`
  window still finish. A torrent with no MP3s is marked fetched with 0
  episodes.
- `run --step X` re-runs just that step over existing episodes — it never
  downloads blobs or touches the torrent client.
- Don't re-`upload` to the same tracker the feed came from — that would
  duplicate the torrent. Configure a different `tracker` if uploading.

### Title Cleaning

Optional rules applied at feed parse time. All off by default; enable globally or per-feed.

- **`strip_date`** -- removes dates from titles, bracketed or bare: `(3/19/26)`, `[2026-03-22]`, `(March 22, 2026)`, and `Show - 2025.10.02 - Ep` becomes `Show - Ep`. Numeric dates accept `/`, `.`, `_`, `-` separators, month-first or year-first -- exactly the formats the torrent fetch phase parses from filenames. Tokens embedded in words (`v2.10.24`, `320kbps`) never match, and only real calendar dates are stripped (`2026-15-43`, `[1080/60/2]` stay) -- the identical validation the filename date inference uses.
- **`reorder_parts`** -- moves `(Part N)` after the common series prefix so multi-part same-day episodes sort correctly.
- **`prepend_episode_number`** -- prepends `itunes:episode` number: `"Rise of the Mongols"` becomes `"123 - Rise of the Mongols"`.
- **`sanitize`** -- replaces filesystem-invalid characters with `_`, collapses separator sequences to ` - `.

Changing title cleaning rules changes episode slugs and filenames. Use `reset` to start fresh if enabling mid-stream.

### Ad Detection

The `detect_ads` step transcribes the episode (local faster-whisper or a remote whisper server) and classifies ad segments with an LLM. The classification prompt lives in `prompts/<name>.txt` at the project root; select one with `ad_detection.llm.prompt` (default `default`). An unknown prompt name fails config validation early.

Detected ad segments are written as a first-class artifact to `output/<slug>/labels/<stem>.json` (parallel to `output/<slug>/transcripts/`), recording the segments, audio duration, and provenance (whisper/LLM config + annotator). The `strip_ads` step reads segments from this labels file. The episode JSON's `detect_ads` result records the `labels_path` rather than embedding segments inline.

If you have older episode data with segments embedded in the episode JSON, migrate it once with:

```sh
uv run python scripts/migrate_labels.py --output-dir output/    # add --dry-run to preview
```

The script ships in the Docker image too, so you can run it against the live `/output` volume in your deployment:

```sh
docker compose run --rm podcast-etl python scripts/migrate_labels.py --output-dir /output --dry-run
```

### Tracker Cookie

To get the `remember_cookie` value: log in to the tracker in your browser, open DevTools, go to Application then Cookies, and copy the value of `remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d`. This works with 2FA-enabled accounts.

## Ad Detection Eval

The eval harness measures ad-detection quality against gold-standard labels and compares model/prompt/whisper combinations in a single run. It is a development tool intended to be run from the repo root.

### Concepts

- A **dataset** is a directory of `Labels` files laid out as `<root>/<podcast-slug>/labels/<stem>.json`. Production's own `output/` directory is a valid dataset — it uses the same layout that `detect_ads` writes. Any two datasets (predictions vs gold) are matched by the `EpisodeRef` embedded in each file, not by filename.
- **Gold** annotations are Labels files whose `provenance.annotator` is `"human"`. The scorer filters by annotator to prevent circular eval (model-bootstrapped labels are not scored against themselves by default).
- **Predictions** are generated by `eval label`, which runs production's classify pipeline and writes Labels files into a named dataset directory.

### Workflow

```sh
# 1. Bootstrap a gold annotation from the production output for an episode
uv run podcast-etl eval annotate my-podcast 2024-01-15-episode.json --blank
# or copy from production labels as a starting point:
uv run podcast-etl eval annotate my-podcast 2024-01-15-episode.json

# 2. Edit the Labels file to correct segments, then set provenance.annotator = "human"

# 3. Validate the gold dataset for consistency (non-zero exit on errors)
uv run podcast-etl eval validate gold

# 4. Generate predictions with a specific config
uv run podcast-etl eval label my-predictions --podcast my-podcast \
  --config path/to/ad_config.yaml

# 5. Score predictions against gold
uv run podcast-etl eval score --predictions my-predictions --gold gold

# 6. Or run the whole matrix from a config file
cp eval/eval_config.yaml.example eval/eval_config.yaml  # edit configs
uv run podcast-etl eval run
# or equivalently:
uv run python eval/run.py
```

Per-run results land in `eval/results/<timestamp>-<config>.json` (gitignored). The comparison table is printed to stdout.

A bundled dataset `eval/datasets/sonnet-4-6-bootstrap/` is included with 3 money-stuff episodes labelled by `claude-sonnet-4-6`. You can use it as a baseline gold by setting `allowed_annotators: ["claude-sonnet-4-6"]` in `eval_config.yaml`.

> **Note:** all `podcast-etl eval` subcommands must be run from the repository root. The `eval/` package lives at the project root (not inside the installed package) and the CLI inserts the current working directory onto `sys.path` to make it importable.

## Pipeline Steps

Steps run in the order listed in `pipeline`. Each step's result is stored per-episode, so re-runs skip completed work.

| Step | Requires | Description |
|------|----------|-------------|
| `download` | -- | Fetch audio from RSS `audio_url` |
| `tag` | `download` | Write ID3 metadata (title, artist, date, TRCK track number, APIC album art) |
| `detect_ads` | `download` | Transcribe via faster-whisper, classify ad segments via LLM; overlapping/near-adjacent (≤5s apart) segments are snapped into a contiguous, non-overlapping sequence (kept distinct, not fused); writes labels to `output/<slug>/labels/<stem>.json` |
| `strip_ads` | `detect_ads` | Remove ad segments via ffmpeg with crossfade (reads segments from the labels file) |
| `stage` | `download` | Copy audio to `torrent_data_dir/`; prefers cleaned audio if available |
| `torrent` | `stage` | Create `.torrent` via `mktorrent` |
| `seed` | `torrent` | Add torrent to qBittorrent via Web API |
| `upload` | `torrent` | Upload `.torrent` + metadata to UNIT3D tracker |
| `audiobookshelf` | `download` | Copy audio to Audiobookshelf library; optionally trigger scan |

## Docker

A pre-built image is published to `ghcr.io/iridium123/podcast-etl:latest` on every push to `main`. It includes `mktorrent` and `ffmpeg` and defaults to `serve` mode (web UI + poll loop on port 8000).

### Docker Compose (recommended)

```yaml
services:
  podcast-etl:
    image: ghcr.io/iridium123/podcast-etl:latest
    ports:
      # Web UI has no auth — bind to host loopback only. Change to
      # "8000:8000" (or put behind a reverse proxy with auth) for LAN access.
      - "127.0.0.1:8000:8000"
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
