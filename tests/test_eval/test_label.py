"""Tests for eval.label: labelling episodes using production detection seams."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance
from podcast_etl.models import Episode, StepStatus

import logging

from eval.label import (
    _classify,
    _get_audio_duration,
    _get_transcript,
    _label_resolved,
    _reuse_production_transcript,
    iter_episode_refs,
    label_dataset,
    label_episode,
)
from eval.resolve import ResolvedEpisode


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_episode(
    download_path: str = "audio/episode.mp3",
    detect_ads_whisper: dict | None = None,
) -> Episode:
    """Build an Episode with a completed download step and optional detect_ads status."""
    status: dict = {
        "download": StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"path": download_path, "size_bytes": 1024},
        ),
    }
    if detect_ads_whisper is not None:
        status["detect_ads"] = StepStatus(
            completed_at="2024-01-15T11:00:00",
            result={"whisper": detect_ads_whisper, "llm": {"provider": "anthropic", "model": "m", "prompt": "default"}},
        )
    return Episode(
        title="Test Episode",
        guid="guid-abc",
        published="Mon, 15 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration="3600",
        description="desc",
        slug="test-episode",
        status=status,
    )


def _write_episode(tmp_path: Path, podcast_slug: str, episode_json: str, episode: Episode) -> Path:
    ep_dir = tmp_path / podcast_slug / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    ep_path = ep_dir / episode_json
    ep_path.write_text(json.dumps(episode.to_dict()), encoding="utf-8")
    return ep_path


def _write_audio(tmp_path: Path, podcast_slug: str, relative_path: str) -> Path:
    audio_path = tmp_path / podcast_slug / relative_path
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"ID3" + b"\x00" * 128)  # minimal fake MP3
    return audio_path


def _write_transcript(tmp_path: Path, podcast_slug: str, audio_stem: str, segments: list) -> Path:
    transcripts_dir = tmp_path / podcast_slug / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    path = transcripts_dir / f"{audio_stem}.json"
    path.write_text(json.dumps(segments), encoding="utf-8")
    return path


def _make_resolved(
    tmp_path: Path,
    podcast_slug: str = "my-podcast",
    episode_json: str = "2024-01-15-test-episode-ab12cd34.json",
    download_path: str = "audio/episode.mp3",
    detect_ads_whisper: dict | None = None,
    with_transcript: list | None = None,
) -> ResolvedEpisode:
    """Write episode + audio to disk and return a ResolvedEpisode."""
    episode = _make_episode(download_path=download_path, detect_ads_whisper=detect_ads_whisper)
    _write_episode(tmp_path, podcast_slug, episode_json, episode)
    audio_path = _write_audio(tmp_path, podcast_slug, download_path)

    transcript_path: Path | None = None
    if with_transcript is not None:
        transcript_path = _write_transcript(tmp_path, podcast_slug, audio_path.stem, with_transcript)

    return ResolvedEpisode(
        podcast_dir=tmp_path / podcast_slug,
        episode=episode,
        audio_path=audio_path,
        transcript_path=transcript_path,
    )


NORM_WHISPER = {"model": "base", "language": "en"}

AD_CONFIG = {
    "whisper": {"model": "base", "language": "en"},
    "llm": {"provider": "anthropic", "model": "test-model", "prompt": "default"},
    "min_confidence": 0.5,
}

TRANSCRIPT = [{"start": 0.0, "end": 10.0, "text": "Buy now!"}]


# ---------------------------------------------------------------------------
# _reuse_production_transcript
# ---------------------------------------------------------------------------

class TestReuseProductionTranscript:
    def test_returns_none_when_no_transcript_file(self, tmp_path):
        resolved = _make_resolved(tmp_path, with_transcript=None)
        result = _reuse_production_transcript(resolved, {"model": "base", "language": "en"})
        assert result is None

    def test_returns_none_when_no_detect_ads_status(self, tmp_path):
        resolved = _make_resolved(tmp_path, with_transcript=TRANSCRIPT)
        # No detect_ads_whisper -> no detect_ads status
        result = _reuse_production_transcript(resolved, {"model": "base", "language": "en"})
        assert result is None

    def test_returns_none_when_no_recorded_whisper(self, tmp_path):
        episode = _make_episode(detect_ads_whisper=None)
        # Manually add detect_ads status without whisper
        episode.status["detect_ads"] = StepStatus(
            completed_at="2024-01-15T11:00:00",
            result={"llm": {"provider": "anthropic"}},  # no 'whisper' key
        )
        _write_episode(tmp_path, "my-podcast", "ep.json", episode)
        audio_path = _write_audio(tmp_path, "my-podcast", "audio/episode.mp3")
        transcript_path = _write_transcript(tmp_path, "my-podcast", "episode", TRANSCRIPT)

        resolved = ResolvedEpisode(
            podcast_dir=tmp_path / "my-podcast",
            episode=episode,
            audio_path=audio_path,
            transcript_path=transcript_path,
        )
        result = _reuse_production_transcript(resolved, {"model": "base", "language": "en"})
        assert result is None

    def test_returns_none_when_whisper_differs(self, tmp_path):
        resolved = _make_resolved(
            tmp_path,
            detect_ads_whisper={"model": "large", "language": "en"},  # different model
            with_transcript=TRANSCRIPT,
        )
        result = _reuse_production_transcript(resolved, {"model": "base", "language": "en"})
        assert result is None

    def test_returns_segments_when_whisper_matches(self, tmp_path):
        resolved = _make_resolved(
            tmp_path,
            detect_ads_whisper={"model": "base", "language": "en"},
            with_transcript=TRANSCRIPT,
        )
        result = _reuse_production_transcript(resolved, {"model": "base", "language": "en"})
        assert result == TRANSCRIPT

    def test_matches_normalized_form(self, tmp_path):
        """Extra fields in eval whisper config that don't affect normalization still match."""
        resolved = _make_resolved(
            tmp_path,
            detect_ads_whisper={"model": "base", "language": "en"},  # stored normalized
            with_transcript=TRANSCRIPT,
        )
        # Pass whisper with extra fields that normalize away (e.g. api_key)
        result = _reuse_production_transcript(
            resolved,
            {"model": "base", "language": "en", "api_key": "secret"},
        )
        assert result == TRANSCRIPT


