from __future__ import annotations

# Suppress hashlib blake2 errors from pyenv Python builds missing OpenSSL support.
# Must run before importing dependencies that trigger hashlib import.
import logging
logging.disable(logging.ERROR)

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
from podcast_etl.pipeline import Pipeline, PipelineContext, STEP_REGISTRY, get_step, merge_config, register_step
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
        return {"feeds": [], "settings": {"poll_interval": 3600, "output_dir": "./output", "pipeline": ["download"]}}
    try:
        return yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        logger.error("Failed to parse config file %s: %s", config_path, exc)
        raise SystemExit(1)


def save_config(config: dict, config_path: Path) -> None:
    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))


def validate_config(config: dict) -> None:
    """Validate config structure and catch common errors early."""
    settings = config.get("settings", {})
    feeds = config.get("feeds", [])
    errors: list[str] = []

    # Validate settings types
    _validate_settings_types(settings, errors)

    # Validate each feed entry
    for i, feed in enumerate(feeds):
        feed_label = feed.get("name") or feed.get("url") or f"feeds[{i}]"

        if not feed.get("url"):
            errors.append(f"Feed {feed_label!r}: missing 'url'")

        # pipeline must be a list, not a bare string
        feed_pipeline = feed.get("pipeline")
        if feed_pipeline is not None and not isinstance(feed_pipeline, list):
            errors.append(f"Feed {feed_label!r}: 'pipeline' must be a list, not {type(feed_pipeline).__name__}")
        else:
            # Check pipeline step names are registered
            for step_name in feed.get("pipeline", []):
                if step_name not in STEP_REGISTRY:
                    errors.append(f"Feed {feed_label!r}: unknown pipeline step {step_name!r}")

        # Check tracker/client references exist
        tracker_name = feed.get("tracker")
        if tracker_name and tracker_name not in settings.get("trackers", {}):
            errors.append(f"Feed {feed_label!r}: tracker {tracker_name!r} not found in settings.trackers")

        client_name = feed.get("client")
        if client_name and client_name not in settings.get("clients", {}):
            errors.append(f"Feed {feed_label!r}: client {client_name!r} not found in settings.clients")

        # Check feed override type compatibility
        _validate_feed_overrides(feed, settings, feed_label, errors)

        # Check required config for steps in this feed's pipeline
        feed_steps = feed.get("pipeline") if isinstance(feed.get("pipeline"), list) else None
        if feed_steps:
            _validate_step_requirements(feed_steps, feed, feed_label, settings, errors)

    # Check global pipeline step names and requirements
    global_pipeline = settings.get("pipeline", [])
    if global_pipeline and not isinstance(global_pipeline, list):
        errors.append(f"settings.pipeline: must be a list, not {type(global_pipeline).__name__}")
    else:
        for step_name in global_pipeline:
            if step_name not in STEP_REGISTRY:
                errors.append(f"settings.pipeline: unknown step {step_name!r}")
        # Validate global pipeline requirements once (applies to feeds without their own pipeline)
        feeds_using_global = [f for f in feeds if not isinstance(f.get("pipeline"), list)]
        if feeds_using_global and isinstance(global_pipeline, list):
            _validate_step_requirements(global_pipeline, {}, "settings.pipeline (global)", settings, errors)

    if errors:
        raise SystemExit("Config validation failed:\n  " + "\n  ".join(errors))


def _validate_settings_types(settings: dict, errors: list[str]) -> None:
    """Validate types of common settings values."""
    output_dir = settings.get("output_dir")
    if output_dir is not None and not isinstance(output_dir, str):
        errors.append(f"settings.output_dir: must be a string, got {type(output_dir).__name__}")

    poll_interval = settings.get("poll_interval")
    if poll_interval is not None:
        if not isinstance(poll_interval, int) or isinstance(poll_interval, bool):
            errors.append(f"settings.poll_interval: must be a positive integer, got {type(poll_interval).__name__}")
        elif poll_interval <= 0:
            errors.append(f"settings.poll_interval: must be a positive integer, got {poll_interval}")

    # ad_detection sub-keys must be dicts, not scalars
    ad_detection = settings.get("ad_detection")
    if isinstance(ad_detection, dict):
        for key in ("whisper", "llm"):
            val = ad_detection.get(key)
            if val is not None and not isinstance(val, dict):
                errors.append(f"settings.ad_detection.{key}: must be a mapping, not {type(val).__name__}")

    # Validate client required keys
    for name, client_cfg in settings.get("clients", {}).items():
        if not isinstance(client_cfg, dict):
            errors.append(f"settings.clients.{name}: must be a mapping")
            continue
        for key in ("url", "username", "password"):
            if not client_cfg.get(key):
                errors.append(f"settings.clients.{name}: missing required key {key!r}")

    # Validate tracker required keys
    for name, tracker_cfg in settings.get("trackers", {}).items():
        if not isinstance(tracker_cfg, dict):
            errors.append(f"settings.trackers.{name}: must be a mapping")
            continue
        for key in ("url", "announce_url"):
            if not tracker_cfg.get(key):
                errors.append(f"settings.trackers.{name}: missing required key {key!r}")
        has_cookie = tracker_cfg.get("remember_cookie")
        has_login = tracker_cfg.get("username") and tracker_cfg.get("password")
        if not has_cookie and not has_login:
            errors.append(f"settings.trackers.{name}: must specify 'remember_cookie' or both 'username' and 'password'")


