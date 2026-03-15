"""Tests for StripAdsStep: ffmpeg ad removal, idempotency, no-ads passthrough."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.strip_ads import (
    StripAdsStep,
    _build_chapters,
    _build_comment,
    _build_ffmpeg_args,
    _format_timestamp,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_podcast():
    return Podcast(
        title="My Podcast",
        url="https://example.com/rss",
        slug="my-podcast",
        description="desc",
        image_url=None,
        episodes=[],
    )


def _make_episode(
    download_path="audio/episode.mp3",
    detect_segments=None,
    audio_duration=3600.0,
):
    status = {}
    if download_path is not None:
        status["download"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"path": download_path, "size_bytes": 1024},
        )
    if detect_segments is not None:
        status["detect_ads"] = StepStatus(
            completed_at="2024-01-15T10:05:00",
            result={
                "segments": [s.to_dict() for s in detect_segments],
                "total_ad_duration": sum(s.end - s.start for s in detect_segments),
                "audio_duration": audio_duration,
                "detectors_used": ["transcription"],
                "transcript_path": "transcripts/episode.json",
            },
        )
    return Episode(
        title="Episode One",
        guid="guid-1",
        published="Mon, 15 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep1.mp3",
        duration="3600",
        description="desc",
        slug="episode-one",
        status=status,
    )


def _make_context(tmp_path):
    podcast = _make_podcast()
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config={"settings": {}},
        feed_config={},
    )


def _create_audio_file(context, relative_path="audio/episode.mp3"):
    audio_path = context.podcast_dir / relative_path
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio data")
    return audio_path


# ---------------------------------------------------------------------------
# _build_ffmpeg_args
# ---------------------------------------------------------------------------

class TestBuildFfmpegArgs:
    def test_single_ad_segment_in_middle(self):
        segments = [AdSegment(start=30.0, end=60.0, confidence=0.9, detector="t")]
        cmd = _build_ffmpeg_args(Path("in.mp3"), Path("out.mp3"), segments, 120.0)

        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "-i" in cmd
        filter_idx = cmd.index("-filter_complex") + 1
        fc = cmd[filter_idx]
        # Should have two trim segments: 0-30 and 60-120
        assert "atrim=start=0.000:end=30.000" in fc
        assert "atrim=start=60.000:end=120.000" in fc
        assert "acrossfade" in fc

    def test_pre_roll_ad_only(self):
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="t")]
        cmd = _build_ffmpeg_args(Path("in.mp3"), Path("out.mp3"), segments, 120.0)

        filter_idx = cmd.index("-filter_complex") + 1
        fc = cmd[filter_idx]
        # Should have one trim: 30-120 and use acopy
        assert "atrim=start=30.000:end=120.000" in fc
        assert "acopy" in fc

    def test_post_roll_ad_only(self):
        segments = [AdSegment(start=100.0, end=120.0, confidence=0.9, detector="t")]
        cmd = _build_ffmpeg_args(Path("in.mp3"), Path("out.mp3"), segments, 120.0)

        filter_idx = cmd.index("-filter_complex") + 1
        fc = cmd[filter_idx]
        assert "atrim=start=0.000:end=100.000" in fc
        assert "acopy" in fc

    def test_multiple_segments(self):
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="t"),
            AdSegment(start=60.0, end=90.0, confidence=0.9, detector="t"),
        ]
        cmd = _build_ffmpeg_args(Path("in.mp3"), Path("out.mp3"), segments, 120.0)

        filter_idx = cmd.index("-filter_complex") + 1
        fc = cmd[filter_idx]
        # Should have keep segments: 30-60, 90-120
        assert "atrim=start=30.000:end=60.000" in fc
        assert "atrim=start=90.000:end=120.000" in fc
        assert "acrossfade" in fc

    def test_raises_when_all_audio_removed(self):
        segments = [AdSegment(start=0.0, end=120.0, confidence=0.9, detector="t")]
        with pytest.raises(ValueError, match="All audio would be removed"):
            _build_ffmpeg_args(Path("in.mp3"), Path("out.mp3"), segments, 120.0)

    def test_uses_libmp3lame_codec(self):
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="t")]
        cmd = _build_ffmpeg_args(Path("in.mp3"), Path("out.mp3"), segments, 120.0)
        codec_idx = cmd.index("-c:a") + 1
        assert cmd[codec_idx] == "libmp3lame"


# ---------------------------------------------------------------------------
# StripAdsStep
# ---------------------------------------------------------------------------

class TestStripAdsStep:
    def test_no_segments_returns_original_path(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(detect_segments=[])
        _create_audio_file(context)

        result = StripAdsStep().process(episode, context)

        assert result.data["path"] == "audio/episode.mp3"
        assert result.data["segments_removed"] == 0
        assert result.data["duration_removed"] == 0.0

    def test_strips_ads_with_ffmpeg(self, tmp_path):
        context = _make_context(tmp_path)
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Ad")]
        episode = _make_episode(detect_segments=segments, audio_duration=600.0)
        _create_audio_file(context)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("podcast_etl.steps.strip_ads.subprocess.run", return_value=mock_result) as mock_run:
            with patch("podcast_etl.steps.strip_ads._write_mp3_metadata"):
                result = StripAdsStep().process(episode, context)

        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "ffmpeg"
        assert result.data["path"] == "cleaned/episode-one/episode.mp3"
        assert result.data["original_path"] == "audio/episode.mp3"
        assert result.data["segments_removed"] == 1
        assert result.data["duration_removed"] == 30.0
        assert len(result.data["chapters"]) == 1
        assert result.data["chapters"][0]["title"] == "Chapter 1"
        assert "Ad" in result.data["comment"]

    def test_idempotent_skips_if_cleaned_exists(self, tmp_path):
        context = _make_context(tmp_path)
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription")]
        episode = _make_episode(detect_segments=segments)
        _create_audio_file(context)

        # Pre-create cleaned file
        cleaned_dir = context.podcast_dir / "cleaned" / "episode-one"
        cleaned_dir.mkdir(parents=True, exist_ok=True)
        (cleaned_dir / "episode.mp3").write_bytes(b"already cleaned")

        with patch("podcast_etl.steps.strip_ads.subprocess.run") as mock_run:
            with patch("podcast_etl.steps.strip_ads._write_mp3_metadata"):
                result = StripAdsStep().process(episode, context)

        mock_run.assert_not_called()
        assert result.data["path"] == "cleaned/episode-one/episode.mp3"

    def test_overwrites_when_overwrite_true(self, tmp_path):
        context = _make_context(tmp_path)
        context.overwrite = True
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription")]
        episode = _make_episode(detect_segments=segments, audio_duration=600.0)
        _create_audio_file(context)

        # Pre-create cleaned file
        cleaned_dir = context.podcast_dir / "cleaned" / "episode-one"
        cleaned_dir.mkdir(parents=True, exist_ok=True)
        (cleaned_dir / "episode.mp3").write_bytes(b"stale cleaned")

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("podcast_etl.steps.strip_ads.subprocess.run", return_value=mock_result) as mock_run:
            with patch("podcast_etl.steps.strip_ads._write_mp3_metadata"):
                StripAdsStep().process(episode, context)

        mock_run.assert_called_once()

    def test_raises_if_no_detect_ads_step(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()  # no detect_ads status

        with pytest.raises(ValueError, match="no completed 'detect_ads' step"):
            StripAdsStep().process(episode, context)

    def test_raises_if_no_download_step(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(download_path=None)
        # Manually add detect_ads status
        episode.status["detect_ads"] = StepStatus(
            completed_at="2024-01-15T10:05:00",
            result={"segments": [], "audio_duration": 600.0},
        )

        # No segments means it tries to return original path, which needs download
        with pytest.raises(ValueError, match="no completed 'download' step"):
            StripAdsStep().process(episode, context)

    def test_raises_on_ffmpeg_failure(self, tmp_path):
        context = _make_context(tmp_path)
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription")]
        episode = _make_episode(detect_segments=segments, audio_duration=600.0)
        _create_audio_file(context)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ffmpeg error details"

        with patch("podcast_etl.steps.strip_ads.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="ffmpeg failed"):
                StripAdsStep().process(episode, context)


# ---------------------------------------------------------------------------
# _format_timestamp
# ---------------------------------------------------------------------------

class TestFormatTimestamp:
    def test_seconds_only(self):
        assert _format_timestamp(5.0) == "0:05"

    def test_minutes_and_seconds(self):
        assert _format_timestamp(65.0) == "1:05"

    def test_hours(self):
        assert _format_timestamp(3661.0) == "1:01:01"

    def test_zero(self):
        assert _format_timestamp(0.0) == "0:00"


# ---------------------------------------------------------------------------
# _build_chapters
# ---------------------------------------------------------------------------

class TestBuildChapters:
    def test_single_pre_roll_ad(self):
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="t")]
        chapters = _build_chapters(segments, 120.0)
        assert len(chapters) == 1
        assert chapters[0]["title"] == "Chapter 1"
        assert chapters[0]["start_ms"] == 0
        assert chapters[0]["end_ms"] == 90000  # 120 - 30 = 90s of content

    def test_mid_roll_ad_creates_two_chapters(self):
        segments = [AdSegment(start=30.0, end=60.0, confidence=0.9, detector="t")]
        chapters = _build_chapters(segments, 120.0)
        assert len(chapters) == 2
        assert chapters[0]["title"] == "Chapter 1"
        assert chapters[0]["start_ms"] == 0
        assert chapters[0]["end_ms"] == 30000
        assert chapters[1]["title"] == "Chapter 2"
        assert chapters[1]["start_ms"] == 30000

    def test_no_segments(self):
        chapters = _build_chapters([], 120.0)
        assert len(chapters) == 1
        assert chapters[0]["start_ms"] == 0
        assert chapters[0]["end_ms"] == 120000


# ---------------------------------------------------------------------------
# _build_comment
# ---------------------------------------------------------------------------

class TestBuildComment:
    def test_single_ad(self):
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="t", label="Pre-roll")]
        comment = _build_comment(segments)
        assert "1 ads removed" in comment
        assert "30.0s total" in comment
        assert "Pre-roll" in comment

    def test_multiple_ads(self):
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="t", label="Pre-roll"),
            AdSegment(start=100.0, end=160.0, confidence=0.9, detector="t", label="Mid-roll"),
        ]
        comment = _build_comment(segments)
        assert "2 ads removed" in comment
        assert "90.0s total" in comment
        assert "Pre-roll" in comment
        assert "Mid-roll" in comment