# ---------------------------------------------------------------------------
# _get_transcript
# ---------------------------------------------------------------------------

class TestGetTranscript:
    def test_returns_fresh_transcription_when_no_cache_no_production(self, tmp_path):
        resolved = _make_resolved(tmp_path, with_transcript=None)
        cache: dict = {}

        with patch("eval.label.transcribe", return_value=TRANSCRIPT) as mock_transcribe:
            result = _get_transcript(resolved, {"model": "base", "language": "en"}, cache, "key-1")

        assert result == TRANSCRIPT
        mock_transcribe.assert_called_once()

    def test_reuses_production_transcript_when_provenance_matches(self, tmp_path):
        resolved = _make_resolved(
            tmp_path,
            detect_ads_whisper={"model": "base", "language": "en"},
            with_transcript=TRANSCRIPT,
        )
        cache: dict = {}

        with patch("eval.label.transcribe") as mock_transcribe:
            result = _get_transcript(resolved, {"model": "base", "language": "en"}, cache, "key-1")

        assert result == TRANSCRIPT
        mock_transcribe.assert_not_called()

    def test_retranscribes_when_provenance_differs(self, tmp_path):
        resolved = _make_resolved(
            tmp_path,
            detect_ads_whisper={"model": "large", "language": "en"},
            with_transcript=TRANSCRIPT,
        )
        cache: dict = {}
        fresh = [{"start": 5.0, "end": 15.0, "text": "Fresh"}]

        with patch("eval.label.transcribe", return_value=fresh) as mock_transcribe:
            result = _get_transcript(resolved, {"model": "base", "language": "en"}, cache, "key-1")

        assert result == fresh
        mock_transcribe.assert_called_once()

    def test_cache_hit_skips_transcription(self, tmp_path):
        resolved = _make_resolved(tmp_path, with_transcript=None)
        cache: dict = {}

        with patch("eval.label.transcribe", return_value=TRANSCRIPT) as mock_transcribe:
            result1 = _get_transcript(resolved, {"model": "base", "language": "en"}, cache, "key-1")
            result2 = _get_transcript(resolved, {"model": "base", "language": "en"}, cache, "key-1")

        assert result1 == result2 == TRANSCRIPT
        assert mock_transcribe.call_count == 1  # only transcribed once

    def test_different_whisper_configs_use_different_cache_keys(self, tmp_path):
        resolved = _make_resolved(tmp_path, with_transcript=None)
        cache: dict = {}
        transcript_base = [{"start": 0.0, "end": 5.0, "text": "base"}]
        transcript_large = [{"start": 0.0, "end": 5.0, "text": "large"}]

        with patch("eval.label.transcribe", side_effect=[transcript_base, transcript_large]):
            r1 = _get_transcript(resolved, {"model": "base", "language": "en"}, cache, "key-1")
            r2 = _get_transcript(resolved, {"model": "large", "language": "en"}, cache, "key-1")

        assert r1 == transcript_base
        assert r2 == transcript_large
        assert len(cache) == 2


