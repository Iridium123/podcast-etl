"""First-class on-disk ad labels.

`Labels` is the shared artifact format written by production's ``detect_ads``
step (to ``output/<slug>/labels/<stem>.json``) and, in the eval harness, by the
``eval label``/``eval annotate`` commands. Both sides write the same shape so
eval scoring is just a comparison of two directories of label files.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from podcast_etl.detectors import AdSegment


@dataclass
class EpisodeRef:
    """Stable reference back to the episode a label file describes."""

    podcast_slug: str
    episode_json: str  # episode JSON filename, e.g. "2024-01-15-title-ab12cd34.json"

    def to_dict(self) -> dict[str, Any]:
        return {"podcast_slug": self.podcast_slug, "episode_json": self.episode_json}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeRef:
        return cls(podcast_slug=data["podcast_slug"], episode_json=data["episode_json"])


@dataclass
class Provenance:
    """How a set of labels was produced — config + who/what annotated."""

    whisper: dict[str, Any]  # normalized whisper config (model, language)
    llm: dict[str, Any]  # {provider, model, prompt} (Any: eval/human paths may add fields)
    annotator: str  # = llm.model unless human-corrected
    created_at: str  # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "whisper": self.whisper,
            "llm": self.llm,
            "annotator": self.annotator,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        return cls(
            whisper=data.get("whisper", {}),
            llm=data.get("llm", {}),
            annotator=data["annotator"],
            created_at=data["created_at"],
        )


@dataclass
class Labels:
    """Ad-segment labels for one episode, plus provenance and validation data."""

    episode_ref: EpisodeRef
    audio_duration: float
    segments: list[AdSegment]
    provenance: Provenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_ref": self.episode_ref.to_dict(),
            "audio_duration": self.audio_duration,
            "segments": [s.to_dict() for s in self.segments],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Labels:
        return cls(
            episode_ref=EpisodeRef.from_dict(data["episode_ref"]),
            audio_duration=data["audio_duration"],
            segments=[AdSegment.from_dict(s) for s in data.get("segments", [])],
            provenance=Provenance.from_dict(data["provenance"]),
        )

    def save(self, path: Path) -> None:
        # Atomic write: a torn labels file would corrupt strip_ads input and, in
        # eval, a dataset being read by a scorer. Write to a temp sibling + rename.
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self.to_dict(), indent=2) + "\n"
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> Labels:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
