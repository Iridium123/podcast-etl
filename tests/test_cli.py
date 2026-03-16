"""Tests for cli.py helper functions: load_config, save_config, get_output_dir,
find_feed_config, get_pipeline_steps, and filter_episodes."""
from datetime import date
from pathlib import Path

import click
import pytest
import yaml

from podcast_etl.cli import (
    filter_episodes,
    find_feed_config,
    get_output_dir,
    get_pipeline_steps,
    load_config,
    parse_date_range,
    save_config,
)
from podcast_etl.models import Episode


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_missing_file_returns_defaults(tmp_path: Path):
    missing = tmp_path / "nonexistent.yaml"
    config = load_config(missing)
    assert "feeds" in config
    assert config["feeds"] == []
    assert "settings" in config


def test_load_config_valid_yaml(tmp_path: Path):
    cfg_file = tmp_path / "feeds.yaml"
    cfg_file.write_text(yaml.dump({"feeds": [{"url": "https://example.com/rss"}], "settings": {"poll_interval": 600}}))
    config = load_config(cfg_file)
    assert config["feeds"][0]["url"] == "https://example.com/rss"
    assert config["settings"]["poll_interval"] == 600


def test_load_config_empty_yaml_returns_empty_dict(tmp_path: Path):
    cfg_file = tmp_path / "feeds.yaml"
    cfg_file.write_text("")
    # An empty YAML file yields None from safe_load; load_config returns {} in that case
    config = load_config(cfg_file)
    assert config == {}


def test_load_config_invalid_yaml_exits(tmp_path: Path):
    cfg_file = tmp_path / "feeds.yaml"
    cfg_file.write_text("feeds:\n  - url: [invalid\n")
    with pytest.raises(SystemExit):
        load_config(cfg_file)


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------

def test_save_config_writes_valid_yaml(tmp_path: Path):
    cfg_file = tmp_path / "feeds.yaml"
    data = {"feeds": [{"url": "https://example.com/rss"}], "settings": {"poll_interval": 3600}}
    save_config(data, cfg_file)
    loaded = yaml.safe_load(cfg_file.read_text())
    assert loaded["feeds"][0]["url"] == "https://example.com/rss"
    assert loaded["settings"]["poll_interval"] == 3600


def test_save_config_roundtrip(tmp_path: Path):
    cfg_file = tmp_path / "feeds.yaml"
    original = {"feeds": [{"url": "https://a.com/rss", "name": "a"}], "settings": {"pipeline": ["download", "tag"]}}
    save_config(original, cfg_file)
    assert load_config(cfg_file) == original


# ---------------------------------------------------------------------------
# get_output_dir
# ---------------------------------------------------------------------------

def test_get_output_dir_default_on_empty_config():
    assert get_output_dir({}) == Path("./output")


def test_get_output_dir_from_settings():
    config = {"settings": {"output_dir": "/tmp/podcast-data"}}
    assert get_output_dir(config) == Path("/tmp/podcast-data")


def test_get_output_dir_missing_settings_key():
    config = {"settings": {}}
    assert get_output_dir(config) == Path("./output")


# ---------------------------------------------------------------------------
# find_feed_config
# ---------------------------------------------------------------------------

def _config_with_feeds(*feeds):
    return {"feeds": list(feeds)}


def test_find_feed_config_by_name():
    config = _config_with_feeds(
        {"url": "https://example.com/rss", "name": "my-show"},
    )
    result = find_feed_config(config, "my-show")
    assert result is not None
    assert result["url"] == "https://example.com/rss"


def test_find_feed_config_by_url():
    config = _config_with_feeds({"url": "https://example.com/rss"})
    result = find_feed_config(config, "https://example.com/rss")
    assert result is not None
    assert result["url"] == "https://example.com/rss"


def test_find_feed_config_not_found_returns_none():
    config = _config_with_feeds({"url": "https://example.com/rss", "name": "show"})
    assert find_feed_config(config, "unknown") is None


def test_find_feed_config_name_takes_priority_over_url():
    """If one entry has a name matching the query and another entry's URL also matches,
    the name match should be returned first."""
    feeds = [
        {"url": "https://other.com/rss", "name": "my-show"},
        {"url": "my-show"},  # URL happens to equal the query string
    ]
    config = {"feeds": feeds}
    result = find_feed_config(config, "my-show")
    # Should match by name (first pass) and return the first entry
    assert result["url"] == "https://other.com/rss"


def test_find_feed_config_empty_feeds():
    assert find_feed_config({"feeds": []}, "anything") is None


def test_find_feed_config_no_feeds_key():
    assert find_feed_config({}, "anything") is None


# ---------------------------------------------------------------------------
# get_pipeline_steps
# ---------------------------------------------------------------------------

