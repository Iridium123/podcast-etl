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
