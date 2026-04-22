"""Tests for the log-tail helper used by the SSE log stream."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from podcast_etl.web.log_stream import (
    read_new_lines,
    read_tail_lines,
    tail_log_events,
)


def test_returns_empty_when_file_missing(tmp_path: Path) -> None:
    lines, new_offset = read_new_lines(tmp_path / "nope.log", 0)
    assert lines == []
    assert new_offset == 0


def test_reads_all_lines_from_offset_zero(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("alpha\nbeta\ngamma\n")
    lines, new_offset = read_new_lines(log, 0)
    assert lines == ["alpha", "beta", "gamma"]
    assert new_offset == log.stat().st_size


def test_returns_only_new_lines_after_offset(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("alpha\nbeta\n")
    _, offset = read_new_lines(log, 0)
    log.write_text("alpha\nbeta\ngamma\n")
    lines, new_offset = read_new_lines(log, offset)
    assert lines == ["gamma"]
    assert new_offset == log.stat().st_size


def test_no_new_content_returns_empty(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("alpha\n")
    _, offset = read_new_lines(log, 0)
    lines, new_offset = read_new_lines(log, offset)
    assert lines == []
    assert new_offset == offset


def test_truncated_file_resets_to_start(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("alpha\nbeta\ngamma\n")
    _, offset = read_new_lines(log, 0)
    log.write_text("delta\n")
    lines, new_offset = read_new_lines(log, offset)
    assert lines == ["delta"]
    assert new_offset == log.stat().st_size


def test_partial_trailing_line_is_held_until_complete(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("alpha\nbeta")
    lines, offset = read_new_lines(log, 0)
    assert lines == ["alpha"]
    assert offset == len("alpha\n")
    log.write_text("alpha\nbeta\n")
    lines, new_offset = read_new_lines(log, offset)
    assert lines == ["beta"]
    assert new_offset == log.stat().st_size


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("")
    lines, new_offset = read_new_lines(log, 0)
    assert lines == []
    assert new_offset == 0


def test_read_tail_lines_returns_all_when_under_limit(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("a\nb\nc\n")
    lines, offset = read_tail_lines(log, n=10)
    assert lines == ["a", "b", "c"]
    assert offset == log.stat().st_size


def test_read_tail_lines_returns_last_n_when_over_limit(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("\n".join(f"line{i}" for i in range(200)) + "\n")
    lines, offset = read_tail_lines(log, n=50)
    assert lines == [f"line{i}" for i in range(150, 200)]
    assert offset == log.stat().st_size


def test_read_tail_lines_missing_file(tmp_path: Path) -> None:
    lines, offset = read_tail_lines(tmp_path / "nope.log", n=10)
    assert lines == []
    assert offset == 0


@pytest.mark.asyncio
async def test_tail_log_events_emits_new_lines_as_sse(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("")

    events: list[str] = []

    async def collect() -> None:
        async for event in tail_log_events(log, poll_interval=0.02):
            events.append(event)
            if len(events) >= 2:
                return

    async def producer() -> None:
        await asyncio.sleep(0.05)
        log.write_text("hello\nworld\n")

    await asyncio.wait_for(asyncio.gather(collect(), producer()), timeout=2.0)

    joined = "".join(events)
    assert "hello" in joined
    assert "world" in joined
    for event in events:
        assert event.startswith("data: ")
        assert event.endswith("\n\n")


@pytest.mark.asyncio
async def test_tail_log_events_html_escapes_line_content(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("")

    events: list[str] = []

    async def collect() -> None:
        async for event in tail_log_events(log, poll_interval=0.02):
            events.append(event)
            return

    async def producer() -> None:
        await asyncio.sleep(0.05)
        log.write_text("<script>alert(1)</script>\n")

    await asyncio.wait_for(asyncio.gather(collect(), producer()), timeout=2.0)

    body = events[0]
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
