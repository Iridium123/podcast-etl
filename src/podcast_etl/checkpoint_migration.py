"""One-time-per-podcast migration: slug-keyed seed/upload checkpoints -> GUID-keyed.

``seeds/<slug>.json`` and ``uploads/<slug>.json`` used to be keyed on
``episode.slug``. Because ``feed.py`` dedups slugs newest-first, a new episode
that repeats an old title can inherit the old episode's slug and, with it, its
stale checkpoint ("poisoned": the new episode's own status now holds the old
episode's hash/URL). This module detects that on disk and heals it, then
replaces the legacy files with GUID-keyed ones via ``checkpoints.write_checkpoint``.

Poisoning is only ever declared from *cross-guid reuse* (the same seed hash or
upload URL recorded under two different GUIDs), and it is per-step: a shared
seed hash poisons only the guids' ``seed`` step, a shared upload URL only
their ``upload`` step. A guid can be poisoned for one step and migrated
normally for the other. Within a group of guids sharing a value, the OWNER
(the one guid that is *not* poisoned) is chosen by evidence where possible --
the guid whose own ``torrent.info_hash`` matches the shared seed hash, or the
guid that is "self-consistent" (its own seed hash matches its own torrent's
info_hash) for a shared upload URL -- and only falls back to earliest
``completed_at`` when no guid in the group (or more than one) satisfies that.
The evidence check exists because a legitimate ``--overwrite`` re-run can give
the true owner a *later* completed_at than the guid it accidentally poisoned;
earliest-wins alone would misidentify the owner in that case.

A guid whose own seed hash disagrees with its own torrent's info_hash, but
isn't implicated in any cross-guid reuse, is merely ``suspect``: left
untouched and logged (no checkpoint written for it at all, seed or upload),
since that signal alone can also be produced by a legitimate torrent
re-creation and clearing it would risk a duplicate tracker upload.

Guids skipped for a same-guid conflict (disagreeing upload URLs across
duplicate episode JSON files) still take part in reuse detection -- a value
recorded under a conflicted guid can still poison another guid that inherited
it -- but a conflicted guid is itself never written to or cleared; it's
already reported in ``skipped_conflict``.

Legacy checkpoint files whose hash/url value is claimed by no episode's
status (of any guid, checked before poisoned status is cleared) are reported
in ``unclaimed`` -- that legacy file may represent completed work that was
never recorded, so a duplicate upload is possible and warrants a manual
check. They are swept to ``.legacy/`` like any other legacy file regardless.

``migrate_checkpoints`` only ever reads/writes JSON under ``episodes/``,
``seeds/`` and ``uploads/`` (never audio, torrents, or images).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from podcast_etl.atomic import atomic_write_text
from podcast_etl.checkpoints import find_checkpoint, load_json_dict, resolve_duplicate_statuses, write_checkpoint
from podcast_etl.models import Episode, StepStatus

logger = logging.getLogger(__name__)

# episode_guid_hash() is 8 hex chars; a filename ending this way is new-style
# (payload-verified elsewhere) and never worth parsing just to decide whether
# a migration is due.
_GUID_SUFFIX_RE = re.compile(r"-[0-9a-f]{8}\.json$")


@dataclass
class MigrationReport:
    migrated: list[str] = field(default_factory=list)
    poisoned: list[str] = field(default_factory=list)
    # guid -> sorted poisoned step names, for callers that need to reconcile
    # in-memory Episode objects rather than just log the report.
    poisoned_steps: dict[str, list[str]] = field(default_factory=dict)
    suspect: list[str] = field(default_factory=list)
    skipped_conflict: list[str] = field(default_factory=list)
    unclaimed: list[str] = field(default_factory=list)
    legacy_moved: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.migrated or self.poisoned or self.suspect
            or self.skipped_conflict or self.unclaimed or self.legacy_moved
        )

    def __str__(self) -> str:
        counts = ", ".join(
            f"{name}={len(value)}"
            for name, value in (
                ("migrated", self.migrated),
                ("poisoned", self.poisoned),
                ("suspect", self.suspect),
                ("skipped_conflict", self.skipped_conflict),
                ("unclaimed", self.unclaimed),
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
        raw = load_json_dict(path)
        if raw is None:
            logger.warning("Unreadable episode JSON, skipping: %s", path)
            continue
        try:
            episode = Episode.from_dict(raw)
        except KeyError as exc:
            logger.warning("Malformed episode JSON %s: missing %s", path, exc)
            continue
        groups.setdefault(episode.guid, []).append((path, raw, episode))

    # Step 1: merge duplicate-file status per guid, for ALL guids -- including
    # ones with a same-guid conflict, which still need to take part in
    # cross-guid reuse detection (a value they hold can poison another guid).
    canonical: dict[str, Episode] = {}
    merged_status: dict[str, dict[str, StepStatus]] = {}
    conflicts: set[str] = set()
    for guid, entries in groups.items():
        episodes = [ep for _, _, ep in entries]
        canonical[guid] = episodes[-1]
        merged_status[guid] = resolve_duplicate_statuses(episodes)
        if len(entries) > 1 and _has_upload_url_conflict(episodes):
            label = _label(episodes[-1], guid)
            logger.warning("Conflicting upload URLs for guid=%s in %s; skipping", guid, podcast_dir)
            report.skipped_conflict.append(label)
            conflicts.add(guid)

    # Step 2: poison/suspect detection, status only — must happen before any write.
    poisoned_steps, suspect = _detect_poisoned(merged_status, conflicts, podcast_dir)

    # Step 3: clear poisoned step(s) in every file of that guid. No checkpoint
    # is ever written for a poisoned step (that's what would re-poison it).
    # Conflicted guids are never touched, even if detection flags them.
    for guid in sorted(poisoned_steps):
        if guid in conflicts:
            continue
        steps = poisoned_steps[guid]
        episode = canonical[guid]
        step_list = ", ".join(sorted(steps))
        logger.warning(
            "Poisoned checkpoint for guid=%s (%s) in %s; clearing %s status",
            guid, episode.title, podcast_dir, step_list,
        )
        report.poisoned.append(f"{_label(episode, guid)}: {step_list}")
        report.poisoned_steps[guid] = sorted(steps)
        if dry_run:
            continue
        for path, raw, _episode in groups[guid]:
            status = raw.get("status", {})
            changed = False
            for step_name in steps:
                if step_name in status:
                    del status[step_name]
                    changed = True
            if changed:
                atomic_write_text(path, json.dumps(raw, indent=2) + "\n")

    # Step 3b: suspect guids (seed-only signal) are left alone — logged only,
    # no status change, no checkpoint at all (writing one would freeze the
    # mismatch in as "resolved").
    for guid in sorted(suspect):
        episode = canonical[guid]
        logger.warning(
            "Suspect checkpoint for guid=%s (%s) in %s: seed hash does not match its "
            "torrent's info_hash; not cleared automatically — check/reset manually",
            guid, episode.title, podcast_dir,
        )
        report.suspect.append(_label(episode, guid))

    # Step 4: write new-style checkpoints for everything else, from status.
    for guid, status in merged_status.items():
        if guid in conflicts:
            continue
        episode = canonical[guid]
        guid_poisoned = poisoned_steps.get(guid, set())
        is_suspect = guid in suspect
        skip_seed = "seed" in guid_poisoned or is_suspect
        skip_upload = "upload" in guid_poisoned or is_suspect
        if _write_checkpoints_for(podcast_dir, episode, status, dry_run, skip_seed, skip_upload):
            report.migrated.append(_label(episode, guid))

    # Step 5: sweep legacy (no-guid-payload or unreadable) files into .legacy/,
    # reporting any whose value matches no episode's (pre-clearing) status.
    claimed = {
        "seeds": {s["seed"].result.get("hash") for s in merged_status.values() if s.get("seed")},
        "uploads": {s["upload"].result.get("url") for s in merged_status.values() if s.get("upload")},
    }
    value_field = {"seeds": "hash", "uploads": "url"}
    for sub in ("seeds", "uploads"):
        directory = podcast_dir / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            payload = load_json_dict(path)
            if not _is_legacy_payload(payload):
                continue
            rel = str(path.relative_to(podcast_dir))
            if payload is None:
                value: object = "unreadable"
                claimed_ok = False
            else:
                value = payload.get(value_field[sub])
                claimed_ok = value in claimed[sub]
            if not claimed_ok:
                report.unclaimed.append(f"{rel}: {value}")
                logger.warning(
                    "Legacy checkpoint %s matches no episode status (value=%s); work may have completed "
                    "without status being saved -- check manually to avoid a duplicate upload",
                    rel, value,
                )
            report.legacy_moved.append(rel)
            if not dry_run:
                _move_to_legacy(path)

    return report


def _label(episode: Episode, guid: str) -> str:
    return f"{episode.title} ({guid})"


def _is_legacy_payload(payload: dict | None) -> bool:
    """Legacy checkpoints predate GUID-keying and carry no 'guid' key (or are unreadable)."""
    return payload is None or not payload.get("guid")


def _has_legacy_checkpoints(podcast_dir: Path) -> bool:
    """Cheap trigger: only parse files whose name doesn't already look new-style."""
    for sub in ("seeds", "uploads"):
        directory = podcast_dir / sub
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            if _GUID_SUFFIX_RE.search(path.name):
                continue
            if _is_legacy_payload(load_json_dict(path)):
                return True
    return False


