"""Tests for checkpoints.py: GUID-keyed seed/upload checkpoint helpers."""
import json
from pathlib import Path

import pytest

from podcast_etl.checkpoints import (
    checkpoint_filename,
    find_checkpoint,
    resolve_duplicate_statuses,
    write_checkpoint,
)
from podcast_etl.models import Episode, StepStatus, episode_json_filename, episode_guid_hash


def _make_episode(**kwargs) -> Episode:
    defaults = dict(
        title="Episode One",
        guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration="1:00:00",
        description="desc",
        slug="episode-one",
        status={},
    )
    defaults.update(kwargs)
    return Episode(**defaults)


# --- checkpoint_filename ---

def test_checkpoint_filename_matches_episode_json_stem():
    ep = _make_episode(raw_title="Raw Episode One")
    expected = episode_json_filename(ep.guid, ep.raw_title, ep.published) + ".json"
    assert checkpoint_filename(ep) == expected


def test_checkpoint_filename_falls_back_to_title_without_raw_title():
    ep = _make_episode(raw_title=None)
    expected = episode_json_filename(ep.guid, ep.title, ep.published) + ".json"
    assert checkpoint_filename(ep) == expected


# --- write_checkpoint / find_checkpoint round trip ---

def test_write_then_find_checkpoint(tmp_path: Path):
    ep = _make_episode()
    directory = tmp_path / "seeds"
    payload = write_checkpoint(directory, ep, data={"client": "qbittorrent", "hash": "abc123"}, info_hash="abc123")

    assert payload == {
        "guid": "guid-1",
        "title": "Episode One",
        "info_hash": "abc123",
        "client": "qbittorrent",
        "hash": "abc123",
    }
    found = find_checkpoint(directory, ep)
    assert found == payload


def test_write_checkpoint_creates_directory(tmp_path: Path):
    ep = _make_episode()
    directory = tmp_path / "uploads"
    assert not directory.exists()
    write_checkpoint(directory, ep, data={}, info_hash=None)
    assert directory.exists()


def test_write_checkpoint_uses_checkpoint_filename(tmp_path: Path):
    ep = _make_episode()
    directory = tmp_path / "seeds"
    write_checkpoint(directory, ep, data={}, info_hash=None)
    assert (directory / checkpoint_filename(ep)).exists()


def test_write_checkpoint_leaves_no_temp_files(tmp_path: Path):
    ep = _make_episode()
    directory = tmp_path / "seeds"
    write_checkpoint(directory, ep, data={}, info_hash=None)
    assert list(directory.glob("*.tmp")) == []


def test_find_checkpoint_no_match_returns_none(tmp_path: Path):
    ep = _make_episode()
    directory = tmp_path / "seeds"
    directory.mkdir()
    assert find_checkpoint(directory, ep) is None


def test_find_checkpoint_missing_directory_returns_none(tmp_path: Path):
    ep = _make_episode()
    directory = tmp_path / "does-not-exist"
    assert find_checkpoint(directory, ep) is None


# --- rename cleanup ---

def test_write_checkpoint_removes_stale_same_guid_file(tmp_path: Path):
    """A rename changes the slug/date in the stem; the old filename for the same GUID is cleaned up."""
    directory = tmp_path / "seeds"
    directory.mkdir()
    old_ep = _make_episode(raw_title="Old Title")
    write_checkpoint(directory, old_ep, data={"hash": "aaa"}, info_hash="aaa")
    old_path = directory / checkpoint_filename(old_ep)
    assert old_path.exists()

    renamed_ep = _make_episode(raw_title="New Title After Rename")
    write_checkpoint(directory, renamed_ep, data={"hash": "aaa"}, info_hash="aaa")
    new_path = directory / checkpoint_filename(renamed_ep)

    assert new_path.exists()
    assert not old_path.exists()
    assert list(directory.glob("*.json")) == [new_path]


