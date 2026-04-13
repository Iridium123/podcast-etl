"""Tests for service.py: load_config, save_config, get_output_dir,
find_feed_config, get_pipeline_steps, filter_episodes, validate_config,
get_feed_status, split_config_fields, merge_config_fields,
and get_resolved_config_with_sources."""
from datetime import date
from pathlib import Path

import pytest
import yaml

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.service import (
    delete_feed,
    filter_episodes,
    find_feed_config,
    find_podcast_dir,
    get_feed_status,
    get_output_dir,
    get_pipeline_steps,
    get_resolved_config_with_sources,
    load_config,
    merge_config_fields,
    replace_feed,
    reset_feed_data,
    save_config,
    split_config_fields,
    validate_config,
)


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


def test_save_config_preserves_original_if_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If os.replace fails mid-write, the original feeds.yaml must be intact.

    This is the whole point of the .tmp + os.replace pattern — interrupting
    the write (crash, kill -9, disk full at commit time) must never leave
    feeds.yaml in a corrupted state.
    """
    cfg_file = tmp_path / "feeds.yaml"
    original = {"feeds": [{"url": "http://original.com/rss"}], "poll_interval": 3600}
    save_config(original, cfg_file)
    original_text = cfg_file.read_text()

    import podcast_etl.service as service_mod

    def boom(*_args, **_kwargs):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(service_mod.os, "replace", boom)

    new_data = {"feeds": [{"url": "http://new.com/rss"}], "poll_interval": 9999}
    with pytest.raises(OSError, match="simulated crash"):
        save_config(new_data, cfg_file)

    # Original content must be unchanged
    assert cfg_file.read_text() == original_text
    loaded = load_config(cfg_file)
    assert loaded["feeds"][0]["url"] == "http://original.com/rss"
    assert loaded["poll_interval"] == 3600


def test_save_config_preserves_original_if_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If the tmp file write itself fails, the original must be intact.

    Covers the case where the process crashes before write_text completes —
    e.g. out-of-disk-space partway through writing. feeds.yaml is never
    touched (only .tmp is), so it must still be readable as the old config.
    """
    cfg_file = tmp_path / "feeds.yaml"
    original = {"feeds": [{"url": "http://original.com/rss"}], "poll_interval": 3600}
    save_config(original, cfg_file)
    original_text = cfg_file.read_text()

    real_write_text = Path.write_text

    def boom(self: Path, *args, **kwargs):
        if self.suffix == ".tmp":
            raise OSError("simulated crash during write")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom)

    new_data = {"feeds": [{"url": "http://new.com/rss"}]}
    with pytest.raises(OSError, match="simulated crash"):
        save_config(new_data, cfg_file)

    assert cfg_file.read_text() == original_text


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
# replace_feed
# ---------------------------------------------------------------------------

def test_replace_feed_by_name():
    feeds = [
        {"url": "http://a.com/rss", "name": "show-a"},
        {"url": "http://b.com/rss", "name": "show-b"},
    ]
    new_feed = {"url": "http://a.com/rss", "name": "show-a", "last": 5}
    result = replace_feed(feeds, "show-a", new_feed)
    assert result[0] == new_feed
    assert result[1] == {"url": "http://b.com/rss", "name": "show-b"}


def test_replace_feed_by_url():
    feeds = [
        {"url": "http://a.com/rss", "name": "show-a"},
        {"url": "http://b.com/rss", "name": "show-b"},
    ]
    new_feed = {"url": "http://b.com/rss", "name": "show-b", "enabled": True}
    result = replace_feed(feeds, "http://b.com/rss", new_feed)
    assert result[0] == {"url": "http://a.com/rss", "name": "show-a"}
    assert result[1] == new_feed


def test_replace_feed_preserves_order():
    feeds = [
        {"url": "http://a.com/rss", "name": "a"},
        {"url": "http://b.com/rss", "name": "b"},
        {"url": "http://c.com/rss", "name": "c"},
    ]
    result = replace_feed(feeds, "b", {"url": "http://b.com/rss", "name": "b", "last": 1})
    assert [f["name"] for f in result] == ["a", "b", "c"]


def test_replace_feed_no_match_returns_unchanged():
    feeds = [{"url": "http://a.com/rss", "name": "show-a"}]
    result = replace_feed(feeds, "nonexistent", {"url": "http://x.com/rss"})
    assert result == feeds


def test_replace_feed_empty_list():
    assert replace_feed([], "anything", {"url": "http://x.com/rss"}) == []


