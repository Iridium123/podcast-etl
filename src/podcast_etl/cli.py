from __future__ import annotations

# Suppress hashlib blake2 errors from pyenv Python builds missing OpenSSL support.
# Must run before importing dependencies that trigger hashlib import.
import logging
logging.disable(logging.ERROR)

import os
import re
import shutil
import sys
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

import click
import yaml

from podcast_etl.feed import parse_feed
from podcast_etl.models import Episode, Podcast

logger = logging.getLogger(__name__)
from podcast_etl.pipeline import Pipeline, PipelineContext, STEP_REGISTRY, get_step, deep_merge, register_step, resolve_feed_config
from podcast_etl.steps.download import DownloadStep
from podcast_etl.steps.tag import TagStep
from podcast_etl.steps.stage import StageStep
from podcast_etl.steps.torrent import TorrentStep
from podcast_etl.steps.seed import SeedStep
from podcast_etl.steps.upload import UploadStep
from podcast_etl.steps.detect_ads import DetectAdsStep
from podcast_etl.steps.strip_ads import StripAdsStep
from podcast_etl.steps.audiobookshelf import AudiobookshelfStep

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

DEFAULT_CONFIG_PATH = Path("feeds.yaml")
DEFAULT_OUTPUT_DIR = Path("output")


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

        # deep_merge is called on the full feed dict (including metadata keys
        # like url/name/enabled).  This is safe because defaults doesn't define
        # those keys as dicts, so no spurious type-mismatch errors fire.
        try:
            deep_merge(defaults, feed)
        except TypeError as exc:
            errors.append(f"Feed {feed_label!r}: {exc}")

    for step_name in defaults.get("pipeline", []):
        if step_name not in STEP_REGISTRY:
            errors.append(f"defaults.pipeline: unknown step {step_name!r}")

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


def reset_feed_data(output_dir: Path, url: str) -> Path | None:
    """Delete the podcast output directory matching the given feed URL.

    Returns the deleted directory path, or None if no match was found.
    """
    if not output_dir.exists() or not url:
        return None

    for podcast_dir in sorted(output_dir.iterdir()):
        if not podcast_dir.is_dir():
            continue
        podcast_json = podcast_dir / "podcast.json"
        if not podcast_json.exists():
            continue
        try:
            podcast = Podcast.load(podcast_dir)
        except Exception:
            continue
        if podcast.url == url:
            abs_path = podcast_dir.resolve()
            logger.info("Deleting podcast directory: %s", abs_path)
            shutil.rmtree(abs_path, ignore_errors=True)
            return abs_path

    return None


def delete_feed(config: dict, config_path: Path, identifier: str) -> tuple[str | None, Path | None]:
    """Remove a feed from config and delete its output directory.

    Returns (url, deleted_dir) on success, or (None, None) if feed not found.
    """
    feed = find_feed_config(config, identifier)
    if feed is None:
        return None, None

    url = feed.get("url", "")
    logger.info("Deleting feed %r (url=%s)", identifier, url)

    config["feeds"] = [
        f for f in config.get("feeds", [])
        if f.get("name") != identifier and f.get("url") != identifier
    ]
    save_config(config, config_path)
    logger.info("Removed feed %r from config at %s", identifier, config_path)

    output_dir = get_output_dir(config)
    deleted_dir = reset_feed_data(output_dir, url)

    return url, deleted_dir


def get_pipeline_steps(resolved_config: dict) -> list[str]:
    return resolved_config.get("pipeline") or ["download"]


