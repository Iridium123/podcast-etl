"""Tests for cli.py helper functions: load_config, save_config, get_output_dir,
find_feed_config, get_pipeline_steps, filter_episodes, reset_feed_data,
and delete_feed."""
import json
from datetime import date
from pathlib import Path

import click
import pytest
import yaml

from podcast_etl.cli import (
    delete_feed,
    filter_episodes,
    find_feed_config,
    get_output_dir,
    get_pipeline_steps,
    load_config,
    parse_date_range,
    reset_feed_data,
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


def test_save_config_is_atomic(tmp_path: Path):
    """save_config must not leave a .tmp file behind after a successful write."""
    cfg_file = tmp_path / "feeds.yaml"
    data = {"feeds": [], "poll_interval": 3600}
    save_config(data, cfg_file)
    assert cfg_file.exists()
    assert not (tmp_path / "feeds.tmp").exists()


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


def test_filter_episodes_regex():
    result = filter_episodes(_EPISODES, episode_filter=r"Ep 3")
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b"]


def test_filter_episodes_regex_no_match():
    result = filter_episodes(_EPISODES, episode_filter=r"^Nonexistent$")
    assert result == []


def test_filter_episodes_regex_combined_with_last():
    result = filter_episodes(_EPISODES, last=3, episode_filter=r"Ep [12]")
    assert [e.title for e in result] == ["Ep 1", "Ep 2"]


def test_filter_episodes_regex_combined_with_date_range():
    result = filter_episodes(_EPISODES, date_range=(date(2026, 3, 3), None), episode_filter=r"3a")
    assert [e.title for e in result] == ["Ep 3a"]


def test_filter_episodes_regex_skips_none_title():
    episodes = [_ep("Match me"), Episode(title=None, guid="g", published=None, audio_url=None, duration=None, description=None, slug="s")]
    result = filter_episodes(episodes, episode_filter=r"Match")
    assert [e.title for e in result] == ["Match me"]


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


# ---------------------------------------------------------------------------
# reset_feed_data
# ---------------------------------------------------------------------------

def _make_podcast_dir(output_dir: Path, slug: str, url: str) -> Path:
    """Create a minimal podcast directory with podcast.json."""
    d = output_dir / slug
    (d / "episodes").mkdir(parents=True)
    (d / "podcast.json").write_text(json.dumps({
        "title": slug, "url": url,
        "description": None, "image_url": None, "slug": slug,
    }))
    return d


def test_reset_feed_data_deletes_matching_dir(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    podcast_dir = _make_podcast_dir(output_dir, "my-show", "http://example.com/rss")
    (podcast_dir / "audio").mkdir()
    (podcast_dir / "audio" / "ep.mp3").write_bytes(b"data")

    deleted = reset_feed_data(output_dir, "http://example.com/rss")
    assert deleted is not None
    assert not podcast_dir.exists()


def test_reset_feed_data_leaves_other_dirs(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _make_podcast_dir(output_dir, "show-a", "http://a.com/rss")
    show_b = _make_podcast_dir(output_dir, "show-b", "http://b.com/rss")

    reset_feed_data(output_dir, "http://a.com/rss")
    assert show_b.exists()


def test_reset_feed_data_no_match_returns_none(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _make_podcast_dir(output_dir, "show-a", "http://a.com/rss")

    result = reset_feed_data(output_dir, "http://nonexistent.com/rss")
    assert result is None


def test_reset_feed_data_missing_output_dir(tmp_path: Path) -> None:
    result = reset_feed_data(tmp_path / "nonexistent", "http://a.com/rss")
    assert result is None


def test_reset_feed_data_empty_url(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _make_podcast_dir(output_dir, "show-a", "http://a.com/rss")

    result = reset_feed_data(output_dir, "")
    assert result is None
    assert (output_dir / "show-a").exists()


def test_reset_feed_data_skips_corrupt_podcast_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    bad_dir = output_dir / "bad-show"
    bad_dir.mkdir(parents=True)
    (bad_dir / "podcast.json").write_text("not valid json{{{")
    good_dir = _make_podcast_dir(output_dir, "good-show", "http://good.com/rss")

    deleted = reset_feed_data(output_dir, "http://good.com/rss")
    assert deleted is not None
    assert not good_dir.exists()
    assert bad_dir.exists()


# ---------------------------------------------------------------------------
# delete_feed
# ---------------------------------------------------------------------------

def test_delete_feed_removes_from_config_and_disk(tmp_path: Path) -> None:
    cfg_file = tmp_path / "feeds.yaml"
    output_dir = tmp_path / "output"
    _make_podcast_dir(output_dir, "my-show", "http://example.com/rss")

    config = {
        "feeds": [{"url": "http://example.com/rss", "name": "my-show"}],
        "defaults": {"output_dir": str(output_dir)},
    }
    save_config(config, cfg_file)

    url, deleted_dir = delete_feed(config, cfg_file, "my-show")
    assert url == "http://example.com/rss"
    assert deleted_dir is not None
    assert not (output_dir / "my-show").exists()
    reloaded = load_config(cfg_file)
    assert reloaded["feeds"] == []


def test_delete_feed_not_found_returns_none(tmp_path: Path) -> None:
    cfg_file = tmp_path / "feeds.yaml"
    config = {
        "feeds": [{"url": "http://example.com/rss", "name": "my-show"}],
        "defaults": {"output_dir": str(tmp_path / "output")},
    }
    save_config(config, cfg_file)

    url, deleted_dir = delete_feed(config, cfg_file, "nonexistent")
    assert url is None
    assert deleted_dir is None


def test_delete_feed_no_data_on_disk(tmp_path: Path) -> None:
    cfg_file = tmp_path / "feeds.yaml"
    config = {
        "feeds": [{"url": "http://example.com/rss", "name": "my-show"}],
        "defaults": {"output_dir": str(tmp_path / "output")},
    }
    save_config(config, cfg_file)

    url, deleted_dir = delete_feed(config, cfg_file, "my-show")
    assert url == "http://example.com/rss"
    assert deleted_dir is None
    reloaded = load_config(cfg_file)
    assert reloaded["feeds"] == []


def test_delete_feed_leaves_other_feeds(tmp_path: Path) -> None:
    cfg_file = tmp_path / "feeds.yaml"
    output_dir = tmp_path / "output"
    _make_podcast_dir(output_dir, "show-a", "http://a.com/rss")
    _make_podcast_dir(output_dir, "show-b", "http://b.com/rss")

    config = {
        "feeds": [
            {"url": "http://a.com/rss", "name": "show-a"},
            {"url": "http://b.com/rss", "name": "show-b"},
        ],
        "defaults": {"output_dir": str(output_dir)},
    }
    save_config(config, cfg_file)

    delete_feed(config, cfg_file, "show-a")
    assert not (output_dir / "show-a").exists()
    assert (output_dir / "show-b").exists()
    reloaded = load_config(cfg_file)
    assert len(reloaded["feeds"]) == 1
    assert reloaded["feeds"][0]["name"] == "show-b"