def test_replace_feed_does_not_mutate_input():
    feeds = [{"url": "http://a.com/rss", "name": "show-a"}]
    original = [dict(f) for f in feeds]
    replace_feed(feeds, "show-a", {"url": "http://a.com/rss", "name": "show-a", "last": 10})
    assert feeds == original


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
# _coerce_start_date
# ---------------------------------------------------------------------------

def test_coerce_start_date_none_returns_none():
    from podcast_etl.service import _coerce_start_date
    assert _coerce_start_date(None) is None


def test_coerce_start_date_date_instance_returns_same():
    from podcast_etl.service import _coerce_start_date
    d = date(2026, 4, 7)
    assert _coerce_start_date(d) is d


def test_coerce_start_date_iso_string_parses():
    from podcast_etl.service import _coerce_start_date
    assert _coerce_start_date("2026-04-07") == date(2026, 4, 7)


def test_coerce_start_date_invalid_string_raises():
    from podcast_etl.service import _coerce_start_date
    with pytest.raises(ValueError, match="not a valid ISO date"):
        _coerce_start_date("not-a-date")


def test_coerce_start_date_wrong_type_raises():
    from podcast_etl.service import _coerce_start_date
    with pytest.raises(TypeError, match="must be a date"):
        _coerce_start_date(42)


def test_coerce_start_date_datetime_returns_date():
    from datetime import datetime
    from podcast_etl.service import _coerce_start_date
    dt = datetime(2026, 4, 7, 12, 0, 0)
    result = _coerce_start_date(dt)
    assert result == date(2026, 4, 7)
    assert type(result) is date


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


def test_filter_episodes_start_date_floor():
    result = filter_episodes(_EPISODES, start_date=date(2026, 3, 3))
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b", "Ep 4"]


def test_filter_episodes_start_date_keeps_equal():
    """Boundary date is included (< not <=)."""
    result = filter_episodes(_EPISODES, start_date=date(2026, 3, 4))
    assert [e.title for e in result] == ["Ep 4"]


def test_filter_episodes_start_date_skips_no_published():
    episodes = [
        _ep("No date"),
        _ep("Has date", "Wed, 04 Mar 2026 00:00:00 +0000"),
    ]
    result = filter_episodes(episodes, start_date=date(2026, 3, 1))
    assert [e.title for e in result] == ["Has date"]


def test_filter_episodes_start_date_skips_unparseable_date():
    episodes = [
        _ep("Bad date", "not a real date"),
        _ep("Good date", "Wed, 04 Mar 2026 00:00:00 +0000"),
    ]
    result = filter_episodes(episodes, start_date=date(2026, 3, 1))
    assert [e.title for e in result] == ["Good date"]


def test_filter_episodes_start_date_combined_with_last():
    """The migration scenario: last N, floored by start_date.

    With last=5 (all 5 episodes) and start_date=2026-03-03, we expect
    only the 3 episodes at or after that date — last and start_date stack.
    """
    result = filter_episodes(_EPISODES, last=5, start_date=date(2026, 3, 3))
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b", "Ep 4"]


def test_filter_episodes_start_date_combined_with_date_range():
    """start_date intersects with date_range — both apply."""
    result = filter_episodes(
        _EPISODES,
        date_range=(date(2026, 3, 1), date(2026, 3, 4)),
        start_date=date(2026, 3, 3),
    )
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b", "Ep 4"]


def test_filter_episodes_start_date_combined_with_episode_filter():
    """regex still applies on top of start_date."""
    result = filter_episodes(
        _EPISODES,
        start_date=date(2026, 3, 3),
        episode_filter=r"Ep 3",
    )
    assert [e.title for e in result] == ["Ep 3a", "Ep 3b"]


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


def test_validate_config_accepts_start_date_as_date_instance():
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": date(2026, 4, 7)}],
    }
    validate_config(config)  # should not raise


def test_validate_config_accepts_start_date_as_iso_string():
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": "2026-04-07"}],
    }
    validate_config(config)  # should not raise


def test_validate_config_rejects_start_date_unparseable_string():
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": "not-a-date"}],
    }
    with pytest.raises(SystemExit, match="not a valid ISO date"):
        validate_config(config)


def test_validate_config_rejects_start_date_wrong_type():
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": 42}],
    }
    with pytest.raises(SystemExit, match="start_date must be a date"):
        validate_config(config)


def test_validate_config_accepts_start_date_in_defaults():
    config = {
        "feeds": [{"url": "https://example.com/rss"}],
        "defaults": {"start_date": "2026-04-07"},
    }
    validate_config(config)  # should not raise


