from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from podcast_etl.detectors import AdSegment
from podcast_etl.models import Episode
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)

_CROSSFADE_DURATION = 0.05  # seconds


def _format_timestamp(seconds: float) -> str:
    """Format seconds as H:MM:SS or M:SS."""
    total_secs = int(seconds)
    hours = total_secs // 3600
    mins = (total_secs % 3600) // 60
    secs = total_secs % 60
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _build_chapters(
    segments: list[AdSegment], audio_duration: float,
) -> list[dict[str, Any]]:
    """Build chapter list for content segments between ad breaks."""
    keep: list[tuple[float, float]] = []
    pos = 0.0
    for seg in sorted(segments, key=lambda s: s.start):
        if seg.start > pos:
            keep.append((pos, seg.start))
        pos = max(pos, seg.end)
    if pos < audio_duration:
        keep.append((pos, audio_duration))

    # Compute chapter timestamps in the cleaned audio (after removal)
    chapters = []
    cleaned_pos = 0.0
    for i, (start, end) in enumerate(keep):
        duration = end - start
        chapters.append({
            "title": f"Chapter {i + 1}",
            "start_ms": int(cleaned_pos * 1000),
            "end_ms": int((cleaned_pos + duration) * 1000),
        })
        cleaned_pos += duration

    return chapters


def _build_comment(segments: list[AdSegment]) -> str:
    """Build a human-readable comment describing removed ads."""
    total_duration = sum(s.end - s.start for s in segments)
    parts = []
    for seg in sorted(segments, key=lambda s: s.start):
        duration = seg.end - seg.start
        label = seg.label or "Ad"
        start_ts = _format_timestamp(seg.start)
        end_ts = _format_timestamp(seg.end)
        parts.append(f"{label} [{start_ts}-{end_ts}, {duration:.1f}s]")
    return f"{len(segments)} ads removed ({total_duration:.1f}s total): {', '.join(parts)}"


def _write_mp3_metadata(output_path: Path, chapters: list[dict], comment: str) -> None:
    """Write CHAP/CTOC frames and COMM tag to an MP3 file."""
    from mutagen.id3 import CHAP, COMM, CTOC, TIT2, ID3

    try:
        tags = ID3(output_path)
    except Exception as exc:
        logger.warning("Could not load existing ID3 tags from %s, starting fresh: %s", output_path, exc)
        tags = ID3()

    # Remove existing chapter and comment frames
    tags.delall("CHAP")
    tags.delall("CTOC")
    tags.delall("COMM")

    # Add chapter frames
    chapter_ids = []
    for i, ch in enumerate(chapters):
        element_id = f"chp{i}"
        chapter_ids.append(element_id)
        tags.add(CHAP(
            element_id=element_id,
            start_time=ch["start_ms"],
            end_time=ch["end_ms"],
            sub_frames=[TIT2(encoding=3, text=[ch["title"]])],
        ))

    # Add table of contents
    if chapter_ids:
        tags.add(CTOC(
            element_id="toc",
            flags=3,  # top-level, ordered
            child_element_ids=chapter_ids,
        ))

    # Add comment
    tags.add(COMM(encoding=3, lang="eng", desc="", text=[comment]))
    tags.save(output_path)


def _build_ffmpeg_args(
    audio_path: Path,
    output_path: Path,
    segments: list[AdSegment],
    audio_duration: float,
) -> list[str]:
    """Build ffmpeg args to cut out ad segments and concatenate the remaining parts."""
    # Compute the "keep" intervals (non-ad segments)
    keep: list[tuple[float, float]] = []
    pos = 0.0
    for seg in sorted(segments, key=lambda s: s.start):
        if seg.start > pos:
            keep.append((pos, seg.start))
        pos = max(pos, seg.end)
    if pos < audio_duration:
        keep.append((pos, audio_duration))

    if not keep:
        raise ValueError("All audio would be removed by ad stripping")

    # Build a complex filter: trim each segment, apply short crossfades at splice points
    filter_parts: list[str] = []
    for i, (start, end) in enumerate(keep):
        filter_parts.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[seg{i}]"
        )

    # Apply crossfades between adjacent segments
    if len(keep) == 1:
        filter_parts.append(f"[seg0]acopy[out]")
    else:
        # Chain crossfades: seg0 x seg1 -> tmp0, tmp0 x seg2 -> tmp1, ...
        cf_duration = _CROSSFADE_DURATION
        current = "seg0"
        for i in range(1, len(keep)):
            output_label = "out" if i == len(keep) - 1 else f"tmp{i - 1}"
            filter_parts.append(
                f"[{current}][seg{i}]acrossfade=d={cf_duration:.3f}:c1=tri:c2=tri[{output_label}]"
            )
            current = output_label

    filter_complex = ";\n".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "libmp3lame",
        str(output_path),
    ]
    return cmd


@dataclass
class StripAdsStep:
    name: str = "strip_ads"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        detect_status = episode.status.get("detect_ads")
        if not detect_status:
            raise ValueError(f"Episode {episode.slug} has no completed 'detect_ads' step")

        raw_segments = detect_status.result.get("segments", [])
        audio_duration = detect_status.result.get("audio_duration", 0.0)

        # Get original audio path
        download_status = episode.status.get("download")
        if not download_status:
            raise ValueError(f"Episode {episode.slug} has no completed 'download' step")
        original_relative = download_status.result.get("path", "")
        audio_path = context.podcast_dir / original_relative

        if not raw_segments:
            logger.info("No ad segments to strip for %s", audio_path.name)
            return StepResult(data={
                "path": original_relative,
                "original_path": original_relative,
                "segments_removed": 0,
                "duration_removed": 0.0,
            })

        segments = [AdSegment.from_dict(s) for s in raw_segments]

        cleaned_dir = context.podcast_dir / "cleaned"
        cleaned_dir.mkdir(parents=True, exist_ok=True)
        output_path = cleaned_dir / audio_path.name

        if output_path.exists() and not context.overwrite:
            logger.info("Cleaned file already exists: %s", output_path)
        else:
            cmd = _build_ffmpeg_args(audio_path, output_path, segments, audio_duration)
            logger.info("Stripping %d ad segment(s) from %s", len(segments), audio_path.name)
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            except FileNotFoundError:
                raise RuntimeError("ffmpeg is not installed or not in PATH") from None
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr.strip()}")

        # Write chapter frames and comment tag
        chapters = _build_chapters(segments, audio_duration)
        comment = _build_comment(segments)
        _write_mp3_metadata(output_path, chapters, comment)

        duration_removed = sum(s.end - s.start for s in segments)
        cleaned_relative = str(output_path.relative_to(context.podcast_dir))

        return StepResult(data={
            "path": cleaned_relative,
            "original_path": original_relative,
            "segments_removed": len(segments),
            "duration_removed": round(duration_removed, 2),
            "chapters": chapters,
            "comment": comment,
        })
