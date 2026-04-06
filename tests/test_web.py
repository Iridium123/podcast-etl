"""Tests for the web UI routes."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from podcast_etl.web import create_app


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    cfg = {
        "feeds": [],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
        "poll_interval": 3600,
    }
    path = tmp_path / "feeds.yaml"
    path.write_text(yaml.dump(cfg))
    return path


@pytest.fixture
def client(config_path: Path) -> TestClient:
    app = create_app(config_path, start_poller=False)
    return TestClient(app)


def test_smoke_app_starts_and_serves_dashboard(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "podcast-etl" in response.text.lower()


@pytest.fixture
def config_with_feeds(tmp_path: Path) -> Path:
    output_dir = tmp_path / "output"
    cfg = {
        "feeds": [
            {"url": "http://a.com/rss", "name": "show-a", "enabled": True},
            {"url": "http://b.com/rss", "name": "show-b", "enabled": False},
        ],
        "defaults": {"output_dir": str(output_dir), "pipeline": ["download"]},
        "poll_interval": 3600,
    }
    path = tmp_path / "feeds.yaml"
    path.write_text(yaml.dump(cfg))
    return path


def test_dashboard_shows_feed_counts(config_with_feeds: Path) -> None:
    app = create_app(config_with_feeds, start_poller=False)
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200


def test_poll_pause_and_resume(config_path: Path) -> None:
    app = create_app(config_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/poll/pause")
    assert response.status_code == 200
    response = client.post("/poll/resume")
    assert response.status_code == 200


def test_poll_pause_rejects_cross_origin(config_path: Path) -> None:
    """A POST with a cross-origin Origin header must be rejected.

    Without this check, any page the user visits can submit a form to
    http://localhost:PORT/poll/pause and silently pause the pipeline.
    """
    app = create_app(config_path, start_poller=False)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/poll/pause",
        headers={"origin": "http://evil.example.com", "host": "localhost:8000"},
    )
    assert response.status_code == 400


def test_poll_pause_accepts_same_origin(config_path: Path) -> None:
    """A POST with a matching Origin header must be accepted."""
    app = create_app(config_path, start_poller=False)
    client = TestClient(app)
    response = client.post(
        "/poll/pause",
        headers={"origin": "http://localhost:8000", "host": "localhost:8000"},
    )
    assert response.status_code == 200


def test_poll_pause_accepts_no_origin(config_path: Path) -> None:
    """Non-browser clients (curl, tests) without Origin/Referer must still work."""
    app = create_app(config_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/poll/pause")
    assert response.status_code == 200


def test_feed_run_rejects_cross_origin(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/feeds/show-a/run",
        headers={"origin": "http://evil.example.com", "host": "localhost:8000"},
    )
    assert response.status_code == 400


def test_feed_add_rejects_cross_origin(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/feeds/add",
        data={"url": "http://new.com/rss", "name": "new"},
        headers={"origin": "http://evil.example.com", "host": "localhost:8000"},
    )
    assert response.status_code == 400


def test_referer_used_when_origin_missing(config_path: Path) -> None:
    """When Origin is absent, fall back to Referer for cross-origin detection."""
    app = create_app(config_path, start_poller=False)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/poll/pause",
        headers={"referer": "http://evil.example.com/page", "host": "localhost:8000"},
    )
    assert response.status_code == 400


def test_empty_host_header_strict_rejects(tmp_path: Path) -> None:
    """Missing Host header with a real Origin should reject, not allow.

    Ensures we don't have a carve-out that would let a proxy with a
    stripped Host header bypass the check.
    """
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app, raise_server_exceptions=False)
    # TestClient always sends a Host header, so we override with empty string
    response = client.post(
        "/poll/pause",
        headers={"origin": "http://example.com", "host": ""},
    )
    assert response.status_code == 400


def test_log_tail_returns_text(config_path: Path, tmp_path: Path) -> None:
    log_file = tmp_path / "output" / "podcast-etl.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("12:00:00 INFO: test log line\n")
    app = create_app(config_path, start_poller=False)
    client = TestClient(app)
    response = client.get("/log-tail")
    assert response.status_code == 200


def test_feeds_list_page(config_with_feeds: Path) -> None:
    app = create_app(config_with_feeds, start_poller=False)
    client = TestClient(app)
    response = client.get("/feeds")
    assert response.status_code == 200
    assert "show-a" in response.text
    assert "show-b" in response.text


def test_feeds_detail_page(config_with_feeds: Path) -> None:
    app = create_app(config_with_feeds, start_poller=False)
    client = TestClient(app)
    response = client.get("/feeds/show-a")
    assert response.status_code == 200
    assert "show-a" in response.text


def test_feeds_detail_not_found(config_path: Path) -> None:
    app = create_app(config_path, start_poller=False)
    client = TestClient(app)
    response = client.get("/feeds/nonexistent")
    assert response.status_code == 404


def _write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "feeds.yaml"
    path.write_text(yaml.dump(config))
    return path


def test_feed_edit_form_loads(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a", "enabled": True}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.get("/feeds/show-a/edit")
    assert response.status_code == 200
    assert "show-a" in response.text


def test_add_feed(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/add", data={
        "url": "http://new.com/rss",
        "name": "new-show",
    }, follow_redirects=False)
    assert response.status_code == 303
    updated = yaml.safe_load(cfg_path.read_text())
    assert any(f["url"] == "http://new.com/rss" for f in updated["feeds"])


def test_defaults_page_loads(config_path: Path) -> None:
    app = create_app(config_path, start_poller=False)
    client = TestClient(app)
    response = client.get("/defaults")
    assert response.status_code == 200


def test_add_feed_without_name_rejected(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/add", data={"url": "http://new.com/rss", "name": ""})
    assert response.status_code == 200
    assert "name is required" in response.text.lower()
    # Feed must not have been added
    updated = yaml.safe_load(cfg_path.read_text())
    assert updated["feeds"] == []


def test_add_feed_duplicate_rejected(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/add", data={
        "url": "http://a.com/rss",
        "name": "show-a",
    })
    assert response.status_code == 200
    assert "already exists" in response.text.lower()


def test_feed_edit_form_shows_full_yaml(tmp_path: Path) -> None:
    """Edit form should show the full feed config YAML in the textarea."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a", "tracker": {"mod_queue_opt_in": 1}}],
        "defaults": {
            "output_dir": str(tmp_path / "output"),
            "pipeline": ["download"],
            "tracker": {"url": "https://tracker.example.com"},
        },
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.get("/feeds/show-a/edit")
    assert response.status_code == 200
    # The full feed YAML should appear in the textarea, including per-feed overrides
    assert "Full feed config" in response.text
    assert "show-a" in response.text
    assert "mod_queue_opt_in" in response.text


