"""Fetch phase for torrent-source feeds.

This is NOT a pipeline step -- service.run_pipeline calls fetch_torrents
before the pipeline runs. Each poll cycle advances each TorrentItem one
state: fetch the .torrent blob, hand it to the torrent client, and once
the download completes spawn Episode objects (one per MP3 in the torrent)
that flow through the existing pipeline unchanged.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path

import httpx
from mutagen.id3 import ID3

from podcast_etl.clients import (
    TorrentClient,
    TorrentFileInfo,
    get_torrent_client,
    read_info_hash,
)
from podcast_etl.models import (
    Episode,
    Podcast,
    StepStatus,
    TorrentItem,
    episode_basename,
    guid_hash,
    slugify,
)
from podcast_etl.text import apply_blacklist
from podcast_etl.title_clean import clean_title

logger = logging.getLogger(__name__)


def to_rfc2822(value: str | None) -> str | None:
    """Normalize an RFC 2822 or ISO 8601 date string to RFC 2822.

    Downstream steps (TagStep) parse Episode.published with
    parsedate_to_datetime, which rejects ISO dates -- the natural format of
    ID3 TDRC values -- so every date is normalized here. None if unparseable.
    """
    if not value:
        return None
    try:
        return format_datetime(parsedate_to_datetime(value))
    except Exception:
        pass
    try:
        return format_datetime(datetime.fromisoformat(value))
    except Exception:
        return None


def _read_id3(path: Path) -> dict:
    """Extract title/date/comment/track from a file's ID3 tags.

    Broken or absent tags must never wedge the fetch -- episodes still
    spawn with fallback metadata -- so any read failure returns {}.
    """
    try:
        tags = ID3(path)
    except Exception:
        return {}

    def first_text(frame_id: str) -> str | None:
        for frame in tags.getall(frame_id):
            for text in frame.text:
                text = str(text)
                if text:
                    return text
        return None

    result: dict = {}
    title = first_text("TIT2")
    if title:
        result["title"] = title
    date = first_text("TDRC") or first_text("TDRL")
    if date:
        result["date"] = date
    comment = first_text("COMM")
    if comment:
        result["comment"] = comment
    track = first_text("TRCK")
    if track:
        try:
            result["track"] = int(track.split("/")[0])
        except ValueError:
            pass
    return result


def _mtime_rfc2822(path: Path) -> str:
    return format_datetime(datetime.fromtimestamp(path.stat().st_mtime).astimezone())


def _episode_guid(item: TorrentItem, fileinfo: TorrentFileInfo) -> str:
    """Stable episode identity: same torrent re-fetched yields the same guid."""
    return f"{item.info_hash}:{fileinfo.relative_path.as_posix()}"


def _to_local_path(path: Path, config: dict) -> Path:
    """Rebase a client-reported path onto this process's torrent_data_dir.

    Inverse of the stage step's _to_client_path: torrent_data_dir (our view)
    and client.save_path (the client's view) are two mounts of the same
    volume, so a file the client reports at save_path/<name> is readable
    here at torrent_data_dir/<name>. Paths outside save_path (e.g. torrents
    that predate this pipeline) are returned unchanged.
    """
    save_path = config.get("client", {}).get("save_path", "")
    data_dir = config.get("torrent_data_dir")
    if not save_path or not data_dir:
        return path
    try:
        relative = path.relative_to(save_path)
    except ValueError:
        return path
    return Path(data_dir) / relative


def _build_episode(
    fileinfo: TorrentFileInfo,
    item: TorrentItem,
    podcast: Podcast,
    config: dict,
    used_slugs: set[str] | None = None,
) -> Episode:
    id3 = _read_id3(fileinfo.absolute_path)

    raw_title = id3.get("title") or fileinfo.relative_path.stem or item.title
    published = (
        to_rfc2822(id3.get("date"))
        or to_rfc2822(item.published)
        or _mtime_rfc2822(fileinfo.absolute_path)
    )
    description = id3.get("comment") or item.description
    if config.get("blacklist") and description:
        description = apply_blacklist(description, config["blacklist"])

    title = clean_title(
        raw_title,
        config.get("title_cleaning") or None,
        published=published,
        episode_number=id3.get("track"),
    )

    # Deduplicate slugs against already-known episodes (mirrors feed.py)
    slug = slugify(title)
    base_slug = slug
    counter = 1
    if used_slugs is None:
        used_slugs = {ep.slug for ep in podcast.episodes}
    while slug in used_slugs:
        counter += 1
        slug = f"{base_slug}-{counter}"
    used_slugs.add(slug)

    return Episode(
        title=title,
        guid=_episode_guid(item, fileinfo),
        published=published,
        audio_url=None,
        duration=None,
        description=description,
        slug=slug,
        episode_number=id3.get("track"),
        raw_title=raw_title,
    )


def _destination_filenames(
    episodes: list[Episode],
    fileinfos: list[TorrentFileInfo],
    effective_title: str,
    claimed: set[str] | None = None,
) -> list[str]:
    """Destination audio filenames for a torrent's episodes.

    Basenames colliding within the torrent -- or with a filename already
    claimed by another torrent's episode (*claimed*) -- get a short sha256
    suffix derived from the file's path inside the torrent. Deterministic:
    the same torrent always yields the same names (claimed names come from
    persisted download statuses), which is what makes interrupted-spawn
    retry idempotent.
    """
    claimed = claimed or set()
    basenames = [
        episode_basename(effective_title, ep.title, ep.published) for ep in episodes
    ]
    counts: dict[str, int] = {}
    for name in basenames:
        counts[name] = counts.get(name, 0) + 1
    filenames = []
    for name, fi in zip(basenames, fileinfos):
        if counts[name] > 1 or f"{name}.mp3" in claimed:
            suffix = hashlib.sha256(fi.relative_path.as_posix().encode()).hexdigest()[:8]
            name = f"{name}-{suffix}"
        filenames.append(name + ".mp3")
    return filenames


def _copy_audio(src: Path, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_size = src.stat().st_size
    if dest.exists() and dest.stat().st_size == src_size:
        logger.debug("Audio already copied: %s", dest)
        return src_size
    shutil.copyfile(src, dest)
    return dest.stat().st_size


def _spawn_episodes(
    item: TorrentItem,
    mp3_files: list[TorrentFileInfo],
    podcast: Podcast,
    podcast_dir: Path,
    config: dict,
) -> None:
    existing = {ep.guid: ep for ep in podcast.episodes}
    effective_title = config.get("title_override") or podcast.title

    episodes: list[Episode] = []
    used_slugs = {ep.slug for ep in podcast.episodes}
    for fileinfo in mp3_files:
        guid = _episode_guid(item, fileinfo)
        if guid in existing:
            # Idempotent re-run: preserve step status of already-spawned episodes
            episodes.append(existing[guid])
        else:
            episodes.append(_build_episode(fileinfo, item, podcast, config, used_slugs))

    # Filenames already claimed by other torrents' episodes must not be reused:
    # a same-titled episode in a second torrent would otherwise clobber (or
    # silently share) the first one's audio file.
    batch_guids = {ep.guid for ep in episodes}
    claimed = {
        Path(ep.status["download"].result["path"]).name
        for ep in podcast.episodes
        if ep.guid not in batch_guids
        and ep.status.get("download")
        and ep.status["download"].result.get("path")
    }
    filenames = _destination_filenames(episodes, mp3_files, effective_title, claimed)

    for episode, fileinfo, filename in zip(episodes, mp3_files, filenames):
        download = episode.status.get("download")
        if download and download.result.get("path"):
            # A previously recorded filename wins -- consistency across re-runs
            filename = Path(download.result["path"]).name
        size = _copy_audio(fileinfo.absolute_path, podcast_dir / "audio" / filename)
        if not download:
            episode.status["download"] = StepStatus(
                completed_at=datetime.now().isoformat(),
                result={"path": f"audio/{filename}", "size_bytes": size},
            )
        # Only a successfully-copied episode joins the podcast: a copy failure
        # mid-torrent must not leak audio-less episodes into the pipeline.
        if episode.guid not in existing:
            podcast.episodes.append(episode)
            existing[episode.guid] = episode
        if episode.guid not in item.episode_guids:
            item.episode_guids.append(episode.guid)
        episode.save(podcast_dir, podcast.title)


def _fetch_blob(url: str) -> bytes:
    resp = httpx.get(url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    return resp.content


def fetch_torrent_item(
    item: TorrentItem,
    podcast: Podcast,
    podcast_dir: Path,
    config: dict,
    client: TorrentClient,
) -> None:
    """Advance one torrent item through its state machine.

    Three states, one advance per call, crash-safe:

    1. No info_hash: download the .torrent blob and record the locally
       computed info hash (never trust the client's add response) -- a
       crash at any point just repeats byte-identically. Falls through.
    2. Client doesn't have the torrent: add it from the stored blob
       (re-fetching the blob if missing). This branch doubles as recovery:
       a torrent deleted from the client gets re-added -- deletion in
       qBittorrent is the supported retry gesture. Waits while incomplete.
    3. Download complete: spawn one Episode per MP3 and mark fetched.
    """
    blob_path = podcast_dir / "torrent_files" / f"{guid_hash(item.guid)}.torrent"

    if not item.info_hash:
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(_fetch_blob(item.torrent_url))
        item.info_hash = read_info_hash(blob_path)
        item.save(podcast_dir)
        logger.info("Fetched torrent blob for %s (%s)", item.title, item.info_hash)
        # Fall through -- no wasted poll cycle

    if not client.has_torrent(item.info_hash):
        if not blob_path.exists():
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            blob_path.write_bytes(_fetch_blob(item.torrent_url))
        client.add_torrent(blob_path, config["client"]["save_path"])
        logger.info("Added torrent %s (%s) to client", item.title, item.info_hash)
        return

    if not client.is_complete(item.info_hash):
        return

    files = []
    for f in client.get_files(item.info_hash):
        # Defense-in-depth: the file list ultimately comes from the tracker's
        # torrent content. Clients sanitize traversal themselves, but don't
        # rely on it — never copy from outside the reported roots.
        if f.relative_path.is_absolute() or ".." in f.relative_path.parts:
            logger.warning(
                "Skipping suspicious path in torrent %s: %s", item.info_hash, f.relative_path
            )
            continue
        files.append(
            TorrentFileInfo(
                absolute_path=_to_local_path(f.absolute_path, config),
                relative_path=f.relative_path,
            )
        )
    mp3s = [f for f in files if f.relative_path.suffix.lower() == ".mp3"]
    if not mp3s:
        logger.warning(
            "Torrent %s (%s) contains no MP3 files; marking fetched with 0 episodes",
            item.title,
            item.info_hash,
        )
        item.fetched_at = datetime.now().isoformat()
        item.save(podcast_dir)
        return

    _spawn_episodes(item, mp3s, podcast, podcast_dir, config)
    item.fetched_at = datetime.now().isoformat()
    item.save(podcast_dir)
    logger.info(
        "Torrent %s complete: spawned %d episode(s)", item.title, len(mp3s)
    )


def fetch_torrents(
    items: list[TorrentItem], podcast: Podcast, output_dir: Path, config: dict
) -> None:
    """Advance all pending torrent items one state each."""
    pending = [item for item in items if not item.fetched_at]
    if not pending:
        return
    podcast_dir = podcast.podcast_dir(output_dir)
    try:
        client = get_torrent_client(config.get("client", {}))
    except Exception:
        # A misconfigured client must not abort the caller (e.g. `run --all`
        # would skip every remaining feed); log and retry next cycle.
        logger.exception("Cannot build torrent client; skipping torrent fetch")
        return
    for item in pending:
        try:
            fetch_torrent_item(item, podcast, podcast_dir, config, client)
        except Exception:
            logger.exception("Torrent fetch failed for %s", item.title)