def test_write_checkpoint_does_not_remove_other_guids(tmp_path: Path):
    directory = tmp_path / "seeds"
    ep_a = _make_episode(guid="guid-a", raw_title="Episode A")
    ep_b = _make_episode(guid="guid-b", raw_title="Episode B")
    write_checkpoint(directory, ep_a, data={}, info_hash=None)
    write_checkpoint(directory, ep_b, data={}, info_hash=None)
    assert len(list(directory.glob("*.json"))) == 2


# --- guid-mismatch rejection ---

def test_find_checkpoint_rejects_payload_with_different_guid(tmp_path: Path):
    """A filename that hash-matches but whose payload belongs to another GUID must not be honoured."""
    ep = _make_episode(guid="guid-real")
    directory = tmp_path / "seeds"
    directory.mkdir()
    # Write a file at the exact name a checkpoint for `ep` would use, but with another guid's payload
    # (simulates a stale/legacy file left over from a slug collision).
    bogus_path = directory / checkpoint_filename(ep)
    bogus_path.write_text(json.dumps({"guid": "guid-other", "hash": "zzz"}))

    assert find_checkpoint(directory, ep) is None


# --- invalid JSON ---

def test_find_checkpoint_skips_invalid_json(tmp_path: Path):
    ep = _make_episode()
    directory = tmp_path / "seeds"
    directory.mkdir()
    bad_path = directory / checkpoint_filename(ep)
    bad_path.write_text("{not valid json")

    assert find_checkpoint(directory, ep) is None


def test_find_checkpoint_skips_invalid_json_then_finds_valid_match(tmp_path: Path):
    ep = _make_episode()
    directory = tmp_path / "seeds"
    directory.mkdir()
    # An invalid file that would sort before the valid one
    (directory / f"2020-01-01-aaaa-{episode_guid_hash(ep.guid)}.json").write_text("not json")
    write_checkpoint(directory, ep, data={"hash": "good"}, info_hash="good")

    found = find_checkpoint(directory, ep)
    assert found is not None
    assert found["hash"] == "good"


# --- resolve_duplicate_statuses ---

def test_resolve_duplicate_statuses_latest_completed_at_wins():
    ep1 = _make_episode(status={
        "download": StepStatus(completed_at="2024-01-01T00:00:00", result={"size_bytes": 1}),
    })
    ep2 = _make_episode(status={
        "download": StepStatus(completed_at="2024-06-01T00:00:00", result={"size_bytes": 2}),
    })
    merged = resolve_duplicate_statuses([ep1, ep2])
    assert merged["download"].result["size_bytes"] == 2


def test_resolve_duplicate_statuses_merges_disjoint_steps():
    ep1 = _make_episode(status={
        "download": StepStatus(completed_at="2024-01-01T00:00:00", result={}),
    })
    ep2 = _make_episode(status={
        "tag": StepStatus(completed_at="2024-01-02T00:00:00", result={}),
    })
    merged = resolve_duplicate_statuses([ep1, ep2])
    assert set(merged) == {"download", "tag"}


def test_resolve_duplicate_statuses_ignores_none_status():
    ep1 = _make_episode(status={"download": None})
    ep2 = _make_episode(status={
        "download": StepStatus(completed_at="2024-01-01T00:00:00", result={}),
    })
    merged = resolve_duplicate_statuses([ep1, ep2])
    assert merged["download"].completed_at == "2024-01-01T00:00:00"


def test_resolve_duplicate_statuses_deterministic_regardless_of_order():
    ep1 = _make_episode(status={
        "download": StepStatus(completed_at="2024-01-01T00:00:00", result={"size_bytes": 1}),
    })
    ep2 = _make_episode(status={
        "download": StepStatus(completed_at="2024-06-01T00:00:00", result={"size_bytes": 2}),
    })
    forward = resolve_duplicate_statuses([ep1, ep2])
    backward = resolve_duplicate_statuses([ep2, ep1])
    assert forward["download"].to_dict() == backward["download"].to_dict()


def test_resolve_duplicate_statuses_empty_list():
    assert resolve_duplicate_statuses([]) == {}
