"""Consistency validation for Labels files.

:func:`validate_labels` checks a single Labels for internal consistency
(no negative timestamps, start < end, within audio duration, no overlaps).
:func:`validate_dataset` runs it over every file under a dataset root and
returns a dict keyed by relative file path so errors are easy to locate.
"""

from __future__ import annotations

from pathlib import Path

from podcast_etl.labels import Labels

from eval.datasets import iter_label_files


def validate_labels(labels: Labels) -> list[str]:
    """Return a list of error messages for *labels*; empty list means valid.

    Checks performed (segments are processed in start order):

    - Negative start or end timestamp.
    - start >= end (zero-duration or inverted segment).
    - end > audio_duration (segment extends past recorded audio length).
    - Overlap with the immediately following segment (current.end > next.start).

    Args:
        labels: The Labels to validate.

    Returns:
        List of human-readable error strings.  Empty list means valid.
    """
    errors: list[str] = []
    segments = sorted(labels.segments, key=lambda s: s.start)

    check_end_bounds = labels.audio_duration > 0
    if not check_end_bounds:
        errors.append(
            f"audio_duration is {labels.audio_duration}; cannot validate segment end bounds"
        )

    for i, seg in enumerate(segments):
        if seg.start < 0:
            errors.append(f"Segment {i}: negative start ({seg.start})")
        if seg.end < 0:
            errors.append(f"Segment {i}: negative end ({seg.end})")
        if seg.start >= seg.end:
            errors.append(
                f"Segment {i}: start ({seg.start}) >= end ({seg.end})"
            )
        if check_end_bounds and seg.end > labels.audio_duration:
            errors.append(
                f"Segment {i}: end ({seg.end}) > audio_duration ({labels.audio_duration})"
            )

    for i, (cur, nxt) in enumerate(zip(segments, segments[1:])):
        if cur.end > nxt.start:
            errors.append(
                f"Segment {i} and {i + 1} overlap: "
                f"[{cur.start}, {cur.end}) overlaps [{nxt.start}, {nxt.end})"
            )

    return errors


def validate_dataset(root: Path) -> dict[str, list[str]]:
    """Validate every label file under *root*, returning results keyed by relative path.

    Scans ``<root>/<podcast-slug>/labels/*.json`` via
    :func:`~eval.datasets.iter_label_files`.  Each file is loaded and passed to
    :func:`validate_labels`.  Every file found appears in the result — valid
    files map to an empty list ``[]``, invalid files map to their error list.

    Args:
        root: Dataset root directory.

    Returns:
        Dict mapping relative file path strings (e.g.
        ``"my-podcast/labels/ep.json"``) to lists of error messages, sorted by
        key.  Empty list value means the file is valid; empty dict means no
        files were found.
    """
    result: dict[str, list[str]] = {}
    for path in iter_label_files(root):
        labels = Labels.load(path)
        errors = validate_labels(labels)
        relative = path.relative_to(root)
        result[str(relative)] = errors
    return dict(sorted(result.items()))