def _validate_step_requirements(
    step_names: list[str], feed: dict, feed_label: str, settings: dict, errors: list[str],
) -> None:
    """Check that required config exists for steps in the pipeline."""
    step_set = set(step_names)

    if step_set & {"stage", "torrent", "seed"} and not settings.get("torrent_data_dir"):
        errors.append(f"Feed {feed_label!r}: pipeline includes stage/torrent/seed but settings.torrent_data_dir is not set")

    if "upload" in step_set:
        if not feed.get("category_id"):
            errors.append(f"Feed {feed_label!r}: pipeline includes 'upload' but 'category_id' is not set")
        if not feed.get("type_id"):
            errors.append(f"Feed {feed_label!r}: pipeline includes 'upload' but 'type_id' is not set")


def _validate_feed_overrides(
    feed: dict, settings: dict, feed_label: str, errors: list[str],
) -> None:
    """Check that per-feed override sections have compatible types with global settings."""
    for section in ("ad_detection", "audiobookshelf"):
        global_cfg = settings.get(section, {})
        feed_cfg = feed.get(section, {})
        if global_cfg and feed_cfg:
            try:
                merge_config(global_cfg, feed_cfg)
            except TypeError as exc:
                errors.append(f"Feed {feed_label!r}, {section}: {exc}")

    feed_tracker_overrides = feed.get("tracker_config", {})
    if feed_tracker_overrides:
        tracker_name = feed.get("tracker")
        trackers = settings.get("trackers", {})
        if tracker_name:
            tracker_cfg = trackers.get(tracker_name, {})
        else:
            tracker_cfg = next(iter(trackers.values()), {}) if trackers else {}
        if tracker_cfg:
            try:
                merge_config(tracker_cfg, feed_tracker_overrides)
            except TypeError as exc:
                errors.append(f"Feed {feed_label!r}, tracker_config: {exc}")


def get_output_dir(config: dict) -> Path:
    return Path(config.get("settings", {}).get("output_dir", "./output"))


def find_feed_config(config: dict, identifier: str) -> dict | None:
    """Find a feed config by name or URL."""
    for feed in config.get("feeds", []):
        if feed.get("name") == identifier:
            return feed
    for feed in config.get("feeds", []):
        if feed.get("url") == identifier:
            return feed
    return None


def get_pipeline_steps(config: dict, feed_config: dict | None = None) -> list[str]:
    if feed_config and feed_config.get("pipeline"):
        return feed_config["pipeline"]
    return config.get("settings", {}).get("pipeline", ["download"])


