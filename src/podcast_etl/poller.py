from __future__ import annotations

import logging
import signal
import time
from pathlib import Path

from podcast_etl.feed import parse_feed
from podcast_etl.pipeline import Pipeline, PipelineContext, get_step
from podcast_etl.cli import resolve_title_cleaning

logger = logging.getLogger(__name__)


def run_poll_loop(config: dict, config_path: Path) -> None:
    """Run the fetch+pipeline cycle on all feeds, repeating on an interval."""
    interval = config.get("settings", {}).get("poll_interval", 3600)
    output_dir = Path(config.get("settings", {}).get("output_dir", "./output"))
    step_names = config.get("settings", {}).get("pipeline", ["download"])

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
        import yaml

        if config_path.exists():
            try:
                new_config = yaml.safe_load(config_path.read_text()) or config
                from podcast_etl.cli import validate_config
                validate_config(new_config)
                config = new_config
            except yaml.YAMLError as exc:
                logger.error("Failed to parse config %s, using previous config: %s", config_path, exc)
            except SystemExit as exc:
                logger.error("Config validation failed, using previous config: %s", exc)

        feeds = config.get("feeds", [])
        if not feeds:
            logger.warning("No feeds configured")
        else:
            for feed_config in feeds:
                if shutdown:
                    break
                if not feed_config.get("enabled", False):
                    logger.debug("Skipping disabled feed: %s", feed_config.get("name") or feed_config["url"])
                    continue
                url = feed_config["url"]
                try:
                    logger.info("Fetching %s", url)
                    blacklist = config.get("settings", {}).get("blacklist", [])
                    title_cleaning = resolve_title_cleaning(config, feed_config)
                    podcast = parse_feed(url, output_dir=output_dir, blacklist=blacklist, title_cleaning=title_cleaning)
                    podcast.save(output_dir)

                    last = feed_config.get("last") if "last" in feed_config else config.get("settings", {}).get("last")
                    episodes = podcast.episodes[:last] if last else podcast.episodes

                    feed_step_names = feed_config.get("pipeline") or step_names
                    steps = [get_step(name) for name in feed_step_names]
                    context = PipelineContext(output_dir=output_dir, podcast=podcast, config=config, feed_config=feed_config)
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
