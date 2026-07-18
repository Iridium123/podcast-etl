# Torrent-Feed Ingestion (UNIT3D) — Implementation Plan

## Context

The approved design spec (`docs/superpowers/specs/2026-05-05-torrent-feed-ingestion-design.md`, on branch `torrent-feed-ingestion-spec`) adds a second feed source: UNIT3D tracker RSS feeds whose enclosures are `.torrent` files. Audio arrives via qBittorrent; once on disk, episodes flow through the existing pipeline unchanged (ad-stripping, tagging, ABS, re-upload to another tracker, etc.).

Core principle: **the source implies the fetch**. Torrent fetching is NOT a pipeline step — `Pipeline`, the `Step` protocol, `StepResult`, and `feed.py` stay untouched. A fetch phase advances `TorrentItem`s through a 3-state machine before the pipeline runs. Spawned Episodes get a synthesized `download` StepStatus so all 9 downstream steps work unchanged.

Work happens on branch `torrent-feed-ingestion-spec`. TDD per task, commit per task, `uv run pytest tests/ -v` (unit tests only) must pass at every commit.

## Key facts from exploration

- `Episode.save(podcast_dir, podcast_title)` is content-deduped; `episode_json_filename` hashes guid (`models.py:54`)
- Downstream steps locate audio via `episode.status["download"].result["path"]` (`tag.py:56`, `detect_ads.py:26`, `stage.py:24`); download result shape is `{"path": f"audio/{filename}", "size_bytes": size}` (`download.py:37`)
- `TagStep` parses `episode.published` with `parsedate_to_datetime` (RFC 2822 only) → dates must be normalized
- `TorrentClient` Protocol lives in `clients/__init__.py` (has_torrent, add_torrent); `QBittorrentClient` uses lazy httpx session; existing `_read_info_hash` via torf already in `qbittorrent.py:65`
- `seed.py:67` has private `_get_client(context)` to hoist
- `service.fetch_feed` (`service.py:250`) and both poll loops call `parse_feed` directly; poller tests patch `poller.parse_feed`/`poller.Pipeline`/`poller.get_step`
- `filter_episodes` (`service.py:217`): `last` first, then regex on title
- `clean_title(title, config, published=, all_entries=, episode_number=)` (`title_clean.py:211`)
- Effective title = `config.get("title_override") or podcast.title` (`pipeline.py:26`)
- Web form fields parsed via `parse_form_section(text_fields=..., int_fields=..., bool_fields=...)`; `apply_text_field` deletes key when empty
- `validate_config` accumulates errors per feed using `resolved = deep_merge(defaults, feed)`

## Tasks

### Task 1: `guid_hash` + `TorrentItem` + `Podcast.torrent_items` (models.py)

- `guid_hash(guid) -> str`: `hashlib.sha256(guid.encode()).hexdigest()[:16]`
- `TorrentItem` dataclass: `guid, title, published, description, torrent_url, info_hash=None, episode_guids=[], fetched_at=None` with `to_dict`/`from_dict`/`load`; `save(podcast_dir)` writes content-deduped JSON to `torrents/{guid_hash(guid)}.json` (mirrors `Episode.save`)
- `Podcast.torrent_items: list[TorrentItem] = field(default_factory=list)`; `Podcast.save` also saves torrent items; `Podcast.load` enumerates `torrents/*.json`
- Tests (`test_models.py`): roundtrip incl. defaults, guid-hash filename, content-dedup (mtime unchanged on re-save), Podcast save/load roundtrip with both episodes and torrent_items, load without torrents dir → `[]`

### Task 2: Hoist `get_torrent_client` to `clients/__init__.py`

```python
def get_torrent_client(client_config: dict) -> TorrentClient:
    if not client_config:
        raise ValueError("No torrent client configured")
    from podcast_etl.clients.qbittorrent import QBittorrentClient  # lazy: avoid cycle
    return QBittorrentClient.from_config(client_config)
```
- `seed.py`: replace `_get_client(context)` with `get_torrent_client(context.config.get("client", {}))`, drop the private helper
- Update `tests/test_seed_step.py` patch targets accordingly (read file first to find them); add tests for `get_torrent_client` (builds QBittorrentClient / raises on empty)

### Task 3: `TorrentFileInfo` + `is_complete` + `get_files` (clients)

- `clients/__init__.py`: frozen dataclass `TorrentFileInfo(absolute_path: Path, relative_path: Path)`; add `is_complete`/`get_files` to the Protocol
- `qbittorrent.py`: private `_torrent_info(info_hash)` helper GETs `/api/v2/torrents/info?hashes=<lower>`, raises `RuntimeError("Torrent not found...")` on empty list
  - `is_complete` → `self._torrent_info(h).get("progress", 0) == 1` (deliberately NOT state names — qBt 5.0 renamed paused*→stopped*)
  - `get_files` → save_path from `_torrent_info`, then `/api/v2/torrents/files?hash=<lower>`; returns `TorrentFileInfo(save_path / f["name"], Path(f["name"]))`