# ---------------------------------------------------------------------------
# _classify (mirroring detect_ads filter-then-resolve order)
# ---------------------------------------------------------------------------

class TestClassify:
    def test_min_confidence_filters_low_segments(self, tmp_path):
        high = AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription")
        low = AdSegment(start=60.0, end=90.0, confidence=0.3, detector="transcription")

        with patch("eval.label.classify", return_value=[high, low]), \
             patch("eval.label.load_prompt", return_value="prompt text"):
            kept = _classify(TRANSCRIPT, {"llm": {"prompt": "default"}, "min_confidence": 0.5}, client=None)

        assert len(kept) == 1
        assert kept[0].start == 0.0

    def test_overlapping_segments_resolved(self):
        seg1 = AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription")
        seg2 = AdSegment(start=20.0, end=50.0, confidence=0.8, detector="transcription")

        with patch("eval.label.classify", return_value=[seg1, seg2]), \
             patch("eval.label.load_prompt", return_value="prompt text"):
            result = _classify(TRANSCRIPT, {"llm": {"prompt": "default"}, "min_confidence": 0.0}, client=None)

        # resolve_overlaps snaps the second start to the first's end
        assert len(result) == 2
        assert result[0] == seg1
        assert result[1].start == 30.0
        assert result[1].end == 50.0

    def test_prompt_loaded_from_llm_config(self):
        with patch("eval.label.classify", return_value=[]) as mock_classify, \
             patch("eval.label.load_prompt", return_value="my prompt") as mock_load:
            _classify(TRANSCRIPT, {"llm": {"prompt": "custom"}, "min_confidence": 0.5}, client=None)

        mock_load.assert_called_once_with("custom")
        mock_classify.assert_called_once_with(TRANSCRIPT, "my prompt", {"prompt": "custom"}, client=None)

    def test_default_prompt_used_when_not_specified(self):
        with patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="default prompt") as mock_load:
            _classify(TRANSCRIPT, {"llm": {}, "min_confidence": 0.5}, client=None)

        mock_load.assert_called_once_with("default")

    def test_filter_happens_before_resolve_overlaps(self):
        """A segment filtered by min_confidence must not survive into resolve_overlaps."""
        # If filter happened AFTER resolve_overlaps, a low-confidence segment
        # that was merged would slip through. Verify filter-first semantics.
        low_conf = AdSegment(start=0.0, end=30.0, confidence=0.2, detector="t")
        high_conf = AdSegment(start=20.0, end=50.0, confidence=0.9, detector="t")

        with patch("eval.label.classify", return_value=[low_conf, high_conf]), \
             patch("eval.label.load_prompt", return_value="p"):
            result = _classify(TRANSCRIPT, {"llm": {}, "min_confidence": 0.5}, client=None)

        # Only high_conf survives; its start should be 20.0 (not snapped to 30.0,
        # because low_conf was dropped before resolve_overlaps ran).
        assert len(result) == 1
        assert result[0].start == 20.0


# ---------------------------------------------------------------------------
# label_episode
# ---------------------------------------------------------------------------

