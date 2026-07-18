# Torrent-Feed Ingestion Design

## Problem

The pipeline today ingests podcast episodes from RSS feeds whose `<enclosure>` is an audio URL (typically MP3). The `download` step fetches the audio over HTTP, and the rest of the pipeline (tag, detect_ads, strip_ads, stage, torrent, seed, upload, audiobookshelf) runs against that file.

We want to also ingest from a different kind of feed: an RSS produced by a UNIT3D-style tracker, where each `<enclosure>` is a `.torrent` file and the audio arrives via BitTorrent. Once the audio lands on disk, the rest of the pipeline should run normally — the user may want to push to Audiobookshelf, strip ads, change tags, upload to a different tracker, or any combination.

## Design

### Guiding principle: the source implies the fetch

Torrent fetching is **not a pipeline step**. The `pipeline:` config list keeps meaning exactly what it means today — "what to do with episodes" — and the feed's `source` determines how audio arrives. For `source: unit3d` feeds, a fetch phase runs over `TorrentItem`s before the (unchanged) `Pipeline` runs over episodes.

This keeps `Pipeline`, the `Step` protocol, and `StepResult` completely untouched: no step-target dispatch, no partial-completion flag, no signature changes, no pipeline-composition validation rules. Invalid configurations (torrent fetching in an RSS pipeline, or vice versa) are unrepresentable rather than validated away.

### Two-layer data model

A torrent feed introduces a layer above `Episode` because one RSS entry corresponds to one torrent, and one torrent may contain one or many MP3 files. Conflating the two would force "Episode" to mean "audio file or pre-fetch placeholder," which breaks every downstream step that assumes Episode = audio.

The disk layout for a torrent-source podcast:

```
output/<podcast-slug>/
  podcast.json
  torrents/                    NEW — only present for torrent-source feeds
    <guid-hash>.json           one per RSS item (TorrentItem state)
  torrent_files/               NEW — fetched .torrent blobs, kept for reproducibility
    <guid-hash>.torrent
  episodes/                    same as today
    <date>-<slug>-<hash>.json  one per MP3 (now possibly N per torrent)
  audio/                       same as today
    <files>.mp3                copied here from qBittorrent's save dir
```

#### `TorrentItem` — new dataclass in `models.py`

```python
@dataclass
class TorrentItem:
    guid: str                                            # from RSS <guid>
    title: str                                           # raw RSS title (display only)
    published: str | None                                # from RSS <pubDate>
    description: str | None
    torrent_url: str                                     # URL to fetch the .torrent blob
    info_hash: str | None = None                         # computed locally from the blob via torf
    episode_guids: list[str] = field(default_factory=list)  # set when fetch completes
    fetched_at: str | None = None                        # set when all episodes are spawned
```

There is no `status: dict[str, StepStatus]` — a `TorrentItem` has exactly one job, and its lifecycle state is fully derivable from its own fields:

- `info_hash is None` → the `.torrent` blob has not been fetched yet
- `info_hash` set, `fetched_at is None` → in the client (downloading), awaiting (re-)add, or spawn was interrupted
- `fetched_at` set → done; skipped on subsequent cycles

`info_hash` is computed **locally** from the blob (`torf.Torrent.read(blob_path).infohash` — `torf` is already a dependency of the `torrent` step) rather than taken from the client's add response. This makes State 1 crash-safe: the hash is a pure function of the blob on disk, so no client interaction can be lost between "added" and "persisted."

Persists to `<podcast-slug>/torrents/<guid-hash>.json` via a `save(podcast_dir, podcast_title)` method that mirrors `Episode.save`. `Podcast.load(podcast_dir)` is extended to also enumerate `torrents/*.json` and populate `Podcast.torrent_items` so a restart of the web server / poll loop fully reconstructs torrent-feed state from disk.

The UNIT3D parser preserves existing `TorrentItem` state across re-parse — same pattern `feed.py` uses for Episodes. When `parse_unit3d_feed()` is given an `output_dir`, it loads any existing `<podcast-slug>/torrents/*.json` files into a `dict[guid -> TorrentItem]` and merges `info_hash`, `episode_guids`, and `fetched_at` onto the freshly-parsed items so in-flight torrents and completed fetches survive feed re-fetches.

