from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from podcast_etl.models import Episode
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)


@dataclass
class TorrentStep:
    name: str = "torrent"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        stage_status = episode.status.get("stage")
        if not stage_status:
            raise ValueError(f"Episode {episode.slug} has no completed 'stage' step")

        local_path = stage_status.result.get("local_path")
        if not local_path:
            raise ValueError(f"Episode {episode.slug} stage result has no 'local_path'")

        audio_path = Path(local_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        torrents_dir = context.podcast_dir / "torrents"
        torrents_dir.mkdir(parents=True, exist_ok=True)

        torrent_path = torrents_dir / f"{episode.slug}.torrent"

        if torrent_path.exists():
            logger.info("Torrent already exists: %s", torrent_path)
        else:
            tracker_config, announce_url = _get_tracker_info(context)
            comment = f"{episode.title} \u2014 {context.effective_title}"
            private = tracker_config.get("private", True)
            _run_mktorrent(audio_path, torrent_path, announce_url, comment, private=private)

        info_hash = _read_info_hash(torrent_path)

        return StepResult(data={
            "torrent_path": str(torrent_path),
            "info_hash": info_hash,
        })


def _get_tracker_info(context: PipelineContext) -> tuple[dict, str]:
    tracker_name = context.feed_config.get("tracker")
    trackers = context.config.get("settings", {}).get("trackers", {})

    if tracker_name:
        tracker_config = trackers.get(tracker_name)
    else:
        tracker_config = next(iter(trackers.values()), None) if trackers else None

    if not tracker_config:
        raise ValueError("No tracker configured; cannot determine announce URL")

    announce_url = tracker_config.get("announce_url")
    if not announce_url:
        raise ValueError("Tracker config missing 'announce_url'")

    return tracker_config, announce_url


def _run_mktorrent(audio_path: Path, torrent_path: Path, announce_url: str, comment: str, *, private: bool) -> None:
    cmd = ["mktorrent", "-a", announce_url, "-o", str(torrent_path), "-c", comment]
    if private:
        cmd.append("-p")
    cmd.append(str(audio_path))

    logger.info("Creating torrent: %s", torrent_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"mktorrent failed (exit {result.returncode}): {result.stderr.strip()}")


def _read_info_hash(torrent_path: Path) -> str:
    import torf
    t = torf.Torrent.read(str(torrent_path))
    return str(t.infohash).lower()
