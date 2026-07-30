"""Tests for eval.review: format_review and review_labels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance
from podcast_etl.models import Episode, StepStatus

from eval.review import format_review, review_labels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AD_MARKER = "▌"  # U+258C LEFT HALF BLOCK


def _make_labels(
    segments: list[AdSegment],
    audio_duration: float = 3600.0,
    slug: str = "my-podcast",
    episode_json: str = "ep.json",
) -> Labels:
    return Labels(
        episode_ref=EpisodeRef(podcast_slug=slug, episode_json=episode_json),
        audio_duration=audio_duration,
        segments=segments,
        provenance=Provenance(
            whisper={}, llm={}, annotator="human", created_at="2026-01-01T00:00:00"
        ),
    )


def _seg(start: float, end: float, label: str = "") -> AdSegment:
    return AdSegment(start=start, end=end, confidence=0.9, detector="test", label=label)


def _transcript(*entries: tuple[float, str]) -> list[dict]:
    """Build a list of transcript segment dicts from (start, text) tuples."""
    return [{"start": s, "end": s + 10.0, "text": t} for s, t in entries]


def _write_episode_on_disk(
    output_dir: Path,
    podcast_slug: str,
    episode_json: str,
    audio_rel: str = "audio/ep.mp3",
    transcript_segments: list[dict] | None = None,
) -> tuple[Path, Path | None]:
    """Write a minimal episode structure and return (audio_path, transcript_path | None)."""
    episode = Episode(
        title="Test Episode",
        guid="guid-abc",
        published="Mon, 15 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration="3600",
        description="desc",
        slug="test-episode",
        status={
            "download": StepStatus(
                completed_at="2024-01-15T10:00:00",
                result={"path": audio_rel, "size_bytes": 1024},
            )
        },
    )

    ep_dir = output_dir / podcast_slug / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    ep_path = ep_dir / episode_json
    ep_path.write_text(json.dumps(episode.to_dict(), indent=2), encoding="utf-8")

    audio_path = output_dir / podcast_slug / audio_rel
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio")

    transcript_path: Path | None = None
    if transcript_segments is not None:
        audio_stem = Path(audio_rel).stem
        t_dir = output_dir / podcast_slug / "transcripts"
        t_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = t_dir / f"{audio_stem}.json"
        transcript_path.write_text(
            json.dumps(transcript_segments), encoding="utf-8"
        )

    return audio_path, transcript_path


# ---------------------------------------------------------------------------
# format_review — basic structure
# ---------------------------------------------------------------------------

class TestFormatReviewStructure:
    def test_header_contains_audio_path(self):
        labels = _make_labels(segments=[])
        result = format_review(labels, [], "/path/to/ep.mp3")
        assert "/path/to/ep.mp3" in result

    def test_header_shows_ad_segment_count(self):
        labels = _make_labels(segments=[_seg(0.0, 30.0)])
        result = format_review(labels, [], "ep.mp3")
        assert "Ad segments: 1" in result

    def test_empty_transcript_no_crash(self):
        labels = _make_labels(segments=[_seg(0.0, 30.0)])
        result = format_review(labels, [], "ep.mp3")
        assert isinstance(result, str)

    def test_returns_string(self):
        labels = _make_labels(segments=[])
        result = format_review(labels, _transcript((0.0, "hello")), "ep.mp3")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# format_review — ad-segment markup
# ---------------------------------------------------------------------------

class TestFormatReviewMarkup:
    def test_in_ad_line_has_marker(self):
        # Ad: [0, 60); transcript line starts at 5 → inside ad
        labels = _make_labels(segments=[_seg(0.0, 60.0)])
        result = format_review(labels, _transcript((5.0, "Buy our product!")), "ep.mp3")
        lines = result.splitlines()
        ad_lines = [l for l in lines if _AD_MARKER in l]
        assert len(ad_lines) == 1

    def test_out_of_ad_line_has_no_marker(self):
        # Ad: [0, 30); transcript line starts at 60 → outside ad
        labels = _make_labels(segments=[_seg(0.0, 30.0)])
        result = format_review(labels, _transcript((60.0, "Content here")), "ep.mp3")
        lines = result.splitlines()
        ad_lines = [l for l in lines if _AD_MARKER in l]
        assert len(ad_lines) == 0

    def test_ad_annotation_shows_start_and_end(self):
        labels = _make_labels(segments=[_seg(10.0, 50.0)])
        result = format_review(labels, _transcript((15.0, "ad text")), "ep.mp3")
        assert "10.0" in result
        assert "50.0" in result

    def test_ad_annotation_shows_label(self):
        labels = _make_labels(segments=[_seg(10.0, 50.0, label="sponsor")])
        result = format_review(labels, _transcript((15.0, "ad text")), "ep.mp3")
        assert "sponsor" in result

    def test_no_label_no_crash(self):
        labels = _make_labels(segments=[_seg(10.0, 50.0, label="")])
        result = format_review(labels, _transcript((15.0, "ad text")), "ep.mp3")
        # Should not crash; ad marker still present
        assert _AD_MARKER in result

    def test_boundary_at_start_inclusive(self):
        # Ad: [10, 50); a transcript line starting exactly at 10 is inside
        labels = _make_labels(segments=[_seg(10.0, 50.0)])
        result = format_review(labels, _transcript((10.0, "at boundary")), "ep.mp3")
        assert _AD_MARKER in result

    def test_boundary_at_end_exclusive(self):
        # Ad: [10, 50); a transcript line starting exactly at 50 is outside
        labels = _make_labels(segments=[_seg(10.0, 50.0)])
        result = format_review(labels, _transcript((50.0, "past ad")), "ep.mp3")
        lines = result.splitlines()
        content_lines = [l for l in lines if "past ad" in l]
        assert all(_AD_MARKER not in l for l in content_lines)

    def test_mixed_ad_and_non_ad_lines(self):
        labels = _make_labels(segments=[_seg(20.0, 40.0)])
        transcript = _transcript(
            (0.0, "intro text"),
            (25.0, "ad material"),
            (60.0, "outro text"),
        )
        result = format_review(labels, transcript, "ep.mp3")
        lines = result.splitlines()
        ad_lines = [l for l in lines if _AD_MARKER in l]
        non_ad_lines = [l for l in lines if "intro text" in l or "outro text" in l]
        assert len(ad_lines) == 1
        assert all(_AD_MARKER not in l for l in non_ad_lines)

    def test_ad_annotation_marker_present(self):
        labels = _make_labels(segments=[_seg(0.0, 30.0)])
        result = format_review(labels, _transcript((5.0, "ad")), "ep.mp3")
        assert "◄ AD" in result


# ---------------------------------------------------------------------------
# review_labels — end to end
# ---------------------------------------------------------------------------

class TestReviewLabels:
    def test_returns_string(self, tmp_path):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        transcript_segs = _transcript((5.0, "hello"))
        audio_path, _ = _write_episode_on_disk(
            tmp_path, "my-podcast", "ep.json",
            transcript_segments=transcript_segs,
        )

        labels = _make_labels(segments=[_seg(0.0, 60.0)])
        label_path = tmp_path / "my-podcast" / "labels" / "ep.json"
        labels.save(label_path)

        result = review_labels(label_path, output_dir=tmp_path)
        assert isinstance(result, str)

    def test_audio_path_in_output(self, tmp_path):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        audio_path, _ = _write_episode_on_disk(
            tmp_path, "my-podcast", "ep.json",
            transcript_segments=[],
        )
        labels = _make_labels(segments=[])
        label_path = tmp_path / "my-podcast" / "labels" / "ep.json"
        labels.save(label_path)

        result = review_labels(label_path, output_dir=tmp_path)
        assert str(audio_path) in result

    def test_transcript_lines_rendered(self, tmp_path):
        transcript_segs = _transcript((100.0, "some content"), (200.0, "more content"))
        _write_episode_on_disk(
            tmp_path, "my-podcast", "ep.json",
            transcript_segments=transcript_segs,
        )
        labels = _make_labels(segments=[])
        label_path = tmp_path / "my-podcast" / "labels" / "ep.json"
        labels.save(label_path)

        result = review_labels(label_path, output_dir=tmp_path)
        assert "some content" in result
        assert "more content" in result

    def test_ad_marker_applied(self, tmp_path):
        transcript_segs = _transcript((5.0, "ad line"), (100.0, "normal"))
        _write_episode_on_disk(
            tmp_path, "my-podcast", "ep.json",
            transcript_segments=transcript_segs,
        )
        labels = _make_labels(segments=[_seg(0.0, 30.0)])
        label_path = tmp_path / "my-podcast" / "labels" / "ep.json"
        labels.save(label_path)

        result = review_labels(label_path, output_dir=tmp_path)
        lines = result.splitlines()
        assert any(_AD_MARKER in l and "ad line" in l for l in lines)
        assert any(_AD_MARKER not in l and "normal" in l for l in lines)

    def test_no_transcript_no_crash(self, tmp_path):
        """review_labels must not crash when transcript_path is None."""
        _write_episode_on_disk(
            tmp_path, "my-podcast", "ep.json",
            transcript_segments=None,  # no transcript file written
        )
        labels = _make_labels(segments=[_seg(0.0, 30.0)])
        label_path = tmp_path / "my-podcast" / "labels" / "ep.json"
        labels.save(label_path)

        result = review_labels(label_path, output_dir=tmp_path)
        # Should succeed and include the header
        assert "Ad segments:" in result

    def test_no_transcript_empty_lines(self, tmp_path):
        """With no transcript, there should be no transcript content lines."""
        _write_episode_on_disk(
            tmp_path, "my-podcast", "ep.json",
            transcript_segments=None,
        )
        labels = _make_labels(segments=[_seg(0.0, 30.0)])
        label_path = tmp_path / "my-podcast" / "labels" / "ep.json"
        labels.save(label_path)

        result = review_labels(label_path, output_dir=tmp_path)
        # Only header lines, no ad or content lines
        assert _AD_MARKER not in result