def test_feed_preview_shows_diff_page(tmp_path: Path) -> None:
    """POST to /preview should show confirm page with diff, not save yet."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a", "enabled": True}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/show-a/preview", data={
        "name": "show-a",
        "url": "http://a.com/rss",
        "enabled": "on",
        "last": "5",
        "extra_yaml": "",
    })
    assert response.status_code == 200
    # Should show confirm page, not redirect
    assert "confirm" in response.text.lower()
    # File should NOT have been updated yet
    saved = yaml.safe_load(cfg_path.read_text())
    feed = next(f for f in saved["feeds"] if f["name"] == "show-a")
    assert "last" not in feed


def test_feed_preview_invalid_yaml_shows_error(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/show-a/preview", data={
        "name": "show-a",
        "url": "http://a.com/rss",
        "extra_yaml": "invalid: [yaml: {",
    })
    assert response.status_code == 200
    assert "invalid" in response.text.lower() or "error" in response.text.lower()


def test_feed_confirm_saves(tmp_path: Path) -> None:
    """Preview then confirm flow should save the new feed config."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a", "enabled": True}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    import yaml as _yaml
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    # Step 1: preview to get a token
    preview_resp = client.post("/feeds/show-a/preview", data={
        "name": "show-a",
        "url": "http://a.com/rss",
        "enabled": "on",
        "last": "7",
        "extra_yaml": "",
    })
    assert preview_resp.status_code == 200
    assert "confirm" in preview_resp.text.lower()
    # Extract token from hidden input
    import re
    token_match = re.search(r'<input type="hidden" name="token" value="([^"]+)"', preview_resp.text)
    assert token_match, "Token not found in preview response"
    token = token_match.group(1)
    # Step 2: confirm with the token
    response = client.post("/feeds/show-a/confirm", data={"token": token}, follow_redirects=False)
    assert response.status_code == 303
    saved = _yaml.safe_load(cfg_path.read_text())
    feed = next(f for f in saved["feeds"] if f["name"] == "show-a")
    assert feed["last"] == 7