class TestLabelEpisode:
    def _setup(self, tmp_path, podcast_slug="my-podcast", episode_json="ep.json"):
        """Write episode + audio to output_dir and return (output_dir, dataset_root, ref)."""
        output_dir = tmp_path / "output"
        dataset_root = tmp_path / "dataset"
        _write_episode(output_dir, podcast_slug, episode_json, _make_episode())
        _write_audio(output_dir, podcast_slug, "audio/episode.mp3")
        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
        return output_dir, dataset_root, ref

    def test_writes_labels_file_at_correct_path(self, tmp_path):
        # episode_json="ep.json", audio stem="episode" — the label file must
        # be named from episode_json ("ep"), not from the audio stem ("episode").
        output_dir, dataset_root, ref = self._setup(tmp_path)

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=3600.0):
            path = label_episode(ref, AD_CONFIG, output_dir, dataset_root)

        # Stem must come from episode_json ("ep"), not from the audio path ("episode").
        expected = dataset_root / "my-podcast" / "labels" / "ep.json"
        assert path == expected
        assert path.exists()
        # Regression guard: filename is derived from episode_json, not audio stem.
        assert path.name == "ep.json", (
            "label file stem must be derived from EpisodeRef.episode_json, not the audio path"
        )

    def test_written_labels_roundtrip(self, tmp_path):
        output_dir, dataset_root, ref = self._setup(tmp_path)
        seg = AdSegment(start=10.0, end=40.0, confidence=0.9, detector="transcription")

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[seg]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=3600.0):
            path = label_episode(ref, AD_CONFIG, output_dir, dataset_root)

        labels = Labels.load(path)
        assert labels.episode_ref == ref
        assert labels.audio_duration == 3600.0
        assert len(labels.segments) == 1
        assert labels.segments[0].start == 10.0
        assert labels.segments[0].end == 40.0

    def test_provenance_annotator_is_llm_model(self, tmp_path):
        output_dir, dataset_root, ref = self._setup(tmp_path)

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=3600.0):
            path = label_episode(ref, AD_CONFIG, output_dir, dataset_root)

        labels = Labels.load(path)
        assert labels.provenance.annotator == "test-model"
        assert labels.provenance.llm["model"] == "test-model"
        assert labels.provenance.llm["provider"] == "anthropic"
        assert labels.provenance.llm["prompt"] == "default"

    def test_provenance_whisper_is_normalized(self, tmp_path):
        output_dir, dataset_root, ref = self._setup(tmp_path)
        config = {
            **AD_CONFIG,
            "whisper": {"model": "base", "language": "en", "api_key": "secret"},
        }

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=3600.0):
            path = label_episode(ref, config, output_dir, dataset_root)

        labels = Labels.load(path)
        # api_key must not appear in provenance
        assert labels.provenance.whisper == {"model": "base", "language": "en"}

    def test_audio_duration_rounded(self, tmp_path):
        output_dir, dataset_root, ref = self._setup(tmp_path)

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=3600.12345):
            path = label_episode(ref, AD_CONFIG, output_dir, dataset_root)

        labels = Labels.load(path)
        assert labels.audio_duration == round(3600.12345, 2)

    def test_shared_transcript_cache_used(self, tmp_path):
        output_dir, dataset_root, ref = self._setup(tmp_path)
        cache: dict = {}

        with patch("eval.label.transcribe", return_value=TRANSCRIPT) as mock_transcribe, \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=3600.0):
            label_episode(ref, AD_CONFIG, output_dir, dataset_root, transcript_cache=cache)
            label_episode(ref, AD_CONFIG, output_dir, dataset_root, transcript_cache=cache)

        # Only one transcription despite two calls
        assert mock_transcribe.call_count == 1

    def test_filenotfounderror_when_audio_missing(self, tmp_path):
        output_dir = tmp_path / "output"
        dataset_root = tmp_path / "dataset"
        podcast_slug = "my-podcast"
        episode_json = "ep.json"
        # Write episode but NOT the audio file
        _write_episode(output_dir, podcast_slug, episode_json, _make_episode())
        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)

        with pytest.raises(FileNotFoundError):
            label_episode(ref, AD_CONFIG, output_dir, dataset_root)


# ---------------------------------------------------------------------------
# label_dataset
# ---------------------------------------------------------------------------

