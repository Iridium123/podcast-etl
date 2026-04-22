"""SSE log streaming: tail a log file and emit new lines as events."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from markupsafe import escape

# Markup must match templates/fragments/log_tail.html so streamed lines look
# identical to the server-rendered initial batch.
LINE_TEMPLATE = '<div class="font-mono text-xs text-gray-300">{}</div>'


def read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Read complete lines from ``path`` starting at byte ``offset``.

    Returns ``(lines, new_offset)``. A partial trailing line (no terminating
    newline) is held back so the caller picks it up complete on the next call.
    If the file shrank below ``offset`` (rotation/truncation) the read restarts
    from byte 0. A missing file yields ``([], offset)``.
    """
    if not path.exists():
        return [], offset

    size = path.stat().st_size
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset

    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read()

    last_nl = chunk.rfind(b"\n")
    if last_nl == -1:
        return [], offset

    complete = chunk[: last_nl + 1].decode("utf-8", errors="replace")
    return complete.splitlines(), offset + last_nl + 1


def read_tail_lines(path: Path, n: int) -> tuple[list[str], int]:
    """Read the last ``n`` lines from ``path`` for initial dashboard render.

    Returns ``(lines, offset_at_eof)``. Missing file yields ``([], 0)``. The
    offset reflects exactly the bytes read (not a separate ``stat()``), so
    callers that resume tailing from it never overshoot.
    """
    if not path.exists():
        return [], 0
    data = path.read_bytes()
    return data.decode("utf-8", errors="replace").splitlines()[-n:], len(data)


async def tail_log_events(
    path: Path, *, poll_interval: float = 1.0
) -> AsyncIterator[str]:
    """Yield SSE-formatted events for each new log line appended to ``path``.

    Starts tailing from the current end of file, so historical lines (which
    the dashboard renders inline) are not duplicated. Each event wraps the
    HTML-escaped line in the same markup the inline log fragment uses, so
    HTMX ``hx-swap="beforeend"`` produces visually identical rows.
    """
    offset = path.stat().st_size if path.exists() else 0
    while True:
        lines, offset = read_new_lines(path, offset)
        for line in lines:
            # markupsafe.escape matches Jinja2 autoescape byte-for-byte so
            # streamed lines render identically to the inline fragment.
            yield f"data: {LINE_TEMPLATE.format(escape(line))}\n\n"
        await asyncio.sleep(poll_interval)