def setup_logging(level: str) -> None:
    logging.disable(logging.NOTSET)
    logging.basicConfig(
        force=True,
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def fetch_feed(url: str, output_dir: Path, resolved_config: dict) -> Podcast:
    blacklist = resolved_config.get("blacklist", [])
    title_cleaning = resolved_config.get("title_cleaning") or None
    podcast = parse_feed(url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
    podcast.save(output_dir)
    return podcast


def parse_date_range(value: str) -> tuple[date | None, date | None]:
    """Parse a date or date range string into (start, end) bounds.

    Supported formats:
      ``2026-03-01``          — single date (start == end)
      ``2026-03-01..2026-03-05`` — closed range
      ``2026-03-01..``        — open-ended (start only)
      ``..2026-03-05``        — open-started (end only)
    """
    if ".." in value:
        left, right = value.split("..", 1)
        if not left and not right:
            raise click.BadParameter("Date range must have at least one bound")
        start = date.fromisoformat(left) if left else None
        end = date.fromisoformat(right) if right else None
        if start is not None and end is not None and start > end:
            raise click.BadParameter(f"Start date {start} is after end date {end}")
        return start, end
    d = date.fromisoformat(value)
    return d, d


def filter_episodes(episodes: list[Episode], last: int | None = None, date_range: tuple[date | None, date | None] | None = None, episode_filter: str | None = None) -> list[Episode]:
    """Filter episodes by count, publication date range, and/or title regex.

    ``last`` keeps the first *N* episodes (RSS feeds typically list newest
    first).  ``date_range`` is a (start, end) tuple where either bound can
    be ``None`` for an open-ended range.  ``last`` and ``date_range`` are
    mutually exclusive – the caller is responsible for ensuring at most one
    is set.  ``episode_filter`` is a regex applied via ``re.search`` against
    the episode title and can be combined with either of the other filters.
    """
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
    if episode_filter is not None:
        pattern = re.compile(episode_filter)
        result = [ep for ep in result if ep.title and pattern.search(ep.title)]
    return result


def run_pipeline(podcast: Podcast, output_dir: Path, resolved_config: dict, step_filter: str | None = None, last: int | None = None, date_range: tuple[date | None, date | None] | None = None, episode_filter: str | None = None, overwrite: bool = False) -> None:
    step_names = get_pipeline_steps(resolved_config)
    steps = [get_step(name) for name in step_names]
    context = PipelineContext(output_dir=output_dir, podcast=podcast, config=resolved_config, overwrite=overwrite)
    pipeline = Pipeline(steps=steps, context=context)
    ep_filter = episode_filter if episode_filter is not None else resolved_config.get("episode_filter")
    episodes = filter_episodes(podcast.episodes, last=last, date_range=date_range, episode_filter=ep_filter)
    pipeline.run(episodes, step_filter=step_filter, overwrite=overwrite)


@click.group()
@click.option("-c", "--config", "config_path", type=click.Path(path_type=Path), default=DEFAULT_CONFIG_PATH)
@click.option("-v", "--verbose", is_flag=True, help="Shorthand for --log-level DEBUG")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False), default="INFO", show_default=True, help="Set log verbosity")
@click.pass_context
def main(ctx: click.Context, config_path: Path, verbose: bool, log_level: str) -> None:
    """Podcast ETL pipeline — ingest, download, and process podcast feeds."""
    if verbose:
        log_level = "DEBUG"
    setup_logging(log_level)
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["config"] = load_config(config_path)
    validate_config(ctx.obj["config"])


@main.command()
@click.argument("feed_url")
@click.option("--name", help="Short name for the feed")
@click.option("--step", "steps", multiple=True, help="Pipeline steps for this feed (repeatable)")
@click.pass_context
def add(ctx: click.Context, feed_url: str, name: str | None, steps: tuple[str, ...]) -> None:
    """Add a feed URL to the config."""
    config = ctx.obj["config"]
    config.setdefault("feeds", [])
    config.setdefault("defaults", {"output_dir": "./output", "pipeline": ["download"]})
    config.setdefault("poll_interval", 3600)

    for feed in config["feeds"]:
        if feed["url"] == feed_url:
            click.echo(f"Feed already exists: {feed_url}")
            return

    entry: dict = {"url": feed_url}
    if name:
        entry["name"] = name
    if steps:
        entry["pipeline"] = list(steps)
    config["feeds"].append(entry)
    save_config(config, ctx.obj["config_path"])
    click.echo(f"Added feed: {feed_url}")