def test_defaults_edit_form_shows_full_yaml(tmp_path: Path) -> None:
    """Edit form should show the full defaults config YAML in the textarea."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {
            "output_dir": "./output",
            "pipeline": ["download"],
            "tracker": {"url": "https://tracker.example.com"},
        },
        "poll_interval": 3600,
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.get("/defaults")
    assert response.status_code == 200
    assert "Full defaults config" in response.text
    assert "tracker" in response.text
    assert "tracker.example.com" in response.text


def test_defaults_preview_shows_diff_page(tmp_path: Path) -> None:
    """POST to /defaults/preview should show confirm page with diff, not save yet."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": "./output", "pipeline": ["download"]},
        "poll_interval": 3600,
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/defaults/preview", data={
        "output_dir": "/new/output",
        "poll_interval": "1800",
        "extra_yaml": "output_dir: ./output\npipeline:\n- download\n",
    })
    assert response.status_code == 200
    assert "confirm" in response.text.lower()
    # File should NOT have been updated yet
    saved = yaml.safe_load(cfg_path.read_text())
    assert saved["defaults"]["output_dir"] == "./output"
    assert saved["poll_interval"] == 3600


def test_defaults_preview_bad_poll_interval_shows_error(tmp_path: Path) -> None:
    """A non-numeric poll_interval must surface an error, not silently drop.

    Previously this was swallowed by `except ValueError: pass` and the user
    saw a success redirect while their input was ignored. The fix surfaces
    the parse error through the form re-render.
    """
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": "./output", "pipeline": ["download"]},
        "poll_interval": 3600,
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/defaults/preview", data={
        "output_dir": "./output",
        "poll_interval": "not-a-number",
        "extra_yaml": "output_dir: ./output\npipeline:\n- download\n",
    })
    assert response.status_code == 200
    assert "poll_interval" in response.text
    # Config must not have been modified
    saved = yaml.safe_load(cfg_path.read_text())
    assert saved["poll_interval"] == 3600


def test_feed_preview_bad_last_shows_error(tmp_path: Path) -> None:
    """A non-numeric `last` value must surface an error, not land in YAML."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a", "enabled": True}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/show-a/preview", data={
        "name": "show-a",
        "url": "http://a.com/rss",
        "last": "not-a-number",
        "extra_yaml": "",
    })
    assert response.status_code == 200
    assert "last" in response.text
    # Config must not have been modified
    saved = yaml.safe_load(cfg_path.read_text())
    feed = next(f for f in saved["feeds"] if f["name"] == "show-a")
    assert "last" not in feed


def test_defaults_preview_invalid_yaml_shows_error(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": "./output", "pipeline": ["download"]},
        "poll_interval": 3600,
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/defaults/preview", data={
        "extra_yaml": "invalid: [yaml: {",
    })
    assert response.status_code == 200
    assert "invalid" in response.text.lower() or "error" in response.text.lower()


def test_defaults_confirm_saves(tmp_path: Path) -> None:
    """Preview then confirm flow should save the new defaults config."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": "./output", "pipeline": ["download"]},
        "poll_interval": 3600,
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    # Step 1: preview to get a token
    preview_resp = client.post("/defaults/preview", data={
        "output_dir": "/confirmed/output",
        "poll_interval": "900",
        "extra_yaml": "output_dir: ./output\npipeline:\n- download\n",
    })
    assert preview_resp.status_code == 200
    assert "confirm" in preview_resp.text.lower()
    # Extract token from hidden input
    import re
    token_match = re.search(r'<input type="hidden" name="token" value="([^"]+)"', preview_resp.text)
    assert token_match, "Token not found in preview response"
    token = token_match.group(1)
    # Step 2: confirm with the token
    response = client.post("/defaults/confirm", data={"token": token}, follow_redirects=False)
    assert response.status_code == 303
    saved = yaml.safe_load(cfg_path.read_text())
    assert saved["defaults"]["output_dir"] == "/confirmed/output"
    assert saved["poll_interval"] == 900


