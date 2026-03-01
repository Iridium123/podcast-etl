"""Tests for cli.py helper functions: load_config, save_config, get_output_dir,
find_feed_config, and get_pipeline_steps."""
from pathlib import Path

import pytest
import yaml

from podcast_etl.cli import (
    find_feed_config,
    get_output_dir,
    get_pipeline_steps,
    load_config,
    save_config,
)


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
