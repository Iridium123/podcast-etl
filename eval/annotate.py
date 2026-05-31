"""Create and bootstrap Labels files for human annotation.

Workflow:
- :func:`create_blank` — scaffolds an empty Labels file ready for hand-authoring.
  Default annotator "human" signals that it represents human ground truth.
- :func:`bootstrap_from_dataset` — copies an existing Labels file from a source
  dataset as a starting point.  The copy keeps the source's provenance and
  annotator unchanged; the human reviewer edits segments and then sets
  ``provenance.annotator = "human"`` to mark the annotation as gold-standard.

Both functions are pure (return a Labels; the caller persists via
``labels.save(path)``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.datasets import episode_key, load_dataset


def create_blank(
    ref: EpisodeRef,
    audio_duration: float,
    *,
    annotator: str = "human",
) -> Labels:
    """Return an empty Labels skeleton ready for hand-authoring.

    Provenance is minimal (empty whisper/llm dicts) because a blank has not
    been produced by any automated system — a human will fill in the segments.
    ``created_at`` is set to the current time so the file has a useful
    timestamp even before editing.

    Args:
        ref: The episode this annotation describes.
        audio_duration: Total audio duration in seconds (used by validate).
        annotator: Annotator identity string; default "human" marks the result
            as a gold-standard human annotation.

    Returns:
        A Labels with no segments and minimal provenance.
    """
    return Labels(
        episode_ref=ref,
        audio_duration=audio_duration,
        segments=[],
        provenance=Provenance(
            whisper={},
            llm={},
            annotator=annotator,
            created_at=datetime.now().isoformat(),
        ),
    )


def bootstrap_from_dataset(ref: EpisodeRef, source_root: Path) -> Labels:
    """Copy an episode's Labels from *source_root* as an annotation starting point.

    Loads *source_root* as a dataset and returns the Labels whose
    ``episode_ref`` matches *ref*.  The returned Labels is a direct copy —
    segments, provenance, and annotator are preserved unchanged.  This means a
    model-bootstrapped annotation retains the model's annotator until a human
    edits the segments and explicitly sets ``provenance.annotator = "human"``.

    The caller is responsible for saving the returned Labels to the target
    dataset (e.g. via ``labels.save(label_file_path(target_root, ...))``).

    Args:
        ref: The episode to look up in the source dataset.
        source_root: Root directory of the source dataset to copy from.

    Returns:
        The Labels for *ref* from the source dataset.

    Raises:
        ValueError: If *ref* is not present in *source_root*, with an
            actionable message naming the key and source directory.
    """
    dataset = load_dataset(source_root)
    key = episode_key(ref)
    if key not in dataset:
        raise ValueError(
            f"Episode {key!r} not found in source dataset {source_root}. "
            f"Available keys: {sorted(dataset)!r}"
        )
    return dataset[key]