def test_validate_config_rejects_bad_start_date_in_defaults():
    config = {
        "feeds": [{"url": "https://example.com/rss"}],
        "defaults": {"start_date": "garbage"},
    }
    with pytest.raises(SystemExit, match="not a valid ISO date"):
        validate_config(config)


def test_validate_config_accepts_start_date_as_datetime():
    from datetime import datetime
    config = {
        "feeds": [{"url": "https://example.com/rss", "start_date": datetime(2026, 4, 7, 12, 0)}],
    }
    validate_config(config)  # should not raise


# ---------------------------------------------------------------------------
# Helpers for get_feed_status tests
# ---------------------------------------------------------------------------

def _make_podcast(tmp_path: Path, url: str = "https://example.com/rss") -> Podcast:
    """Create a minimal Podcast with two episodes and save it to tmp_path."""
    ep1 = Episode(
        title="Episode One",
        guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep1.mp3",
        duration="30:00",
        description="First episode",
        slug="episode-one",
        status={"download": StepStatus(completed_at="2024-01-01T00:00:00")},
    )
    ep2 = Episode(
        title="Episode Two",
        guid="guid-2",
        published="Tue, 02 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep2.mp3",
        duration="45:00",
        description="Second episode",
        slug="episode-two",
        status={},  # no steps done
    )
    podcast = Podcast(
        title="My Podcast",
        url=url,
        description="A test podcast",
        image_url=None,
        slug="my-podcast",
        episodes=[ep1, ep2],
    )
    podcast.save(tmp_path)
    return podcast


# ---------------------------------------------------------------------------
# get_feed_status
# ---------------------------------------------------------------------------

def test_get_feed_status_empty_output_dir(tmp_path: Path) -> None:
    """Returns empty list when output_dir does not exist."""
    result = get_feed_status(tmp_path / "nonexistent", config={})
    assert result == []


def test_get_feed_status_no_podcasts(tmp_path: Path) -> None:
    """Returns empty list when output_dir exists but contains no podcast dirs."""
    result = get_feed_status(tmp_path, config={})
    assert result == []


def test_get_feed_status_basic_counts(tmp_path: Path) -> None:
    """Returns one entry per podcast with correct counts."""
    _make_podcast(tmp_path)
    config = {
        "defaults": {"pipeline": ["download"]},
        "feeds": [],
    }
    result = get_feed_status(tmp_path, config)
    assert len(result) == 1
    entry = result[0]
    assert entry["title"] == "My Podcast"
    assert entry["slug"] == "my-podcast"
    assert entry["episode_count"] == 2
    assert entry["step_names"] == ["download"]
    # ep1 has download done → completed; ep2 has no steps → pending
    assert entry["completed_count"] == 1
    assert entry["pending_count"] == 1


def test_get_feed_status_episode_statuses(tmp_path: Path) -> None:
    """Per-episode status dicts report done/pending correctly."""
    _make_podcast(tmp_path)
    config = {
        "defaults": {"pipeline": ["download"]},
        "feeds": [],
    }
    result = get_feed_status(tmp_path, config)
    episodes = result[0]["episodes"]
    assert len(episodes) == 2
    statuses_by_title = {ep["title"]: ep["statuses"] for ep in episodes}
    assert statuses_by_title["Episode One"] == {"download": "done"}
    assert statuses_by_title["Episode Two"] == {"download": "pending"}


def test_get_feed_status_enabled_from_feed_config(tmp_path: Path) -> None:
    """enabled and name are read from the matching feed config entry."""
    url = "https://example.com/rss"
    _make_podcast(tmp_path, url=url)
    config = {
        "defaults": {"pipeline": ["download"]},
        "feeds": [{"url": url, "name": "my-pod", "enabled": True}],
    }
    result = get_feed_status(tmp_path, config)
    assert result[0]["enabled"] is True
    assert result[0]["name"] == "my-pod"


def test_get_feed_status_unknown_feed_defaults_disabled(tmp_path: Path) -> None:
    """Podcasts not in feeds config have enabled=False and name=None."""
    _make_podcast(tmp_path)
    config = {"defaults": {"pipeline": ["download"]}, "feeds": []}
    result = get_feed_status(tmp_path, config)
    assert result[0]["enabled"] is False
    assert result[0]["name"] is None


