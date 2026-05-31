"""Tests for TranscriptionDetector: whisper transcription and LLM classification."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.detectors import AdSegment, resolve_overlaps
from podcast_etl.detectors.transcription import (
    AnthropicProvider,
    TranscriptionDetector,
    _format_transcript,
    _get_whisper_model,
    _parse_llm_response,
    _transcribe_local,
    get_llm_provider,
    transcribe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _whisper_response(segments):
    """Build a mock whisper API JSON response."""
    return {"segments": segments}


def _whisper_segments():
    return [
        {"start": 0.0, "end": 10.0, "text": "This episode is brought to you by Acme Corp."},
        {"start": 10.0, "end": 30.0, "text": "Welcome to the show, today we discuss..."},
        {"start": 30.0, "end": 60.0, "text": "Let's get into the topic."},
    ]


def _llm_response_json(segments):
    return json.dumps({"segments": segments})


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------

class TestTranscribe:
    def test_calls_whisper_endpoint(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        mock_resp = MagicMock()
        mock_resp.json.return_value = _whisper_response(_whisper_segments())
        mock_resp.raise_for_status = MagicMock()

        config = {"whisper": {"url": "http://localhost:9000", "model": "large-v3"}}

        with patch("podcast_etl.detectors.transcription.httpx.post", return_value=mock_resp) as mock_post:
            result = transcribe(audio_file, config)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "localhost:9000" in call_kwargs.args[0]
        assert len(result) == 3

    def test_passes_api_key_header(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        mock_resp = MagicMock()
        mock_resp.json.return_value = _whisper_response([])
        mock_resp.raise_for_status = MagicMock()

        config = {"whisper": {"url": "http://localhost:9000", "api_key": "sk-test-key"}}

        with patch("podcast_etl.detectors.transcription.httpx.post", return_value=mock_resp) as mock_post:
            transcribe(audio_file, config)

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-test-key"

    def test_returns_empty_list_when_no_segments(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"segments": []}
        mock_resp.raise_for_status = MagicMock()

        config = {"whisper": {"url": "http://localhost:9000"}}

        with patch("podcast_etl.detectors.transcription.httpx.post", return_value=mock_resp):
            result = transcribe(audio_file, config)

        assert result == []

    def test_propagates_http_error(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 Internal Server Error")

        config = {"whisper": {"url": "http://localhost:9000"}}

        with patch("podcast_etl.detectors.transcription.httpx.post", return_value=mock_resp):
            with pytest.raises(Exception, match="500"):
                transcribe(audio_file, config)

    def test_uses_local_when_no_url(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        config = {"whisper": {"model": "tiny"}}

        with patch("podcast_etl.detectors.transcription._transcribe_local", return_value=[]) as mock_local:
            result = transcribe(audio_file, config)

        mock_local.assert_called_once_with(audio_file, config["whisper"])
        assert result == []

    def test_uses_remote_when_url_set(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"segments": []}
        mock_resp.raise_for_status = MagicMock()

        config = {"whisper": {"url": "http://localhost:9000", "model": "base"}}

        with patch("podcast_etl.detectors.transcription.httpx.post", return_value=mock_resp):
            result = transcribe(audio_file, config)

        assert result == []


# ---------------------------------------------------------------------------
# _transcribe_local
# ---------------------------------------------------------------------------

class TestTranscribeLocal:
    def test_calls_faster_whisper(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        mock_segment = MagicMock()
        mock_segment.start = 0.0
        mock_segment.end = 10.0
        mock_segment.text = "Hello world"

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], MagicMock())

        with patch("podcast_etl.detectors.transcription._get_whisper_model", return_value=mock_model) as mock_get:
            result = _transcribe_local(audio_file, {"model": "tiny", "language": "en"})

        mock_get.assert_called_once_with("tiny", "cpu", "int8")
        assert len(result) == 1
        assert result[0]["start"] == 0.0
        assert result[0]["text"] == "Hello world"


# ---------------------------------------------------------------------------
# _format_transcript
# ---------------------------------------------------------------------------

class TestFormatTranscript:
    def test_formats_segments(self):
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello world"},
            {"start": 5.0, "end": 10.0, "text": "Goodbye world"},
        ]
        result = _format_transcript(segments)
        assert "[0.0s - 5.0s] Hello world" in result
        assert "[5.0s - 10.0s] Goodbye world" in result

    def test_handles_empty_segments(self):
        assert _format_transcript([]) == ""


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------

class TestParseLlmResponse:
    def test_parses_valid_response(self):
        response = _llm_response_json([
            {"start": 0.0, "end": 45.0, "confidence": 0.9, "label": "Ad for Acme"},
        ])
        result = _parse_llm_response(response)
        assert len(result) == 1
        assert result[0].start == 0.0
        assert result[0].end == 45.0
        assert result[0].confidence == 0.9
        assert result[0].label == "Ad for Acme"
        assert result[0].detector == "transcription"

    def test_parses_empty_segments(self):
        response = _llm_response_json([])
        assert _parse_llm_response(response) == []

    def test_defaults_confidence_to_0_8(self):
        response = _llm_response_json([{"start": 0.0, "end": 10.0}])
        result = _parse_llm_response(response)
        assert result[0].confidence == 0.8

    def test_raises_on_invalid_json(self):
        with pytest.raises(ValueError, match="LLM returned invalid JSON"):
            _parse_llm_response("not json")

    def test_strips_markdown_fences(self):
        fenced = '```json\n{"segments": [{"start": 0.0, "end": 10.0, "confidence": 0.9, "label": "Ad"}]}\n```'
        result = _parse_llm_response(fenced)
        assert len(result) == 1
        assert result[0].start == 0.0


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------

class TestAnthropicProvider:
    def test_classify_ads_calls_anthropic(self):
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=_llm_response_json([
            {"start": 0.0, "end": 30.0, "confidence": 0.85, "label": "Pre-roll ad"},
        ]))]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        config = {"llm": {"model": "claude-sonnet-4-20250514"}}

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = AnthropicProvider()
            result = provider.classify_ads(_whisper_segments(), config)

        assert len(result) == 1
        assert result[0].start == 0.0
        assert result[0].label == "Pre-roll ad"
        mock_client.messages.create.assert_called_once()

    def test_passes_configured_model(self):
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=_llm_response_json([]))]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        config = {"llm": {"model": "claude-haiku-4-5-20251001"}}

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            AnthropicProvider().classify_ads([], config)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# get_llm_provider
# ---------------------------------------------------------------------------

class TestGetLlmProvider:
    def test_returns_anthropic_by_default(self):
        provider = get_llm_provider({})
        assert isinstance(provider, AnthropicProvider)

    def test_returns_anthropic_when_configured(self):
        provider = get_llm_provider({"llm": {"provider": "anthropic"}})
        assert isinstance(provider, AnthropicProvider)

    def test_raises_on_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm_provider({"llm": {"provider": "unknown"}})


# ---------------------------------------------------------------------------
# TranscriptionDetector
# ---------------------------------------------------------------------------

class TestTranscriptionDetector:
    def test_detect_returns_ad_segments(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        ad_segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Ad"),
        ]

        config = {"whisper": {"url": "http://localhost:9000"}, "llm": {"provider": "anthropic"}}

        with patch("podcast_etl.detectors.transcription.transcribe", return_value=_whisper_segments()):
            with patch("podcast_etl.detectors.transcription.get_llm_provider") as mock_get:
                mock_provider = MagicMock()
                mock_provider.classify_ads.return_value = ad_segments
                mock_get.return_value = mock_provider

                detector = TranscriptionDetector()
                result = detector.detect(audio_file, config)

        assert len(result) == 1
        assert result[0].start == 0.0

    def test_detect_filters_by_min_confidence(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        ad_segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.3, detector="transcription", label="Low confidence"),
            AdSegment(start=100.0, end=130.0, confidence=0.9, detector="transcription", label="High confidence"),
        ]

        config = {"whisper": {"url": "http://localhost:9000"}, "min_confidence": 0.5}

        with patch("podcast_etl.detectors.transcription.transcribe", return_value=_whisper_segments()):
            with patch("podcast_etl.detectors.transcription.get_llm_provider") as mock_get:
                mock_provider = MagicMock()
                mock_provider.classify_ads.return_value = ad_segments
                mock_get.return_value = mock_provider

                result = TranscriptionDetector().detect(audio_file, config)

        assert len(result) == 1
        assert result[0].label == "High confidence"

    def test_detect_returns_empty_when_no_transcript(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio")

        config = {"whisper": {"url": "http://localhost:9000"}}

        with patch("podcast_etl.detectors.transcription.transcribe", return_value=[]):
            result = TranscriptionDetector().detect(audio_file, config)

        assert result == []


# ---------------------------------------------------------------------------
# resolve_overlaps
# ---------------------------------------------------------------------------

class TestResolveOverlaps:
    def test_overlap_snaps_later_start_keeping_segments_separate(self):
        # Earlier wins: the overlap [20,30] goes to the first segment; the second
        # is kept distinct with its start snapped to the frontier.
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a", label="Ad 1"),
            AdSegment(start=20.0, end=50.0, confidence=0.8, detector="b", label="Ad 2"),
        ]
        result = resolve_overlaps(segments)
        assert [(s.start, s.end) for s in result] == [(0.0, 30.0), (30.0, 50.0)]
        # Each segment keeps its own metadata — nothing is fused.
        assert [s.label for s in result] == ["Ad 1", "Ad 2"]
        assert [s.confidence for s in result] == [0.9, 0.8]
        assert [s.detector for s in result] == ["a", "b"]

    def test_chain_of_overlaps(self):
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a"),
            AdSegment(start=20.0, end=50.0, confidence=0.8, detector="a"),
            AdSegment(start=40.0, end=70.0, confidence=0.7, detector="a"),
        ]
        result = resolve_overlaps(segments)
        assert [(s.start, s.end) for s in result] == [(0.0, 30.0), (30.0, 50.0), (50.0, 70.0)]

    def test_fully_contained_segment_is_dropped(self):
        segments = [
            AdSegment(start=0.0, end=100.0, confidence=0.9, detector="a", label="Big"),
            AdSegment(start=10.0, end=20.0, confidence=0.8, detector="a", label="Inner"),
            AdSegment(start=30.0, end=40.0, confidence=0.8, detector="a", label="Inner2"),
        ]
        result = resolve_overlaps(segments)
        assert [(s.start, s.end) for s in result] == [(0.0, 100.0)]
        assert result[0].label == "Big"

    def test_gap_larger_than_buffer_is_preserved(self):
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a"),
            AdSegment(start=100.0, end=130.0, confidence=0.8, detector="a"),
        ]
        result = resolve_overlaps(segments)
        assert [(s.start, s.end) for s in result] == [(0.0, 30.0), (100.0, 130.0)]

    def test_small_gap_within_buffer_is_closed(self):
        # 3s gap (< 5s buffer): the later start snaps back to the frontier so the
        # two ads become contiguous (no content sliver left between them).
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a", label="Ad 1"),
            AdSegment(start=33.0, end=60.0, confidence=0.8, detector="a", label="Ad 2"),
        ]
        result = resolve_overlaps(segments)
        assert [(s.start, s.end) for s in result] == [(0.0, 30.0), (30.0, 60.0)]
        assert [s.label for s in result] == ["Ad 1", "Ad 2"]

    def test_gap_exactly_at_buffer_is_closed(self):
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a"),
            AdSegment(start=35.0, end=60.0, confidence=0.8, detector="a"),
        ]
        result = resolve_overlaps(segments)
        assert [(s.start, s.end) for s in result] == [(0.0, 30.0), (30.0, 60.0)]

    def test_buffer_is_configurable(self):
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a"),
            AdSegment(start=33.0, end=60.0, confidence=0.8, detector="a"),
        ]
        # With a 1s buffer the 3s gap is a real gap and is preserved.
        result = resolve_overlaps(segments, buffer=1.0)
        assert [(s.start, s.end) for s in result] == [(0.0, 30.0), (33.0, 60.0)]

    def test_exactly_adjacent_segments_stay_separate(self):
        segments = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a", label="Ad 1"),
            AdSegment(start=30.0, end=60.0, confidence=0.8, detector="a", label="Ad 2"),
        ]
        result = resolve_overlaps(segments)
        assert [(s.start, s.end) for s in result] == [(0.0, 30.0), (30.0, 60.0)]
        assert [s.label for s in result] == ["Ad 1", "Ad 2"]

    def test_empty_input(self):
        assert resolve_overlaps([]) == []

    def test_single_segment(self):
        segments = [AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a")]
        result = resolve_overlaps(segments)
        assert len(result) == 1

    def test_unsorted_input_is_handled(self):
        segments = [
            AdSegment(start=40.0, end=70.0, confidence=0.7, detector="a"),
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="a"),
        ]
        result = resolve_overlaps(segments)
        assert [(s.start, s.end) for s in result] == [(0.0, 30.0), (40.0, 70.0)]