@main.command()
@click.option("--feed", "feed_url", help="Fetch a specific feed URL")
@click.option("--all", "fetch_all", is_flag=True, help="Fetch all configured feeds")
@click.pass_context
def fetch(ctx: click.Context, feed_url: str | None, fetch_all: bool) -> None:
    """Fetch feed metadata and save to disk (no processing)."""
    config = ctx.obj["config"]
    output_dir = get_output_dir(config)

    if feed_url:
        feed_config = find_feed_config(config, feed_url)
        urls = [feed_config["url"] if feed_config else feed_url]
    elif fetch_all:
        urls = [f["url"] for f in config.get("feeds", [])]
    else:
        click.echo("Specify --feed URL or --all")
        sys.exit(1)

    if not urls:
        click.echo("No feeds configured. Use 'podcast-etl add <url>' first.")
        return

    defaults = config.get("defaults", {})
    for url in urls:
        fc = find_feed_config(config, url)
        resolved = resolve_feed_config(defaults, fc or {"url": url})
        click.echo(f"Fetching {url}...")
        podcast = fetch_feed(url, output_dir, resolved)
        click.echo(f"  {podcast.title}: {len(podcast.episodes)} episodes")


@main.command()
@click.option("--feed", "feed_url", help="Run pipeline for a specific feed URL")
@click.option("--all", "run_all", is_flag=True, help="Run pipeline for all configured feeds")
@click.option("--step", "step_filter", help="Only run a specific step")
@click.option("--last", "last", type=int, default=None, help="Only process the last N episodes")
@click.option("--date", "date_str", default=None, help="Filter by date: YYYY-MM-DD, START..END, START.., or ..END")
@click.option("--filter", "episode_filter", default=None, help="Only process episodes whose title matches this regex")
@click.option("--overwrite", is_flag=True, help="Re-process episodes even if already completed")
@click.pass_context
def run(ctx: click.Context, feed_url: str | None, run_all: bool, step_filter: str | None, last: int | None, date_str: str | None, episode_filter: str | None, overwrite: bool) -> None:
    """Fetch feeds and run the processing pipeline."""
    if last is not None and date_str is not None:
        raise click.UsageError("Cannot use --last and --date together.")

    date_range = None
    if date_str is not None:
        try:
            date_range = parse_date_range(date_str)
        except (ValueError, click.BadParameter) as exc:
            raise click.BadParameter(str(exc), param_hint="'--date'")

    if episode_filter is not None:
        try:
            re.compile(episode_filter)
        except re.error as exc:
            raise click.BadParameter(str(exc), param_hint="'--filter'")

    config = ctx.obj["config"]
    output_dir = get_output_dir(config)

    if feed_url:
        fc = find_feed_config(config, feed_url)
        feeds_to_run = [(fc["url"] if fc else feed_url, fc)]
    elif run_all:
        feeds_to_run = [(f["url"], f) for f in config.get("feeds", [])]
    else:
        click.echo("Specify --feed URL or --all")
        sys.exit(1)

    if not feeds_to_run:
        click.echo("No feeds configured. Use 'podcast-etl add <url>' first.")
        return

    defaults = config.get("defaults", {})
    for url, fc in feeds_to_run:
        resolved = resolve_feed_config(defaults, fc or {"url": url})
        click.echo(f"Processing {url}...")
        podcast = fetch_feed(url, output_dir, resolved)
        click.echo(f"  {podcast.title}: {len(podcast.episodes)} episodes")
        run_pipeline(podcast, output_dir, resolved, step_filter=step_filter, last=last, date_range=date_range, episode_filter=episode_filter, overwrite=overwrite)


@main.command()
@click.option("--interval", type=int, help="Poll interval in seconds (overrides config)")
@click.pass_context
def poll(ctx: click.Context, interval: int | None) -> None:
    """Long-running mode: fetch and process feeds on an interval."""
    from podcast_etl.poller import run_poll_loop

    config = ctx.obj["config"]
    config_path = ctx.obj["config_path"]
    enabled_feeds = [f for f in config.get("feeds", []) if f.get("enabled", False)]
    logger.info("Config loaded from %s: %d feeds configured, %d enabled for polling", config_path, len(config.get("feeds", [])), len(enabled_feeds))
    if interval:
        config["poll_interval"] = interval
    run_poll_loop(config, config_path)


