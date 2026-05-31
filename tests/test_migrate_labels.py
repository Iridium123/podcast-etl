"""Tests for the one-time scripts/migrate_labels.py migration."""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_labels.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("migrate_labels", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migrate_labels = _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_output_dir(tmp_path, *, segments, migrated=False):
    """Create an output/<slug> tree with one episode JSON."""
    podcast_dir = tmp_path / "output" / "my-podcast"
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir(parents=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({"slug": "my-podcast"}))

    detect_result = {
        "detectors_used": ["transcription"],
        "transcript_path": "transcripts/episode.json",
        "total_ad_duration": sum(s["end"] - s["start"] for s in segments),
    }
    if migrated:
        detect_result["labels_path"] = "labels/episode.json"
    else:
        detect_result["segments"] = segments
        detect_result["audio_duration"] = 600.0

    episode = {
        "title": "Episode One",
        "guid": "guid-1",
        "published": "Mon, 15 Jan 2024 00:00:00 +0000",
        "slug": "episode-one",
        "status": {
            "download": {"completed_at": "t", "result": {"path": "audio/episode.mp3"}},
            "detect_ads": {"completed_at": "2024-01-15T10:05:00", "result": detect_result},
        },
    }
    episode_json = episodes_dir / "2024-01-15-episode-one-ab12cd34.json"
    episode_json.write_text(json.dumps(episode, indent=2))
    return tmp_path / "output", podcast_dir, episode_json


_SEGMENTS = [
    {"start": 0.0, "end": 30.0, "confidence": 0.9, "detector": "transcription", "label": "Ad"},
]


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------

class TestMigrate:
    def test_migrates_embedded_segments_to_label_file(self, tmp_path):
        output_dir, podcast_dir, episode_json = _make_output_dir(tmp_path, segments=_SEGMENTS)

        changed, failed = migrate_labels.migrate(output_dir, dry_run=False)
        assert (changed, failed) == (1, 0)

        # Label file written with the segments + audio_duration.
        labels_file = podcast_dir / "labels" / "episode.json"
        assert labels_file.exists()
        labels = json.loads(labels_file.read_text())
        assert labels["audio_duration"] == 600.0
        assert len(labels["segments"]) == 1
        assert labels["segments"][0]["label"] == "Ad"
        assert labels["episode_ref"] == {
            "podcast_slug": "my-podcast",
            "episode_json": "2024-01-15-episode-one-ab12cd34.json",
        }

        # Episode result rewritten: no segments/audio_duration, has labels_path.
        result = json.loads(episode_json.read_text())["status"]["detect_ads"]["result"]
        assert "segments" not in result
        assert "audio_duration" not in result
        assert result["labels_path"] == "labels/episode.json"

    def test_dry_run_writes_nothing(self, tmp_path):
        output_dir, podcast_dir, episode_json = _make_output_dir(tmp_path, segments=_SEGMENTS)
        before = episode_json.read_text()

        changed, failed = migrate_labels.migrate(output_dir, dry_run=True)
        assert (changed, failed) == (1, 0)
        assert not (podcast_dir / "labels").exists()
        assert episode_json.read_text() == before

    def test_idempotent_skips_already_migrated(self, tmp_path):
        output_dir, _podcast_dir, _episode_json = _make_output_dir(
            tmp_path, segments=_SEGMENTS, migrated=True,
        )
        assert migrate_labels.migrate(output_dir, dry_run=False) == (0, 0)

    def test_rerun_after_migration_is_noop(self, tmp_path):
        output_dir, _podcast_dir, _episode_json = _make_output_dir(tmp_path, segments=_SEGMENTS)
        assert migrate_labels.migrate(output_dir, dry_run=False) == (1, 0)
        assert migrate_labels.migrate(output_dir, dry_run=False) == (0, 0)

    def test_underivable_stem_is_isolated_failure(self, tmp_path):
        # No transcript_path and no download path -> stem can't be derived.
        output_dir, podcast_dir, episode_json = _make_output_dir(tmp_path, segments=_SEGMENTS)
        data = json.loads(episode_json.read_text())
        data["status"]["detect_ads"]["result"].pop("transcript_path")
        data["status"]["download"]["result"]["path"] = ""
        episode_json.write_text(json.dumps(data))
        before = episode_json.read_text()

        changed, failed = migrate_labels.migrate(output_dir, dry_run=False)
        assert (changed, failed) == (0, 1)
        # Nothing written: no colliding labels/.json, episode untouched.
        assert not (podcast_dir / "labels" / ".json").exists()
        assert episode_json.read_text() == before


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_missing_output_dir_returns_error(self, tmp_path):
        assert migrate_labels.main(["--output-dir", str(tmp_path / "nope")]) == 1

    def test_main_runs_migration(self, tmp_path):
        output_dir, podcast_dir, _ = _make_output_dir(tmp_path, segments=_SEGMENTS)
        assert migrate_labels.main(["--output-dir", str(output_dir)]) == 0
        assert (podcast_dir / "labels" / "episode.json").exists()
