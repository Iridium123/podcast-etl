"""Tests for cli.py: parse_date_range, reset and delete commands."""
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import click
import pytest
import yaml
from click.testing import CliRunner

from podcast_etl.cli import main, parse_date_range


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
# reset / delete commands
# ---------------------------------------------------------------------------

def _write_cfg(tmp_path: Path, feeds: list[dict]) -> Path:
    """Write a minimal feeds.yaml with the given feeds and return its path."""
    cfg = {
        "feeds": feeds,
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
        "poll_interval": 3600,
    }
    path = tmp_path / "feeds.yaml"
    path.write_text(yaml.dump(cfg))
    return path


def _create_podcast_dir(tmp_path: Path, slug: str, url: str) -> Path:
    """Create a fake podcast directory with a podcast.json matching the given URL."""
    podcast_dir = tmp_path / "output" / slug
    (podcast_dir / "episodes").mkdir(parents=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": slug, "url": url,
        "description": None, "image_url": None, "slug": slug,
    }))
    return podcast_dir


def test_reset_nonexistent_feed_does_not_prompt(tmp_path: Path):
    """`reset --feed nonexistent` must NOT prompt, must echo 'no data found'.

    Regression guard: an earlier revision of this command prompted the user
    for confirmation before checking whether any data actually existed, so
    the user would confirm and then be told there was nothing to delete.
    """
    cfg_path = _write_cfg(tmp_path, [{"url": "http://a.com/rss", "name": "show-a"}])
    runner = CliRunner()
    # No input provided — if the code prompts, the test will fail with an abort.
    result = runner.invoke(main, ["-c", str(cfg_path), "reset", "--feed", "nonexistent"])
    assert result.exit_code == 0, result.output
    assert "No data found" in result.output


def test_reset_feed_with_yes_flag_deletes_data(tmp_path: Path):
    cfg_path = _write_cfg(tmp_path, [{"url": "http://a.com/rss", "name": "show-a"}])
    podcast_dir = _create_podcast_dir(tmp_path, "show-a", "http://a.com/rss")
    assert podcast_dir.exists()

    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "reset", "--feed", "show-a", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output
    assert not podcast_dir.exists()
    # Feed is still in config — reset wipes data, not config
    updated = yaml.safe_load(cfg_path.read_text())
    assert any(f.get("name") == "show-a" for f in updated["feeds"])


def test_reset_feed_cancelled_leaves_data(tmp_path: Path):
    cfg_path = _write_cfg(tmp_path, [{"url": "http://a.com/rss", "name": "show-a"}])
    podcast_dir = _create_podcast_dir(tmp_path, "show-a", "http://a.com/rss")

    runner = CliRunner()
    # Respond "n" to the confirmation prompt
    result = runner.invoke(main, ["-c", str(cfg_path), "reset", "--feed", "show-a"], input="n\n")
    assert result.exit_code != 0  # click.confirm(abort=True) -> non-zero on decline
    assert podcast_dir.exists(), "Cancelled reset must not delete data"


def test_reset_all_empty_output_dir(tmp_path: Path):
    cfg_path = _write_cfg(tmp_path, [{"url": "http://a.com/rss", "name": "show-a"}])
    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "reset", "--all"])
    assert result.exit_code == 0, result.output
    assert "No data found" in result.output


def test_reset_without_feed_or_all_exits(tmp_path: Path):
    cfg_path = _write_cfg(tmp_path, [])
    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "reset"])
    assert result.exit_code == 1
    assert "--feed" in result.output or "--all" in result.output


def test_delete_nonexistent_feed_exits(tmp_path: Path):
    cfg_path = _write_cfg(tmp_path, [{"url": "http://a.com/rss", "name": "show-a"}])
    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "delete", "nonexistent", "--yes"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_delete_feed_with_yes_removes_from_config_and_deletes_data(tmp_path: Path):
    cfg_path = _write_cfg(tmp_path, [
        {"url": "http://a.com/rss", "name": "show-a"},
        {"url": "http://b.com/rss", "name": "show-b"},
    ])
    podcast_dir = _create_podcast_dir(tmp_path, "show-a", "http://a.com/rss")
    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "delete", "show-a", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Removed feed" in result.output
    assert "Deleted data directory" in result.output
    assert not podcast_dir.exists()
    updated = yaml.safe_load(cfg_path.read_text())
    assert len(updated["feeds"]) == 1
    assert updated["feeds"][0]["name"] == "show-b"


def test_delete_feed_with_no_data_on_disk_still_updates_config(tmp_path: Path):
    cfg_path = _write_cfg(tmp_path, [{"url": "http://a.com/rss", "name": "show-a"}])
    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "delete", "show-a", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Removed feed" in result.output
    assert "No data directory" in result.output
    updated = yaml.safe_load(cfg_path.read_text())
    assert updated["feeds"] == []


def test_delete_feed_cancelled_leaves_config_and_data(tmp_path: Path):
    cfg_path = _write_cfg(tmp_path, [{"url": "http://a.com/rss", "name": "show-a"}])
    podcast_dir = _create_podcast_dir(tmp_path, "show-a", "http://a.com/rss")
    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "delete", "show-a"], input="n\n")
    assert result.exit_code != 0
    assert podcast_dir.exists()
    updated = yaml.safe_load(cfg_path.read_text())
    assert len(updated["feeds"]) == 1


# ---------------------------------------------------------------------------
# serve command
# ---------------------------------------------------------------------------

def test_serve_default_host_is_loopback(tmp_path: Path):
    """The web UI has no auth, so `serve` must default to 127.0.0.1.

    Binding 0.0.0.0 by default would expose unauth'd credential read/write
    to anyone on the network the moment a user runs the bare `serve` command.
    Users who want LAN access must opt in explicitly with `--host 0.0.0.0`.
    """
    cfg_path = _write_cfg(tmp_path, [])
    runner = CliRunner()

    captured: dict = {}

    def fake_uvicorn_run(app, host, port, log_level):
        captured["host"] = host
        captured["port"] = port

    with patch("uvicorn.run", side_effect=fake_uvicorn_run), \
         patch("podcast_etl.web.create_app", return_value=object()):
        result = runner.invoke(main, ["-c", str(cfg_path), "serve"])

    assert result.exit_code == 0, result.output
    assert captured["host"] == "127.0.0.1"
