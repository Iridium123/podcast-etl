from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from podcast_etl.service import filter_episodes, validate_config
from podcast_etl.feed import parse_feed
from podcast_etl.pipeline import Pipeline, PipelineContext, get_step, resolve_feed_config

logger = logging.getLogger(__name__)


@dataclass
class PollControl:
    """Shared state for controlling the async poll loop from web routes."""

    paused: bool = False
    run_now: asyncio.Event = field(default_factory=asyncio.Event)
    shutdown: asyncio.Event = field(default_factory=asyncio.Event)


def run_poll_loop(config: dict, config_path: Path) -> None:
    """Run the fetch+pipeline cycle on all feeds, repeating on an interval."""
    interval = config.get("poll_interval", 3600)
    defaults = config.get("defaults", {})
    output_dir = Path(defaults.get("output_dir", "./output"))

    shutdown = False

    def handle_signal(signum: int, frame: object) -> None:
        nonlocal shutdown
        logger.info("Received signal %d, shutting down after current cycle...", signum)
        shutdown = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("Starting poll loop (interval=%ds)", interval)

    while not shutdown:
        # Reload config each cycle to pick up new feeds
        if config_path.exists():
            try:
                new_config = yaml.safe_load(config_path.read_text()) or config
                validate_config(new_config)
                config = new_config
            except yaml.YAMLError as exc:
                logger.error("Failed to parse config %s, using previous config: %s", config_path, exc)
            except SystemExit as exc:
                logger.error("Config validation failed, using previous config: %s", exc)

        interval = config.get("poll_interval", 3600)
        defaults = config.get("defaults", {})
        output_dir = Path(defaults.get("output_dir", "./output"))

        feeds = config.get("feeds", [])
        if not feeds:
            logger.warning("No feeds configured")
        else:
            for feed_entry in feeds:
                if shutdown:
                    break
                if not feed_entry.get("enabled", False):
                    logger.debug("Skipping disabled feed: %s", feed_entry.get("name") or feed_entry["url"])
                    continue
                url = feed_entry["url"]
                try:
                    logger.info("Fetching %s", url)
                    resolved = resolve_feed_config(defaults, feed_entry)
                    blacklist = resolved.get("blacklist", [])
                    title_cleaning = resolved.get("title_cleaning") or None
                    podcast = parse_feed(url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
                    podcast.save(output_dir)

                    last = resolved.get("last")
                    episode_filter = resolved.get("episode_filter")
                    episodes = filter_episodes(podcast.episodes, last=last, episode_filter=episode_filter)

                    feed_step_names = resolved.get("pipeline") or ["download"]
                    steps = [get_step(name) for name in feed_step_names]
                    context = PipelineContext(output_dir=output_dir, podcast=podcast, config=resolved)
                    pipeline = Pipeline(steps=steps, context=context)
                    pipeline.run(episodes)
                    logger.info("Completed %s: %d episodes processed", podcast.title, len(episodes))
                except Exception:
                    logger.exception("Error processing feed %s", url)

        if shutdown:
            break

        logger.info("Sleeping %ds until next poll...", interval)
        # Sleep in small increments to respond to signals quickly
        for _ in range(interval):
            if shutdown:
                break
            time.sleep(1)

    logger.info("Poll loop stopped")


async def async_poll_loop(config: dict, config_path: Path, control: PollControl) -> None:
    """Async version of run_poll_loop for use as a FastAPI background task.

    Uses asyncio.sleep so the event loop stays responsive. Checks control for
    pause/shutdown/run_now between cycles.
    """
    interval = config.get("poll_interval", 3600)

    logger.info("Starting async poll loop (interval=%ds)", interval)

    while not control.shutdown.is_set():
        # Reload config each cycle to pick up new feeds
        if config_path.exists():
            try:
                new_config = yaml.safe_load(config_path.read_text()) or config
                validate_config(new_config)
                config = new_config
            except yaml.YAMLError as exc:
                logger.error("Failed to parse config %s, using previous config: %s", config_path, exc)
            except SystemExit as exc:
                logger.error("Config validation failed, using previous config: %s", exc)

        interval = config.get("poll_interval", 3600)
        defaults = config.get("defaults", {})
        output_dir = Path(defaults.get("output_dir", "./output"))

        # Acknowledge run_now at the start of each cycle so it's always cleared
        control.run_now.clear()

        if control.paused:
            logger.debug("Poll loop paused, skipping cycle")
        else:
            feeds = config.get("feeds", [])
            if not feeds:
                logger.warning("No feeds configured")
            else:
                for feed_entry in feeds:
                    if control.shutdown.is_set():
                        break
                    if not feed_entry.get("enabled", False):
                        logger.debug("Skipping disabled feed: %s", feed_entry.get("name") or feed_entry["url"])
                        continue
                    url = feed_entry["url"]
                    try:
                        logger.info("Fetching %s", url)
                        resolved = resolve_feed_config(defaults, feed_entry)
                        blacklist = resolved.get("blacklist", [])
                        title_cleaning = resolved.get("title_cleaning") or None
                        podcast = await asyncio.to_thread(
                            parse_feed, url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning
                        )
                        await asyncio.to_thread(podcast.save, output_dir)

                        last = resolved.get("last")
                        episode_filter = resolved.get("episode_filter")
                        episodes = filter_episodes(podcast.episodes, last=last, episode_filter=episode_filter)

                        feed_step_names = resolved.get("pipeline") or ["download"]
                        steps = [get_step(name) for name in feed_step_names]
                        context = PipelineContext(output_dir=output_dir, podcast=podcast, config=resolved)
                        pipeline = Pipeline(steps=steps, context=context)
                        await asyncio.to_thread(pipeline.run, episodes)
                        logger.info("Completed %s: %d episodes processed", podcast.title, len(episodes))
                    except Exception:
                        logger.exception("Error processing feed %s", url)

        if control.shutdown.is_set():
            break

        logger.info("Sleeping %ds until next poll...", interval)
        try:
            await asyncio.wait_for(
                asyncio.shield(control.run_now.wait()),
                timeout=interval,
            )
            logger.debug("run_now triggered, starting cycle immediately")
        except asyncio.TimeoutError:
            pass

    logger.info("Async poll loop stopped")
