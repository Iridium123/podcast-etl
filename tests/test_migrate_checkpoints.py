"""Tests for checkpoint_migration.py: slug-keyed -> GUID-keyed seed/upload checkpoints."""
from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

from podcast_etl.checkpoint_migration import migrate_checkpoints
from podcast_etl.checkpoints import find_checkpoint
from podcast_etl.models import Episode, StepStatus


def _status(completed_at: str, result: dict) -> StepStatus:
    return StepStatus(completed_at=completed_at, result=result)


def _episode(guid: str, title: str, published: str, status: dict | None = None) -> Episode:
    return Episode(
        title=title,
        guid=guid,
        published=published,
        audio_url="https://example.com/ep.mp3",
        duration="1:00:00",
        description="desc",
        slug=title.lower().replace(" ", "-"),
        status=status or {},
    )


def _write_episode(episodes_dir: Path, episode: Episode, filename: str | None = None) -> Path:
    episodes_dir.mkdir(parents=True, exist_ok=True)
    name = filename or f"{episode.slug}.json"
    path = episodes_dir / name
    path.write_text(json.dumps(episode.to_dict(), indent=2) + "\n")
    return path


def _write_legacy(directory: Path, name: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# THE INCIDENT (primary test)
# ---------------------------------------------------------------------------

def test_incident_new_episode_poisoned_by_slug_collision_is_healed(tmp_path):
    """potpourri (new) inherits potpourri-2 (old)'s slug-keyed checkpoint.

    Both episodes' own guid-keyed episode JSON files record the poisoning
    faithfully: the new episode's seed/upload status holds the old episode's
    hash/URL, with a later completed_at (it ran second). Migration must clear
    the new episode's status without ever writing it a checkpoint, and give
    the old (healthy) episode a correct GUID-keyed checkpoint.
    """
    podcast_dir = tmp_path / "my-podcast"
    episodes_dir = podcast_dir / "episodes"

    old = _episode(
        guid="guid-old", title="Potpourri", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={
            "torrent": _status("2024-01-01T00:00:00", {"torrent_path": "t/old.torrent", "info_hash": "a" * 40}),
            "seed": _status("2024-01-01T00:05:00", {"client": "qbittorrent", "hash": "a" * 40}),
            "upload": _status(
                "2024-01-01T00:10:00",
                {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/1111"},
            ),
        },
    )
    old_path = _write_episode(episodes_dir, old, filename="potpourri-2.json")

    new = _episode(
        guid="guid-new", title="Potpourri", published="Mon, 08 Jan 2024 00:00:00 +0000",
        status={
            "torrent": _status("2024-01-08T00:00:00", {"torrent_path": "t/new.torrent", "info_hash": "b" * 40}),
            # Poisoned: SeedStep/UploadStep found seeds|uploads/potpourri.json
            # (the OLD episode's legacy checkpoint) and inherited its result.
            "seed": _status("2024-01-08T00:05:00", {"client": "qbittorrent", "hash": "a" * 40}),
            "upload": _status(
                "2024-01-08T00:10:00",
                {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/1111"},
            ),
        },
    )
    new_path = _write_episode(episodes_dir, new, filename="potpourri.json")

    _write_legacy(podcast_dir / "seeds", "potpourri.json", {"client": "qbittorrent", "hash": "a" * 40})
    _write_legacy(
        podcast_dir / "uploads", "potpourri.json",
        {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/1111"},
    )

    report = migrate_checkpoints(podcast_dir)

    # Poisoned (new) episode: status cleared, no checkpoint written, ever.
    new_data = json.loads(new_path.read_text())
    assert "seed" not in new_data["status"]
    assert "upload" not in new_data["status"]
    assert find_checkpoint(podcast_dir / "seeds", new) is None
    assert find_checkpoint(podcast_dir / "uploads", new) is None

    # Healthy (old) sibling: status untouched, correct new-style checkpoint.
    old_data = json.loads(old_path.read_text())
    assert old_data["status"]["seed"] is not None
    assert old_data["status"]["upload"] is not None
    seed_checkpoint = find_checkpoint(podcast_dir / "seeds", old)
    assert seed_checkpoint == {
        "guid": "guid-old", "title": "Potpourri", "info_hash": "a" * 40,
        "client": "qbittorrent", "hash": "a" * 40,
    }
    upload_checkpoint = find_checkpoint(podcast_dir / "uploads", old)
    assert upload_checkpoint["url"] == "https://tracker.example/torrents/download_check/1111"

    # Legacy files swept, never deleted.
    assert not (podcast_dir / "seeds" / "potpourri.json").exists()
    assert not (podcast_dir / "uploads" / "potpourri.json").exists()
    assert (podcast_dir / "seeds" / ".legacy" / "potpourri.json").exists()
    assert (podcast_dir / "uploads" / ".legacy" / "potpourri.json").exists()

    assert len(report.poisoned) == 1
    assert "guid-new" in report.poisoned[0]
    assert len(report.migrated) == 1
    assert "guid-old" in report.migrated[0]
    assert len(report.legacy_moved) == 2


# ---------------------------------------------------------------------------
# Healthy migration
# ---------------------------------------------------------------------------

def test_healthy_episode_is_migrated(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    ep = _episode(
        guid="guid-1", title="Episode One", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={
            "torrent": _status("t0", {"torrent_path": "x.torrent", "info_hash": "c" * 40}),
            "seed": _status("t1", {"client": "qbittorrent", "hash": "c" * 40}),
            "upload": _status("t2", {"torrent_id": 5, "url": "https://tracker.example/torrents/download_check/2222"}),
        },
    )
    _write_episode(episodes_dir, ep)
    _write_legacy(podcast_dir / "seeds", "episode-one.json", {"client": "qbittorrent", "hash": "c" * 40})

    report = migrate_checkpoints(podcast_dir)

    assert report.poisoned == []
    assert report.skipped_conflict == []
    assert len(report.migrated) == 1
    assert find_checkpoint(podcast_dir / "seeds", ep)["hash"] == "c" * 40
    assert find_checkpoint(podcast_dir / "uploads", ep)["url"].endswith("2222")


# ---------------------------------------------------------------------------
# Signal (b) without a seed step
# ---------------------------------------------------------------------------

def test_signal_b_poisons_via_upload_url_reuse_without_seed_step(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    old = _episode(
        guid="guid-old", title="Show", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={"upload": _status(
            "2024-01-01T00:00:00", {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/9"},
        )},
    )
    new = _episode(
        guid="guid-new", title="Show", published="Mon, 08 Jan 2024 00:00:00 +0000",
        status={"upload": _status(
            "2024-01-08T00:00:00", {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/9"},
        )},
    )
    _write_episode(episodes_dir, old, filename="show-old.json")
    new_path = _write_episode(episodes_dir, new, filename="show-new.json")
    _write_legacy(
        podcast_dir / "uploads", "show.json",
        {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/9"},
    )

    report = migrate_checkpoints(podcast_dir)

    assert len(report.poisoned) == 1
    assert "guid-new" in report.poisoned[0]
    new_data = json.loads(new_path.read_text())
    assert "upload" not in new_data["status"]
    assert find_checkpoint(podcast_dir / "uploads", new) is None
    assert find_checkpoint(podcast_dir / "uploads", old) is not None


# ---------------------------------------------------------------------------
# Signal (a) alone: seed hash disagrees with this guid's own torrent
# info_hash, but no cross-guid reuse -> suspect, not poisoned.
# ---------------------------------------------------------------------------

def test_signal_a_alone_is_suspect_not_poisoned(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    ep = _episode(
        guid="guid-1", title="Episode One", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={
            "torrent": _status("t0", {"torrent_path": "x.torrent", "info_hash": "1" * 40}),
            "seed": _status("t1", {"client": "qbittorrent", "hash": "2" * 40}),
        },
    )
    episode_path = _write_episode(episodes_dir, ep)
    before = episode_path.read_text()
    _write_legacy(podcast_dir / "seeds", "unrelated.json", {"client": "qbittorrent", "hash": "9" * 40})

    report = migrate_checkpoints(podcast_dir)

    assert report.poisoned == []
    assert len(report.suspect) == 1
    assert "guid-1" in report.suspect[0]
    # status untouched, no checkpoint written
    assert episode_path.read_text() == before
    assert find_checkpoint(podcast_dir / "seeds", ep) is None
    # legacy sweep still happens
    assert (podcast_dir / "seeds" / ".legacy" / "unrelated.json").exists()


# ---------------------------------------------------------------------------
# Signal (b) chain: three guids share one upload url, sharing owner is the
# earliest completed_at; the two later ones are poisoned.
# ---------------------------------------------------------------------------

def test_signal_b_chain_of_three_poisons_all_but_earliest(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    url = "https://tracker.example/torrents/download_check/42"
    first = _episode(
        guid="guid-1", title="Show", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={"upload": _status("2024-01-01T00:00:00", {"torrent_id": None, "url": url})},
    )
    second = _episode(
        guid="guid-2", title="Show", published="Mon, 08 Jan 2024 00:00:00 +0000",
        status={"upload": _status("2024-01-08T00:00:00", {"torrent_id": None, "url": url})},
    )
    third = _episode(
        guid="guid-3", title="Show", published="Mon, 15 Jan 2024 00:00:00 +0000",
        status={"upload": _status("2024-01-15T00:00:00", {"torrent_id": None, "url": url})},
    )
    _write_episode(episodes_dir, first, filename="show-1.json")
    second_path = _write_episode(episodes_dir, second, filename="show-2.json")
    third_path = _write_episode(episodes_dir, third, filename="show-3.json")
    _write_legacy(podcast_dir / "uploads", "show.json", {"torrent_id": None, "url": url})

    report = migrate_checkpoints(podcast_dir)

    assert len(report.poisoned) == 2
    poisoned_guids = {label for label in report.poisoned}
    assert any("guid-2" in label for label in poisoned_guids)
    assert any("guid-3" in label for label in poisoned_guids)
    assert not any("guid-1" in label for label in poisoned_guids)

    assert "upload" not in json.loads(second_path.read_text())["status"]
    assert "upload" not in json.loads(third_path.read_text())["status"]
    assert find_checkpoint(podcast_dir / "uploads", first) is not None
    assert find_checkpoint(podcast_dir / "uploads", second) is None
    assert find_checkpoint(podcast_dir / "uploads", third) is None

    assert len(report.migrated) == 1
    assert "guid-1" in report.migrated[0]


# ---------------------------------------------------------------------------
# Duplicate JSONs, same guid, agreeing
# ---------------------------------------------------------------------------

def test_duplicate_json_same_guid_agreeing_merges_to_one_checkpoint(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    shared_status = {
        "torrent": _status("t0", {"torrent_path": "x.torrent", "info_hash": "d" * 40}),
        "upload": _status(
            "2024-01-01T00:00:00", {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/3"},
        ),
    }
    ep_a = _episode(guid="guid-dup", title="Dup A", published="Mon, 01 Jan 2024 00:00:00 +0000", status=dict(shared_status))
    ep_b = _episode(guid="guid-dup", title="Dup B", published="Mon, 01 Jan 2024 00:00:00 +0000", status=dict(shared_status))
    _write_episode(episodes_dir, ep_a, filename="dup-a.json")
    _write_episode(episodes_dir, ep_b, filename="dup-b.json")
    _write_legacy(
        podcast_dir / "uploads", "dup.json",
        {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/3"},
    )

    report = migrate_checkpoints(podcast_dir)

    assert report.skipped_conflict == []
    assert report.poisoned == []
    assert len(report.migrated) == 1
    upload_files = list((podcast_dir / "uploads").glob("*.json"))
    assert len(upload_files) == 1


# ---------------------------------------------------------------------------
# Conflicting URLs -> skipped, untouched
# ---------------------------------------------------------------------------

def test_conflicting_upload_urls_are_skipped_and_left_untouched(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    ep_a = _episode(
        guid="guid-conflict", title="Conflict A", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={"upload": _status(
            "2024-01-01T00:00:00", {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/AAA"},
        )},
    )
    ep_b = _episode(
        guid="guid-conflict", title="Conflict B", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={"upload": _status(
            "2024-01-02T00:00:00", {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/BBB"},
        )},
    )
    path_a = _write_episode(episodes_dir, ep_a, filename="conflict-a.json")
    path_b = _write_episode(episodes_dir, ep_b, filename="conflict-b.json")
    before_a, before_b = path_a.read_text(), path_b.read_text()
    _write_legacy(
        podcast_dir / "uploads", "conflict.json",
        {"torrent_id": None, "url": "https://tracker.example/torrents/download_check/AAA"},
    )

    report = migrate_checkpoints(podcast_dir)

    assert len(report.skipped_conflict) == 1
    assert report.migrated == []
    assert report.poisoned == []
    assert path_a.read_text() == before_a
    assert path_b.read_text() == before_b
    assert find_checkpoint(podcast_dir / "uploads", ep_a) is None
    # Step 5 (legacy sweep) is unconditional; it isn't gated on per-guid conflicts.
    assert (podcast_dir / "uploads" / ".legacy" / "conflict.json").exists()


# ---------------------------------------------------------------------------
# Rename orphan: status complete, no legacy file under the current slug
# ---------------------------------------------------------------------------

def test_rename_orphan_without_matching_legacy_file_still_gets_checkpoint(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    # Triggers the migration via an unrelated legacy file elsewhere.
    _write_legacy(podcast_dir / "seeds", "unrelated.json", {"client": "qbittorrent", "hash": "e" * 40})

    orphan = _episode(
        guid="guid-orphan", title="Renamed Title", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={
            "torrent": _status("t0", {"torrent_path": "x.torrent", "info_hash": "f" * 40}),
            "seed": _status("t1", {"client": "qbittorrent", "hash": "f" * 40}),
        },
    )
    _write_episode(episodes_dir, orphan, filename="renamed-title.json")

    report = migrate_checkpoints(podcast_dir)

    assert report.poisoned == []
    checkpoint = find_checkpoint(podcast_dir / "seeds", orphan)
    assert checkpoint is not None
    assert checkpoint["hash"] == "f" * 40


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_second_run_is_noop_with_empty_report(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    ep = _episode(
        guid="guid-1", title="Episode One", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={"seed": _status("t1", {"client": "qbittorrent", "hash": "1" * 40})},
    )
    _write_episode(episodes_dir, ep)
    _write_legacy(podcast_dir / "seeds", "episode-one.json", {"client": "qbittorrent", "hash": "1" * 40})

    first = migrate_checkpoints(podcast_dir)
    assert len(first.migrated) == 1

    second = migrate_checkpoints(podcast_dir)
    assert second.is_empty()
    assert second.migrated == [] and second.poisoned == [] and second.skipped_conflict == [] and second.legacy_moved == []


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_computes_report_without_writing(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    ep = _episode(
        guid="guid-1", title="Episode One", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={"seed": _status("t1", {"client": "qbittorrent", "hash": "1" * 40})},
    )
    episode_path = _write_episode(episodes_dir, ep)
    before = episode_path.read_text()
    legacy_path = _write_legacy(podcast_dir / "seeds", "episode-one.json", {"client": "qbittorrent", "hash": "1" * 40})

    report = migrate_checkpoints(podcast_dir, dry_run=True)

    assert len(report.migrated) == 1
    assert len(report.legacy_moved) == 1
    assert episode_path.read_text() == before
    assert legacy_path.exists()
    assert not (podcast_dir / "seeds" / ".legacy").exists()
    assert find_checkpoint(podcast_dir / "seeds", ep) is None


def test_dry_run_does_not_clear_poisoned_status(tmp_path):
    """Poisoning via signal (b): the same seed hash reused across two guids."""
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    old = _episode(
        guid="guid-old", title="Show", published="Mon, 01 Jan 2024 00:00:00 +0000",
        status={"seed": _status("2024-01-01T00:00:00", {"client": "qbittorrent", "hash": "2" * 40})},
    )
    new = _episode(
        guid="guid-new", title="Show", published="Mon, 08 Jan 2024 00:00:00 +0000",
        status={"seed": _status("2024-01-08T00:00:00", {"client": "qbittorrent", "hash": "2" * 40})},
    )
    _write_episode(episodes_dir, old, filename="show-old.json")
    new_path = _write_episode(episodes_dir, new, filename="show-new.json")
    before = new_path.read_text()
    _write_legacy(podcast_dir / "seeds", "show.json", {"client": "qbittorrent", "hash": "2" * 40})

    report = migrate_checkpoints(podcast_dir, dry_run=True)

    assert len(report.poisoned) == 1
    assert "guid-new" in report.poisoned[0]
    assert new_path.read_text() == before


# ---------------------------------------------------------------------------
# Legacy classified by payload, not filename shape
# ---------------------------------------------------------------------------

def test_legacy_filename_ending_in_hex_digits_still_classified_legacy(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    ep = _episode(guid="guid-1", title="Episode One", published="Mon, 01 Jan 2024 00:00:00 +0000", status={})
    _write_episode(episodes_dir, ep)
    # Filename shape mimics a new-style checkpoint (ends in what looks like a
    # hash suffix), but the payload has no guid key -> still legacy.
    _write_legacy(
        podcast_dir / "seeds", "2024-01-01-episode-one-deadbeef.json",
        {"client": "qbittorrent", "hash": "9" * 40},
    )

    report = migrate_checkpoints(podcast_dir)

    assert len(report.legacy_moved) == 1
    assert (podcast_dir / "seeds" / ".legacy" / "2024-01-01-episode-one-deadbeef.json").exists()


def test_no_legacy_files_returns_empty_report_immediately(tmp_path):
    podcast_dir = tmp_path / "podcast"
    episodes_dir = podcast_dir / "episodes"
    ep = _episode(guid="guid-1", title="Episode One", published="Mon, 01 Jan 2024 00:00:00 +0000", status={})
    _write_episode(episodes_dir, ep)

    report = migrate_checkpoints(podcast_dir)

    assert report.is_empty()


# ---------------------------------------------------------------------------
# Script CLI
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_checkpoints.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("migrate_checkpoints_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migrate_checkpoints_script = _load_script()


class TestScriptCLI:
    def test_dry_run_reports_without_writing(self, tmp_path):
        output_dir = tmp_path / "output"
        podcast_dir = output_dir / "my-podcast"
        episodes_dir = podcast_dir / "episodes"
        ep = _episode(
            guid="guid-1", title="Episode One", published="Mon, 01 Jan 2024 00:00:00 +0000",
            status={"seed": _status("t1", {"client": "qbittorrent", "hash": "1" * 40})},
        )
        _write_episode(episodes_dir, ep)
        _write_legacy(podcast_dir / "seeds", "episode-one.json", {"client": "qbittorrent", "hash": "1" * 40})

        assert migrate_checkpoints_script.main(["--output-dir", str(output_dir), "--dry-run"]) == 0
        assert (podcast_dir / "seeds" / "episode-one.json").exists()
        assert not (podcast_dir / "seeds" / ".legacy").exists()

    def test_missing_output_dir_returns_error(self, tmp_path):
        assert migrate_checkpoints_script.main(["--output-dir", str(tmp_path / "nope")]) == 1

    def test_podcast_filter_only_touches_named_podcast(self, tmp_path):
        output_dir = tmp_path / "output"
        podcast_a = output_dir / "podcast-a"
        podcast_b = output_dir / "podcast-b"
        ep_a = _episode(
            guid="guid-a", title="A", published="Mon, 01 Jan 2024 00:00:00 +0000",
            status={"seed": _status("t1", {"client": "qbittorrent", "hash": "a" * 40})},
        )
        ep_b = _episode(
            guid="guid-b", title="B", published="Mon, 01 Jan 2024 00:00:00 +0000",
            status={"seed": _status("t1", {"client": "qbittorrent", "hash": "b" * 40})},
        )
        _write_episode(podcast_a / "episodes", ep_a)
        _write_episode(podcast_b / "episodes", ep_b)
        _write_legacy(podcast_a / "seeds", "a.json", {"client": "qbittorrent", "hash": "a" * 40})
        _write_legacy(podcast_b / "seeds", "b.json", {"client": "qbittorrent", "hash": "b" * 40})

        assert migrate_checkpoints_script.main(["--output-dir", str(output_dir), "--podcast", "podcast-a"]) == 0
        assert (podcast_a / "seeds" / ".legacy" / "a.json").exists()
        assert (podcast_b / "seeds" / "b.json").exists()

    def test_dry_run_prints_poisoned_suspect_and_conflict_entries_by_name(self, tmp_path, caplog):
        """Dry-run previews must name the affected episodes, not just report counts."""
        output_dir = tmp_path / "output"
        podcast_dir = output_dir / "my-podcast"
        episodes_dir = podcast_dir / "episodes"

        old = _episode(
            guid="guid-old", title="Poisoned Old", published="Mon, 01 Jan 2024 00:00:00 +0000",
            status={"upload": _status("2024-01-01T00:00:00", {"torrent_id": None, "url": "https://tracker/1"})},
        )
        new = _episode(
            guid="guid-new", title="Poisoned New", published="Mon, 08 Jan 2024 00:00:00 +0000",
            status={"upload": _status("2024-01-08T00:00:00", {"torrent_id": None, "url": "https://tracker/1"})},
        )
        suspect = _episode(
            guid="guid-suspect", title="Suspect Show", published="Mon, 01 Jan 2024 00:00:00 +0000",
            status={
                "torrent": _status("t0", {"torrent_path": "x.torrent", "info_hash": "1" * 40}),
                "seed": _status("t1", {"client": "qbittorrent", "hash": "2" * 40}),
            },
        )
        _write_episode(episodes_dir, old, filename="old.json")
        _write_episode(episodes_dir, new, filename="new.json")
        _write_episode(episodes_dir, suspect, filename="suspect.json")
        _write_legacy(podcast_dir / "uploads", "poisoned.json", {"torrent_id": None, "url": "https://tracker/1"})
        _write_legacy(podcast_dir / "seeds", "suspect.json", {"client": "qbittorrent", "hash": "2" * 40})

        with caplog.at_level(logging.INFO, logger="migrate_checkpoints"):
            assert migrate_checkpoints_script.main(["--output-dir", str(output_dir), "--dry-run"]) == 0

        assert any("guid-new" in r.message for r in caplog.records)
        assert any("guid-suspect" in r.message for r in caplog.records)
