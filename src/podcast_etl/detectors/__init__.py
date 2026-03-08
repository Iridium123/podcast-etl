from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class AdSegment:
    start: float  # seconds
    end: float  # seconds
    confidence: float  # 0.0–1.0
    detector: str  # name of detector that found this
    label: str = ""  # human-readable description

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
            "detector": self.detector,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdSegment:
        return cls(
            start=data["start"],
            end=data["end"],
            confidence=data["confidence"],
            detector=data["detector"],
            label=data.get("label", ""),
        )


class LLMProvider(Protocol):
    name: str

    def classify_ads(self, transcript: list[dict[str, Any]], config: dict[str, Any]) -> list[AdSegment]: ...


class Detector(Protocol):
    name: str

    def detect(self, audio_path: Path, config: dict[str, Any]) -> list[AdSegment]: ...


def merge_segments(segments: list[AdSegment]) -> list[AdSegment]:
    """Merge overlapping or adjacent ad segments, keeping the highest confidence."""
    if not segments:
        return []

    sorted_segs = sorted(segments, key=lambda s: s.start)
    merged: list[AdSegment] = [sorted_segs[0]]

    for seg in sorted_segs[1:]:
        prev = merged[-1]
        if seg.start <= prev.end:
            # Overlapping — extend and keep the higher confidence
            labels = [prev.label, seg.label]
            merged[-1] = AdSegment(
                start=prev.start,
                end=max(prev.end, seg.end),
                confidence=max(prev.confidence, seg.confidence),
                detector=prev.detector if prev.confidence >= seg.confidence else seg.detector,
                label="; ".join(lbl for lbl in labels if lbl),
            )
        else:
            merged.append(seg)

    return merged