def test_get_feed_status_all_steps_completed(tmp_path: Path) -> None:
    """completed_count equals episode_count when all steps are done."""
    ep = Episode(
        title="Full Episode",
        guid="guid-full",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/full.mp3",
        duration="60:00",
        description="Complete",
        slug="full-episode",
        status={
            "download": StepStatus(completed_at="2024-01-01T00:00:00"),
            "tag": StepStatus(completed_at="2024-01-01T00:01:00"),
        },
    )
    podcast = Podcast(
        title="Full Podcast",
        url="https://full.example.com/rss",
        description=None,
        image_url=None,
        slug="full-podcast",
        episodes=[ep],
    )
    podcast.save(tmp_path)
    config = {"defaults": {"pipeline": ["download", "tag"]}, "feeds": []}
    result = get_feed_status(tmp_path, config)
    assert result[0]["completed_count"] == 1
    assert result[0]["pending_count"] == 0


# ---------------------------------------------------------------------------
# split_config_fields
# ---------------------------------------------------------------------------

def test_split_config_fields_separates_known_and_extra() -> None:
    config = {"url": "https://example.com", "name": "pod", "tracker": {"url": "https://t.example.com"}}
    known_fields = {"url", "name"}
    known, extra = split_config_fields(config, known_fields)
    assert known == {"url": "https://example.com", "name": "pod"}
    assert extra == {"tracker": {"url": "https://t.example.com"}}


def test_split_config_fields_all_known() -> None:
    config = {"url": "https://example.com", "name": "pod"}
    known, extra = split_config_fields(config, {"url", "name"})
    assert known == config
    assert extra == {}


def test_split_config_fields_all_extra() -> None:
    config = {"tracker": {}, "client": {}}
    known, extra = split_config_fields(config, {"url", "name"})
    assert known == {}
    assert extra == config


def test_split_config_fields_empty_config() -> None:
    known, extra = split_config_fields({}, {"url"})
    assert known == {}
    assert extra == {}


# ---------------------------------------------------------------------------
# merge_config_fields
# ---------------------------------------------------------------------------

def test_merge_config_fields_combines_dicts() -> None:
    known = {"url": "https://example.com", "name": "pod"}
    extra = {"tracker": {"url": "https://t.example.com"}}
    result = merge_config_fields(known, extra)
    assert result == {"url": "https://example.com", "name": "pod", "tracker": {"url": "https://t.example.com"}}


def test_merge_config_fields_extra_overwrites_known_on_conflict() -> None:
    """extra keys win on overlap (dict.update order: known first, then extra)."""
    known = {"url": "https://from-known.com"}
    extra = {"url": "https://from-extra.com"}
    result = merge_config_fields(known, extra)
    assert result["url"] == "https://from-extra.com"


def test_merge_config_fields_empty_inputs() -> None:
    assert merge_config_fields({}, {}) == {}


def test_merge_config_fields_roundtrip_with_split() -> None:
    """split then merge restores the original dict."""
    original = {"url": "https://example.com", "name": "pod", "tracker": {"url": "t"}}
    known_fields = {"url", "name"}
    known, extra = split_config_fields(original, known_fields)
    result = merge_config_fields(known, extra)
    assert result == original


# ---------------------------------------------------------------------------
# get_resolved_config_with_sources
# ---------------------------------------------------------------------------

def test_resolved_config_with_sources_feed_key_attribution() -> None:
    """Keys present only in feed are attributed to 'feed'."""
    defaults = {"pipeline": ["download"], "output_dir": "./output"}
    feed = {"url": "https://example.com/rss", "pipeline": ["download", "tag"]}
    resolved, source_map = get_resolved_config_with_sources(defaults, feed)
    assert resolved["pipeline"] == ["download", "tag"]
    assert source_map["pipeline"] == "feed"


def test_resolved_config_with_sources_default_key_attribution() -> None:
    """Keys present only in defaults are attributed to 'default'."""
    defaults = {"pipeline": ["download"], "output_dir": "./output"}
    feed = {"url": "https://example.com/rss"}
    _resolved, source_map = get_resolved_config_with_sources(defaults, feed)
    assert source_map["output_dir"] == "default"
    assert source_map["pipeline"] == "default"


def test_resolved_config_with_sources_nested_keys() -> None:
    """Nested dicts produce dot-separated keys with correct attribution."""
    defaults = {
        "tracker": {
            "url": "https://tracker.example.com",
            "mod_queue_opt_in": 0,
        }
    }
    feed = {
        "url": "https://example.com/rss",
        "tracker": {"mod_queue_opt_in": 1},
    }
    _resolved, source_map = get_resolved_config_with_sources(defaults, feed)
    assert source_map["tracker.mod_queue_opt_in"] == "feed"
    assert source_map["tracker.url"] == "default"


