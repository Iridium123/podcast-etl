"""Display a transcript with ad-segment highlights, for reviewing labels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import Labels

from eval.resolve import resolve_episode


def _covering_segment(time: float, segments: list[AdSegment]) -> AdSegment | None:
    """Return the ad segment containing *time*, or None."""
    for seg in segments:
        if seg.start <= time < seg.end:
            return seg
    return None


def format_review(labels: Labels, transcript: list[dict[str, Any]], audio_path: str) -> str:
    """Format transcript lines, marking those inside an ad segment.

    Ad lines are prefixed with U+258C (left half block) and tagged with the
    covering segment's range, so a reviewer can eyeball where labels start/end.
    """
    lines = [f"\nAudio: {audio_path}\n"]

    for seg in transcript:
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()

        ad = _covering_segment(start, labels.segments)
        if ad is not None:
            ad_range = f"[{ad.start:.1f} - {ad.end:.1f}]"
            lines.append(f"▌ [{start:.1f}s - {end:.1f}s]  {text:<50}  ◀ AD {ad_range}")
        else:
            lines.append(f"  [{start:.1f}s - {end:.1f}s]  {text}")

    return "\n".join(lines)


def review_labels_file(labels_path: Path, output_dir: Path) -> str:
    """Load a Labels file and its episode transcript, then format for review."""
    labels = Labels.load(labels_path)
    resolved = resolve_episode(labels.episode_ref, output_dir)

    transcript: list[dict[str, Any]] = []
    if resolved.transcript_path and resolved.transcript_path.exists():
        transcript = json.loads(resolved.transcript_path.read_text())

    return format_review(labels, transcript, str(resolved.audio_path))