def _has_upload_url_conflict(episodes: list[Episode]) -> bool:
    urls = {
        ep.status["upload"].result.get("url")
        for ep in episodes
        if ep.status.get("upload") is not None
    }
    return len(urls) > 1


def _is_self_consistent(status: dict[str, StepStatus]) -> bool:
    seed = status.get("seed")
    torrent = status.get("torrent")
    return bool(seed) and bool(torrent) and seed.result.get("hash") == torrent.result.get("info_hash")


def _seed_owner(entries: list[tuple[str, StepStatus]], value: str, merged_status: dict) -> str | None:
    """Owner is the guid whose own torrent info_hash equals the shared seed hash, if exactly one is."""
    matches = [
        guid for guid, _ in entries
        if (torrent := merged_status[guid].get("torrent")) is not None and torrent.result.get("info_hash") == value
    ]
    return matches[0] if len(matches) == 1 else None


def _upload_owner(entries: list[tuple[str, StepStatus]], value: str, merged_status: dict) -> str | None:
    """Owner is the guid that is self-consistent (seed hash == own torrent info_hash), if exactly one is."""
    matches = [guid for guid, _ in entries if _is_self_consistent(merged_status[guid])]
    return matches[0] if len(matches) == 1 else None


def _detect_poisoned(
    merged_status: dict[str, dict[str, StepStatus]], conflicts: set[str], podcast_dir: Path,
) -> tuple[dict[str, set[str]], set[str]]:
    poisoned_steps: dict[str, set[str]] = {}
    for guid, steps in _poison_by_reuse(merged_status, "seed", "hash", podcast_dir, _seed_owner).items():
        poisoned_steps.setdefault(guid, set()).update(steps)
    for guid, steps in _poison_by_reuse(merged_status, "upload", "url", podcast_dir, _upload_owner).items():
        poisoned_steps.setdefault(guid, set()).update(steps)

    # This guid's own seed hash disagrees with its own torrent info_hash, and
    # its seed step wasn't already poisoned by cross-guid reuse. On its own
    # this can also mean a legitimate torrent re-creation, so it only earns
    # "suspect" (logged, untouched). Conflicted guids are never flagged --
    # they're already reported and left alone.
    suspect: set[str] = set()
    for guid, status in merged_status.items():
        if guid in conflicts or "seed" in poisoned_steps.get(guid, set()):
            continue
        seed = status.get("seed")
        torrent = status.get("torrent")
        if not seed or not torrent:
            continue
        if seed.result.get("hash") != torrent.result.get("info_hash"):
            suspect.add(guid)

    return poisoned_steps, suspect