def test_get_pipeline_steps_uses_feed_specific_pipeline():
    config = {"settings": {"pipeline": ["download"]}}
    feed_config = {"url": "...", "pipeline": ["download", "tag"]}
    assert get_pipeline_steps(config, feed_config) == ["download", "tag"]


def test_get_pipeline_steps_falls_back_to_settings():
    config = {"settings": {"pipeline": ["download", "tag"]}}
    feed_config = {"url": "..."}  # no pipeline key
    assert get_pipeline_steps(config, feed_config) == ["download", "tag"]


def test_get_pipeline_steps_falls_back_to_default_when_no_settings():
    assert get_pipeline_steps({}) == ["download"]


def test_get_pipeline_steps_no_feed_config_uses_settings():
    config = {"settings": {"pipeline": ["tag"]}}
    assert get_pipeline_steps(config, None) == ["tag"]


def test_get_pipeline_steps_empty_feed_pipeline_falls_back_to_settings():
    """An explicitly empty list in feed config should still fall back to settings."""
    config = {"settings": {"pipeline": ["download"]}}
    feed_config = {"url": "...", "pipeline": []}  # empty list is falsy
    assert get_pipeline_steps(config, feed_config) == ["download"]


# ---------------------------------------------------------------------------
# filter_episodes
# ---------------------------------------------------------------------------

def _ep(title: str, published: str | None = None) -> Episode:
    return Episode(
        title=title,
        guid=title,
        published=published,
        audio_url=None,
        duration=None,
        description=None,
        slug=title.lower(),
    )


_EPISODES = [
    _ep("Ep 1", "Fri, 01 Mar 2026 00:00:00 +0000"),
    _ep("Ep 2", "Sun, 02 Mar 2026 12:00:00 +0000"),
    _ep("Ep 3a", "Tue, 03 Mar 2026 08:00:00 +0000"),
    _ep("Ep 3b", "Tue, 03 Mar 2026 20:00:00 +0000"),
    _ep("Ep 4", "Wed, 04 Mar 2026 00:00:00 +0000"),
]


def test_filter_episodes_no_filters_returns_all():
    assert filter_episodes(_EPISODES) == _EPISODES


def test_filter_episodes_last_n():
    result = filter_episodes(_EPISODES, last=2)
    assert [e.title for e in result] == ["Ep 1", "Ep 2"]


def test_filter_episodes_last_zero():
    result = filter_episodes(_EPISODES, last=0)
    assert result == []


def test_filter_episodes_single_date():
    result = filter_episodes(_EPISODES, date_range=(date(2026, 3, 3), date(2026, 3, 3)))
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b"]


def test_filter_episodes_closed_range():
    result = filter_episodes(_EPISODES, date_range=(date(2026, 3, 2), date(2026, 3, 3)))
    assert [e.title for e in result] == ["Ep 2", "Ep 3a", "Ep 3b"]


def test_filter_episodes_open_end():
    result = filter_episodes(_EPISODES, date_range=(date(2026, 3, 3), None))
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b", "Ep 4"]


def test_filter_episodes_open_start():
    result = filter_episodes(_EPISODES, date_range=(None, date(2026, 3, 2)))
    assert [e.title for e in result] == ["Ep 1", "Ep 2"]


def test_filter_episodes_date_range_no_matches():
    result = filter_episodes(_EPISODES, date_range=(date(2026, 4, 1), date(2026, 4, 5)))
    assert result == []


def test_filter_episodes_date_range_skips_no_published():
    episodes = [_ep("No date"), _ep("Has date", "Mon, 03 Mar 2026 00:00:00 +0000")]
    result = filter_episodes(episodes, date_range=(date(2026, 3, 1), None))
    assert [e.title for e in result] == ["Has date"]


# ---------------------------------------------------------------------------
# parse_date_range
# ---------------------------------------------------------------------------

def test_parse_date_range_single_date():
    assert parse_date_range("2026-03-01") == (date(2026, 3, 1), date(2026, 3, 1))


def test_parse_date_range_closed():
    assert parse_date_range("2026-03-01..2026-03-05") == (date(2026, 3, 1), date(2026, 3, 5))


def test_parse_date_range_open_end():
    assert parse_date_range("2026-03-01..") == (date(2026, 3, 1), None)


def test_parse_date_range_open_start():
    assert parse_date_range("..2026-03-05") == (None, date(2026, 3, 5))


def test_parse_date_range_start_after_end_raises():
    with pytest.raises(click.BadParameter):
        parse_date_range("2026-03-05..2026-03-01")


def test_parse_date_range_both_empty_raises():
    with pytest.raises(click.BadParameter):
        parse_date_range("..")


def test_parse_date_range_invalid_format_raises():
    with pytest.raises(ValueError):
        parse_date_range("not-a-date")
