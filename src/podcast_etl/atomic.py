"""Shared atomic-write helper: temp file in the target dir, then os.replace."""

from __future__ import annotations

import os
import threading
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Write content to path atomically, leaving the original untouched on failure."""
    # Unique per writer so concurrent saves never collide; opened normally (not mkstemp,
    # which forces 0600) so file permissions keep following the process umask.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