#### `Podcast.torrent_items` — added field

```python
@dataclass
class Podcast:
    ...
    episodes: list[Episode] = field(default_factory=list)
    torrent_items: list[TorrentItem] = field(default_factory=list)  # NEW
```

Documented as: only populated when the feed source is torrent-based; defaults to empty for RSS-source feeds. Same nullability pattern the codebase already uses for optional Episode fields. No new types, no class hierarchy, no wrapper.

#### `Episode` — unchanged in shape

For torrent-source feeds, `Episode.guid` is `<info_hash>:<relative_path_inside_torrent>`. Stable: same torrent re-fetched yields the same GUIDs, so step status survives resets. Other fields come from ID3 tags (see "ID3 extraction" below).

### Lifecycle for a torrent-source feed

1. Feed parser pulls down the RSS and creates `TorrentItem` objects (one per RSS entry). `Podcast.torrent_items` populated; `Podcast.episodes` starts empty (beyond what `Podcast.load` restored from disk).
2. **Fetch phase** (`fetch_torrents`, see below) runs over each unfinished `TorrentItem`: fetch the `.torrent` blob, hand it to qBittorrent, and — once the download completes — spawn one `Episode` per MP3 inside the torrent, appending them to `Podcast.episodes`.
3. **Pipeline** runs over `Podcast.episodes` exactly as it does today, via the unchanged `Pipeline.run(episodes, ...)`.

`service.run_pipeline` calls the fetch phase before constructing/running the pipeline when the feed's `source` is torrent-based. An unfinished `TorrentItem` (still downloading, qBittorrent unreachable) is simply retried on the next poll cycle — the same retry cadence pipeline steps already get.

### Feed parser — new module `unit3d_feed.py`

No `feeds/` package, no `FeedSource` protocol, no registry for v1. With exactly two sources — one of them the default — a two-line dispatch is simpler than an abstraction layer, and `feed.py` (plus its tests and git history) stays untouched. Extracting a registry is a mechanical refactor we can do if a third source ever appears.

- `src/podcast_etl/feed.py` — unchanged. Existing `parse_feed(url, output_dir, blacklist, title_cleaning)`.
- `src/podcast_etl/unit3d_feed.py` — NEW. `parse_unit3d_feed(url, output_dir=None, blacklist=None, title_cleaning=None) -> Podcast`, mirroring `parse_feed`'s signature. Reads `.torrent` enclosures (`type="application/x-bittorrent"`), builds `TorrentItem`s, populates `Podcast.torrent_items`, leaves freshly-parsed `Podcast.episodes` empty (existing on-disk episodes are still restored, as `parse_feed` does today).

Dispatch lives in `service.fetch_feed`:

```python
def fetch_feed(url: str, output_dir: Path, resolved_config: dict) -> Podcast:
    ...
    if resolved_config.get("source", "rss") == "unit3d":
        return parse_unit3d_feed(url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
    return parse_feed(url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
```

`poller.run_poll_loop` currently calls `parse_feed` directly; it switches to `service.fetch_feed` so both entry points share the dispatch.

### `TorrentClient` Protocol extension

The existing protocol has `has_torrent(info_hash)` and `add_torrent(torrent_path, save_path) -> info_hash`. Two methods added:

```python
@dataclass(frozen=True)
class TorrentFileInfo:
    absolute_path: Path     # full path on disk (save_path / relative_path)
    relative_path: Path     # path relative to the torrent's root, as reported by the client

def is_complete(self, info_hash: str) -> bool: ...
def get_files(self, info_hash: str) -> list[TorrentFileInfo]: ...
```

`QBittorrentClient` implements both:

- `is_complete` queries `/api/v2/torrents/info?hashes=<hash>` and returns `progress == 1` (equivalently `amount_left == 0`). **Deliberately not a state-name allowlist**: qBittorrent has renamed states across versions (5.0 renamed `pausedUP`/`pausedDL` to `stoppedUP`/`stoppedDL`), so enumerating "done" states is a per-version maintenance liability, while `progress` is stable and unambiguous. Raises if the torrent is unknown to qBittorrent — defensive only, since the fetch phase checks `has_torrent` first (a raise can only surface on a race with a concurrent deletion, and resolves next cycle via the re-add path).
- `get_files` queries `/api/v2/torrents/info` for the `save_path`, then `/api/v2/torrents/files?hash=<hash>` for the file list. For each entry it returns `TorrentFileInfo(absolute_path=Path(save_path)/f["name"], relative_path=Path(f["name"]))`. The `name` field from the qBittorrent files API is the path relative to `save_path`, which equals the file's path inside the torrent.