def _poison_by_reuse(
    merged_status: dict[str, dict[str, StepStatus]],
    step_name: str,
    result_key: str,
    podcast_dir: Path,
    owner_fn: Callable[[list[tuple[str, StepStatus]], str, dict], str | None],
) -> dict[str, set[str]]:
    """Flag every guid but the OWNER (by owner_fn, else earliest completed_at) that shares a value."""
    by_value: dict[object, list[tuple[str, StepStatus]]] = {}
    for guid, status in merged_status.items():
        step = status.get(step_name)
        if not step:
            continue
        value = step.result.get(result_key)
        if not value:
            continue
        by_value.setdefault(value, []).append((guid, step))

    poisoned: dict[str, set[str]] = {}
    for value, entries in by_value.items():
        if len({guid for guid, _ in entries}) < 2:
            continue

        owner = owner_fn(entries, value, merged_status)
        if owner is None:
            timestamps = [status.completed_at for _, status in entries]
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
            owner = next(guid for guid, status in entries if status.completed_at == earliest)

        for guid, _ in entries:
            if guid != owner:
                poisoned.setdefault(guid, set()).add(step_name)

    return poisoned


def _write_checkpoints_for(
    podcast_dir: Path, episode: Episode, status: dict[str, StepStatus], dry_run: bool,
    skip_seed: bool, skip_upload: bool,
) -> bool:
    torrent = status.get("torrent")
    info_hash = torrent.result.get("info_hash") if torrent else None
    wrote = False

    seed = status.get("seed")
    if seed is not None and not skip_seed:
        seeds_dir = podcast_dir / "seeds"
        if find_checkpoint(seeds_dir, episode) is None:
            wrote = True
            if not dry_run:
                data = {k: seed.result[k] for k in ("client", "hash") if k in seed.result}
                write_checkpoint(seeds_dir, episode, data=data, info_hash=info_hash)

    upload = status.get("upload")
    if upload is not None and not skip_upload:
        uploads_dir = podcast_dir / "uploads"
        if find_checkpoint(uploads_dir, episode) is None:
            wrote = True
            if not dry_run:
                write_checkpoint(uploads_dir, episode, data=dict(upload.result), info_hash=info_hash)

    return wrote


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
