from __future__ import annotations

import logging
from pathlib import Path

import feedparser

from podcast_etl.models import Episode, Podcast, slugify

logger = logging.getLogger(__name__)


def parse_feed(url: str, output_dir: Path | None = None) -> Podcast:
    """Fetch and parse an RSS feed, returning a Podcast with episodes.

    If output_dir is provided and existing episode data is found on disk,
    step status is preserved for known episodes.
    """
    feed = feedparser.parse(url)
    print(url)


    podcast_title = feed.feed.get("title", "Untitled")
    podcast_slug = slugify(podcast_title)
    image_url = None
    if hasattr(feed.feed, "image") and feed.feed.image:
        image_url = feed.feed.image.get("href")

    # Load existing episode data to preserve step status
    existing_episodes: dict[str, Episode] = {}
    if output_dir:
        podcast_dir = output_dir / podcast_slug / "episodes"
        if podcast_dir.exists():
            for ep_path in podcast_dir.glob("*.json"):
                ep = Episode.load(ep_path)
                existing_episodes[ep.guid] = ep

    episodes = []
    for entry in feed.entries:
        audio_url = None
        for link in entry.get("links", []):
            if link.get("type", "").startswith("audio/") or link.get("rel") == "enclosure":
                audio_url = link.get("href")
                break
        if not audio_url:
            for enclosure in entry.get("enclosures", []):
                audio_url = enclosure.get("href")
                break

        title = entry.get("title", "Untitled")
        guid = entry.get("id", entry.get("link", title))
        ep_slug = slugify(title)

        # Deduplicate slugs
        base_slug = ep_slug
        counter = 1
        used_slugs = {e.slug for e in episodes}
        while ep_slug in used_slugs:
            counter += 1
            ep_slug = f"{base_slug}-{counter}"

        episode = Episode(
            title=title,
            guid=guid,
            published=entry.get("published"),
            audio_url=audio_url,
            duration=entry.get("itunes_duration"),
            description=entry.get("summary"),
            slug=ep_slug,
        )

        # Preserve step status from existing data
        if guid in existing_episodes:
            episode.status = existing_episodes[guid].status

        episodes.append(episode)

    podcast = Podcast(
        title=podcast_title,
        url=url,
        description=feed.feed.get("subtitle") or feed.feed.get("summary"),
        image_url=image_url,
        slug=podcast_slug,
        episodes=episodes,
    )

    logger.info("Parsed feed %s: %d episodes", podcast.title, len(episodes))
    return podcast