These methods are added to the `TorrentClient` Protocol so any future client (Transmission, Deluge) implements them too.

Client construction: the seed step's private `_get_client(context)` is hoisted to a shared `get_torrent_client(client_config)` in `clients/__init__.py`, used by both `seed` and the fetch phase.

### Fetch phase — new module `torrent_fetch.py`

`fetch_torrents(podcast, output_dir, config)` iterates `podcast.torrent_items`, skipping items with `fetched_at` set, and advances each through a three-state machine. Per-item exceptions are logged and skip to the next item (mirroring how the pipeline isolates per-episode failures) so one bad torrent doesn't block the rest.

```python
def fetch_torrent_item(item: TorrentItem, podcast: Podcast, podcast_dir: Path, config: dict) -> None:
    client = get_torrent_client(config["client"])
    blob_path = podcast_dir / "torrent_files" / f"{guid_hash(item.guid)}.torrent"

    # State 1: fetch the .torrent blob; compute its info hash locally
    if not item.info_hash:
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(_fetch_blob(item.torrent_url))
        item.info_hash = torf.Torrent.read(blob_path).infohash
        item.save(podcast_dir, podcast.title)
        # falls through — no reason to wait a cycle before adding

    # State 2: ensure the torrent is in the client, then wait for completion
    if not client.has_torrent(item.info_hash):
        if not blob_path.exists():
            blob_path.write_bytes(_fetch_blob(item.torrent_url))
        client.add_torrent(blob_path, config["client"]["save_path"])
        return  # freshly (re-)added; check progress next cycle
    if not client.is_complete(item.info_hash):
        return  # still downloading; retried next cycle

    # State 3: complete — spawn Episodes from each MP3
    mp3_files = [f for f in client.get_files(item.info_hash) if f.absolute_path.suffix.lower() == ".mp3"]
    if not mp3_files:
        # Permanent condition — retrying cannot grow MP3s. Warn once, mark done.
        logger.warning("Torrent %s (%s) contains no MP3 files; marking fetched with 0 episodes", item.info_hash, item.title)
        item.fetched_at = datetime.now().isoformat()
        item.save(podcast_dir, podcast.title)
        return

    for fileinfo in mp3_files:
        ep = _build_episode_from_mp3(fileinfo, item, podcast, config)
        _copy_to_audio_dir(fileinfo.absolute_path, ep, podcast_dir)
        podcast.episodes.append(ep)
        if ep.guid not in item.episode_guids:
            item.episode_guids.append(ep.guid)
        ep.save(podcast_dir, podcast.title)

    item.fetched_at = datetime.now().isoformat()
    item.save(podcast_dir, podcast.title)
```

Two properties of the state machine do most of the work:

1. **`info_hash` is computed locally from the blob, before any client interaction.** State 1 is a pure download-and-hash; a crash at any point inside it just repeats it, byte-identical, next cycle.
2. **State 2 is "ensure present, then wait" rather than "wait."** The `has_torrent` guard makes the add idempotent *and* doubles as the recovery path: any way the client can lose the torrent (user deletion, client reinstall, wiped state) is healed by re-adding from the stored blob. This is the mechanism that makes the kept `.torrent` blob earn its disk space.

#### Failure scenarios

Every scenario below either self-heals on a later poll cycle or terminates in a state visible in the web UI with a documented operator gesture. Nothing requires editing JSON on disk.

