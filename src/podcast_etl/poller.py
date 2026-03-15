from __future__ import annotations

import logging
import signal
import time
from pathlib import Path

from podcast_etl.feed import parse_feed
from podcast_etl.pipeline import Pipeline, PipelineContext, get_step

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
            config = yaml.safe_load(config_path.read_text()) or config

        feeds = config.get("feeds", [])
        if not feeds:
            logger.warning("No feeds configured")
        else:
            for feed_config in feeds:
                if shutdown:
                    break
                url = feed_config["url"]
                try:
                    logger.info("Fetching %s", url)
                    blacklist = config.get("settings", {}).get("blacklist", [])
                    podcast = parse_feed(url, output_dir=output_dir, blacklist=blacklist)
                    podcast.save(output_dir)

                    feed_step_names = feed_config.get("pipeline") or step_names
                    steps = [get_step(name) for name in feed_step_names]
                    context = PipelineContext(output_dir=output_dir, podcast=podcast, config=config, feed_config=feed_config)
                    pipeline = Pipeline(steps=steps, context=context)
                    pipeline.run(podcast.episodes)
                    logger.info("Completed %s: %d episodes", podcast.title, len(podcast.episodes))
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
