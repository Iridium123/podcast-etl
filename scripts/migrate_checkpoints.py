#!/usr/bin/env python
"""One-time migration: slug-keyed seed/upload checkpoints -> GUID-keyed.

``seeds/<slug>.json`` and ``uploads/<slug>.json`` used to be keyed on
``episode.slug``. Slug reuse (feed dedup
renumbering, publisher title renames) could poison a new episode's status with
an old episode's cached hash/URL. This script runs
``podcast_etl.checkpoint_migration.migrate_checkpoints`` over every podcast
directory to detect and heal that, replacing legacy checkpoints with
GUID-keyed ones.

The pipeline also runs this automatically (``service.run_pipeline``) before
each pipeline invocation, so running this script by hand is only useful for a
one-off audit or dry-run report.

Usage:
    uv run python scripts/migrate_checkpoints.py --output-dir output/
    uv run python scripts/migrate_checkpoints.py --output-dir output/ --dry-run
    uv run python scripts/migrate_checkpoints.py --output-dir output/ --podcast my-podcast
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running as a plain script (``python scripts/migrate_checkpoints.py``).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from podcast_etl.checkpoint_migration import migrate_checkpoints  # noqa: E402

logger = logging.getLogger("migrate_checkpoints")


def migrate(output_dir: Path, dry_run: bool, podcast: str | None = None) -> int:
    """Run migrate_checkpoints over every podcast dir under output_dir (or just *podcast*).

    Returns the number of podcast dirs with a non-empty report.
    """
    touched = 0
    for podcast_dir in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        if podcast is not None and podcast_dir.name != podcast:
            continue
        report = migrate_checkpoints(podcast_dir, dry_run=dry_run)
        if report.is_empty():
            continue
        touched += 1
        logger.info("%s: %s", podcast_dir.name, report)
        for label in report.poisoned:
            logger.info("  poisoned: %s", label)
        for label in report.suspect:
            logger.info("  suspect: %s", label)
        for label in report.skipped_conflict:
            logger.info("  skipped_conflict: %s", label)
    return touched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output"),
        help="Output directory containing per-podcast folders (default: output/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would change without writing or moving anything",
    )
    parser.add_argument(
        "--podcast", default=None,
        help="Only migrate this podcast's output directory (by slug)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.output_dir.is_dir():
        logger.error("Output directory not found: %s", args.output_dir)
        return 1

    touched = migrate(args.output_dir, args.dry_run, args.podcast)
    verb = "would touch" if args.dry_run else "touched"
    logger.info("Done: %s %d podcast(s).", verb, touched)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
