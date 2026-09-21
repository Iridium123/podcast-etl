"""Tests for atomic.py: atomic_write_text."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from podcast_etl.atomic import atomic_write_text


def test_writes_content(tmp_path: Path):
    path = tmp_path / "file.txt"
    atomic_write_text(path, "hello")
    assert path.read_text(encoding="utf-8") == "hello"


def test_leaves_no_temp_files_behind(tmp_path: Path):
    path = tmp_path / "file.txt"
    atomic_write_text(path, "hello")
    assert list(tmp_path.glob("*.tmp")) == []


def test_overwrites_existing_file(tmp_path: Path):
    path = tmp_path / "file.txt"
    path.write_text("old", encoding="utf-8")
    atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "new"


def test_failure_during_replace_leaves_original_intact_and_no_temp_file(tmp_path: Path, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("original", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_temp_filename_is_dotfile_ending_tmp(tmp_path: Path, monkeypatch):
    """Temp files must never match a `*.json` glob mid-write."""
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["name"] = str(src)
        real_replace(src, dst)

    monkeypatch.setattr("podcast_etl.atomic.os.replace", spy)
    atomic_write_text(tmp_path / "file.json", "{}")
    assert Path(seen["name"]).name.startswith(".file.json.")
    assert seen["name"].endswith(".tmp")


def test_atomic_write_text_respects_umask(tmp_path):
    old = os.umask(0o002)
    try:
        target = tmp_path / "f.json"
        atomic_write_text(target, "{}")
        assert target.stat().st_mode & 0o777 == 0o664
    finally:
        os.umask(old)