class TestLabelDataset:
    def _setup_episode(self, tmp_path, podcast_slug, episode_json):
        output_dir = tmp_path / "output"
        _write_episode(output_dir, podcast_slug, episode_json, _make_episode())
        _write_audio(output_dir, podcast_slug, "audio/episode.mp3")
        return EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)

    def test_labels_written_for_all_resolved_episodes(self, tmp_path):
        dataset_root = tmp_path / "dataset"
        refs = [
            self._setup_episode(tmp_path, "p1", "ep1.json"),
            self._setup_episode(tmp_path, "p2", "ep2.json"),
        ]
        output_dir = tmp_path / "output"

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            paths = label_dataset(refs, AD_CONFIG, output_dir, dataset_root)

        assert len(paths) == 2
        for path in paths:
            assert path.exists()

    def test_unresolvable_episode_skipped(self, tmp_path):
        dataset_root = tmp_path / "dataset"
        good = self._setup_episode(tmp_path, "good-podcast", "ep.json")
        bad = EpisodeRef(podcast_slug="nonexistent", episode_json="ep.json")
        output_dir = tmp_path / "output"

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            paths = label_dataset([good, bad], AD_CONFIG, output_dir, dataset_root)

        # Only the good episode is in the results
        assert len(paths) == 1
        assert paths[0].exists()

    def test_shared_client_built_once(self, tmp_path):
        dataset_root = tmp_path / "dataset"
        refs = [
            self._setup_episode(tmp_path, "p1", "ep1.json"),
            self._setup_episode(tmp_path, "p2", "ep2.json"),
        ]
        output_dir = tmp_path / "output"
        fake_client = MagicMock()

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=fake_client) as mock_build, \
             patch("eval.label._get_audio_duration", return_value=600.0):
            label_dataset(refs, AD_CONFIG, output_dir, dataset_root)

        # build_llm_client called once for the whole dataset
        mock_build.assert_called_once()

    def test_shared_transcript_cache_across_configs(self, tmp_path):
        """Sharing the same transcript_cache means the same episode is only transcribed once."""
        dataset_root = tmp_path / "dataset"
        ref = self._setup_episode(tmp_path, "p1", "ep1.json")
        output_dir = tmp_path / "output"
        shared_cache: dict = {}

        with patch("eval.label.transcribe", return_value=TRANSCRIPT) as mock_transcribe, \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            label_dataset([ref], AD_CONFIG, output_dir, dataset_root, transcript_cache=shared_cache)
            label_dataset([ref], AD_CONFIG, output_dir, dataset_root, transcript_cache=shared_cache)

        assert mock_transcribe.call_count == 1

    def test_caller_supplied_client_not_rebuilt(self, tmp_path):
        dataset_root = tmp_path / "dataset"
        ref = self._setup_episode(tmp_path, "p1", "ep1.json")
        output_dir = tmp_path / "output"
        supplied_client = MagicMock()

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client") as mock_build, \
             patch("eval.label._get_audio_duration", return_value=600.0):
            label_dataset([ref], AD_CONFIG, output_dir, dataset_root, client=supplied_client)

        mock_build.assert_not_called()

    def test_returns_empty_when_all_unresolvable(self, tmp_path):
        dataset_root = tmp_path / "dataset"
        output_dir = tmp_path / "output"
        refs = [EpisodeRef(podcast_slug="ghost", episode_json="ep.json")]

        with patch("eval.label.build_llm_client", return_value=None):
            paths = label_dataset(refs, AD_CONFIG, output_dir, dataset_root)

        assert paths == []

    def test_missing_prompt_propagates_not_swallowed(self, tmp_path):
        """FileNotFoundError from load_prompt must NOT be silently skipped."""
        dataset_root = tmp_path / "dataset"
        ref = self._setup_episode(tmp_path, "p1", "ep1.json")
        output_dir = tmp_path / "output"

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.load_prompt", side_effect=FileNotFoundError("no such prompt")), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            with pytest.raises(FileNotFoundError, match="no such prompt"):
                label_dataset([ref], AD_CONFIG, output_dir, dataset_root)

    def test_unresolvable_episode_still_skipped_with_prompt_error_sibling(self, tmp_path):
        """Confirm the legitimate-skip path still works after the propagation fix."""
        dataset_root = tmp_path / "dataset"
        good = self._setup_episode(tmp_path, "good-podcast", "ep.json")
        bad = EpisodeRef(podcast_slug="nonexistent", episode_json="ep.json")
        output_dir = tmp_path / "output"

        with patch("eval.label.transcribe", return_value=TRANSCRIPT), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            paths = label_dataset([good, bad], AD_CONFIG, output_dir, dataset_root)

        assert len(paths) == 1
        assert paths[0].exists()


# ---------------------------------------------------------------------------
# _label_resolved: empty transcript warning
# ---------------------------------------------------------------------------

