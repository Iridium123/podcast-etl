"""Datasets are directories of production-format ``Labels`` files.

Layout: ``<root>/<podcast-slug>/labels/<stem>.json``. This mirrors production's
``output/<slug>/labels/`` exactly, so a production output directory is a valid
dataset out of the box. Scoring compares two datasets by episode, matching on
each file's ``episode_ref`` (not its filename), so prediction and gold datasets
need not share filenames.
"""

from __future__ import annotations

import logging
from pathlib import Path

from podcast_etl.labels import EpisodeRef, Labels

logger = logging.getLogger(__name__)

DATASETS_DIR = Path("eval/datasets")


def ref_key(ref: EpisodeRef) -> str:
    """Stable key identifying an episode across datasets."""
    return f"{ref.podcast_slug}/{ref.episode_json}"


def load_dataset(root: Path) -> dict[str, Labels]:
    """Load all ``Labels`` files under *root*, keyed by :func:`ref_key`.

    Globs ``*/labels/*.json`` so sibling directories (``episodes/``,
    ``transcripts/``) are ignored. Raises ``FileNotFoundError`` if *root*
    does not exist, so a typo in a dataset name fails loudly instead of
    silently scoring nothing.
    """
    if not root.exists():
        raise FileNotFoundError(f"Dataset directory not found: {root}")
    dataset: dict[str, Labels] = {}
    for path in sorted(root.glob("*/labels/*.json")):
        labels = Labels.load(path)
        key = ref_key(labels.episode_ref)
        if key in dataset:
            # Can't happen in production layout (one episode -> one stem), but a
            # hand-built dataset with two files for the same episode_ref would
            # silently lose one — surface it rather than dropping data quietly.
            logger.warning("Duplicate episode_ref %s in dataset %s; %s overrides earlier file", key, root, path)
        dataset[key] = labels
    return dataset


def resolve_dataset_path(name_or_path: str, datasets_dir: Path = DATASETS_DIR) -> Path:
    """Resolve a dataset reference to a directory.

    Accepts either a literal path (used as-is when it exists, so ``--gold output``
    works) or a bare dataset name resolved under *datasets_dir*.
    """
    literal = Path(name_or_path)
    if literal.exists():
        return literal
    return datasets_dir / name_or_path
