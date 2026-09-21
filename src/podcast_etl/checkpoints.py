from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from podcast_etl.models import Episode, StepStatus, episode_guid_hash, episode_json_filename

logger = logging.getLogger(__name__)


def checkpoint_filename(episode: Episode) -> str:
    """Filename for episode's checkpoint, matching its episodes/<stem>.json stem."""
    return episode_json_filename(episode.guid, episode.raw_title or episode.title, episode.published) + ".json"


def find_checkpoint(directory: Path, episode: Episode) -> dict | None:
    """Return the checkpoint payload for episode's GUID, or None if there isn't one.

    Matches by episode_guid_hash in the filename (survives title/slug renames) but verifies
    the payload's own guid field, since a hash-suffix match alone doesn't prove identity.
    """
    hash_suffix = episode_guid_hash(episode.guid)
    candidates = set(directory.glob(f"*-{hash_suffix}.json"))
    exact = directory / f"{hash_suffix}.json"
    if exact.exists():
        candidates.add(exact)

    for path in sorted(candidates):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Unreadable checkpoint %s: %s", path, exc)
            continue
        if isinstance(payload, dict) and payload.get("guid") == episode.guid:
            return payload
    return None


def write_checkpoint(directory: Path, episode: Episode, data: dict, info_hash: str | None) -> dict:
    """Write episode's checkpoint atomically and clean up any stale same-guid checkpoint left by a rename."""
    payload = {"guid": episode.guid, "title": episode.title, "info_hash": info_hash, **data}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / checkpoint_filename(episode)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)

    hash_suffix = episode_guid_hash(episode.guid)
    for other in directory.glob(f"*-{hash_suffix}.json"):
        if other == path:
            continue
        try:
            other_payload = json.loads(other.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(other_payload, dict) and other_payload.get("guid") == episode.guid:
            other.unlink()

    return payload


def _status_sort_key(status: StepStatus) -> tuple[str, str]:
    # Break completed_at ties on serialized content so the merge is order-independent.
    return (status.completed_at, json.dumps(status.to_dict(), sort_keys=True))


def resolve_duplicate_statuses(episodes: list[Episode]) -> dict[str, StepStatus]:
    """Merge status dicts from Episode objects sharing one GUID, latest completed_at per step wins."""
    merged: dict[str, StepStatus] = {}
    for episode in episodes:
        for step_name, status in episode.status.items():
            if status is None:
                continue
            current = merged.get(step_name)
            if current is None or _status_sort_key(status) > _status_sort_key(current):
                merged[step_name] = status
    return merged
