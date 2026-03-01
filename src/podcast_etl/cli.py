from __future__ import annotations

# Suppress hashlib blake2 errors from pyenv Python builds missing OpenSSL support.
# Must run before importing dependencies that trigger hashlib import.
import logging
logging.disable(logging.ERROR)

import sys
from pathlib import Path

import click
import yaml

from podcast_etl.feed import parse_feed
from podcast_etl.models import Podcast
from podcast_etl.pipeline import Pipeline, PipelineContext, get_step, register_step
from podcast_etl.steps.download import DownloadStep
from podcast_etl.steps.tag import TagStep
from podcast_etl.steps.stage import StageStep
from podcast_etl.steps.torrent import TorrentStep
from podcast_etl.steps.seed import SeedStep
from podcast_etl.steps.upload import UploadStep

# Register built-in steps
register_step(DownloadStep())
register_step(TagStep())
register_step(StageStep())
register_step(TorrentStep())
register_step(SeedStep())
register_step(UploadStep())

DEFAULT_CONFIG_PATH = Path("feeds.yaml")
DEFAULT_OUTPUT_DIR = Path("output")


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {"feeds": [], "settings": {"poll_interval": 3600, "output_dir": "./output", "pipeline": ["download"]}}
    return yaml.safe_load(config_path.read_text()) or {}


def save_config(config: dict, config_path: Path) -> None:
    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))


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


def setup_logging(verbose: bool) -> None:
    logging.disable(logging.NOTSET)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def fetch_feed(url: str, output_dir: Path) -> Podcast:
    podcast = parse_feed(url, output_dir=output_dir)
    podcast.save(output_dir)
    return podcast


def run_pipeline(podcast: Podcast, output_dir: Path, config: dict, feed_config: dict | None = None, step_filter: str | None = None, last: int | None = None, overwrite: bool = False) -> None:
    step_names = get_pipeline_steps(config, feed_config)
    steps = [get_step(name) for name in step_names]
    context = PipelineContext(output_dir=output_dir, podcast=podcast, config=config, feed_config=feed_config or {})
    pipeline = Pipeline(steps=steps, context=context)
    episodes = podcast.episodes[:last] if last is not None else podcast.episodes
    pipeline.run(episodes, step_filter=step_filter, overwrite=overwrite)


@click.group()
@click.option("-c", "--config", "config_path", type=click.Path(path_type=Path), default=DEFAULT_CONFIG_PATH)
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def main(ctx: click.Context, config_path: Path, verbose: bool) -> None:
    """Podcast ETL pipeline — ingest, download, and process podcast feeds."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["config"] = load_config(config_path)


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

    for url in urls:
        click.echo(f"Fetching {url}...")
        podcast = fetch_feed(url, output_dir)
        click.echo(f"  {podcast.title}: {len(podcast.episodes)} episodes")


@main.command()
@click.option("--feed", "feed_url", help="Run pipeline for a specific feed URL")
@click.option("--all", "run_all", is_flag=True, help="Run pipeline for all configured feeds")
@click.option("--step", "step_filter", help="Only run a specific step")
@click.option("--last", "last", type=int, default=None, help="Only process the last N episodes")
@click.option("--overwrite", is_flag=True, help="Re-process episodes even if already completed")
@click.pass_context
def run(ctx: click.Context, feed_url: str | None, run_all: bool, step_filter: str | None, last: int | None, overwrite: bool) -> None:
    """Fetch feeds and run the processing pipeline."""
    config = ctx.obj["config"]
    output_dir = get_output_dir(config)

    if feed_url:
        feed_config = find_feed_config(config, feed_url)
        feeds_to_run = [(feed_config["url"] if feed_config else feed_url, feed_config)]
    elif run_all:
        feeds_to_run = [(f["url"], f) for f in config.get("feeds", [])]
    else:
        click.echo("Specify --feed URL or --all")
        sys.exit(1)

    if not feeds_to_run:
        click.echo("No feeds configured. Use 'podcast-etl add <url>' first.")
        return

    for url, feed_config in feeds_to_run:
        click.echo(f"Processing {url}...")
        podcast = fetch_feed(url, output_dir)
        click.echo(f"  {podcast.title}: {len(podcast.episodes)} episodes")
        run_pipeline(podcast, output_dir, config, feed_config=feed_config, step_filter=step_filter, last=last, overwrite=overwrite)


@main.command()
@click.option("--interval", type=int, help="Poll interval in seconds (overrides config)")
@click.pass_context
def poll(ctx: click.Context, interval: int | None) -> None:
    """Long-running mode: fetch and process feeds on an interval."""
    from podcast_etl.poller import run_poll_loop

    config = ctx.obj["config"]
    if interval:
        config.setdefault("settings", {})["poll_interval"] = interval
    run_poll_loop(config, ctx.obj["config_path"])


@main.command()
@click.option("--feed", "feed_identifier", default=None, help="Feed name or URL to reset")
@click.option("--all", "reset_all", is_flag=True, help="Reset all feeds")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def reset(ctx: click.Context, feed_identifier: str | None, reset_all: bool, yes: bool) -> None:
    """Delete all data for a feed so it can be reprocessed from scratch."""
    import shutil

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
