"""Dataset loading for the thin-scorer eval harness.

A *dataset* is a directory laid out as::

    <root>/<podcast-slug>/labels/<stem>.json

where each ``.json`` file is a production-format :class:`Labels` JSON.
Production's own ``output/`` directory is a valid dataset because
``detect_ads`` writes ``<slug>/labels/<stem>.json`` there.

Two datasets (e.g. ``output/`` and ``eval/datasets/gold``) are compared by
matching on :func:`episode_key` derived from the ``episode_ref`` embedded in
each file — filenames are not used for matching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from podcast_etl.labels import EpisodeRef, Labels


def episode_key(ref: EpisodeRef) -> str:
    """Return a stable match key for an episode: ``podcast_slug/episode_json``."""
    return f"{ref.podcast_slug}/{ref.episode_json}"


def label_file_path(root: Path, podcast_slug: str, stem: str) -> Path:
    """Return the canonical path for a label file within a dataset root.

    ``stem`` is the filename stem without ``.json`` (e.g. the audio stem).
    """
    return root / podcast_slug / "labels" / f"{stem}.json"


def iter_label_files(root: Path) -> Iterator[Path]:
    """Yield every label file under *root* in sorted order.

    Matches ``<root>/<podcast-slug>/labels/<stem>.json``.  Files whose name
    starts with ``"."`` (e.g. atomic-write temps like ``.ep.json.tmp``) are
    excluded.
    """
    for path in sorted(root.glob("*/labels/*.json")):
        if not path.name.startswith("."):
            yield path


def load_dataset(root: Path) -> dict[str, Labels]:
    """Load all label files under *root*, keyed by :func:`episode_key`.

    The key is derived from the ``episode_ref`` embedded in each file, not
    from the filename, so renaming a file does not break matching.

    Raises :exc:`FileNotFoundError` if *root* does not exist.
    """
    if not root.exists():
        raise FileNotFoundError(f"Dataset root not found: {root}")
    return {episode_key(labels.episode_ref): labels for path in iter_label_files(root) if (labels := Labels.load(path))}


def resolve_dataset_root(name_or_path: str, output_dir: Path, datasets_dir: Path) -> Path:
    """Resolve a dataset name or path to a concrete directory.

    Resolution order:

    1. ``"output"`` → *output_dir* (production labels).
    2. An existing directory path → returned as-is (explicit path).
    3. Anything else → ``datasets_dir / name_or_path`` (named dataset).
    """
    if name_or_path == "output":
        return output_dir
    candidate = Path(name_or_path)
    if candidate.is_dir():
        return candidate
    return datasets_dir / name_or_path
