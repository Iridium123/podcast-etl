#!/usr/bin/env python
"""One-time migration: move embedded detect_ads segments into label files.

Before this refactor, the ``detect_ads`` step stored its result inline in each
episode JSON (``status.detect_ads.result.segments`` + ``audio_duration``). Labels
are now first-class artifacts under ``output/<slug>/labels/<stem>.json``.

For every episode JSON whose detect_ads result still carries embedded
``segments``, this script:

1. Constructs a ``Labels`` object from the embedded data.
2. Writes it to ``output/<slug>/labels/<stem>.json``.
3. Rewrites the detect_ads result to drop ``segments``/``audio_duration`` and add
   ``labels_path``.

Idempotent: episodes already migrated (no embedded ``segments``) are skipped.

Usage:
    uv run python scripts/migrate_labels.py --output-dir output/
    uv run python scripts/migrate_labels.py --output-dir output/ --dry-run

This script is slated for deletion after the one-time migration has run.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Allow running as a plain script (``python scripts/migrate_labels.py``).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from podcast_etl.detectors import AdSegment  # noqa: E402
from podcast_etl.labels import EpisodeRef, Labels, Provenance  # noqa: E402

logger = logging.getLogger("migrate_labels")


def _podcast_slug(podcast_dir: Path) -> str:
    podcast_json = podcast_dir / "podcast.json"
    if podcast_json.is_file():
        try:
            return json.loads(podcast_json.read_text())["slug"]
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning(
                "podcast.json for %s unreadable (%s); falling back to dir name %r as slug",
                podcast_dir, exc, podcast_dir.name,
            )
    return podcast_dir.name


def _labels_stem(detect_result: dict, download_result: dict) -> str:
    """Derive the label file stem, matching detect_ads' ``audio_path.stem``."""
    transcript_path = detect_result.get("transcript_path")
    if transcript_path:
        return Path(transcript_path).stem
    download_path = download_result.get("path", "")
    return Path(download_path).stem


def _build_labels(
    episode_data: dict, episode_json_name: str, podcast_slug: str,
) -> tuple[Labels, str]:
    """Build a Labels object and its relative path from embedded detect_ads data."""
    detect_result = episode_data["status"]["detect_ads"]["result"]
    download_result = episode_data.get("status", {}).get("download", {}).get("result", {}) or {}

    segments = [AdSegment.from_dict(s) for s in detect_result.get("segments", [])]
    audio_duration = detect_result.get("audio_duration", 0.0)
    whisper = detect_result.get("whisper", {})
    llm = detect_result.get("llm", {})
    annotator = llm.get("model") or "unknown"
    created_at = episode_data["status"]["detect_ads"].get("completed_at", "")

    if not whisper and not llm:
        # Pre-refactor results predate recorded whisper/llm provenance; the label
        # file will carry empty provenance + annotator "unknown". Surface it.
        logger.warning(
            "%s: no whisper/llm provenance in old result; writing empty provenance",
            episode_json_name,
        )

    stem = _labels_stem(detect_result, download_result)
    if not stem:
        raise ValueError(
            f"cannot derive label-file stem for {episode_json_name} "
            f"(no transcript_path or download path) — refusing to write labels/.json"
        )

    labels = Labels(
        episode_ref=EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json_name),
        audio_duration=audio_duration,
        segments=segments,
        provenance=Provenance(
            whisper=whisper, llm=llm, annotator=annotator, created_at=created_at,
        ),
    )
    return labels, f"labels/{stem}.json"


def migrate_episode(episode_json: Path, podcast_dir: Path, dry_run: bool) -> bool:
    """Migrate one episode JSON. Returns True if it was (or would be) changed."""
    data = json.loads(episode_json.read_text())
    detect = data.get("status", {}).get("detect_ads")
    if not detect or "segments" not in detect.get("result", {}):
        return False  # not present or already migrated

    podcast_slug = _podcast_slug(podcast_dir)
    labels, labels_relative = _build_labels(data, episode_json.name, podcast_slug)

    logger.info(
        "%s%s -> %s (%d segment(s))",
        "[dry-run] " if dry_run else "",
        episode_json.name,
        labels_relative,
        len(labels.segments),
    )

    if not dry_run:
        # Labels first, then rewrite the episode. A crash between the two leaves
        # an orphan labels file but an un-rewritten episode (still has embedded
        # segments), so a re-run re-migrates and overwrites it — self-healing.
        labels.save(podcast_dir / labels_relative)
        result = detect["result"]
        result.pop("segments", None)
        result.pop("audio_duration", None)
        result["labels_path"] = labels_relative
        _atomic_write(episode_json, json.dumps(data, indent=2) + "\n")

    return True


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def migrate(output_dir: Path, dry_run: bool) -> tuple[int, int]:
    """Migrate all episodes under *output_dir*.

    Returns ``(changed, failed)``. A malformed/unwritable episode is logged and
    skipped rather than aborting the whole run, so one bad file can't strand the
    rest half-migrated.
    """
    changed = 0
    failed = 0
    for podcast_dir in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        episodes_dir = podcast_dir / "episodes"
        if not episodes_dir.is_dir():
            continue
        for episode_json in sorted(episodes_dir.glob("*.json")):
            try:
                if migrate_episode(episode_json, podcast_dir, dry_run):
                    changed += 1
            except Exception as exc:
                failed += 1
                logger.error("FAILED %s: %s", episode_json, exc)
    return changed, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output"),
        help="Output directory containing per-podcast folders (default: output/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would change without writing anything",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.output_dir.is_dir():
        logger.error("Output directory not found: %s", args.output_dir)
        return 1

    changed, failed = migrate(args.output_dir, args.dry_run)
    verb = "would migrate" if args.dry_run else "migrated"
    logger.info("Done: %s %d episode(s), %d failed.", verb, changed, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
