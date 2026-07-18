from __future__ import annotations

import logging
from pathlib import Path

import feedparser

from podcast_etl.models import Episode, Podcast, TorrentItem, slugify
from podcast_etl.text import apply_blacklist, clean_description

logger = logging.getLogger(__name__)


def parse_unit3d_feed(
    url: str,
    output_dir: Path | None = None,
    blacklist: list[str] | None = None,
    title_cleaning: dict | None = None,
) -> Podcast:
    """Fetch and parse a UNIT3D tracker RSS feed, returning a Podcast with torrent items.

    Each entry's enclosure is a ``.torrent`` file rather than audio. Entries are
    parsed into ``TorrentItem``s; entries without a torrent enclosure are skipped
    with a warning.

    If *output_dir* is provided, prior state is loaded from disk:

    - Torrent items under ``<slug>/torrents/`` are merged onto feed-present
      entries (``info_hash``, ``episode_guids``, ``fetched_at`` are preserved).
      On-disk items whose guid is no longer in the feed are orphans (the torrent
      was deleted on the tracker) and are excluded from ``torrent_items``.
    - ALL episodes under ``<slug>/episodes/`` are restored into
      ``Podcast.episodes``. This deliberately diverges from ``parse_feed``,
      which only keeps feed-present episodes: torrent-spawned episodes never
      appear in the RSS feed, so every on-disk episode must be restored.

    *title_cleaning* is accepted for signature parity with ``parse_feed`` but is
    unused here — title cleaning applies when episodes are spawned from
    torrents, not at parse time.

    Descriptions are cleaned to plain text. If *blacklist* is provided, any
    description containing a blacklisted string is blanked to null.
    """
    feed = feedparser.parse(url)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Failed to parse feed: {feed.bozo_exception}")

    podcast_title = feed.feed.get("title", "Untitled")
    podcast_slug = slugify(podcast_title)
    image_url = None
    if hasattr(feed.feed, "image") and feed.feed.image:
        image_url = feed.feed.image.get("href")

    # Load existing torrent-item state and all on-disk episodes
    existing_items: dict[str, TorrentItem] = {}
    episodes: list[Episode] = []
    if output_dir:
        torrents_dir = output_dir / podcast_slug / "torrents"
        if torrents_dir.exists():
            for item_path in sorted(torrents_dir.glob("*.json")):
                item = TorrentItem.load(item_path)
                existing_items[item.guid] = item
        episodes_dir = output_dir / podcast_slug / "episodes"
        if episodes_dir.exists():
            for ep_path in sorted(episodes_dir.glob("*.json")):
                episodes.append(Episode.load(ep_path))

    torrent_items = []
    for entry in feed.entries:
        raw_title = entry.get("title", "Untitled")

        torrent_url = None
        for enclosure in entry.get("enclosures", []):
            href = enclosure.get("href", "")
            if enclosure.get("type") == "application/x-bittorrent" or href.endswith(".torrent"):
                torrent_url = href
                break
        if not torrent_url:
            for link in entry.get("links", []):
                if link.get("rel") == "enclosure":
                    torrent_url = link.get("href")
                    break
        if not torrent_url:
            # UNIT3D feeds put the .torrent download URL in the item's plain
            # <link> element (no enclosure at all) — e.g.
            # https://tracker/torrent/download/<id>.<rsskey>
            torrent_url = entry.get("link")
        if not torrent_url:
            logger.warning("Skipping entry with no torrent link: %s", raw_title)
            continue

        guid = entry.get("id", entry.get("link", raw_title))

        description = clean_description(entry.get("summary"))
        bl = blacklist or []
        if bl:
            description = apply_blacklist(description, bl)

        item = TorrentItem(
            guid=guid,
            title=raw_title,
            published=entry.get("published"),
            description=description,
            torrent_url=torrent_url,
        )

        # Preserve lifecycle state from existing data
        if guid in existing_items:
            existing = existing_items[guid]
            item.info_hash = existing.info_hash
            item.episode_guids = existing.episode_guids
            item.fetched_at = existing.fetched_at

        torrent_items.append(item)

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
        torrent_items=torrent_items,
    )

    logger.info(
        "Parsed torrent feed %s: %d torrents, %d on-disk episodes",
        podcast.title,
        len(torrent_items),
        len(episodes),
    )
    return podcast