| Scenario | Behavior | Path to resolution |
|---|---|---|
| Tracker RSS unreachable / malformed | Feed fetch fails; cycle logs and skips this feed | Self-heals when the tracker recovers (next poll) |
| Blob HTTP fetch fails (State 1) | Per-item exception logged; `info_hash` stays unset | Retried from State 1 next cycle |
| Tracker serves an unparseable blob | `torf` raises; blob is overwritten on the next attempt | Self-heals if the tracker recovers; if permanent, filter the item out (see "Abandoning a torrent") |
| Crash between blob write and `item.save` | Blob re-downloaded, hash recomputed — identical result | Self-heals next cycle; no duplicate client state possible |
| qBittorrent unreachable | Per-item exception logged; other items unaffected | Retried next cycle |
| `add_torrent` rejected by client | Per-item exception logged | Retried next cycle (blob and hash already persisted) |
| **Torrent deleted from the client** | `has_torrent` returns False → re-added from the stored blob; download restarts | **This is the supported retry gesture**: delete a dead torrent (with its data) in qBittorrent and the next poll re-attempts it from scratch |
| Stored blob missing at re-add time | Re-fetched from `torrent_url` | Self-heals; if the tracker no longer serves it, logs each cycle until the item is filtered out or leaves the feed |
| Torrent stalled (no seeders) or errored (`missingFiles` etc.) | `progress` never reaches 1; item stays in "downloading", visible in the web UI torrents table | Operator decides: delete it in qBittorrent to restart from scratch, or filter it out to abandon |
| Torrent contains no MP3s | Warn once, set `fetched_at` with zero `episode_guids` — a permanent condition is not retried | Terminal; shows as fetched / 0 episodes in the UI |
| ID3 tags unreadable on an MP3 | Treated as absent tags — the fallback chain (filename stem, RSS metadata, mtime) applies; never fatal | Episode spawns with fallback metadata |
| Crash / disk full mid-spawn (State 3) | `fetched_at` stays unset; already-spawned Episodes persisted with stable GUIDs | State 3 re-entered next cycle and resumes: stable `<info_hash>:<relative_path>` GUIDs mean re-spawning loads existing JSON (preserving step status), and deterministic filenames + the size-match check prevent duplicate copies |

**Abandoning a torrent** — to permanently skip an item that keeps failing, exclude it with `episode_filter` (or wait for it to age out of the feed). Deleting its JSON from `torrents/` does *not* abandon it: the next parse recreates the item with fresh state and the fetch starts over. This asymmetry is deliberate — every automatic path retries, and only an explicit config change gives up.

### MP3 → Episode construction

`_build_episode_from_mp3(fileinfo: TorrentFileInfo, item: TorrentItem, podcast, config) -> Episode`:

- `guid` = `f"{item.info_hash}:{fileinfo.relative_path.as_posix()}"`. Stable across re-fetches of the same torrent. Always uses POSIX-style separators for portability.
- `title` = ID3 `TIT2` → MP3 filename stem (`fileinfo.relative_path.stem`) → fallback to `item.title`
- `published` = ID3 `TDRC` or `TDRL` → `item.published` (RSS pubDate) → file mtime. **Whatever the source, the value is normalized to RFC 2822 via `email.utils.format_datetime` before being stored.** This is required, not cosmetic: `TagStep` parses `episode.published` with `parsedate_to_datetime`, which rejects ISO-format dates (the natural format of ID3 `TDRC` and mtimes) — an unnormalized date would fail the tag step on every cycle.
- `description` = ID3 `COMM` (first non-empty) → `item.description`
- `episode_number` = ID3 `TRCK` (parsed as int, prefix before `/` if present) → None
- `image_url` = None (out of scope for v1 — see "Out of scope")
- `audio_url` = None (already-downloaded; `download` step is not needed)
- `raw_title` = the raw ID3 title (or filename stem) before cleaning
- `title` (final) = result of `clean_title(raw_title, config.get("title_cleaning"), ...)`, same path as the existing RSS feed parser
- `slug` = `slugify(title)`, deduplicated against other Episodes already on this podcast (existing pattern from `feed.py`)

ID3 read errors (missing header, corrupt tags — anything mutagen raises) are treated as "no tags present": every field falls through its chain to the filename/RSS/mtime fallbacks. A torrent whose audio has broken metadata still spawns Episodes; it never wedges the fetch.

Title cleaning still applies — same `title_cleaning` config block, same `clean_title()` function. Blacklist still applies to descriptions.

### `_copy_to_audio_dir`

