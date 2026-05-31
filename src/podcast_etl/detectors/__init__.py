from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

# Two segments separated by no more than this gap are treated as adjacent: the
# gap is closed by snapping the later segment's start back to the running
# frontier, so a few seconds of content between two ads doesn't survive as a
# sliver. Overlaps are always closed regardless of this value.
ADJACENCY_BUFFER_SECONDS = 5.0


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


def resolve_overlaps(
    segments: list[AdSegment], buffer: float = ADJACENCY_BUFFER_SECONDS,
) -> list[AdSegment]:
    """Resolve overlapping/near-adjacent ad segments into a clean, non-overlapping
    sequence while keeping each segment distinct.

    Greedy, earliest-start-wins. Segments are sorted by start and walked while
    tracking the *frontier* — the furthest end kept so far:

    - A segment whose end is at or before the frontier is fully covered by an
      earlier segment and is dropped.
    - A segment that overlaps the frontier, or starts within ``buffer`` seconds
      of it, has its start snapped to the frontier — closing the overlap or gap
      so the result is contiguous. It keeps its own end, label, confidence,
      detector (and any other fields).
    - A segment that starts more than ``buffer`` seconds after the frontier
      keeps its real start, preserving the gap.

    Unlike a fusing merge, each input ad remains its own output segment, so
    per-segment metadata (distinct labels, confidences) is never collapsed.
    """
    if buffer < 0:
        # A negative buffer would let a truly-overlapping segment slip through
        # unsnapped (its start failing the `<= frontier + buffer` test), leaving
        # an overlap in the output — the opposite of this function's contract.
        raise ValueError(f"buffer must be non-negative, got {buffer!r}")
    if not segments:
        return []

    ordered = sorted(segments, key=lambda s: s.start)
    resolved: list[AdSegment] = [ordered[0]]
    frontier = ordered[0].end

    for seg in ordered[1:]:
        if seg.end <= frontier:
            continue  # fully covered by an earlier segment
        start = frontier if seg.start <= frontier + buffer else seg.start
        resolved.append(dataclasses.replace(seg, start=start))
        frontier = seg.end

    return resolved
