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
    validate_config,
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
    assert "defaults" in config


def test_load_config_valid_yaml(tmp_path: Path):
    cfg_file = tmp_path / "feeds.yaml"
    cfg_file.write_text(yaml.dump({"feeds": [{"url": "https://example.com/rss"}], "poll_interval": 600}))
    config = load_config(cfg_file)
    assert config["feeds"][0]["url"] == "https://example.com/rss"
    assert config["poll_interval"] == 600


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
    data = {"feeds": [{"url": "https://example.com/rss"}], "defaults": {"output_dir": "./output"}, "poll_interval": 3600}
    save_config(data, cfg_file)
    loaded = yaml.safe_load(cfg_file.read_text())
    assert loaded["feeds"][0]["url"] == "https://example.com/rss"
    assert loaded["poll_interval"] == 3600


def test_save_config_roundtrip(tmp_path: Path):
    cfg_file = tmp_path / "feeds.yaml"
    original = {"feeds": [{"url": "https://a.com/rss", "name": "a"}], "defaults": {"pipeline": ["download", "tag"]}}
    save_config(original, cfg_file)
    assert load_config(cfg_file) == original


# ---------------------------------------------------------------------------
# get_output_dir
# ---------------------------------------------------------------------------

def test_get_output_dir_default_on_empty_config():
    assert get_output_dir({}) == Path("./output")


def test_get_output_dir_from_settings():
    config = {"defaults": {"output_dir": "/tmp/podcast-data"}}
    assert get_output_dir(config) == Path("/tmp/podcast-data")


def test_get_output_dir_missing_settings_key():
    config = {"defaults": {}}
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
    resolved = {"pipeline": ["download", "tag"]}
    assert get_pipeline_steps(resolved) == ["download", "tag"]


def test_get_pipeline_steps_falls_back_to_default():
    resolved = {}
    assert get_pipeline_steps(resolved) == ["download"]


def test_get_pipeline_steps_empty_pipeline_falls_back():
    resolved = {"pipeline": []}
    assert get_pipeline_steps(resolved) == ["download"]


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


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------

def test_validate_config_passes_valid_config():
    config = {
        "feeds": [{"url": "https://example.com/rss", "pipeline": ["download"], "ad_detection": {"llm": {"model": "haiku"}}}],
        "defaults": {"ad_detection": {"llm": {"provider": "anthropic", "model": "sonnet"}}},
    }
    validate_config(config)  # should not raise


def test_validate_config_empty():
    validate_config({})  # should not raise
    validate_config({"feeds": [], "defaults": {}})  # should not raise


def test_validate_config_feed_missing_url():
    config = {"feeds": [{"name": "no-url"}]}
    with pytest.raises(SystemExit, match="missing 'url'"):
        validate_config(config)


def test_validate_config_unknown_pipeline_step_in_feed():
    config = {"feeds": [{"url": "https://example.com/rss", "pipeline": ["download", "nonexistent"]}]}
    with pytest.raises(SystemExit, match="unknown pipeline step 'nonexistent'"):
        validate_config(config)


def test_validate_config_unknown_pipeline_step_in_defaults():
    config = {"feeds": [], "defaults": {"pipeline": ["bogus"]}}
    with pytest.raises(SystemExit, match="defaults.pipeline.*'bogus'"):
        validate_config(config)


def test_validate_config_catches_type_mismatch():
    config = {
        "feeds": [{"url": "https://example.com/rss", "ad_detection": {"llm": "haiku"}}],
        "defaults": {"ad_detection": {"llm": {"provider": "anthropic"}}},
    }
    with pytest.raises(SystemExit, match="Type mismatch"):
        validate_config(config)


def test_validate_config_collects_multiple_errors():
    config = {
        "feeds": [
            {"name": "no-url"},
            {"url": "https://example.com/rss", "pipeline": ["nonexistent"]},
        ],
    }
    with pytest.raises(SystemExit, match="missing 'url'") as exc_info:
        validate_config(config)
    assert "nonexistent" in str(exc_info.value)


def test_validate_config_passes_valid_title_cleaning():
    config = {
        "feeds": [{"url": "https://example.com/rss", "title_cleaning": {"strip_date": True}}],
        "defaults": {"title_cleaning": {"reorder_parts": True}},
    }
    validate_config(config)  # should not raise


def test_validate_config_pipeline_must_be_list():
    config = {"feeds": [{"url": "https://example.com/rss", "pipeline": "download"}]}
    with pytest.raises(SystemExit, match="must be a list"):
        validate_config(config)