Copies `fileinfo.absolute_path` (the MP3 inside qBittorrent's save dir) to `<podcast-slug>/audio/<filename>.mp3`. qBittorrent retains the original file and continues seeding; the pipeline owns its own copy and may freely tag / strip-ads / stage it.

**Filename construction** — `episode_basename(podcast_title, episode.title, episode.published) + ".mp3"`, using the existing `episode_basename` function. Collisions can only occur between MP3s *within the same torrent* (e.g., identical ID3 titles), so disambiguation is computed deterministically up front: build the basenames for all of the torrent's MP3s, and for any that collide, append `-` plus the first 8 hex chars of `sha256(relative_path)` before the extension. No filesystem probing, no ordering dependence — the same torrent always produces the same filenames, which is what makes the interrupted-spawn retry in State 3 idempotent. The chosen filename is recorded in the Episode's synthesized `download` step status (see below) so subsequent steps locate the file consistently.

**Idempotency** — if the destination file already exists and its size matches the source's size, the copy is skipped. If it exists with a different size, the destination is overwritten (assume the source is canonical; previous copy was incomplete or interrupted).

### Recording the audio path on spawned Episodes

Downstream steps (`tag`, `detect_ads`, `strip_ads`, `stage`) locate the audio file by reading `episode.status["download"].result["path"]` (with a glob-fallback that matches against `episode.slug` and is unreliable when the slug doesn't appear verbatim in the filename). Torrent-spawned episodes never run the `download` step, so to keep downstream steps working unchanged, the fetch phase synthesizes a `download` `StepStatus` on each spawned Episode, matching the real download step's result shape:

```python
ep.status["download"] = StepStatus(
    completed_at=datetime.now().isoformat(),
    result={"path": f"audio/{filename}", "size_bytes": size},
)
```

This has two effects: (1) the downstream steps' primary lookup succeeds, and (2) if a user includes `download` in a torrent-source pipeline, it is treated as already-complete and harmlessly skipped — no validation rule needed.

### Config schema

#### Per-feed `source` field

New optional field on each feed entry, defaults to `"rss"`:

```yaml
feeds:
  - url: https://tracker.example.com/torrents/rss?rsskey=ABC123&categories=14
    name: archived-podcast
    source: unit3d                          # NEW — "rss" (default) or "unit3d"
    enabled: true
    pipeline: [audiobookshelf]              # episode steps only; fetching is implied by source
    episode_filter: "^The Daily - "
    audiobookshelf: {dir: /podcasts/archived}
```

#### Filtering

`episode_filter` and `last` continue to apply at the source-appropriate layer:
- For RSS-source feeds, `parse_feed` filters `episodes` (existing behavior).
- For torrent-source feeds, `parse_unit3d_feed` filters `torrent_items` by the same regex against their raw RSS titles. Episodes spawned from a torrent are not further filtered — once a torrent is included, all its MP3s become Episodes.

This keeps the filter semantics tied to "what the user sees in the feed listing" and avoids spurious half-fetched torrents.

#### `client.save_path` reuse

qBittorrent's leech destination for torrent-source feeds is the existing `client.save_path` config — the same path the `seed` step uses. No new config field for v1. If a future need arises to separate leech and seed destinations, a per-feed override can be added without breaking existing configs.

#### No `unit3d` config block in v1

UNIT3D RSS keys are embedded in the URL and self-authenticate both the RSS feed and the `.torrent` enclosures. No tracker-specific config is required for v1. If a second tracker needs custom auth or options, a `<source-name>` config block can be added at that point.

### Validation

`service.validate_config` extended with a single rule:

- `source`, when present, must be `"rss"` or `"unit3d"`. Unknown value → validation error.

That's the whole list. Because fetching is implied by `source` rather than expressed as a pipeline step, there are no pipeline-composition rules to enforce: torrent fetching can't be misplaced (it isn't a step), and a stray `download` in a torrent pipeline is harmlessly skipped via the synthesized status.

`KNOWN_FEED_FIELDS` in `service.py` gains `"source"` so the web UI form/YAML split keeps it as a structured field.

### Web UI

- Feed-add and feed-edit forms: `source` dropdown (`rss` / `unit3d`), defaulting to `rss`.
- Feed-detail page: when `Podcast.torrent_items` is non-empty, render a "Torrents" table above the existing "Episodes" table, showing `title`, `published`, `info_hash`, derived state (pending / downloading / fetched, from `info_hash`/`fetched_at`), and `episode_guids` count.
- `KNOWN_FEED_FIELDS` includes `source`.

The existing config-form / raw-YAML split, diff preview, and confirmation flows are reused unchanged.

### Idempotency / resumability

- A `TorrentItem` with `fetched_at` set is skipped by the fetch phase on subsequent runs.
- The `.torrent` blob at `<podcast-slug>/torrent_files/<guid-hash>.torrent` is kept on disk and actively used: State 2's `has_torrent` guard re-adds from it whenever qBittorrent loses the torrent (user deletion, reinstall, wiped state).
- Spawned `Episode` GUIDs are `<info_hash>:<relative_path>`, stable across re-fetches. Reset and re-run preserves step status.
- A `TorrentItem` whose RSS entry disappears in a later poll cycle becomes an orphan JSON; not cleaned up automatically (mirrors existing Episode behavior).

### Tests

#### `tests/test_unit3d_feed.py` (new)

Given fixture RSS payloads:
- Each `<item>` produces one `TorrentItem` with correct `guid`, `title` (raw, untouched), `published`, `description`, `torrent_url` extracted from `<enclosure type="application/x-bittorrent">`.
- Empty / malformed feed → `ValueError`.
- Existing `TorrentItem` JSONs on disk preserve `info_hash`, `episode_guids`, and `fetched_at` across re-parse (parallels existing Episode preservation behavior in `feed.py`).
- `episode_filter` filters `torrent_items` by raw RSS title.

#### `tests/test_torrent_fetch.py` (new)

Parameterized over the three states using a fake `TorrentClient`:
- State 1 (no `info_hash`): fetches `.torrent` (mocked HTTP), computes `info_hash` locally from the blob via `torf` (no client call involved), persists it, and proceeds to the add in the same invocation. The `.torrent` blob lands at `torrent_files/<guid-hash>.torrent`.
- State 2, not in client (`has_torrent` returns False): `add_torrent` is called with the stored blob; no Episodes spawned. Covers both the first add and re-add-after-deletion — the fake client's torrent is removed between calls and the item recovers.
- State 2, blob missing at re-add: the blob is re-fetched from `torrent_url` before adding.
- State 2, downloading (`has_torrent` True, `is_complete` False): returns without spawning Episodes, `fetched_at` unset, `add_torrent` NOT called again.
- State 3 (`is_complete` returns True): for one MP3 spawns one Episode; for N MP3s spawns N Episodes; non-MP3 files in the torrent are skipped. Episode GUIDs are `<info_hash>:<relative_path>`.
- A torrent with no MP3s logs a warning and sets `fetched_at` with zero `episode_guids` — it is not retried on the next cycle.
- Unreadable ID3 tags (mutagen raises) fall back to filename/RSS metadata instead of failing the spawn.
- An item with `fetched_at` set is skipped entirely — no client calls at all.
- A per-item exception doesn't prevent other items from being processed.
- ID3 extraction precedence: title fallback chain (`TIT2` → filename → torrent name); date fallback chain (`TDRC`/`TDRL` → RSS pubDate → file mtime).
- `published` is RFC 2822-formatted regardless of which source in the fallback chain produced it (ID3 ISO dates and mtimes are normalized).
- Colliding basenames within one torrent get deterministic `sha256(relative_path)`-suffixed filenames; the same torrent produces the same filenames on re-run.
- Re-running State 3 after a partial spawn (some Episode JSONs already exist) is idempotent — same GUIDs, no duplicates, no double-copy of audio, pre-existing step status preserved.
- The synthesized `download` status matches the real download step's result shape.
- Title cleaning is applied to ID3-derived titles.

#### `tests/test_qbittorrent_client.py` (additions)

Using `httpx` mock transport:
- `is_complete` returns True when `progress == 1`, False when `progress < 1` — independent of the reported state string.
- `get_files` combines `save_path` from `info` endpoint with `name` from `files` endpoint, returns a list of `TorrentFileInfo` with both `absolute_path` and `relative_path` populated. `relative_path == Path(f["name"])`; `absolute_path == Path(save_path) / f["name"]`.
- Both raise `RuntimeError` on missing torrent.

#### `tests/test_models.py` (additions)

- `TorrentItem` `to_dict` / `from_dict` roundtrip including `episode_guids`, `info_hash`, `fetched_at`.
- `TorrentItem.save` writes to `<podcast-dir>/torrents/<guid-hash>.json` and is content-deduped (no rewrite if unchanged).
- `Podcast` with both `episodes` and `torrent_items` populated round-trips through save/load.

#### `tests/test_service.py` (additions)

- `validate_config` accepts `source: rss` (explicit) and `source: unit3d`, rejects unknown values.
- Source dispatch: a feed with `source: unit3d` calls `parse_unit3d_feed`; default and explicit `rss` call `parse_feed`.
- `run_pipeline` for a torrent-source feed runs the fetch phase before the pipeline, and episodes spawned by the fetch phase are visible to the pipeline in the same run.

#### `tests/test_web.py` (additions)

- `source` field appears in feed-add and feed-edit forms.
- POST to feed-edit with `source: unit3d` is accepted and persisted.

#### `tests/test_integration.py` (addition, marked `integration`)

- Real `.torrent` fixture containing one MP3.
- `client.is_complete` mocked to return True; `client.get_files` returns the real path to the fixture MP3 inside the test's temporary save dir.
- The fetch phase runs end-to-end: ID3 read by real `mutagen`, real Episode JSON written, real audio file copied to `<podcast-slug>/audio/`.
- Verifies the Episode's GUID, title, slug, and the destination file exist.

### Files changed

**New:**
- `src/podcast_etl/unit3d_feed.py`
- `src/podcast_etl/torrent_fetch.py`
- `tests/test_unit3d_feed.py`
- `tests/test_torrent_fetch.py`

**Modified:**
- `src/podcast_etl/models.py` — adds `TorrentItem`; adds `Podcast.torrent_items`; `Podcast.load` enumerates `torrents/*.json`
- `src/podcast_etl/clients/__init__.py` — `get_torrent_client` hoisted from `steps/seed.py`
- `src/podcast_etl/clients/qbittorrent.py` — `is_complete`, `get_files`
- `src/podcast_etl/steps/seed.py` — uses shared `get_torrent_client`
- `src/podcast_etl/service.py` — `source` dispatch in `fetch_feed`; fetch phase in `run_pipeline`; `source` validation; `KNOWN_FEED_FIELDS`
- `src/podcast_etl/poller.py` — `run_poll_loop` calls `service.fetch_feed` instead of `parse_feed` directly
- `src/podcast_etl/web/routes/feeds.py` — `source` field in forms; render `torrent_items` on detail page
- `src/podcast_etl/web/templates/*.html` — feed form `source` dropdown; feed-detail torrents table
- `tests/test_models.py` — `TorrentItem` roundtrip, Podcast with torrent_items
- `tests/test_qbittorrent_client.py` — `is_complete`, `get_files`
- `tests/test_service.py` — source validation, dispatch, fetch-phase ordering
- `tests/test_web.py` — source field in forms
- `tests/test_integration.py` — torrent-source end-to-end fixture
- `CLAUDE.md` — document `source` config, fetch phase, `unit3d_feed.py` / `torrent_fetch.py` modules
- `README.md` — document `source` config and torrent-source workflow

Notably unchanged: `pipeline.py`, `feed.py`, `tests/test_pipeline.py`, `tests/test_feed.py`, and every existing step.

### Out of scope for v1

- Multiple trackers beyond UNIT3D — and with them the `FeedSource` protocol/registry abstraction; a two-way dispatch is simpler until a third source exists, and extracting a registry then is mechanical
- Multiple torrent clients beyond qBittorrent (the `TorrentClient` Protocol extension is generic)
- Re-uploading torrent-sourced episodes to the same tracker (would create a duplicate; users can configure a different upload tracker if desired)
- Per-feed leech/seed save-path separation (`client.save_path` is shared)
- Cleanup of orphan `TorrentItem` JSONs when an RSS entry disappears
- Migration / backfill of existing UNIT3D-uploaded torrents — only newly-discovered RSS items are fetched
- APIC / cover-image embedding for torrent-spawned episodes (the existing `tag` step expects an image URL; torrent-spawned Episodes have `image_url=None` and skip APIC embedding)
