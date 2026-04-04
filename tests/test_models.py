"""Tests for models.py: slugify, Episode, and Podcast."""
import json
from pathlib import Path

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus, sanitize_filename, slugify
from podcast_etl.models import episode_json_filename


# --- slugify ---

def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"


def test_slugify_special_chars():
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_numbers():
    assert slugify("Episode 1: Foo") == "episode-1-foo"


def test_slugify_extra_spaces_and_dashes():
    assert slugify("  foo  --  bar  ") == "foo-bar"


# --- sanitize_filename ---

def test_sanitize_filename_colon_becomes_dash():
    assert sanitize_filename("Ep 1: Title") == "Ep 1 - Title"

def test_sanitize_filename_removes_quotes():
    assert sanitize_filename('Ep 3: "God Picked a Loser"') == "Ep 3 - God Picked a Loser"

def test_sanitize_filename_removes_question_mark():
    assert sanitize_filename("Ep 0: What Is the Meaning of This?") == "Ep 0 - What Is the Meaning of This"

def test_sanitize_filename_removes_windows_forbidden_chars():
    assert sanitize_filename('a\\b/c*d?e"f<g>h|i') == "abcdefghi"

def test_sanitize_filename_collapses_extra_spaces():
    assert sanitize_filename("Ep  1   Title") == "Ep 1 Title"

def test_sanitize_filename_preserves_case_and_punctuation():
    assert sanitize_filename("Ep 2: To Suffer or Take Arms") == "Ep 2 - To Suffer or Take Arms"

def test_sanitize_filename_strips_leading_trailing_whitespace():
    assert sanitize_filename("  Hello  ") == "Hello"


# --- StepStatus roundtrip ---

def test_step_status_roundtrip():
    s = StepStatus(completed_at="2024-01-01T00:00:00", result={"key": "val"})
    assert StepStatus.from_dict(s.to_dict()) == s


# --- Episode roundtrip ---

def _make_episode(**kwargs) -> Episode:
    defaults = dict(
        title="Test Episode",
        guid="guid-123",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration="1:00:00",
        description="A test episode.",
        slug="test-episode",
        status={},
    )
    defaults.update(kwargs)
    return Episode(**defaults)


def test_episode_dict_roundtrip():
    ep = _make_episode()
    assert Episode.from_dict(ep.to_dict()) == ep


def test_episode_dict_roundtrip_with_status():
    status = {"download": StepStatus(completed_at="2024-01-01T00:00:00", result={"size_bytes": 42})}
    ep = _make_episode(status=status)
    assert Episode.from_dict(ep.to_dict()) == ep


def test_episode_save_and_load(tmp_path: Path):
    ep = _make_episode()
    ep.save(tmp_path, "My Podcast")
    loaded = Episode.load(tmp_path / "episodes" / "My Podcast - 2024-01-01 - Test Episode.json")
    assert loaded == ep


def test_episode_save_creates_directory(tmp_path: Path):
    ep = _make_episode()
    ep.save(tmp_path, "My Podcast")
    assert (tmp_path / "episodes" / "My Podcast - 2024-01-01 - Test Episode.json").exists()


# --- Podcast roundtrip ---

def _make_podcast(**kwargs) -> Podcast:
    defaults = dict(
        title="My Podcast",
        url="https://example.com/feed.xml",
        description="A podcast.",
        image_url="https://example.com/img.png",
        slug="my-podcast",
    )
    defaults.update(kwargs)
    return Podcast(**defaults)


def test_podcast_dict_roundtrip():
    p = _make_podcast()
    assert Podcast.from_dict(p.to_dict()) == p


def test_podcast_save_and_load(tmp_path: Path):
    ep = _make_episode()
    p = _make_podcast()
    p.episodes = [ep]
    p.save(tmp_path)

    loaded = Podcast.load(tmp_path / "my-podcast")
    assert loaded.title == p.title
    assert loaded.slug == p.slug
    assert len(loaded.episodes) == 1
    assert loaded.episodes[0].slug == ep.slug


