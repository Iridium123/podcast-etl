"""Annotation data model for ad detection evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from podcast_etl.detectors import AdSegment


@dataclass
class EpisodeRef:
    podcast_slug: str
    episode_json: str  # e.g. "2024-01-15-episode-one-a1b2c3d4.json"

    def to_dict(self) -> dict[str, Any]:
        return {"podcast_slug": self.podcast_slug, "episode_json": self.episode_json}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeRef:
        return cls(podcast_slug=data["podcast_slug"], episode_json=data["episode_json"])


@dataclass
class Annotation:
    episode_ref: EpisodeRef
    audio_duration: float
    segments: list[dict[str, Any]]  # [{start, end, label, notes}]
    annotator: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_ref": self.episode_ref.to_dict(),
            "audio_duration": self.audio_duration,
            "segments": self.segments,
            "annotator": self.annotator,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Annotation:
        return cls(
            episode_ref=EpisodeRef.from_dict(data["episode_ref"]),
            audio_duration=data["audio_duration"],
            segments=data["segments"],
            annotator=data["annotator"],
            created_at=data["created_at"],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> Annotation:
        data = json.loads(path.read_text())
        return cls.from_dict(data)

    def segments_as_ad_segments(self) -> list[AdSegment]:
        """Convert annotation segments to AdSegment objects for scoring."""
        return [
            AdSegment(
                start=seg["start"],
                end=seg["end"],
                confidence=1.0,
                detector="gold",
                label=seg.get("label", ""),
            )
            for seg in self.segments
        ]
