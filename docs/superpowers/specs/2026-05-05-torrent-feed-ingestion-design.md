# Torrent-Feed Ingestion Design

## Problem

The pipeline today ingests podcast episodes from RSS feeds whose `<enclosure>` is an audio URL (typically MP3). The `download` step fetches the audio over HTTP, and the rest of the pipeline (tag, detect_ads, strip_ads, stage, torrent, seed, upload, audiobookshelf) runs against that file.

We want to also ingest from a different kind of feed: an RSS produced by a UNIT3D-style tracker, where each `<enclosure>` is a `.torrent` file and the audio arrives via BitTorrent. Once the audio lands on disk, the rest of the pipeline should run normally — the user may want to push to Audiobookshelf, strip ads, change tags, upload to a different tracker, or any combination.

## Design

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
    info_hash: str | None = None                         # set after .torrent is downloaded + added
    episode_guids: list[str] = field(default_factory=list)  # set when fetch_torrent completes
    status: dict[str, StepStatus | None] = field(default_factory=dict)
```

Persists to `<podcast-slug>/torrents/<guid-hash>.json` via a `save(podcast_dir, podcast_title)` method that mirrors `Episode.save`. `Podcast.load(podcast_dir)` is extended to also enumerate `torrents/*.json` and populate `Podcast.torrent_items` so a restart of the web server / poll loop fully reconstructs torrent-feed state from disk.

The UNIT3D parser preserves existing `TorrentItem` state across re-parse — same pattern `feeds.rss` uses for Episodes. When `parse()` is given an `output_dir`, it loads any existing `<podcast-slug>/torrents/*.json` files into a `dict[guid -> TorrentItem]` and merges `info_hash`, `episode_guids`, and `status` onto the freshly-parsed items so in-flight torrents and completed fetches survive feed re-fetches.

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

1. Feed parser pulls down the RSS and creates `TorrentItem` objects (one per RSS entry). `Podcast.torrent_items` populated; `Podcast.episodes` starts empty.
2. The pipeline runs in two phases. **Phase 1**: `fetch_torrent` runs over each `TorrentItem`. **Phase 2**: every other step runs over `Podcast.episodes`.
3. `fetch_torrent` is a three-state step (see below). On completion, it spawns one `Episode` per MP3 inside the torrent and appends them to `Podcast.episodes`, so Phase 2 sees them.

### `FeedSource` Protocol — new module `feeds/`

```python
class FeedSource(Protocol):
    name: str  # "rss", "unit3d"

    def parse(
        self,
        url: str,
        output_dir: Path | None,
        config: dict,  # blacklist, title_cleaning, source-specific options
    ) -> Podcast: ...
```

Module layout:

- `src/podcast_etl/feeds/__init__.py` — `FeedSource` Protocol, `register_source(source)`, `get_source(name)`. Mirrors the existing `register_step` / `get_step` registry in `pipeline.py`.
- `src/podcast_etl/feeds/rss.py` — refactored from existing `feed.py`. Same behavior, populates `Podcast.episodes`. Registered under `name="rss"`.
- `src/podcast_etl/feeds/unit3d.py` — UNIT3D RSS parser. Reads `.torrent` enclosures, builds `TorrentItem`s. Populates `Podcast.torrent_items`, leaves `Podcast.episodes` empty. Registered under `name="unit3d"`.

`service.py` calls `get_source(feed_config.get("source", "rss")).parse(...)`. The existing `feed.py` is deleted in the same change; its sole public function (`parse_feed`) becomes `RssFeedSource.parse`. All call sites are updated atomically (small number, all in this repo).

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

- `is_complete` queries `/api/v2/torrents/info?hashes=<hash>` and returns True when the reported `state` is one of qBittorrent's "done" states (`uploading`, `stalledUP`, `pausedUP`, `forcedUP`, `queuedUP`, `checkingUP`). Returns False for `downloading`, `stalledDL`, `metaDL`, `queuedDL`, `checkingDL`, `allocating`, `pausedDL`, `error`, `missingFiles`. Raises if the torrent is unknown to qBittorrent.
- `get_files` queries `/api/v2/torrents/info` for the `save_path`, then `/api/v2/torrents/files?hash=<hash>` for the file list. For each entry it returns `TorrentFileInfo(absolute_path=Path(save_path)/f["name"], relative_path=Path(f["name"]))`. The `name` field from the qBittorrent files API is the path relative to `save_path`, which equals the file's path inside the torrent.

These methods are added to the `TorrentClient` Protocol so any future client (Transmission, Deluge) implements them too.

### `StepResult` extension

`StepResult` gains one field:

```python
@dataclass
class StepResult:
    data: dict[str, Any] = field(default_factory=dict)
    complete: bool = True   # NEW. False means "try again next cycle"
```

Default `True` keeps every existing step unchanged. Only `fetch_torrent` returns `complete=False`. The runner records `status[step.name] = StepStatus(...)` only when `complete=True`. Item state (e.g., a newly-set `info_hash` on a `TorrentItem`) is saved via the item's `save(...)` method regardless, so partial progress persists across cycles.

This is preferred over a `StepIncomplete` exception — exceptions are reserved for failures, and the runner already handles failures by logging and stopping that item's pipeline for the cycle.

### `Step` Protocol generalization

```python
class Step(Protocol):
    name: str
    target: str  # "episode" (default) or "torrent_item"
    def process(self, item, context: PipelineContext) -> StepResult: ...
```

Steps that don't define `target` are treated as `target="episode"` (preserves all existing step classes without modification). `FetchTorrentStep` sets `target = "torrent_item"`.

### `Pipeline.run` — two-phase dispatch

`Pipeline.run`'s signature changes from accepting `episodes: list[Episode]` to accepting the `Podcast` directly:

```python
def run(self, podcast: Podcast, step_filter: str | None = None, overwrite: bool = False) -> None:
    steps = self._select(step_filter)

    # Phase 1: torrent steps over torrent_items
    for step in (s for s in steps if getattr(s, "target", "episode") == "torrent_item"):
        for item in podcast.torrent_items:
            self._run_step_for(item, step, overwrite=overwrite)

    # Phase 2: episode steps over episodes (which may have been spawned in Phase 1)
    for step in (s for s in steps if getattr(s, "target", "episode") == "episode"):
        for episode in podcast.episodes:
            self._run_step_for(episode, step, overwrite=overwrite)
```

`_run_step_for` is the existing per-item-per-step body, factored out: skip if already complete (and not overwrite), call `step.process`, on success record `StepStatus` only when `result.complete`, save the item, on exception log and break out of *that item's* remaining steps for the cycle.

Phase ordering matters: torrent steps must run before episode steps because Phase 1 populates `podcast.episodes`. Within a phase, the original order from the configured pipeline is preserved.

**Caller updates** — Callers that previously passed `episodes` directly must now pass the `Podcast`. Concrete sites: `service.run_pipeline` (one call), `poller.run_poll_loop` and `poller.async_poll_loop` (one call each), `tests/test_pipeline.py` (multiple calls — mechanical update).

**Filtering** — `episode_filter` and `last` continue to apply at the source-appropriate layer:
- For RSS-source feeds, `feeds.rss.parse` filters `episodes` (existing behavior).
- For torrent-source feeds, `feeds.unit3d.parse` filters `torrent_items` by the same regex against their raw RSS titles. Episodes spawned from a torrent are not further filtered — once a torrent is included, all its MP3s become Episodes.

This keeps the filter semantics tied to "what the user sees in the feed listing" and avoids spurious half-fetched torrents.

### `FetchTorrentStep` — three-state implementation

```python
class FetchTorrentStep:
    name: str = "fetch_torrent"
    target: str = "torrent_item"

    def process(self, item: TorrentItem, ctx: PipelineContext) -> StepResult:
        client = build_torrent_client(ctx.config["client"])

        # State 1: first call — fetch .torrent, hand to qBittorrent
        if not item.info_hash:
            blob_path = ctx.podcast_dir / "torrent_files" / f"{guid_hash(item.guid)}.torrent"
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            blob_path.write_bytes(_fetch_blob(item.torrent_url))
            item.info_hash = client.add_torrent(blob_path, ctx.config["client"]["save_path"])
            return StepResult(data={"info_hash": item.info_hash, "state": "added"}, complete=False)

        # State 2: still downloading
        if not client.is_complete(item.info_hash):
            return StepResult(data={"state": "downloading"}, complete=False)

        # State 3: complete — spawn Episodes from each MP3
        mp3_files = [f for f in client.get_files(item.info_hash) if f.absolute_path.suffix.lower() == ".mp3"]
        if not mp3_files:
            raise ValueError(f"Torrent {item.info_hash} contains no MP3 files")

        for fileinfo in mp3_files:
            ep = _build_episode_from_mp3(fileinfo, item, ctx)
            _copy_to_audio_dir(fileinfo.absolute_path, ep, ctx)
            ctx.podcast.episodes.append(ep)
            item.episode_guids.append(ep.guid)
            ep.save(ctx.podcast_dir, ctx.podcast.title)

        return StepResult(data={"episode_count": len(mp3_files)}, complete=True)
```

Failure modes:
- State 1 throws (HTTP failure fetching `.torrent`, qBittorrent rejects add) → `info_hash` stays unset → State 1 retried on next cycle.
- State 2 throws (qBittorrent unreachable) → status not recorded → State 2 retried.
- State 3 throws (e.g., disk full mid-copy) → status not recorded; partially-spawned Episodes have already been saved with their stable GUIDs → next cycle re-enters State 3 and resumes from where it stopped. Idempotency comes from two facts: (1) each Episode's JSON filename is derived from its stable `<info_hash>:<relative_path>` GUID, so re-spawning loads the existing JSON via `Episode.load` and preserves any pre-existing step status; (2) the audio-copy step keys off the path recorded in the previous run's synthesized `download` step status (see "Recording the audio path on spawned Episodes" below), so the same destination filename is reused — including any collision-deduplication suffix — and the size-match check skips redundant copies.

### MP3 → Episode construction

`_build_episode_from_mp3(fileinfo: TorrentFileInfo, item: TorrentItem, ctx) -> Episode`:

- `guid` = `f"{item.info_hash}:{fileinfo.relative_path.as_posix()}"`. Stable across re-fetches of the same torrent. Always uses POSIX-style separators for portability.
- `title` = ID3 `TIT2` → MP3 filename stem (`fileinfo.relative_path.stem`) → fallback to `item.title`
- `published` = ID3 `TDRC` or `TDRL` → `item.published` (RSS pubDate) → file mtime ISO-formatted
- `description` = ID3 `COMM` (first non-empty) → `item.description`
- `episode_number` = ID3 `TRCK` (parsed as int, prefix before `/` if present) → None
- `image_url` = None (out of scope for v1 — see "Out of scope")
- `audio_url` = None (already-downloaded; `download` step is not in this pipeline)
- `raw_title` = the raw ID3 title (or filename stem) before cleaning
- `title` (final) = result of `clean_title(raw_title, ctx.config.get("title_cleaning"), ...)`, same path as the existing RSS feed parser
- `slug` = `slugify(title)`, deduplicated against other Episodes already on this podcast (existing pattern from `feeds.rss`)

Title cleaning still applies — same `title_cleaning` config block, same `clean_title()` function. Blacklist still applies to descriptions.

### `_copy_to_audio_dir`

Copies `fileinfo.absolute_path` (the MP3 inside qBittorrent's save dir) to `<podcast-slug>/audio/<filename>.mp3`. qBittorrent retains the original file and continues seeding; the pipeline owns its own copy and may freely tag / strip-ads / stage it.

**Filename construction** — `episode_basename(podcast_title, episode.title, episode.published) + ".mp3"`, using the existing `episode_basename` function. To avoid collisions when two MP3s in the same torrent produce the same basename (e.g., identical ID3 titles), the basename is deduplicated against the audio directory at copy time: if the candidate filename already exists and was NOT produced from the same source, append `-2`, `-3`, ... before the extension. The chosen filename is recorded in the Episode's `download` step status (mirroring the existing `download` step's `{"path": "audio/<filename>", "size_bytes": N}` shape) so subsequent steps locate the file consistently.

**Idempotency** — if the destination file already exists and its size matches the source's size, the copy is skipped. If it exists with a different size, the destination is overwritten (assume the source is canonical; previous copy was incomplete or interrupted).

### Recording the audio path on spawned Episodes

Downstream steps (`tag`, `detect_ads`, `strip_ads`, `stage`) locate the audio file by reading `episode.status["download"].result["path"]` (with a glob-fallback that matches against `episode.slug` and is unreliable when the slug doesn't appear verbatim in the filename). Torrent-spawned episodes never run the `download` step, so to keep downstream steps working unchanged, `fetch_torrent` synthesizes a `download` `StepStatus` on each spawned Episode:

```python
ep.status["download"] = StepStatus(
    completed_at=datetime.now().isoformat(),
    result={"path": f"audio/{filename}", "size_bytes": size},
)
```

This has two effects: (1) the `tag` step's primary lookup succeeds, and (2) if a user mistakenly includes `download` in a torrent-source pipeline, it is treated as already-complete and skipped. Validation (see "Validation" below) explicitly rejects pipelines that mix `fetch_torrent` and `download`, so this synthesis is a robustness measure rather than the contract.

### Config schema

#### Per-feed `source` field

New optional field on each feed entry, defaults to `"rss"`:

```yaml
feeds:
  - url: https://tracker.example.com/torrents/rss?rsskey=ABC123&categories=14
    name: archived-podcast
    source: unit3d                          # NEW — "rss" (default) or "unit3d"
    enabled: true
    pipeline: [fetch_torrent, audiobookshelf]
    episode_filter: "^The Daily - "
    audiobookshelf: {dir: /podcasts/archived}
```

#### `client.save_path` reuse

qBittorrent's leech destination for torrent-source feeds is the existing `client.save_path` config — the same path the `seed` step uses. No new config field for v1. If a future need arises to separate leech and seed destinations, a per-feed override can be added without breaking existing configs.

#### No `unit3d` config block in v1

UNIT3D RSS keys are embedded in the URL and self-authenticate both the RSS feed and the `.torrent` enclosures. No tracker-specific config is required for v1. If a second tracker needs custom auth or options, a `<source-name>` config block can be added at that point.

### Validation

`service.validate_config` extended:

- `source` must be a registered source name (default `"rss"`). Unknown source → validation error.
- For `source: unit3d` feeds, `pipeline` must contain `fetch_torrent`. Missing → validation error.
- For `source: rss` feeds, `fetch_torrent` must NOT appear in `pipeline` (no `torrent_items` to operate on). Present → validation error.
- A pipeline must NOT contain both `fetch_torrent` and `download` — they are mutually exclusive ways of producing audio. Both present → validation error.
- `KNOWN_FEED_FIELDS` in `service.py` gains `"source"` so the web UI form/YAML split keeps it as a structured field.

### `service.py` registration changes

At module import time:

```python
from podcast_etl.feeds.rss import RssFeedSource
from podcast_etl.feeds.unit3d import Unit3dFeedSource
from podcast_etl.feeds import register_source

register_source(RssFeedSource())
register_source(Unit3dFeedSource())

# existing step registrations + new one:
register_step(FetchTorrentStep())
```

### Web UI

- Feed-add and feed-edit forms: `source` dropdown (`rss` / `unit3d`), defaulting to `rss`.
- Feed-detail page: when `Podcast.torrent_items` is non-empty, render a "Torrents" table above the existing "Episodes" table, showing `title`, `published`, `info_hash`, `fetch_torrent` status, `episode_guids` count.
- `KNOWN_FEED_FIELDS` includes `source`.

The existing config-form / raw-YAML split, diff preview, and confirmation flows are reused unchanged.

### Idempotency / resumability

- `TorrentItem.status` mirrors `Episode.status`. Once `fetch_torrent` is recorded as complete, it's skipped on subsequent runs (existing skip logic in `Pipeline.run`).
- The `.torrent` blob at `<podcast-slug>/torrent_files/<guid-hash>.torrent` is kept on disk so a torrent can be re-added if qBittorrent loses its state.
- Spawned `Episode` GUIDs are `<info_hash>:<relative_path>`, stable across re-fetches. Reset and re-run preserves step status.
- A `TorrentItem` whose RSS entry disappears in a later poll cycle becomes an orphan JSON; not cleaned up automatically (mirrors existing Episode behavior).

### Tests

#### `tests/test_feeds_unit3d.py` (new)

Given fixture RSS payloads:
- Each `<item>` produces one `TorrentItem` with correct `guid`, `title` (raw, untouched), `published`, `description`, `torrent_url` extracted from `<enclosure type="application/x-bittorrent">`.
- Empty / malformed feed → `ValueError`.
- Existing `TorrentItem` JSONs on disk preserve `info_hash` and `status` across re-parse (parallels existing Episode preservation behavior in `feeds.rss`).

#### `tests/test_fetch_torrent_step.py` (new)

Parameterized over the three states using a fake `TorrentClient`:
- State 1 (no `info_hash`): fetches `.torrent` (mocked HTTP), calls `client.add_torrent`, persists `info_hash` on the `TorrentItem`, returns `complete=False`. The `.torrent` blob lands at `torrent_files/<guid-hash>.torrent`.
- State 2 (`info_hash` set, `client.is_complete` returns False): returns `complete=False`, no Episodes spawned.
- State 3 (`is_complete` returns True): for one MP3 spawns one Episode; for N MP3s spawns N Episodes; non-MP3 files in the torrent are skipped; empty MP3 list raises `ValueError`. Episode GUIDs are `<info_hash>:<relative_path>`.
- ID3 extraction precedence: title fallback chain (`TIT2` → filename → torrent name); date fallback chain (`TDRC`/`TDRL` → RSS pubDate → file mtime).
- Re-running State 3 after a partial spawn (some Episode JSONs already exist) is idempotent — same GUIDs, no duplicates, no double-copy of audio.
- Title cleaning is applied to ID3-derived titles.

#### `tests/test_qbittorrent_client.py` (additions)

Using `httpx` mock transport:
- `is_complete` returns True for done states (`uploading`, `stalledUP`, `pausedUP`, `forcedUP`, `queuedUP`, `checkingUP`), False for in-progress states.
- `get_files` combines `save_path` from `info` endpoint with `name` from `files` endpoint, returns a list of `TorrentFileInfo` with both `absolute_path` and `relative_path` populated. `relative_path == Path(f["name"])`; `absolute_path == Path(save_path) / f["name"]`.
- Both raise `RuntimeError` on missing torrent.

#### `tests/test_pipeline.py` (additions)

Using a fake `Step`:
- A step returning `complete=False` does NOT cause `status[step.name]` to be set, but the item's `save(...)` is still called.
- A step with `target="torrent_item"` iterates `podcast.torrent_items`, not `podcast.episodes`.
- Phase ordering: torrent-target steps run before episode-target steps in a single `Pipeline.run` call, even if the pipeline list interleaves them.
- A torrent step that mutates `podcast.episodes` in Phase 1 makes those episodes visible to Phase 2 in the same `run()` call.

#### `tests/test_models.py` (additions)

- `TorrentItem` `to_dict` / `from_dict` roundtrip including `status`, `episode_guids`, `info_hash`.
- `TorrentItem.save` writes to `<podcast-dir>/torrents/<guid-hash>.json` and is content-deduped (no rewrite if unchanged).
- `Podcast` with both `episodes` and `torrent_items` populated round-trips through save/load.

#### `tests/test_service.py` (additions)

- `validate_config` accepts `source: rss` (explicit) and `source: unit3d`, rejects unknown values.
- For `source: unit3d`, missing `fetch_torrent` from pipeline → validation error; for `source: rss`, `fetch_torrent` present → validation error.
- Source dispatch: a feed with `source: unit3d` calls `Unit3dFeedSource.parse`; default and explicit `rss` call `RssFeedSource.parse`.

#### `tests/test_web.py` (additions)

- `source` field appears in feed-add and feed-edit forms.
- POST to feed-edit with `source: unit3d` is accepted and persisted.

#### `tests/test_integration.py` (addition, marked `integration`)

- Real `.torrent` fixture containing one MP3.
- `client.is_complete` mocked to return True; `client.get_files` returns the real path to the fixture MP3 inside the test's temporary save dir.
- The `fetch_torrent` step runs end-to-end: ID3 read by real `mutagen`, real Episode JSON written, real audio file copied to `<podcast-slug>/audio/`.
- Verifies the Episode's GUID, title, slug, and the destination file exist.

### Files changed

**New:**
- `src/podcast_etl/feeds/__init__.py`
- `src/podcast_etl/feeds/rss.py` (refactored from `feed.py`)
- `src/podcast_etl/feeds/unit3d.py`
- `src/podcast_etl/steps/fetch_torrent.py`
- `tests/test_feeds_rss.py` (renamed from `tests/test_feed.py`)
- `tests/test_feeds_unit3d.py`
- `tests/test_fetch_torrent_step.py`

**Modified:**
- `src/podcast_etl/models.py` — adds `TorrentItem`; adds `Podcast.torrent_items`
- `src/podcast_etl/pipeline.py` — `StepResult.complete`; `Step.target`; two-phase `Pipeline.run`
- `src/podcast_etl/clients/qbittorrent.py` — `is_complete`, `get_files`
- `src/podcast_etl/service.py` — register new sources and step; validate `source` and `fetch_torrent` placement; dispatch on `source`; `KNOWN_FEED_FIELDS`
- `src/podcast_etl/web/routes/feeds.py` — `source` field in forms; render `torrent_items` on detail page
- `src/podcast_etl/web/templates/*.html` — feed form `source` dropdown; feed-detail torrents table
- `src/podcast_etl/feed.py` — deleted; logic moves to `feeds/rss.py`
- `tests/test_models.py` — `TorrentItem` roundtrip, Podcast with torrent_items
- `tests/test_pipeline.py` — `complete=False` behavior, two-phase dispatch
- `tests/test_qbittorrent_client.py` — `is_complete`, `get_files`
- `tests/test_service.py` — source validation and dispatch
- `tests/test_web.py` — source field in forms
- `tests/test_integration.py` — torrent-source end-to-end fixture
- `CLAUDE.md` — document `source` config, `fetch_torrent` step, `feeds/` layout, two-phase pipeline
- `README.md` — document `source` config and torrent-source workflow

### Out of scope for v1

- Multiple trackers beyond UNIT3D (the `FeedSource` registry makes adding more drop-in)
- Multiple torrent clients beyond qBittorrent (the `TorrentClient` Protocol extension is generic)
- Re-uploading torrent-sourced episodes to the same tracker (would create a duplicate; users can configure a different upload tracker if desired)
- Per-feed leech/seed save-path separation (`client.save_path` is shared)
- Cleanup of orphan `TorrentItem` JSONs when an RSS entry disappears
- Migration / backfill of existing UNIT3D-uploaded torrents — only newly-discovered RSS items are fetched
- APIC / cover-image embedding for torrent-spawned episodes (the existing `tag` step expects an image URL; torrent-spawned Episodes have `image_url=None` and skip APIC embedding)