def setup_logging(level: str) -> None:
    logging.disable(logging.NOTSET)
    logging.basicConfig(
        force=True,
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def fetch_feed(
    url: str,
    output_dir: Path,
    blacklist: list[str] | None = None,
) -> Podcast:
    podcast = parse_feed(url, output_dir=output_dir, blacklist=blacklist)
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


def filter_episodes(episodes: list[Episode], last: int | None = None, date_range: tuple[date | None, date | None] | None = None) -> list[Episode]:
    """Filter episodes by count or publication date range.

    ``last`` keeps the first *N* episodes (RSS feeds typically list newest
    first).  ``date_range`` is a (start, end) tuple where either bound can
    be ``None`` for an open-ended range.  The two filters are mutually
    exclusive – the caller is responsible for ensuring at most one is set.
    """
    if last is not None:
        return episodes[:last]
    if date_range is not None:
        start, end = date_range
        filtered = []
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
            filtered.append(ep)
        return filtered
    return episodes


def run_pipeline(podcast: Podcast, output_dir: Path, config: dict, feed_config: dict | None = None, step_filter: str | None = None, last: int | None = None, date_range: tuple[date | None, date | None] | None = None, overwrite: bool = False) -> None:
    step_names = get_pipeline_steps(config, feed_config)
    steps = [get_step(name) for name in step_names]
    context = PipelineContext(output_dir=output_dir, podcast=podcast, config=config, feed_config=feed_config or {}, overwrite=overwrite)
    pipeline = Pipeline(steps=steps, context=context)
    episodes = filter_episodes(podcast.episodes, last=last, date_range=date_range)
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
    config.setdefault("settings", {"poll_interval": 3600, "output_dir": "./output", "pipeline": ["download"]})

    for feed in config["feeds"]:
        if feed["url"] == feed_url:
            click.echo(f"Feed already exists: {feed_url}")
            return

    if steps:
        unknown = [s for s in steps if s not in STEP_REGISTRY]
        if unknown:
            raise click.UsageError(f"Unknown pipeline step(s): {', '.join(unknown)}. Available: {', '.join(STEP_REGISTRY)}")

    if name:
        existing_names = [f.get("name") for f in config["feeds"] if f.get("name")]
        if name in existing_names:
            raise click.UsageError(f"Feed name {name!r} is already used by another feed")

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
        if feed_config is None and not feed_url.startswith(("http://", "https://")):
            raise click.UsageError(
                f"Feed {feed_url!r} not found in config. Use a feed name, URL, or 'podcast-etl add' to add it first."
            )
        urls = [feed_config["url"] if feed_config else feed_url]
    elif fetch_all:
        urls = [f["url"] for f in config.get("feeds", [])]
    else:
        click.echo("Specify --feed URL or --all")
        sys.exit(1)

    if not urls:
        click.echo("No feeds configured. Use 'podcast-etl add <url>' first.")
        return

    blacklist = config.get("settings", {}).get("blacklist", [])
    for url in urls:
        click.echo(f"Fetching {url}...")
        try:
            podcast = fetch_feed(url, output_dir, blacklist=blacklist)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"  {podcast.title}: {len(podcast.episodes)} episodes")


@main.command()
@click.option("--feed", "feed_url", help="Run pipeline for a specific feed URL")
@click.option("--all", "run_all", is_flag=True, help="Run pipeline for all configured feeds")
@click.option("--step", "step_filter", help="Only run a specific step")
@click.option("--last", "last", type=int, default=None, help="Only process the last N episodes")
@click.option("--date", "date_str", default=None, help="Filter by date: YYYY-MM-DD, START..END, START.., or ..END")
@click.option("--overwrite", is_flag=True, help="Re-process episodes even if already completed")
@click.pass_context
def run(ctx: click.Context, feed_url: str | None, run_all: bool, step_filter: str | None, last: int | None, date_str: str | None, overwrite: bool) -> None:
    """Fetch feeds and run the processing pipeline."""
    if last is not None and date_str is not None:
        raise click.UsageError("Cannot use --last and --date together.")

    date_range = None
    if date_str is not None:
        try:
            date_range = parse_date_range(date_str)
        except (ValueError, click.BadParameter) as exc:
            raise click.BadParameter(str(exc), param_hint="'--date'")

    config = ctx.obj["config"]
    output_dir = get_output_dir(config)

    if feed_url:
        feed_config = find_feed_config(config, feed_url)
        if feed_config is None and not feed_url.startswith(("http://", "https://")):
            raise click.UsageError(
                f"Feed {feed_url!r} not found in config. Use a feed name, URL, or 'podcast-etl add' to add it first."
            )
        feeds_to_run = [(feed_config["url"] if feed_config else feed_url, feed_config)]
    elif run_all:
        feeds_to_run = [(f["url"], f) for f in config.get("feeds", [])]
    else:
        click.echo("Specify --feed URL or --all")
        sys.exit(1)

    if not feeds_to_run:
        click.echo("No feeds configured. Use 'podcast-etl add <url>' first.")
        return

    blacklist = config.get("settings", {}).get("blacklist", [])
    for url, feed_config in feeds_to_run:
        click.echo(f"Processing {url}...")
        try:
            podcast = fetch_feed(url, output_dir, blacklist=blacklist)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"  {podcast.title}: {len(podcast.episodes)} episodes")
        try:
            run_pipeline(podcast, output_dir, config, feed_config=feed_config, step_filter=step_filter, last=last, date_range=date_range, overwrite=overwrite)
        except ValueError as exc:
            raise click.ClickException(str(exc))


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
        config.setdefault("settings", {})["poll_interval"] = interval
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
                podcast = Podcast.load(d)
                feed_config = find_feed_config(config, feed_identifier)  # type: ignore[arg-type]
                resolved_url = feed_config["url"] if feed_config else feed_identifier
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

    step_names = get_pipeline_steps(config, resolved_feed_config)

    for podcast_dir in podcast_dirs:
        if not podcast_dir.is_dir():
            continue
        podcast_json = podcast_dir / "podcast.json"
        if not podcast_json.exists():
            continue
        podcast = Podcast.load(podcast_dir)
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
