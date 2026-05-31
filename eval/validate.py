"""Structural validation for Labels files (gold annotations or predictions)."""

from __future__ import annotations

from pathlib import Path

from podcast_etl.labels import Labels


def validate_labels(labels: Labels) -> list[str]:
    """Check a single Labels object for consistency. Returns error messages.

    Flags negative timestamps, start >= end, segments running past the audio,
    and overlaps between adjacent segments (touching boundaries are allowed).
    """
    errors: list[str] = []

    sorted_segs = sorted(labels.segments, key=lambda s: s.start)
    for i, seg in enumerate(sorted_segs):
        if seg.start < 0 or seg.end < 0:
            errors.append(f"Segment {i}: negative timestamp (start={seg.start}, end={seg.end})")

        if seg.start >= seg.end:
            errors.append(f"Segment {i}: start >= end ({seg.start} >= {seg.end})")

        if seg.end > labels.audio_duration:
            errors.append(
                f"Segment {i}: end ({seg.end}) exceeds audio duration ({labels.audio_duration})"
            )

        if i + 1 < len(sorted_segs):
            next_start = sorted_segs[i + 1].start
            if seg.end > next_start:
                errors.append(
                    f"Segment {i} (end={seg.end}) overlaps with segment {i + 1} (start={next_start})"
                )

    return errors


def validate_dataset(dataset_dir: Path) -> dict[str, list[str]]:
    """Validate every Labels file under a dataset directory.

    Returns ``{filename: [errors]}``. Raises ``FileNotFoundError`` if the
    directory does not exist, so a mistyped path fails loudly.
    """
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset directory not found: {dataset_dir}")
    results: dict[str, list[str]] = {}
    for path in sorted(dataset_dir.glob("*/labels/*.json")):
        results[path.name] = validate_labels(Labels.load(path))
    return results
