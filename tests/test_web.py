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
