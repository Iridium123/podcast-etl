"""Create Labels files for hand correction (blank skeletons or bootstrapped)."""

from __future__ import annotations

import copy
from datetime import datetime

from podcast_etl.labels import EpisodeRef, Labels, Provenance


def create_blank(ref: EpisodeRef, audio_duration: float) -> Labels:
    """Create an empty Labels skeleton for manual labeling.

    The annotator is left blank; set it to ``"human"`` once segments are filled
    in so the eval's annotator filter treats it as gold.
    """
    return Labels(
        episode_ref=ref,
        audio_duration=audio_duration,
        segments=[],
        provenance=Provenance(
            whisper={},
            llm={},
            annotator="",
            created_at=datetime.now().isoformat(),
        ),
    )


def bootstrap_labels(source: Labels, annotator: str | None = None) -> Labels:
    """Copy *source* labels as a starting point for hand correction.

    Returns an independent deep copy so edits don't mutate the source dataset.
    By default the source annotator is preserved (so you can see what produced
    the starting point); pass ``annotator="human"`` once corrections are done.
    """
    boot = copy.deepcopy(source)
    if annotator is not None:
        boot.provenance.annotator = annotator
    return boot
