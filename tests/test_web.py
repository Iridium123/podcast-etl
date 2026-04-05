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


def test_feed_edit_save_updates_yaml(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a", "enabled": True}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/show-a", data={
        "name": "show-a",
        "url": "http://a.com/rss",
        "enabled": "on",
        "last": "10",
        "extra_yaml": "",
    }, follow_redirects=False)
    assert response.status_code == 303
    updated = yaml.safe_load(cfg_path.read_text())
    feed = next(f for f in updated["feeds"] if f["name"] == "show-a")
    assert feed["last"] == 10


def test_feed_edit_invalid_yaml_shows_error(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [{"url": "http://a.com/rss", "name": "show-a"}],
        "defaults": {"output_dir": str(tmp_path / "output"), "pipeline": ["download"]},
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/feeds/show-a", data={
        "name": "show-a",
        "url": "http://a.com/rss",
        "extra_yaml": "invalid: [yaml: {",
    })
    assert response.status_code == 200
    assert "error" in response.text.lower() or "invalid" in response.text.lower()


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


def test_defaults_save(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {
        "feeds": [],
        "defaults": {"output_dir": "./output", "pipeline": ["download"]},
        "poll_interval": 3600,
    })
    app = create_app(cfg_path, start_poller=False)
    client = TestClient(app)
    response = client.post("/defaults", data={
        "output_dir": "/new/output",
        "poll_interval": "1800",
        "extra_yaml": "",
    }, follow_redirects=False)
    assert response.status_code == 303
    updated = yaml.safe_load(cfg_path.read_text())
    assert updated["defaults"]["output_dir"] == "/new/output"
    assert updated["poll_interval"] == 1800


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
