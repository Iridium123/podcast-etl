"""Tests for DetectAdsStep: orchestration, segment merging, config, transcript reuse."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import Labels
from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.detect_ads import DetectAdsStep, _get_ad_detection_config


def _classify_transcript_patch(return_value):
    """Patch TranscriptionDetector.classify_transcript to return *return_value*."""
    transcription = __import__(
        "podcast_etl.detectors.transcription", fromlist=["TranscriptionDetector"],
    )
    return patch.object(
        transcription.TranscriptionDetector, "classify_transcript",
        return_value=return_value,
    )


def _no_client():
    """Patch build_llm_client so no real Anthropic client is constructed."""
    return patch("podcast_etl.steps.detect_ads.build_llm_client", return_value=None)


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
    def test_process_writes_labels_file(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000", "model": "base", "language": "en"},
            "llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        })
        episode = _make_episode()
        _create_audio_file(context)

        ad_segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Pre-roll ad"),
        ]
        whisper_segments = [{"start": 0.0, "end": 30.0, "text": "Ad copy"}]

        with _no_client(), \
             patch("podcast_etl.steps.detect_ads.transcribe", return_value=whisper_segments), \
             _classify_transcript_patch(ad_segments), \
             patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=3600.0):
            result = DetectAdsStep().process(episode, context)

        # Result records the label path + provenance, NOT inline segments.
        assert "segments" not in result.data
        assert "audio_duration" not in result.data
        assert result.data["labels_path"] == "labels/episode.json"
        assert result.data["total_ad_duration"] == 30.0
        assert "transcription" in result.data["detectors_used"]
        assert result.data["whisper"] == {"model": "base", "language": "en"}
        assert result.data["llm"]["model"] == "claude-haiku-4-5-20251001"
        assert result.data["llm"]["prompt"] == "default"

        # The standalone labels file holds the segments + audio_duration.
        labels = Labels.load(context.podcast_dir / result.data["labels_path"])
        assert len(labels.segments) == 1
        assert labels.segments[0].label == "Pre-roll ad"
        assert labels.audio_duration == 3600.0
        assert labels.episode_ref.podcast_slug == "my-podcast"
        assert labels.provenance.annotator == "claude-haiku-4-5-20251001"

    def test_process_saves_transcript(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
        })
        episode = _make_episode()
        _create_audio_file(context)

        whisper_segments = [{"start": 0.0, "end": 10.0, "text": "Hello"}]

        with _no_client(), \
             patch("podcast_etl.steps.detect_ads.transcribe", return_value=whisper_segments), \
             _classify_transcript_patch([]), \
             patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
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

        with _no_client(), \
             patch("podcast_etl.steps.detect_ads.transcribe", return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}]), \
             _classify_transcript_patch([]), \
             patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
            result = DetectAdsStep().process(episode, context)

        assert result.data["total_ad_duration"] == 0
        labels = Labels.load(context.podcast_dir / result.data["labels_path"])
        assert labels.segments == []

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

    def test_process_resolves_overlapping_segments(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"url": "http://localhost:9000"},
        })
        episode = _make_episode()
        _create_audio_file(context)

        ad_segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Ad 1"),
            AdSegment(start=20.0, end=50.0, confidence=0.8, detector="transcription", label="Ad 2"),
        ]

        with _no_client(), \
             patch("podcast_etl.steps.detect_ads.transcribe", return_value=[{"start": 0.0, "end": 60.0, "text": "stuff"}]), \
             _classify_transcript_patch(ad_segments), \
             patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
            result = DetectAdsStep().process(episode, context)

        # Overlap is resolved (later start snapped to the frontier) but the two
        # ads stay distinct with their own labels in the labels file — not fused.
        labels = Labels.load(context.podcast_dir / result.data["labels_path"])
        assert [(s.start, s.end) for s in labels.segments] == [(0.0, 30.0), (30.0, 50.0)]
        assert [s.label for s in labels.segments] == ["Ad 1", "Ad 2"]
        # total_ad_duration counts the union once (no double-count from overlap).
        assert result.data["total_ad_duration"] == 50.0

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

        with _no_client(), \
             patch("podcast_etl.steps.detect_ads.transcribe") as mock_transcribe, \
             _classify_transcript_patch([]), \
             patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
            result = DetectAdsStep().process(episode, context)

        mock_transcribe.assert_not_called()
        assert result.data["transcript_path"] == "transcripts/episode.json"

    def test_result_records_whisper_and_llm_provenance(self, tmp_path):
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"model": "base", "language": "en", "api_key": "secret"},
            "llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "api_key": "secret"},
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

        # Whisper provenance is normalized (api_key dropped)
        assert result.data["whisper"] == {"model": "base", "language": "en"}
        # LLM records provider + model + prompt — never api_key
        assert result.data["llm"] == {
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "prompt": "default",
        }

    def test_reuses_legacy_transcript_without_recorded_whisper(self, tmp_path):
        """Older detect_ads results have no whisper field; we still reuse the on-disk transcript."""
        context = _make_context(tmp_path, ad_detection_config={
            "whisper": {"model": "base", "language": "en"},
            "llm": {"provider": "anthropic", "model": "x"},
        })
        episode = _make_episode()
        # Legacy: prior result has no "whisper" key
        episode.status["detect_ads"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"segments": []},
        )
        _create_audio_file(context)
        transcripts_dir = context.podcast_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        (transcripts_dir / "episode.json").write_text(json.dumps([{"start": 0.0, "end": 10.0, "text": "Hi"}]))

        with patch("podcast_etl.steps.detect_ads.transcribe") as mock_transcribe:
            with patch.object(
                __import__("podcast_etl.detectors.transcription", fromlist=["TranscriptionDetector"]).TranscriptionDetector,
                "classify_transcript",
                return_value=[],
            ):
                with patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
                    DetectAdsStep().process(episode, context)

        mock_transcribe.assert_not_called()

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

        with _no_client(), \
             patch("podcast_etl.steps.detect_ads.transcribe", return_value=[{"start": 0.0, "end": 10.0, "text": "Hi"}]) as mock_transcribe, \
             _classify_transcript_patch([]), \
             patch("podcast_etl.steps.detect_ads._get_audio_duration", return_value=600.0):
            DetectAdsStep().process(episode, context)

        mock_transcribe.assert_called_once()