- Tests (`test_qbittorrent_client.py`, existing MagicMock-session style): progress 1→True, 0.42→False, ignores unfamiliar state strings, missing→RuntimeError; get_files combines paths (use `side_effect=[info_resp, files_resp]`), missing→RuntimeError

### Task 4: `unit3d_feed.py` parser

`parse_unit3d_feed(url, output_dir=None, blacklist=None, title_cleaning=None) -> Podcast` (title_cleaning accepted for parity, applied at spawn time):
- feedparser; bozo+no entries → `ValueError` (same as `parse_feed`)
- Per entry: torrent_url from enclosure with `type == "application/x-bittorrent"` or `.torrent` href, fallback to `rel=enclosure` link; entries without → warn + skip
- Build `TorrentItem(guid=entry.id|link|title, title=raw, published, description=clean_description(summary) with blacklist applied)`
- Merge on-disk state: load `torrents/*.json` into `{guid: item}`, copy `info_hash`/`episode_guids`/`fetched_at` onto feed-present items. **Orphans (on disk, not in feed) are NOT included** — that's the abandonment semantics
- **Load ALL `episodes/*.json` into `podcast.episodes`** (deliberate divergence from `parse_feed`: torrent-spawned episodes are never feed-present)
- NO episode_filter/last here — filtering happens in service (Task 7), mirroring where RSS filtering actually lives
- Tests (`tests/test_unit3d_feed.py`, new): inline XML fixtures passed straight to `feedparser.parse` — item fields extracted, no-enclosure skipped, malformed→ValueError, state preserved across re-parse, orphan excluded, all on-disk episodes loaded, blacklist applied

### Task 5: `torrent_fetch.py` — episode construction + copy helpers

- `to_rfc2822(value) -> str | None`: try `parsedate_to_datetime`, then `datetime.fromisoformat`, format via `email.utils.format_datetime`; None if both fail. (Required: TagStep rejects ISO dates)
- `_read_id3(path) -> dict`: `ID3(path)` in try/except-everything → `{}` (broken tags must not wedge the fetch); extracts first non-empty TIT2/TDRC-or-TDRL/COMM/TRCK (int prefix before `/`)
- `_build_episode(fileinfo, item, podcast, config) -> Episode`:
  - guid = `f"{item.info_hash}:{fileinfo.relative_path.as_posix()}"`
  - raw_title = TIT2 → filename stem → item.title; title = `clean_title(raw_title, config.get("title_cleaning") or None, published=published, episode_number=track)`
  - published = `to_rfc2822(id3 date)` → `to_rfc2822(item.published)` → file-mtime formatted RFC 2822
  - description = COMM → item.description, blacklist applied; episode_number = TRCK; audio_url/duration/image_url = None
  - slug = `slugify(title)` deduped against `podcast.episodes` (counter suffix, same as feed.py)
- `_destination_filenames(episodes, fileinfos, effective_title)`: `episode_basename(...) + ".mp3"`; basenames colliding *within the torrent* get `-{sha256(relative_path.as_posix())[:8]}` before extension — deterministic, no fs probing
- `_copy_audio(src, dest) -> int`: mkdir, skip if dest exists with equal size, else `shutil.copyfile`; return size
- `_spawn_episodes(item, mp3_files, podcast, podcast_dir, config)`: reuse existing Episode from `{ep.guid: ep}` map when present (idempotent re-run); a pre-existing `download` status wins for filename; copy audio; synthesize `ep.status["download"] = StepStatus(completed_at=now, result={"path": f"audio/{filename}", "size_bytes": size})` only if absent; append guid to `item.episode_guids` (no dupes); `ep.save(podcast_dir, podcast.title)`
- Tests (`tests/test_torrent_fetch.py`, new): to_rfc2822 (RFC2822 passthrough, ISO conversion, garbage→None); ID3 helpers via real files built with `mutagen.ID3().save()` over placeholder bytes; fallback chains; unreadable-tags fallback; collision suffix determinism; wiring of clean_title (patch it, assert called + title used); published always `parsedate_to_datetime`-parseable

### Task 6: `torrent_fetch.py` — state machine

