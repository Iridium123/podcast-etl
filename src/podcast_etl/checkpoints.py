from __future__ import annotations

import json
import logging
from pathlib import Path

from podcast_etl.atomic import atomic_write_text
from podcast_etl.models import Episode, StepStatus, episode_guid_hash, episode_json_filename

logger = logging.getLogger(__name__)


def checkpoint_filename(episode: Episode) -> str:
    """Filename for episode's checkpoint, matching its episodes/<stem>.json stem."""
    return episode_json_filename(episode.guid, episode.raw_title or episode.title, episode.published) + ".json"


def load_json_dict(path: Path) -> dict | None:
    """Tolerant JSON load: None for unreadable, invalid, or non-dict content."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def find_checkpoint(directory: Path, episode: Episode) -> dict | None:
    """Return the checkpoint payload for episode's GUID, or None if there isn't one.

    Matches by episode_guid_hash in the filename (survives title/slug renames) but verifies
    the payload's own guid field, since a hash-suffix match alone doesn't prove identity.
    """
    hash_suffix = episode_guid_hash(episode.guid)
    for path in sorted(directory.glob(f"*-{hash_suffix}.json")):
        payload = load_json_dict(path)
        if payload is None:
            logger.warning("Unreadable checkpoint %s", path)
            continue
        if payload.get("guid") == episode.guid:
            return payload
    return None


def write_checkpoint(directory: Path, episode: Episode, data: dict, info_hash: str | None) -> dict:
    """Write episode's checkpoint atomically and clean up any stale same-guid checkpoint left by a rename."""
    # Identity keys last so a tracker/client result can never overwrite them.
    payload = {**data, "guid": episode.guid, "title": episode.title, "info_hash": info_hash}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / checkpoint_filename(episode)
    atomic_write_text(path, json.dumps(payload))

    hash_suffix = episode_guid_hash(episode.guid)
    for other in directory.glob(f"*-{hash_suffix}.json"):
        if other == path:
            continue
        other_payload = load_json_dict(other)
        if other_payload is not None and other_payload.get("guid") == episode.guid:
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