def test_podcast_load_no_episodes_dir(tmp_path: Path):
    # Save a podcast with no episodes — episodes dir is never created
    p = _make_podcast()
    p.save(tmp_path)
    assert not (tmp_path / "my-podcast" / "episodes").exists()

    loaded = Podcast.load(tmp_path / "my-podcast")
    assert loaded.title == p.title
    assert loaded.episodes == []


# --- Edge cases ---

def test_slugify_empty_string():
    assert slugify("") == ""


def test_step_status_from_dict_missing_result_defaults_to_empty():
    s = StepStatus.from_dict({"completed_at": "2024-01-01T00:00:00"})
    assert s.result == {}


def test_episode_from_dict_with_none_status_value():
    ep = _make_episode()
    d = ep.to_dict()
    d["status"]["download"] = None
    loaded = Episode.from_dict(d)
    assert loaded.status["download"] is None


def test_episode_dict_roundtrip_with_image_url():
    ep = _make_episode(image_url="https://example.com/ep1.jpg")
    assert Episode.from_dict(ep.to_dict()) == ep
    assert ep.to_dict()["image_url"] == "https://example.com/ep1.jpg"


def test_episode_dict_roundtrip_without_image_url():
    ep = _make_episode()
    assert ep.image_url is None
    roundtripped = Episode.from_dict(ep.to_dict())
    assert roundtripped.image_url is None


def test_episode_dict_roundtrip_with_episode_number():
    ep = _make_episode(episode_number=42)
    assert ep.to_dict()["episode_number"] == 42
    assert Episode.from_dict(ep.to_dict()) == ep


def test_episode_dict_roundtrip_without_episode_number():
    ep = _make_episode()
    assert ep.episode_number is None
    roundtripped = Episode.from_dict(ep.to_dict())
    assert roundtripped.episode_number is None


# --- episode_json_filename ---

def test_episode_json_filename_basic():
    result = episode_json_filename("guid-123", "My Episode Title", "Mon, 01 Jan 2024 00:00:00 +0000")
    assert result.startswith("2024-01-01-my-episode-title-")
    assert len(result.split("-")[-1]) == 8  # 8-char hex hash


def test_episode_json_filename_no_published():
    result = episode_json_filename("guid-123", "Title", None)
    assert result.startswith("unknown-date-title-")


def test_episode_json_filename_raw_title_none_uses_empty():
    result = episode_json_filename("guid-123", None, None)
    assert result.startswith("unknown-date-")
    assert len(result) == len("unknown-date-") + 8  # 8-char hash


def test_episode_json_filename_truncates_long_slug():
    long_title = "A " + "very " * 30 + "long title"
    result = episode_json_filename("guid-123", long_title, "Mon, 01 Jan 2024 00:00:00 +0000")
    without_date = result[len("2024-01-01-"):]
    slug = without_date[:-(8 + 1)]  # remove -hash
    assert len(slug) <= 60


def test_episode_json_filename_truncates_at_word_boundary():
    title = " ".join(["abcdefgh"] * 10)  # slug: "abcdefgh-abcdefgh-..." = 89 chars
    result = episode_json_filename("guid-123", title, "Mon, 01 Jan 2024 00:00:00 +0000")
    without_date = result[len("2024-01-01-"):]
    slug = without_date[:-(8 + 1)]  # remove -hash
    assert len(slug) <= 60
    assert not slug.endswith("-")


def test_episode_json_filename_same_guid_same_hash():
    a = episode_json_filename("guid-123", "Title A", None)
    b = episode_json_filename("guid-123", "Title B", None)
    assert a.split("-")[-1] == b.split("-")[-1]


def test_episode_json_filename_different_guid_different_hash():
    a = episode_json_filename("guid-1", "Same Title", None)
    b = episode_json_filename("guid-2", "Same Title", None)
    assert a.split("-")[-1] != b.split("-")[-1]