def test_delete_feed_confirmation_page(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.get("/feeds/show-a/delete")
    assert response.status_code == 200
    assert "show-a" in response.text
    assert "delete" in response.text.lower()


def _get_delete_token(client: TestClient, name: str) -> str:
    """Get the CSRF token from the delete confirmation page."""
    import re
    response = client.get(f"/feeds/{name}/delete")
    match = re.search(r'name="token"\s+value="([^"]+)"', response.text)
    assert match, "Delete confirmation page should contain a token"
    return match.group(1)


def test_delete_feed_removes_from_config(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [
            {"url": "http://a.com/rss", "name": "show-a"},
            {"url": "http://b.com/rss", "name": "show-b"},
        ],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    token = _get_delete_token(client, "show-a")
    response = client.post("/feeds/show-a/delete", data={"token": token}, follow_redirects=False)
    assert response.status_code == 303
    updated = yaml.safe_load(cfg_path.read_text())
    assert len(updated["feeds"]) == 1
    assert updated["feeds"][0]["name"] == "show-b"


def test_delete_feed_rejects_missing_token(tmp_path: Path) -> None:
    """POST without a valid token should be rejected."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/feeds/show-a/delete", data={"token": ""})
    assert response.status_code == 400
    # Feed should NOT be deleted
    updated = yaml.safe_load(cfg_path.read_text())
    assert len(updated["feeds"]) == 1


def test_delete_feed_removes_matching_directory(tmp_path: Path) -> None:
    """Delete should remove the podcast dir whose podcast.url matches the feed URL."""
    import json

    output_dir = tmp_path / "output"
    # Create podcast dir for show-a
    show_a_dir = output_dir / "show-a-slug"
    (show_a_dir / "episodes").mkdir(parents=True)
    (show_a_dir / "audio").mkdir()
    (show_a_dir / "podcast.json").write_text(json.dumps({
        "title": "Show A", "url": "http://a.com/rss",
        "description": None, "image_url": None, "slug": "show-a-slug",
    }))
    # Create a dummy audio file inside
    (show_a_dir / "audio" / "episode.mp3").write_bytes(b"fake audio")

    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(output_dir), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    token = _get_delete_token(client, "show-a")
    client.post("/feeds/show-a/delete", data={"token": token}, follow_redirects=False)

    assert not show_a_dir.exists(), "Podcast directory should be deleted"


def test_delete_feed_leaves_other_directories_intact(tmp_path: Path) -> None:
    """Delete should only remove the directory matching the feed URL, not others."""
    import json

    output_dir = tmp_path / "output"
    # Create podcast dirs for two feeds
    for slug, url in [("show-a-slug", "http://a.com/rss"), ("show-b-slug", "http://b.com/rss")]:
        d = output_dir / slug
        (d / "episodes").mkdir(parents=True)
        (d / "podcast.json").write_text(json.dumps({
            "title": slug, "url": url,
            "description": None, "image_url": None, "slug": slug,
        }))
        (d / "data.txt").write_text("keep me")

    cfg_path = _write_config(tmp_path, {
        "feeds": [
            {"url": "http://a.com/rss", "name": "show-a"},
            {"url": "http://b.com/rss", "name": "show-b"},
        ],
        "defaults": {"output_dir": str(output_dir), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    token = _get_delete_token(client, "show-a")
    client.post("/feeds/show-a/delete", data={"token": token}, follow_redirects=False)

    assert not (output_dir / "show-a-slug").exists(), "Deleted feed dir should be gone"
    assert (output_dir / "show-b-slug").exists(), "Other feed dir should remain"
    assert (output_dir / "show-b-slug" / "data.txt").read_text() == "keep me"


def test_delete_feed_no_output_dir_does_not_crash(tmp_path: Path) -> None:
    """Delete should work even if the output directory doesn't exist."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(tmp_path / "nonexistent"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    token = _get_delete_token(client, "show-a")
    response = client.post("/feeds/show-a/delete", data={"token": token}, follow_redirects=False)
    assert response.status_code == 303
    updated = yaml.safe_load(cfg_path.read_text())
    assert len(updated["feeds"]) == 0


def test_delete_feed_skips_dirs_without_podcast_json(tmp_path: Path) -> None:
    """Directories without podcast.json should not be touched."""
    import json

    output_dir = tmp_path / "output"
    # Create a random dir without podcast.json
    random_dir = output_dir / "random-stuff"
    random_dir.mkdir(parents=True)
    (random_dir / "important.txt").write_text("don't delete me")

    # Create the actual podcast dir
    show_dir = output_dir / "show-a-slug"
    (show_dir / "episodes").mkdir(parents=True)
    (show_dir / "podcast.json").write_text(json.dumps({
        "title": "Show A", "url": "http://a.com/rss",
        "description": None, "image_url": None, "slug": "show-a-slug",
    }))

    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(output_dir), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    token = _get_delete_token(client, "show-a")
    client.post("/feeds/show-a/delete", data={"token": token}, follow_redirects=False)

    assert not show_dir.exists(), "Matching podcast dir should be deleted"
    assert random_dir.exists(), "Non-podcast dir should remain"
    assert (random_dir / "important.txt").read_text() == "don't delete me"


def test_delete_feed_does_not_delete_mismatched_url(tmp_path: Path) -> None:
    """A podcast dir with a different URL should not be deleted."""
    import json

    output_dir = tmp_path / "output"
    # Create podcast dir with a different URL than the feed being deleted
    other_dir = output_dir / "other-show"
    (other_dir / "episodes").mkdir(parents=True)
    (other_dir / "podcast.json").write_text(json.dumps({
        "title": "Other", "url": "http://other.com/rss",
        "description": None, "image_url": None, "slug": "other-show",
    }))

    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(output_dir), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    token = _get_delete_token(client, "show-a")
    client.post("/feeds/show-a/delete", data={"token": token}, follow_redirects=False)

    assert other_dir.exists(), "Dir with different URL should not be deleted"


def test_add_feed_saves_all_fields(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/add", data={
        "url": "http://new.com/rss",
        "name": "new-show",
        "enabled": "on",
        "last": "5",
        "pipeline_download": "on",
        "pipeline_tag": "on",
        "title_sanitize": "on",
        "extra_yaml": "",
    }, follow_redirects=False)
    assert response.status_code == 303
    updated = yaml.safe_load(cfg_path.read_text())
    feed = next(f for f in updated["feeds"] if f["name"] == "new-show")
    assert feed["enabled"] is True
    assert feed["last"] == 5
    assert "download" in feed["pipeline"]
    assert "tag" in feed["pipeline"]


def test_pipeline_chips_show_checked_state(tmp_path: Path) -> None:
    """Pipeline chips should use Jinja2 conditional classes for checked state."""
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a", "pipeline": ["download"]}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.get("/feeds/show-a/edit")
    assert response.status_code == 200
    # The checked chip should have the blue styling applied via server-side class
    assert "bg-blue-800" in response.text
    # Should have visible checkbox input (not sr-only)
    assert "sr-only" not in response.text