class TestLabelResolvedEmptyTranscript:
    def _setup(self, tmp_path):
        output_dir = tmp_path / "output"
        dataset_root = tmp_path / "dataset"
        podcast_slug = "my-podcast"
        episode_json = "ep.json"
        _write_episode(output_dir, podcast_slug, episode_json, _make_episode())
        audio_path = _write_audio(output_dir, podcast_slug, "audio/episode.mp3")
        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
        resolved = ResolvedEpisode(
            podcast_dir=output_dir / podcast_slug,
            episode=_make_episode(),
            audio_path=audio_path,
            transcript_path=None,
        )
        return resolved, ref, dataset_root

    def test_empty_transcript_writes_labels_with_zero_segments(self, tmp_path):
        resolved, ref, dataset_root = self._setup(tmp_path)

        with patch("eval.label.transcribe", return_value=[]), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            path = _label_resolved(
                resolved, ref, AD_CONFIG, dataset_root,
                client=None,
                transcript_cache={},
            )

        labels = Labels.load(path)
        assert labels.segments == []

    def test_empty_transcript_logs_warning(self, tmp_path, caplog):
        resolved, ref, dataset_root = self._setup(tmp_path)

        with patch("eval.label.transcribe", return_value=[]), \
             patch("eval.label._get_audio_duration", return_value=600.0), \
             caplog.at_level(logging.WARNING, logger="eval.label"):
            _label_resolved(
                resolved, ref, AD_CONFIG, dataset_root,
                client=None,
                transcript_cache={},
            )

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Empty transcript" in m for m in warning_messages), (
            f"Expected 'Empty transcript' warning, got: {warning_messages}"
        )


# ---------------------------------------------------------------------------
# iter_episode_refs
# ---------------------------------------------------------------------------

class TestIterEpisodeRefs:
    def _write_episodes(self, tmp_path, podcast_slug, filenames):
        episodes_dir = tmp_path / podcast_slug / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            (episodes_dir / name).write_text("{}", encoding="utf-8")

    def test_returns_refs_sorted_by_filename(self, tmp_path):
        names = ["2024-01-20-ep-c.json", "2024-01-10-ep-a.json", "2024-01-15-ep-b.json"]
        self._write_episodes(tmp_path, "my-podcast", names)

        refs = iter_episode_refs(tmp_path, "my-podcast")
        assert [r.episode_json for r in refs] == sorted(names)

    def test_all_refs_have_correct_podcast_slug(self, tmp_path):
        self._write_episodes(tmp_path, "my-podcast", ["ep1.json", "ep2.json"])
        refs = iter_episode_refs(tmp_path, "my-podcast")
        assert all(r.podcast_slug == "my-podcast" for r in refs)

    def test_episode_filter_regex_applied(self, tmp_path):
        names = ["2024-01-10-part-1-abc.json", "2024-01-15-full-def.json", "2024-01-20-part-2-ghi.json"]
        self._write_episodes(tmp_path, "my-podcast", names)

        refs = iter_episode_refs(tmp_path, "my-podcast", episode_filter="part")
        assert len(refs) == 2
        assert all("part" in r.episode_json for r in refs)

    def test_episode_filter_none_returns_all(self, tmp_path):
        names = ["ep1.json", "ep2.json", "ep3.json"]
        self._write_episodes(tmp_path, "my-podcast", names)

        refs = iter_episode_refs(tmp_path, "my-podcast", episode_filter=None)
        assert len(refs) == 3

    def test_missing_episodes_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Episodes directory not found"):
            iter_episode_refs(tmp_path, "nonexistent-podcast")

    def test_empty_episodes_dir_returns_empty(self, tmp_path):
        episodes_dir = tmp_path / "my-podcast" / "episodes"
        episodes_dir.mkdir(parents=True)
        refs = iter_episode_refs(tmp_path, "my-podcast")
        assert refs == []

    def test_episode_filter_regex_search_not_match(self, tmp_path):
        """re.search, not re.match — filter can match anywhere in filename."""
        names = ["2024-part-1.json", "2024-full.json"]
        self._write_episodes(tmp_path, "my-podcast", names)

        # "part" appears in the middle of the first filename
        refs = iter_episode_refs(tmp_path, "my-podcast", episode_filter="part")
        assert len(refs) == 1
        assert refs[0].episode_json == "2024-part-1.json"
