"""Human-readable transcript review with ad-segment highlighting.

:func:`format_review` renders a transcript line by line, marking lines whose
start time falls inside an ad segment with the U+258C left-half-block prefix
and a trailing annotation.  :func:`review_labels` wires it to disk: loads a
Labels file, resolves the episode (for the audio path), reads the transcript,
and calls :func:`format_review`.
"""

from __future__ import annotations

import json
from pathlib import Path

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import Labels

from eval.resolve import resolve_episode

_AD_MARKER = "▌"  # ▌ U+258C LEFT HALF BLOCK
_INDENT = " "           # one space to align non-ad lines with ad-marker lines


def _find_ad_segment(
    transcript_start: float, segments: list[AdSegment]
) -> AdSegment | None:
    """Return the first ad segment whose [start, end) contains *transcript_start*."""
    for seg in segments:
        if seg.start <= transcript_start < seg.end:
            return seg
    return None


def format_review(labels: Labels, transcript: list[dict], audio_path: str) -> str:
    """Format a transcript with ad-segment highlights for human review.

    Each line of the transcript is rendered as::

        ▌ [00:10 - 00:45] LABEL   ← lines inside an ad segment
          text …                  ← non-ad lines (indented to align)

    The header shows the audio path and summary counts.

    Args:
        labels: Labels containing the ad segments to highlight.
        transcript: List of transcript segment dicts, each with at least
            ``"start"`` (float, seconds) and ``"text"`` (str) keys.
        audio_path: Display path for the audio file (shown in header).

    Returns:
        Multi-line string ready to print/display.
    """
    ad_count = len(labels.segments)
    lines: list[str] = [
        f"Audio: {audio_path}",
        f"Ad segments: {ad_count}",
        "",
    ]

    for seg_dict in transcript:
        start: float = seg_dict.get("start", 0.0)
        text: str = seg_dict.get("text", "").strip()
        ad = _find_ad_segment(start, labels.segments)
        if ad is not None:
            label_part = f" {ad.label}" if ad.label else ""
            annotation = f"◄ AD [{ad.start:.1f} - {ad.end:.1f}]{label_part}"
            lines.append(f"{_AD_MARKER} {text}  {annotation}")
        else:
            lines.append(f"{_INDENT} {text}")

    return "\n".join(lines)


def review_labels(label_path: Path, output_dir: Path) -> str:
    """Load a Labels file and return a formatted transcript review string.

    Resolves the episode from *output_dir* to obtain the audio path, then
    reads the transcript JSON (if present — an absent transcript yields an
    empty transcript and no lines are rendered).

    Args:
        label_path: Path to a Labels JSON file.
        output_dir: Production output directory (for resolving episode paths).

    Returns:
        Formatted review string from :func:`format_review`.

    Raises:
        FileNotFoundError: If the episode cannot be resolved (missing podcast
            dir, episode JSON, or audio file).
    """
    labels = Labels.load(label_path)
    resolved = resolve_episode(labels.episode_ref, output_dir)

    transcript: list[dict] = []
    if resolved.transcript_path is not None:
        transcript = json.loads(resolved.transcript_path.read_text(encoding="utf-8"))

    return format_review(labels, transcript, str(resolved.audio_path))
