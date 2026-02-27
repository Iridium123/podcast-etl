"""Tests for models.py: slugify, Episode, and Podcast."""
import json
from pathlib import Path

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus, slugify


# --- slugify ---

def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"


def test_slugify_special_chars():
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_numbers():
    assert slugify("Episode 1: Foo") == "episode-1-foo"


def test_slugify_extra_spaces_and_dashes():
    assert slugify("  foo  --  bar  ") == "foo-bar"


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
    ep.save(tmp_path)
    loaded = Episode.load(tmp_path / "episodes" / "test-episode.json")
    assert loaded == ep


def test_episode_save_creates_directory(tmp_path: Path):
    ep = _make_episode()
    ep.save(tmp_path)
    assert (tmp_path / "episodes" / "test-episode.json").exists()


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