```python
def fetch_torrent_item(item, podcast, podcast_dir, config, client) -> None:
    blob_path = podcast_dir / "torrent_files" / f"{guid_hash(item.guid)}.torrent"
    if not item.info_hash:                      # State 1: pure download-and-hash
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(_fetch_blob(item.torrent_url))
        item.info_hash = _read_info_hash(blob_path)   # torf, local — crash-safe
        item.save(podcast_dir)                  # falls through, no wasted cycle
    if not client.has_torrent(item.info_hash):  # State 2: ensure present, then wait
        if not blob_path.exists():
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            blob_path.write_bytes(_fetch_blob(item.torrent_url))
        client.add_torrent(blob_path, config["client"]["save_path"])
        return
    if not client.is_complete(item.info_hash):
        return
    mp3s = [f for f in client.get_files(item.info_hash) if f.relative_path.suffix.lower() == ".mp3"]
    if not mp3s:                                # terminal, not retried forever
        logger.warning(...); item.fetched_at = now; item.save(podcast_dir); return
    _spawn_episodes(item, mp3s, podcast, podcast_dir, config)
    item.fetched_at = now; item.save(podcast_dir)
```
- `_fetch_blob(url)`: `httpx.get(url, follow_redirects=True, timeout=60)` + raise_for_status
- `fetch_torrents(items, podcast, output_dir, config)`: skip `fetched_at` items; no-op early return if none pending; one `get_torrent_client(config.get("client", {}))`; per-item try/except with `logger.exception` (one bad torrent doesn't block the rest)
- Tests: `FakeTorrentClient` (sets for has/complete, dict for files, list recording adds); patch `torrent_fetch._fetch_blob` and `torrent_fetch._read_info_hash`. Cover: State-1 blob+hash persisted then add in same call; re-add after client deletion; blob re-fetch when missing; downloading → no add/no spawn; State-3 N MP3s → N episodes with `hash:relpath` guids + download status shape; non-MP3 skipped; no-MP3 → warn + fetched_at + 0 guids; fetched_at item → client never touched; partial-spawn re-run idempotent (no dup episodes, statuses preserved, no re-copy); per-item exception isolation

### Task 7: Service integration + poller refactor

- `service.py`:
  - `fetch_feed`: dispatch `if resolved_config.get("source", "rss") == "unit3d": parse_unit3d_feed(...) else parse_feed(...)`
  - `filter_torrent_items(items, last=None, episode_filter=None)`: mirrors `filter_episodes` (last first, then regex on raw `item.title`)
  - `run_pipeline`: for unit3d — `items = filter_torrent_items(podcast.torrent_items, last=last, episode_filter=ep_filter)`; `fetch_torrents(items, podcast, output_dir, resolved_config)`; `episodes = podcast.episodes` (torrent-level filtering only; spawned episodes not re-filtered). RSS path unchanged
  - `validate_config`: per feed, `resolved.get("source", "rss") in ("rss", "unit3d")` else error
  - `KNOWN_FEED_FIELDS` += `"source"`
- `poller.py`: both loops replace the parse_feed + inline-Pipeline blocks with `podcast = fetch_feed(url, output_dir, resolved)` + `run_pipeline(podcast, output_dir, resolved, last=resolved.get("last"))` (async via `asyncio.to_thread`). This dedupes 3 entry points and gives the fetch phase everywhere. Drop now-unused imports
- Test updates: `test_poller.py`/`test_async_poller.py` — patch targets become `poller.fetch_feed` + `poller.run_pipeline`; enabled/disabled tests assert fetched urls; last/filter tests become assertions on the `resolved` config passed to `run_pipeline` (filtering logic itself is service-tested). `test_service.py` additions: source validation accept/reject, dispatch (patch `service.parse_unit3d_feed`/`service.parse_feed`), `filter_torrent_items`, run_pipeline fetch-phase ordering (patch `service.fetch_torrents`, assert episodes spawned by its side-effect reach `Pipeline.run` — patch `service.Pipeline`)

### Task 8: Web UI

- `templates/feeds/form.html`: `source` `<select>` (rss/unit3d, default rss) styled like neighboring fields
- `routes/feeds.py`: `_parse_feed_form` text_fields += `"source"`; `feed_detail` builds `torrents` context when `podcast.torrent_items` non-empty: `{title, published: format_date(...), info_hash, state, episode_count}` where state = fetched if `fetched_at` else downloading if `info_hash` else pending
- `templates/feeds/detail.html`: "Torrents" table above Episodes table (same styling), rendered only when torrents present
- Tests (`test_web.py`): source field in add/edit forms; POST with `source: unit3d` persisted; detail page shows torrents table when items exist on disk

### Task 9: Integration test + docs

- `test_integration.py` (marked `integration`): build a real MP3 fixture (placeholder bytes + real mutagen ID3 tags), create a real `.torrent` via `torf.Torrent`, fake client with `is_complete=True`/`get_files` pointing at tmp save dir; run `fetch_torrent_item` end-to-end; assert Episode JSON on disk with correct guid/title/slug, audio copied, synthesized download status present
- Save spec-derived detailed plan to `docs/superpowers/plans/2026-07-17-torrent-feed-ingestion.md` (per superpowers convention)
- `CLAUDE.md`: `source` config, `unit3d_feed.py`/`torrent_fetch.py` modules, fetch phase, new test files
- `README.md`: `source: unit3d` config + torrent-source workflow

## Verification

1. `uv run pytest tests/ -v` green after every task; full suite incl. integration at the end: `uv run pytest tests/ -v -m ''`
2. End-to-end smoke via `/verify` after Task 9: config with a `source: unit3d` feed pointing at a local fixture RSS file (feedparser accepts file paths), fake/real qBittorrent unavailable → confirm graceful per-item error + retry semantics in logs; then with mocked client confirm episodes spawn and a `pipeline: [tag]` run tags the copied audio
3. Web UI: `uv run podcast-etl serve`, add a unit3d feed, confirm source dropdown persists and detail page renders

## Out of scope (per spec)

FeedSource registry, non-qBittorrent clients, same-tracker re-upload, orphan cleanup, APIC for torrent episodes, leech/seed path separation.
