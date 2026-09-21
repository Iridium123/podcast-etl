"""One-time-per-podcast migration: slug-keyed seed/upload checkpoints -> GUID-keyed.

``seeds/<slug>.json`` and ``uploads/<slug>.json`` used to be keyed on
``episode.slug``. Because ``feed.py`` dedups slugs newest-first, a new episode
that repeats an old title can inherit the old episode's slug and, with it, its
stale checkpoint ("poisoned": the new episode's own status now holds the old
episode's hash/URL). This module detects that on disk and heals it, then
replaces the legacy files with GUID-keyed ones via ``checkpoints.write_checkpoint``.

Poisoning is only ever declared from *cross-guid reuse* (the same seed hash or
upload URL recorded under two different GUIDs) -- a poisoned GUID has its
seed/upload status cleared and gets no checkpoint. A GUID whose own seed hash
disagrees with its own torrent's info_hash, but isn't implicated in any
cross-guid reuse, is merely ``suspect``: left untouched and logged, since that
signal alone can also be produced by a legitimate torrent re-creation and
clearing it would risk a duplicate tracker upload.

``migrate_checkpoints`` only ever reads/writes JSON under ``episodes/``,
``seeds/`` and ``uploads/`` (never audio, torrents, or images).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from podcast_etl.checkpoints import find_checkpoint, resolve_duplicate_statuses, write_checkpoint
from podcast_etl.models import Episode

logger = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    migrated: list[str] = field(default_factory=list)
    poisoned: list[str] = field(default_factory=list)
    # Bare GUIDs (not display labels) of `poisoned`, for callers that need to
    # reconcile in-memory Episode objects rather than just log the report.
    poisoned_guids: list[str] = field(default_factory=list)
    suspect: list[str] = field(default_factory=list)
    skipped_conflict: list[str] = field(default_factory=list)
    legacy_moved: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.migrated or self.poisoned or self.suspect or self.skipped_conflict or self.legacy_moved)

    def __str__(self) -> str:
        counts = ", ".join(
            f"{name}={len(value)}"
            for name, value in (
                ("migrated", self.migrated),
                ("poisoned", self.poisoned),
                ("suspect", self.suspect),
                ("skipped_conflict", self.skipped_conflict),
                ("legacy_moved", self.legacy_moved),
            )
            if value
        )
        return f"checkpoint migration: {counts}" if counts else "checkpoint migration: nothing to do"


def migrate_checkpoints(podcast_dir: Path, dry_run: bool = False) -> MigrationReport:
    report = MigrationReport()
    if not _has_legacy_checkpoints(podcast_dir):
        return report

    episodes_dir = podcast_dir / "episodes"
    groups: dict[str, list[tuple[Path, dict, Episode]]] = {}
    for path in sorted(episodes_dir.glob("*.json")):
        raw = _load_json(path)
        if raw is None:
            logger.warning("Unreadable episode JSON, skipping: %s", path)
            continue
        try:
            episode = Episode.from_dict(raw)
        except KeyError as exc:
            logger.warning("Malformed episode JSON %s: missing %s", path, exc)
            continue
        groups.setdefault(episode.guid, []).append((path, raw, episode))

    # Step 1: merge duplicate-file status per guid; detect same-guid conflicts.
    canonical: dict[str, Episode] = {}
    merged_status = {}
    for guid, entries in groups.items():
        episodes = [ep for _, _, ep in entries]
        canonical[guid] = episodes[-1]
        if len(entries) > 1 and _has_upload_url_conflict(episodes):
            label = _label(episodes[-1], guid)
            logger.warning("Conflicting upload URLs for guid=%s in %s; skipping", guid, podcast_dir)
            report.skipped_conflict.append(label)
            continue
        merged_status[guid] = resolve_duplicate_statuses(episodes)

    # Step 2: poison detection, status only — must happen before any write.
    poisoned, suspect = _detect_poisoned(merged_status, podcast_dir)

    # Step 3: clear poisoned status in every file of that guid. No checkpoint
    # is ever written for a poisoned guid (that's what would re-poison it).
    for guid in poisoned:
        episode = canonical[guid]
        label = _label(episode, guid)
        logger.warning(
            "Poisoned checkpoint for guid=%s (%s) in %s; clearing seed/upload status",
            guid, episode.title, podcast_dir,
        )
        report.poisoned.append(label)
        report.poisoned_guids.append(guid)
        if dry_run:
            continue
        for path, raw, _episode in groups[guid]:
            status = raw.get("status", {})
            changed = False
            for step_name in ("seed", "upload"):
                if step_name in status:
                    del status[step_name]
                    changed = True
            if changed:
                _atomic_write_json(path, raw)

    # Step 3b: suspect guids are left alone — logged only, no status change,
    # no checkpoint (writing one would freeze the mismatch in as "resolved").
    for guid in suspect:
        episode = canonical[guid]
        logger.warning(
            "Suspect checkpoint for guid=%s (%s) in %s: seed hash does not match its "
            "torrent's info_hash; not cleared automatically — check/reset manually",
            guid, episode.title, podcast_dir,
        )
        report.suspect.append(_label(episode, guid))

    # Step 4: write new-style checkpoints for everything else, from status.
    for guid, status in merged_status.items():
        if guid in poisoned or guid in suspect:
            continue
        episode = canonical[guid]
        if _write_checkpoints_for(podcast_dir, episode, status, dry_run):
            report.migrated.append(_label(episode, guid))

    # Step 5: sweep legacy (no-guid-payload or unreadable) files into .legacy/.
    for sub in ("seeds", "uploads"):
        directory = podcast_dir / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if not _is_legacy_payload(_load_json(path)):
                continue
            report.legacy_moved.append(str(path.relative_to(podcast_dir)))
            if not dry_run:
                _move_to_legacy(path)

    return report


def _label(episode: Episode, guid: str) -> str:
    return f"{episode.title} ({guid})"


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _is_legacy_payload(payload: dict | None) -> bool:
    """Legacy checkpoints predate GUID-keying and carry no 'guid' key (or are unreadable)."""
    return payload is None or not payload.get("guid")


def _has_legacy_checkpoints(podcast_dir: Path) -> bool:
    for sub in ("seeds", "uploads"):
        directory = podcast_dir / sub
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            if _is_legacy_payload(_load_json(path)):
                return True
    return False


def _has_upload_url_conflict(episodes: list[Episode]) -> bool:
    urls = {
        ep.status["upload"].result.get("url")
        for ep in episodes
        if ep.status.get("upload") is not None
    }
    return len(urls) > 1


def _detect_poisoned(merged_status: dict, podcast_dir: Path) -> tuple[set[str], set[str]]:
    # (b) the same seed hash / upload url is shared across different guids —
    # the only signal that actually poisons (clears status, blocks a checkpoint).
    poisoned: set[str] = set()
    poisoned |= _poisoned_by_reuse(merged_status, "seed", "hash", podcast_dir)
    poisoned |= _poisoned_by_reuse(merged_status, "upload", "url", podcast_dir)

    # (a) this guid's own seed hash disagrees with its own torrent info_hash.
    # On its own this can also mean a legitimate torrent re-creation, so it
    # only earns "suspect" (logged, untouched) unless (b) already poisoned it.
    suspect: set[str] = set()
    for guid, status in merged_status.items():
        if guid in poisoned:
            continue
        seed = status.get("seed")
        torrent = status.get("torrent")
        if not seed or not torrent:
            continue
        seed_hash = seed.result.get("hash")
        info_hash = torrent.result.get("info_hash")
        if seed_hash != info_hash:
            suspect.add(guid)

    return poisoned, suspect


def _poisoned_by_reuse(merged_status: dict, step_name: str, result_key: str, podcast_dir: Path) -> set[str]:
    """Flag every guid but the earliest (the original owner) that shares a value."""
    by_value: dict[object, list[tuple[str, str]]] = {}
    for guid, status in merged_status.items():
        step = status.get(step_name)
        if not step:
            continue
        value = step.result.get(result_key)
        if not value:
            continue
        by_value.setdefault(value, []).append((guid, step.completed_at))

    poisoned: set[str] = set()
    for value, entries in by_value.items():
        if len({guid for guid, _ in entries}) < 2:
            continue
        timestamps = [ts for _, ts in entries]
        if not all(timestamps):
            logger.warning(
                "%s %r shared across guids with a missing completed_at in %s; not flagging either",
                step_name, value, podcast_dir,
            )
            continue
        earliest = min(timestamps)
        if timestamps.count(earliest) > 1:
            logger.warning(
                "%s %r shared across guids with tied earliest completed_at in %s; not flagging either",
                step_name, value, podcast_dir,
            )
            continue
        for guid, ts in entries:
            if ts != earliest:
                poisoned.add(guid)
    return poisoned


def _write_checkpoints_for(podcast_dir: Path, episode: Episode, status: dict, dry_run: bool) -> bool:
    torrent = status.get("torrent")
    info_hash = torrent.result.get("info_hash") if torrent else None
    wrote = False

    seed = status.get("seed")
    if seed is not None:
        seeds_dir = podcast_dir / "seeds"
        if find_checkpoint(seeds_dir, episode) is None:
            wrote = True
            if not dry_run:
                data = {k: seed.result[k] for k in ("client", "hash") if k in seed.result}
                write_checkpoint(seeds_dir, episode, data=data, info_hash=info_hash)

    upload = status.get("upload")
    if upload is not None:
        uploads_dir = podcast_dir / "uploads"
        if find_checkpoint(uploads_dir, episode) is None:
            wrote = True
            if not dry_run:
                write_checkpoint(uploads_dir, episode, data=dict(upload.result), info_hash=info_hash)

    return wrote


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _move_to_legacy(path: Path) -> None:
    legacy_dir = path.parent / ".legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    dest = legacy_dir / path.name
    counter = 1
    while dest.exists():
        dest = legacy_dir / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    try:
        shutil.move(str(path), str(dest))
    except FileNotFoundError:
        pass
