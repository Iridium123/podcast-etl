from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

from podcast_etl.feed import parse_feed
from podcast_etl.models import Episode, Podcast
from podcast_etl.pipeline import (
    STEP_REGISTRY,
    Pipeline,
    PipelineContext,
    deep_merge,
    get_step,
    register_step,
    resolve_feed_config,
)
from podcast_etl.steps.audiobookshelf import AudiobookshelfStep
from podcast_etl.steps.detect_ads import DetectAdsStep
from podcast_etl.steps.download import DownloadStep
from podcast_etl.steps.seed import SeedStep
from podcast_etl.steps.stage import StageStep
from podcast_etl.steps.strip_ads import StripAdsStep
from podcast_etl.steps.tag import TagStep
from podcast_etl.steps.torrent import TorrentStep
from podcast_etl.steps.upload import UploadStep

logger = logging.getLogger(__name__)


def _coerce_start_date(value: object) -> date | None:
    """Normalize a raw config value into ``date | None``.

    Accepts ``None``, a ``datetime.date`` instance (PyYAML parses bare ISO
    dates into one), or an ISO-8601 string (e.g. from the web UI). Raises
    ``ValueError`` on an unparseable string and ``TypeError`` on any other
    type. This is the single place the rest of the codebase converts raw
    config values into the typed floor that ``filter_episodes`` expects.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"start_date {value!r} is not a valid ISO date") from exc
    raise TypeError(f"start_date must be a date or ISO string, got {type(value).__name__}")


# Register built-in steps
register_step(DownloadStep())
register_step(TagStep())
register_step(DetectAdsStep())
register_step(StripAdsStep())
register_step(StageStep())
register_step(TorrentStep())
register_step(SeedStep())
register_step(UploadStep())
register_step(AudiobookshelfStep())


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        logger.warning("Config file not found: %s — using defaults", config_path)
        return {"feeds": [], "defaults": {"output_dir": "./output", "pipeline": ["download"]}, "poll_interval": 3600}
    try:
        return yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        logger.error("Failed to parse config file %s: %s", config_path, exc)
        raise SystemExit(1)


def save_config(config: dict, config_path: Path) -> None:
    text = yaml.dump(config, default_flow_style=False, sort_keys=False)
    tmp = config_path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, config_path)


def validate_config(config: dict) -> None:
    """Validate config structure and catch common errors early."""
    defaults = config.get("defaults", {})
    feeds = config.get("feeds", [])
    errors: list[str] = []

    for i, feed in enumerate(feeds):
        feed_label = feed.get("name") or feed.get("url") or f"feeds[{i}]"

        if not feed.get("url"):
            errors.append(f"Feed {feed_label!r}: missing 'url'")

        for step_name in feed.get("pipeline", []):
            if step_name not in STEP_REGISTRY:
                errors.append(f"Feed {feed_label!r}: unknown pipeline step {step_name!r}")

        try:
            deep_merge(defaults, feed)
        except TypeError as exc:
            errors.append(f"Feed {feed_label!r}: {exc}")

        if "start_date" in feed:
            try:
                _coerce_start_date(feed["start_date"])
            except (TypeError, ValueError) as exc:
                errors.append(f"Feed {feed_label!r}: {exc}")

    for step_name in defaults.get("pipeline", []):
        if step_name not in STEP_REGISTRY:
            errors.append(f"defaults.pipeline: unknown step {step_name!r}")

    if "start_date" in defaults:
        try:
            _coerce_start_date(defaults["start_date"])
        except (TypeError, ValueError) as exc:
            errors.append(f"defaults: {exc}")

    if errors:
        raise SystemExit("Config validation failed:\n  " + "\n  ".join(errors))


def get_output_dir(config: dict) -> Path:
    return Path(config.get("defaults", {}).get("output_dir", "./output"))


def find_feed_config(config: dict, identifier: str) -> dict | None:
    """Find a feed config by name or URL."""
    for feed in config.get("feeds", []):
        if feed.get("name") == identifier:
            return feed
    for feed in config.get("feeds", []):
        if feed.get("url") == identifier:
            return feed
    return None


def replace_feed(feeds: list[dict], identifier: str, new_feed: dict) -> list[dict]:
    """Return ``feeds`` with the entry matching ``identifier`` replaced by ``new_feed``.

    Matches by ``name`` or ``url`` field (same semantics as
    :func:`find_feed_config`). Preserves the original ordering. If no
    entry matches, returns the list unchanged — callers that need to
    guarantee the feed exists should pre-check with
    :func:`find_feed_config`.
    """
    return [
        new_feed if (f.get("name") == identifier or f.get("url") == identifier) else f
        for f in feeds
    ]


def find_podcast_dir(output_dir: Path, url: str) -> Path | None:
    """Find the podcast output directory matching the given feed URL."""
    if not output_dir.exists() or not url:
        return None

    for podcast_dir in sorted(output_dir.iterdir()):
        if not podcast_dir.is_dir():
            continue
        podcast_json = podcast_dir / "podcast.json"
        if not podcast_json.exists():
            continue
        try:
            data = json.loads(podcast_json.read_text())
        except Exception:
            continue
        if data.get("url") == url:
            return podcast_dir.resolve()

    return None


def reset_feed_data(output_dir: Path, url: str) -> Path | None:
    """Delete the podcast output directory matching the given feed URL.

    Returns the deleted directory path, or None if no match was found.
    """
    abs_path = find_podcast_dir(output_dir, url)
    if abs_path is None:
        logger.info("No podcast directory found on disk for url=%s", url)
        return None

    logger.info("Deleting podcast directory: %s", abs_path)
    shutil.rmtree(abs_path)
    logger.info("Deleted podcast directory: %s", abs_path)
    return abs_path


def delete_feed(config: dict, config_path: Path, identifier: str) -> tuple[str | None, Path | None]:
    """Remove a feed from config and delete its output directory.

    Returns (url, deleted_dir) on success, or (None, None) if feed not found.
    """
    feed = find_feed_config(config, identifier)
    if feed is None:
        return None, None

    url = feed.get("url", "")
    logger.info("Deleting feed %r (url=%s)", identifier, url)

    # Remove feed from config (match by URL, not identifier)
    config["feeds"] = [f for f in config.get("feeds", []) if f.get("url") != url]
    save_config(config, config_path)
    logger.info("Removed feed %r from config at %s", identifier, config_path)

    # Delete output data
    output_dir = get_output_dir(config)
    deleted_dir = reset_feed_data(output_dir, url)

    return url, deleted_dir


def get_pipeline_steps(resolved_config: dict) -> list[str]:
    return resolved_config.get("pipeline") or ["download"]


def filter_episodes(
    episodes: list[Episode],
    last: int | None = None,
    date_range: tuple[date | None, date | None] | None = None,
    episode_filter: str | None = None,
    start_date: date | None = None,
) -> list[Episode]:
    """Filter episodes by count, publication date range, start-date floor, and/or title regex."""
    if last is not None:
        result = episodes[:last]
    elif date_range is not None:
        start, end = date_range
        result = []
        for ep in episodes:
            if ep.published is None:
                continue
            try:
                pub_date = parsedate_to_datetime(ep.published).date()
            except Exception:
                logger.warning("Skipping episode %r: unable to parse published date %r", ep.title, ep.published)
                continue
            if start is not None and pub_date < start:
                continue
            if end is not None and pub_date > end:
                continue
            result.append(ep)
    else:
        result = episodes
    if start_date is not None:
        floored: list[Episode] = []
        for ep in result:
            if ep.published is None:
                continue
            try:
                pub_date = parsedate_to_datetime(ep.published).date()
            except Exception:
                logger.warning("Skipping episode %r: unable to parse published date %r", ep.title, ep.published)
                continue
            if pub_date < start_date:
                continue
            floored.append(ep)
        result = floored
    if episode_filter is not None:
        pattern = re.compile(episode_filter)
        result = [ep for ep in result if ep.title and pattern.search(ep.title)]
    return result


def fetch_feed(url: str, output_dir: Path, resolved_config: dict) -> Podcast:
    blacklist = resolved_config.get("blacklist", [])
    title_cleaning = resolved_config.get("title_cleaning") or None
    podcast = parse_feed(url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
    podcast.save(output_dir)
    return podcast


def run_pipeline(
    podcast: Podcast,
    output_dir: Path,
    resolved_config: dict,
    step_filter: str | None = None,
    last: int | None = None,
    date_range: tuple[date | None, date | None] | None = None,
    episode_filter: str | None = None,
    overwrite: bool = False,
) -> None:
    step_names = get_pipeline_steps(resolved_config)
    steps = [get_step(name) for name in step_names]
    context = PipelineContext(output_dir=output_dir, podcast=podcast, config=resolved_config, overwrite=overwrite)
    pipeline = Pipeline(steps=steps, context=context)
    ep_filter = episode_filter if episode_filter is not None else resolved_config.get("episode_filter")
    start_date = _coerce_start_date(resolved_config.get("start_date"))
    episodes = filter_episodes(
        podcast.episodes,
        last=last,
        date_range=date_range,
        episode_filter=ep_filter,
        start_date=start_date,
    )
    pipeline.run(episodes, step_filter=step_filter, overwrite=overwrite)


def get_feed_status(output_dir: Path, config: dict) -> list[dict]:
    """Return per-feed status data for the dashboard.

    Each entry contains: title, url, slug, enabled, episode_count,
    completed_count, pending_count, step_names, episodes (list of
    {title, statuses: {step: done|pending}}).
    """
    defaults = config.get("defaults", {})
    result = []
    if not output_dir.exists():
        return result
    for podcast_dir in sorted(output_dir.iterdir()):
        if not podcast_dir.is_dir():
            continue
        podcast_json = podcast_dir / "podcast.json"
        if not podcast_json.exists():
            continue
        podcast = Podcast.load(podcast_dir)
        fc = find_feed_config(config, podcast.url) or {}
        resolved = resolve_feed_config(defaults, fc)
        step_names = get_pipeline_steps(resolved)
        episodes_data = []
        completed = 0
        pending = 0
        for ep in podcast.episodes:
            ep_statuses = {}
            ep_done = True
            for step_name in step_names:
                if ep.status.get(step_name) is not None:
                    ep_statuses[step_name] = "done"
                else:
                    ep_statuses[step_name] = "pending"
                    ep_done = False
            if ep_done:
                completed += 1
            else:
                pending += 1
            episodes_data.append({"title": ep.title, "statuses": ep_statuses})
        result.append({
            "title": podcast.title,
            "url": podcast.url,
            "slug": podcast.slug,
            "enabled": fc.get("enabled", False),
            "name": fc.get("name"),
            "episode_count": len(podcast.episodes),
            "completed_count": completed,
            "pending_count": pending,
            "step_names": step_names,
            "episodes": episodes_data,
        })
    return result


# Known fields that get structured form controls.
# Everything else in a feed/defaults dict goes to the raw YAML editor.
KNOWN_FEED_FIELDS = {
    "url", "name", "enabled", "last", "episode_filter",
    "category_id", "type_id", "pipeline", "title_cleaning",
    "title_override",
}

KNOWN_DEFAULTS_FIELDS = {
    "output_dir", "pipeline", "title_cleaning",
    "blacklist", "torrent_data_dir",
}


def split_config_fields(config: dict, known_fields: set[str]) -> tuple[dict, dict]:
    """Split a config dict into known (form) fields and extra (raw YAML) fields."""
    known = {k: v for k, v in config.items() if k in known_fields}
    extra = {k: v for k, v in config.items() if k not in known_fields}
    return known, extra


def merge_config_fields(known: dict, extra: dict) -> dict:
    """Merge form fields and raw YAML fields back into a single config dict."""
    merged = {}
    merged.update(known)
    merged.update(extra)
    return merged


def get_resolved_config_with_sources(
    defaults: dict, feed: dict
) -> tuple[dict, dict]:
    """Return (resolved_config, source_map).

    source_map is a flat dict mapping dot-separated keys to 'feed' or 'default'.
    E.g. {"tracker.mod_queue_opt_in": "feed", "tracker.url": "default"}
    """
    resolved = resolve_feed_config(defaults, feed)
    source_map: dict[str, str] = {}

    def _walk(base: dict, override: dict, resolved: dict, prefix: str = "") -> None:
        for key in resolved:
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if isinstance(resolved[key], dict):
                _walk(
                    base.get(key, {}) if isinstance(base.get(key), dict) else {},
                    override.get(key, {}) if isinstance(override.get(key), dict) else {},
                    resolved[key],
                    full_key,
                )
            else:
                if key in override:
                    source_map[full_key] = "feed"
                else:
                    source_map[full_key] = "default"

    _walk(defaults, feed, resolved)
    return resolved, source_map
