"""Tests for DetectAdsStep: orchestration, segment merging, config, transcript reuse."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.detect_ads import DetectAdsStep, _get_ad_detection_config


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


def _make_episode(download_path="audio/episode.mp3"):
    status = {}
    if download_path is not None:
        status["download"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"path": download_path, "size_bytes": 1024},
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


def _make_context(tmp_path, ad_detection_config=None):
    podcast = _make_podcast()
    config: dict = {}
    if ad_detection_config:
        config["ad_detection"] = ad_detection_config
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config=config,
    )


def _create_audio_file(context, relative_path="audio/episode.mp3"):
    audio_path = context.podcast_dir / relative_path
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio data")
    return audio_path


# ---------------------------------------------------------------------------
# _get_ad_detection_config
# ---------------------------------------------------------------------------

class TestGetAdDetectionConfig:
    def test_returns_global_config(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
            "llm": {"provider": "anthropic"},
        })
        config = _get_ad_detection_config(context)
        assert config["whisper"]["url"] == "http://localhost:9000"
        assert config["llm"]["provider"] == "anthropic"

    def test_feed_overrides_global(self, tmp_path):
        context = _make_context(
            tmp_path,
            ad_detection_config={"llm": {"model": "claude-haiku-4-5-20251001", "provider": "anthropic"}},
        )
        config = _get_ad_detection_config(context)
        assert config["llm"]["model"] == "claude-haiku-4-5-20251001"
        assert config["llm"]["provider"] == "anthropic"

    def test_empty_config(self, tmp_path):
        context = _make_context(tmp_path)
        config = _get_ad_detection_config(context)
        assert config == {}


# ---------------------------------------------------------------------------
# DetectAdsStep
# ---------------------------------------------------------------------------

class TestDetectAdsStep:
    def test_process_returns_detected_segments(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
        })
        episode = _make_episode()
        _create_audio_file(context)

        ad_segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Pre-roll ad"),
        ]
        whisper_segments = [{"start": 0.0, "end": 30.0, "text": "Ad copy"}]

        with patch("podcast_etl.steps.detect_ads.transcribe", return_value=whisper_segments):
            with patch.object(
                __import__("podcast_etl.detectors.transcription", fromlist=["TranscriptionDetector"]).TranscriptionDetector,
                "classify_transcript",
                return_value=ad_segments,
            ):
                with patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=3600.0):
                    result = DetectAdsStep().process(episode, context)

        assert len(result.data["segments"]) == 1
        assert result.data["segments"][0]["start"] == 0.0
        assert result.data["segments"][0]["label"] == "Pre-roll ad"
        assert result.data["total_ad_duration"] == 30.0
        assert result.data["audio_duration"] == 3600.0
        assert "transcription" in result.data["detectors_used"]

    def test_process_saves_transcript(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
        })
        episode = _make_episode()
        _create_audio_file(context)

        whisper_segments = [{"start": 0.0, "end": 10.0, "text": "Hello"}]

        with patch("podcast_etl.steps.detect_ads.transcribe", return_value=whisper_segments):
            with patch.object(
                __import__("podcast_etl.detectors.transcription", fromlist=["TranscriptionDetector"]).TranscriptionDetector,
                "classify_transcript",
                return_value=[],
            ):
                with patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
                    result = DetectAdsStep().process(episode, context)

        assert result.data["transcript_path"].startswith("transcripts/")
        transcript_file = context.podcast_dir / result.data["transcript_path"]
        assert transcript_file.exists()
        saved = json.loads(transcript_file.read_text())
        assert saved == whisper_segments

    def test_process_empty_detection(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
        })
        episode = _make_episode()
        _create_audio_file(context)

        with patch("podcast_etl.steps.detect_ads.transcribe", return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}]):
            with patch.object(
                __import__("podcast_etl.detectors.transcription", fromlist=["TranscriptionDetector"]).TranscriptionDetector,
                "classify_transcript",
                return_value=[],
            ):
                with patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
                    result = DetectAdsStep().process(episode, context)

        assert result.data["segments"] == []
        assert result.data["total_ad_duration"] == 0

    def test_raises_without_download_step(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(download_path=None)

        with pytest.raises(ValueError, match="no completed 'download' step"):
            DetectAdsStep().process(episode, context)

    def test_raises_when_audio_file_missing(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()
        # Don't create the audio file

        with pytest.raises(FileNotFoundError):
            DetectAdsStep().process(episode, context)

    def test_process_merges_overlapping_segments(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
        })
        episode = _make_episode()
        _create_audio_file(context)

        ad_segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Ad 1"),
            AdSegment(start=20.0, end=50.0, confidence=0.8, detector="transcription", label="Ad 2"),
        ]

        with patch("podcast_etl.steps.detect_ads.transcribe", return_value=[{"start": 0.0, "end": 60.0, "text": "stuff"}]):
            with patch.object(
                __import__("podcast_etl.detectors.transcription", fromlist=["TranscriptionDetector"]).TranscriptionDetector,
                "classify_transcript",
                return_value=ad_segments,
            ):
                with patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
                    result = DetectAdsStep().process(episode, context)

        # Should be merged into one segment
        assert len(result.data["segments"]) == 1
        assert result.data["segments"][0]["start"] == 0.0
        assert result.data["segments"][0]["end"] == 50.0

    def test_reuses_existing_transcript(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
        })
        episode = _make_episode()
        _create_audio_file(context)

        # Pre-create transcript file
        transcript_segments = [{"start": 0.0, "end": 10.0, "text": "Hello"}]
        transcripts_dir = context.podcast_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        (transcripts_dir / "episode.json").write_text(json.dumps(transcript_segments))

        with patch("podcast_etl.steps.detect_ads.transcribe") as mock_transcribe:
            with patch.object(
                __import__("podcast_etl.detectors.transcription", fromlist=["TranscriptionDetector"]).TranscriptionDetector,
                "classify_transcript",
                return_value=[],
            ):
                with patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
                    result = DetectAdsStep().process(episode, context)

        mock_transcribe.assert_not_called()
        assert result.data["transcript_path"] == "transcripts/episode.json"

    def test_retranscribes_when_overwrite_true(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
        })
        context.overwrite = True
        episode = _make_episode()
        _create_audio_file(context)

        # Pre-create transcript file
        transcripts_dir = context.podcast_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        (transcripts_dir / "episode.json").write_text("[]")

        with patch("podcast_etl.steps.detect_ads.transcribe", return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}]) as mock_transcribe:
            with patch.object(
                __import__("podcast_etl.detectors.transcription", fromlist=["TranscriptionDetector"]).TranscriptionDetector,
                "classify_transcript",
                return_value=[],
            ):
                with patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
                    DetectAdsStep().process(episode, context)

        mock_transcribe.assert_called_once()