def test_resolved_config_with_sources_resolved_values() -> None:
    """The resolved config contains the merged (feed-overriding) values."""
    defaults = {"output_dir": "./output", "pipeline": ["download"]}
    feed = {"url": "https://example.com/rss", "pipeline": ["download", "tag"]}
    resolved, _ = get_resolved_config_with_sources(defaults, feed)
    assert resolved["output_dir"] == "./output"
    assert resolved["pipeline"] == ["download", "tag"]
    assert resolved["url"] == "https://example.com/rss"


def test_resolved_config_with_sources_empty_feed() -> None:
    """All keys come from defaults when feed is empty."""
    defaults = {"pipeline": ["download"], "output_dir": "./output"}
    feed = {}
    _resolved, source_map = get_resolved_config_with_sources(defaults, feed)
    assert all(v == "default" for v in source_map.values())


def test_resolved_config_with_sources_empty_defaults() -> None:
    """All keys come from feed when defaults is empty."""
    defaults = {}
    feed = {"url": "https://example.com/rss", "pipeline": ["download"]}
    _resolved, source_map = get_resolved_config_with_sources(defaults, feed)
    assert all(v == "feed" for v in source_map.values())


# ---------------------------------------------------------------------------
# reset_feed_data
# ---------------------------------------------------------------------------

def _make_podcast_dir(output_dir: Path, slug: str, url: str) -> Path:
    """Create a minimal podcast directory with podcast.json."""
    import json
    d = output_dir / slug
    (d / "episodes").mkdir(parents=True)
    (d / "podcast.json").write_text(json.dumps({
        "title": slug, "url": url,
        "description": None, "image_url": None, "slug": slug,
    }))
    return d


def test_reset_feed_data_deletes_matching_dir(tmp_path: Path) -> None:
    from podcast_etl.service import reset_feed_data
    output_dir = tmp_path / "output"
    podcast_dir = _make_podcast_dir(output_dir, "my-show", "http://example.com/rss")
    (podcast_dir / "audio").mkdir()
    (podcast_dir / "audio" / "ep.mp3").write_bytes(b"data")

    deleted = reset_feed_data(output_dir, "http://example.com/rss")
    assert deleted is not None
    assert not podcast_dir.exists()


def test_reset_feed_data_leaves_other_dirs(tmp_path: Path) -> None:
    from podcast_etl.service import reset_feed_data
    output_dir = tmp_path / "output"
    _make_podcast_dir(output_dir, "show-a", "http://a.com/rss")
    show_b = _make_podcast_dir(output_dir, "show-b", "http://b.com/rss")

    reset_feed_data(output_dir, "http://a.com/rss")
    assert show_b.exists()


def test_reset_feed_data_no_match_returns_none(tmp_path: Path) -> None:
    from podcast_etl.service import reset_feed_data
    output_dir = tmp_path / "output"
    _make_podcast_dir(output_dir, "show-a", "http://a.com/rss")

    result = reset_feed_data(output_dir, "http://nonexistent.com/rss")
    assert result is None


def test_reset_feed_data_missing_output_dir(tmp_path: Path) -> None:
    from podcast_etl.service import reset_feed_data
    result = reset_feed_data(tmp_path / "nonexistent", "http://a.com/rss")
    assert result is None


def test_reset_feed_data_empty_url(tmp_path: Path) -> None:
    from podcast_etl.service import reset_feed_data
    output_dir = tmp_path / "output"
    _make_podcast_dir(output_dir, "show-a", "http://a.com/rss")

    result = reset_feed_data(output_dir, "")
    assert result is None
    assert (output_dir / "show-a").exists()


def test_reset_feed_data_skips_corrupt_podcast_json(tmp_path: Path) -> None:
    from podcast_etl.service import reset_feed_data
    output_dir = tmp_path / "output"
    # Create a dir with bad podcast.json
    bad_dir = output_dir / "bad-show"
    bad_dir.mkdir(parents=True)
    (bad_dir / "podcast.json").write_text("not valid json{{{")
    # Create a good dir
    good_dir = _make_podcast_dir(output_dir, "good-show", "http://good.com/rss")

    deleted = reset_feed_data(output_dir, "http://good.com/rss")
    assert deleted is not None
    assert not good_dir.exists()
    assert bad_dir.exists()  # corrupt dir untouched


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