@main.command()
@click.option("--feed", "feed_identifier", default=None, help="Feed name or URL to reset")
@click.option("--all", "reset_all", is_flag=True, help="Reset all feeds")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def reset(ctx: click.Context, feed_identifier: str | None, reset_all: bool, yes: bool) -> None:
    """Delete all data for a feed so it can be reprocessed from scratch."""
    config = ctx.obj["config"]
    output_dir = get_output_dir(config)

    if not feed_identifier and not reset_all:
        click.echo("Specify --feed NAME or --all")
        sys.exit(1)

    target_dirs: list[Path] = []
    if output_dir.exists():
        for d in sorted(output_dir.iterdir()):
            if not d.is_dir() or not (d / "podcast.json").exists():
                continue
            if reset_all:
                target_dirs.append(d)
            else:
                try:
                    podcast = Podcast.load(d)
                except Exception:
                    continue
                fc = find_feed_config(config, feed_identifier)  # type: ignore[arg-type]
                resolved_url = fc["url"] if fc else feed_identifier
                if podcast.url == resolved_url:
                    target_dirs.append(d)
                    break

    if not target_dirs:
        click.echo(f"No data found for {'all feeds' if reset_all else feed_identifier}")
        return

    if not yes:
        dirs_display = ", ".join(str(d) for d in target_dirs)
        click.confirm(f"Delete all data in {dirs_display}? This cannot be undone.", abort=True)

    for d in target_dirs:
        shutil.rmtree(d)
        click.echo(f"Deleted {d}")


@main.command()
@click.argument("feed_identifier")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def delete(ctx: click.Context, feed_identifier: str, yes: bool) -> None:
    """Remove a feed from config and delete all its data."""
    config = ctx.obj["config"]
    config_path = ctx.obj["config_path"]

    feed = find_feed_config(config, feed_identifier)
    if not feed:
        click.echo(f"Feed not found: {feed_identifier}")
        sys.exit(1)

    feed_name = feed.get("name") or feed.get("url")
    if not yes:
        click.confirm(
            f"Delete feed {feed_name!r} and all its data? This cannot be undone.",
            abort=True,
        )

    url, deleted_dir = delete_feed(config, config_path, feed_identifier)
    click.echo(f"Removed feed {feed_name!r} from config")
    if deleted_dir:
        click.echo(f"Deleted data directory: {deleted_dir}")
    else:
        click.echo("No data directory found on disk")


@main.command()
@click.option("--feed", "feed_url", help="Show status for a specific feed")
@click.pass_context
def status(ctx: click.Context, feed_url: str | None) -> None:
    """Show per-episode step completion status."""
    config = ctx.obj["config"]
    output_dir = get_output_dir(config)

    if not output_dir.exists():
        click.echo("No output directory found. Run 'podcast-etl fetch' first.")
        return

    podcast_dirs = sorted(output_dir.iterdir()) if not feed_url else []
    resolved_feed_config: dict | None = None
    if feed_url:
        resolved_feed_config = find_feed_config(config, feed_url)
        resolved_url = resolved_feed_config["url"] if resolved_feed_config else feed_url
        # Find the podcast dir matching this feed URL
        for d in output_dir.iterdir():
            if not d.is_dir():
                continue
            podcast_json = d / "podcast.json"
            if podcast_json.exists():
                podcast = Podcast.load(d)
                if podcast.url == resolved_url:
                    podcast_dirs = [d]
                    break
        if not podcast_dirs:
            click.echo(f"No data found for feed: {feed_url}")
            return

    defaults = config.get("defaults", {})

    for podcast_dir in podcast_dirs:
        if not podcast_dir.is_dir():
            continue
        podcast_json = podcast_dir / "podcast.json"
        if not podcast_json.exists():
            continue
        podcast = Podcast.load(podcast_dir)
        fc = find_feed_config(config, podcast.url) or {}
        resolved = resolve_feed_config(defaults, fc)
        step_names = get_pipeline_steps(resolved)
        click.echo(f"\n{podcast.title} ({len(podcast.episodes)} episodes)")
        click.echo(f"  {'Episode':<40} " + " ".join(f"{s:<12}" for s in step_names))
        click.echo(f"  {'─' * 40} " + " ".join("─" * 12 for _ in step_names))
        for ep in podcast.episodes:
            statuses = []
            for step_name in step_names:
                step_status = ep.status.get(step_name)
                if step_status is not None:
                    statuses.append("done")
                else:
                    statuses.append("pending")
            title_display = ep.title[:38] + ".." if len(ep.title) > 40 else ep.title
            click.echo(f"  {title_display:<40} " + " ".join(f"{s:<12}" for s in statuses))