def test_validate_config_global_pipeline_must_be_list():
    config = {"feeds": [], "defaults": {"pipeline": "download"}}
    with pytest.raises(SystemExit, match="must be a list"):
        validate_config(config)


def test_validate_config_poll_interval_must_be_positive_int():
    config = {"feeds": [], "defaults": {"poll_interval": -10}}
    with pytest.raises(SystemExit, match="positive integer"):
        validate_config(config)


def test_validate_config_poll_interval_rejects_string():
    config = {"feeds": [], "defaults": {"poll_interval": "hourly"}}
    with pytest.raises(SystemExit, match="positive integer"):
        validate_config(config)


def test_validate_config_upload_requires_category_and_type_id():
    config = {"feeds": [{"url": "https://example.com/rss", "pipeline": ["upload"]}]}
    with pytest.raises(SystemExit, match="category_id") as exc_info:
        validate_config(config)
    assert "type_id" in str(exc_info.value)


def test_validate_config_stage_requires_torrent_data_dir():
    config = {"feeds": [{"url": "https://example.com/rss", "pipeline": ["stage"]}], "defaults": {}}
    with pytest.raises(SystemExit, match="torrent_data_dir"):
        validate_config(config)


def test_validate_config_ad_detection_whisper_must_be_dict():
    config = {"feeds": [], "defaults": {"ad_detection": {"whisper": "base"}}}
    with pytest.raises(SystemExit, match="ad_detection.whisper.*must be a mapping"):
        validate_config(config)


def test_validate_config_tracker_not_validated_when_unreferenced():
    """Tracker config is not validated when no pipeline includes upload/torrent."""
    config = {
        "feeds": [{"url": "https://example.com/rss", "pipeline": ["download"]}],
        "defaults": {"tracker": {"url": "https://t.example.com"}},  # incomplete — but unused
    }
    validate_config(config)  # should not raise


def test_validate_config_tracker_validated_when_upload_in_pipeline():
    """Tracker config is validated when a pipeline includes upload."""
    config = {
        "feeds": [{"url": "https://example.com/rss", "pipeline": ["upload"], "category_id": 1, "type_id": 1}],
        "defaults": {"tracker": {"url": "https://t.example.com"}},  # missing announce_url and auth
    }
    with pytest.raises(SystemExit, match="announce_url"):
        validate_config(config)


def test_validate_config_tracker_auth_validated():
    """Tracker config must have remember_cookie or username+password."""
    config = {
        "feeds": [{"url": "https://example.com/rss", "pipeline": ["upload"], "category_id": 1, "type_id": 1}],
        "defaults": {"tracker": {"url": "https://t.example.com", "announce_url": "https://t.example.com/announce"}},
    }
    with pytest.raises(SystemExit, match="remember_cookie.*username"):
        validate_config(config)


def test_validate_config_client_not_validated_when_unreferenced():
    """Client config is not validated when no pipeline includes seed."""
    config = {
        "feeds": [{"url": "https://example.com/rss", "pipeline": ["download"]}],
        "defaults": {"client": {"url": "http://qbt:8080"}},  # incomplete — but unused
    }
    validate_config(config)  # should not raise


def test_validate_config_client_validated_when_seed_in_pipeline():
    """Client config is validated when a pipeline includes seed."""
    config = {
        "feeds": [{"url": "https://example.com/rss", "pipeline": ["seed"]}],
        "defaults": {"client": {"url": "http://qbt:8080"}, "torrent_data_dir": "/data"},
    }
    with pytest.raises(SystemExit, match="missing required key 'username'"):
        validate_config(config)


def test_validate_config_global_pipeline_upload_checks_each_feed():
    """Global pipeline with 'upload' should validate per-feed config, not fail with empty feed."""
    config = {
        "feeds": [
            {"url": "https://a.com/rss", "category_id": 14, "type_id": 9},
            {"url": "https://b.com/rss", "category_id": 5, "type_id": 3},
        ],
        "defaults": {"pipeline": ["upload"]},
    }
    validate_config(config)  # should not raise — both feeds have required keys


def test_validate_config_global_pipeline_upload_reports_feed_missing_keys():
    """Global pipeline with 'upload' should report the specific feed missing category_id."""
    config = {
        "feeds": [
            {"url": "https://a.com/rss", "category_id": 14, "type_id": 9},
            {"url": "https://b.com/rss"},  # missing category_id and type_id
        ],
        "defaults": {"pipeline": ["upload"]},
    }
    with pytest.raises(SystemExit, match="b.com") as exc_info:
        validate_config(config)
    # First feed should NOT appear in errors
    assert "a.com" not in str(exc_info.value)
