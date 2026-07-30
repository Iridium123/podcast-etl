"""Display transcript with annotation highlights for review."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.models import Annotation
from eval.resolve import resolve_episode


def _is_in_ad(time: float, segments: list[dict[str, Any]]) -> tuple[bool, dict[str, Any] | None]:
    """Check if a timestamp falls within any annotated ad segment."""
    for seg in segments:
        if seg["start"] <= time < seg["end"]:
            return True, seg
    return False, None


def format_review(
    annotation: Annotation,
    transcript: list[dict[str, Any]],
    audio_path: str,
) -> str:
    """Format transcript lines with ad segments highlighted."""
    lines = [f"\nAudio: {audio_path}\n"]

    for seg in transcript:
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()

        in_ad, ad_seg = _is_in_ad(start, annotation.segments)
        if in_ad and ad_seg is not None:
            ad_range = f"[{ad_seg['start']:.1f} - {ad_seg['end']:.1f}]"
            lines.append(f"▌ [{start:.1f}s - {end:.1f}s]  {text:<50}  ◀ AD {ad_range}")
        else:
            lines.append(f"  [{start:.1f}s - {end:.1f}s]  {text}")

    return "\n".join(lines)


def review_annotation(annotation_path: Path, output_dir: Path) -> str:
    """Load an annotation and its transcript, then format for review.

    This is the main entry point for the review CLI.
    """
    annotation = Annotation.load(annotation_path)
    resolved = resolve_episode(annotation.episode_ref, output_dir)

    transcript: list[dict[str, Any]] = []
    if resolved.transcript_path and resolved.transcript_path.exists():
        transcript = json.loads(resolved.transcript_path.read_text())

    return format_review(annotation, transcript, str(resolved.audio_path))
