from __future__ import annotations

import logging
from pathlib import Path

import feedparser

from podcast_etl.models import Episode, Podcast, slugify
from podcast_etl.text import apply_blacklist, clean_description
from podcast_etl.title_clean import clean_title

logger = logging.getLogger(__name__)


def parse_feed(
    url: str,
    output_dir: Path | None = None,
    blacklist: list[str] | None = None,
    title_cleaning: dict | None = None,
) -> Podcast:
    """Fetch and parse an RSS feed, returning a Podcast with episodes.

    If output_dir is provided and existing episode data is found on disk,
    step status is preserved for known episodes.

    Descriptions are cleaned to plain text. If *blacklist* is provided,
    any description containing a blacklisted string is blanked to null.
    """
    feed = feedparser.parse(url)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Failed to parse feed: {feed.bozo_exception}")

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
        title = clean_title(title, title_cleaning)
        guid = entry.get("id", entry.get("link", title))
        ep_slug = slugify(title)

        # Deduplicate slugs
        base_slug = ep_slug
        counter = 1
        used_slugs = {e.slug for e in episodes}
        while ep_slug in used_slugs:
            counter += 1
            ep_slug = f"{base_slug}-{counter}"

        description = clean_description(entry.get("summary"))
        bl = blacklist or []
        if bl:
            description = apply_blacklist(description, bl)

        episode = Episode(
            title=title,
            guid=guid,
            published=entry.get("published"),
            audio_url=audio_url,
            duration=entry.get("itunes_duration"),
            description=description,
            slug=ep_slug,
        )

        # Preserve step status from existing data
        if guid in existing_episodes:
            episode.status = existing_episodes[guid].status

        episodes.append(episode)

    podcast_description = clean_description(
        feed.feed.get("subtitle") or feed.feed.get("summary")
    )

    podcast = Podcast(
        title=podcast_title,
        url=url,
        description=podcast_description,
        image_url=image_url,
        slug=podcast_slug,
        episodes=episodes,
    )

    logger.info("Parsed feed %s: %d episodes", podcast.title, len(episodes))
    return podcast
