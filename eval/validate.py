"""Validate annotation files for consistency."""

from __future__ import annotations

from pathlib import Path

from eval.models import Annotation


def validate_annotation(ann: Annotation) -> list[str]:
    """Check a single annotation for consistency. Returns list of error messages."""
    errors: list[str] = []

    sorted_segs = sorted(ann.segments, key=lambda s: s.get("start", 0))
    for i, seg in enumerate(sorted_segs):
        if "start" not in seg or "end" not in seg:
            errors.append(f"Segment {i}: missing 'start' or 'end' field")
            continue

        start, end = seg["start"], seg["end"]

        if start < 0 or end < 0:
            errors.append(f"Segment {i}: negative timestamp (start={start}, end={end})")

        if start >= end:
            errors.append(f"Segment {i}: start >= end ({start} >= {end})")

        if end > ann.audio_duration:
            errors.append(f"Segment {i}: end ({end}) exceeds audio duration ({ann.audio_duration})")

        # Check overlap with next segment
        if i + 1 < len(sorted_segs) and "start" in sorted_segs[i + 1]:
            next_start = sorted_segs[i + 1]["start"]
            if end > next_start:
                errors.append(f"Segment {i} (end={end}) overlaps with segment {i + 1} (start={next_start})")

    return errors


def validate_annotations(annotations_dir: Path) -> dict[str, list[str]]:
    """Validate all annotation JSON files in a directory.

    Returns a dict of filename -> list of error messages.
    Only processes .json files.
    """
    results: dict[str, list[str]] = {}

    for path in sorted(annotations_dir.iterdir()):
        if path.suffix != ".json":
            continue
        ann = Annotation.load(path)
        results[path.name] = validate_annotation(ann)

    return results
