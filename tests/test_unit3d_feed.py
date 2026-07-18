from podcast_etl.models import Episode, TorrentItem
from podcast_etl.unit3d_feed import parse_unit3d_feed

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _rss(*items: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Tracker - My Podcast</title>
<description>Tracker feed</description>
{"".join(items)}
</channel></rss>"""


def _item(num: int, title: str | None = None, enclosure: bool = True,
          description: str | None = None) -> str:
    title = title or f"My Podcast - Episode {num}"
    description = description or f"Description for episode {num}"
    enclosure_xml = (
        f'<enclosure url="https://tracker.example/download/{num}.rsskey" '
        f'length="1" type="application/x-bittorrent"/>'
        if enclosure
        else ""
    )
    return f"""<item>
<title>{title}</title>
<guid>https://tracker.example/torrents/{num}</guid>
<pubDate>Tue, 05 May 2026 12:00:00 +0000</pubDate>
<description>{description}</description>
{enclosure_xml}
</item>"""


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------

def test_parse_two_items():
    podcast = parse_unit3d_feed(_rss(_item(1), _item(2)))

    assert podcast.title == "Tracker - My Podcast"
    assert podcast.slug == "tracker-my-podcast"
    assert podcast.description == "Tracker feed"
    assert podcast.episodes == []
    assert len(podcast.torrent_items) == 2

    first, second = podcast.torrent_items
    assert first.guid == "https://tracker.example/torrents/1"
    assert first.title == "My Podcast - Episode 1"
    assert first.published == "Tue, 05 May 2026 12:00:00 +0000"
    assert first.torrent_url == "https://tracker.example/download/1.rsskey"
    assert first.description == "Description for episode 1"
    assert second.guid == "https://tracker.example/torrents/2"
    assert second.torrent_url == "https://tracker.example/download/2.rsskey"


def test_fresh_items_have_no_lifecycle_state():
    podcast = parse_unit3d_feed(_rss(_item(1)))

    item = podcast.torrent_items[0]
    assert item.info_hash is None
    assert item.episode_guids == []
    assert item.fetched_at is None


def test_entry_without_enclosure_skipped():
    podcast = parse_unit3d_feed(_rss(_item(1), _item(2, enclosure=False), _item(3)))

    assert len(podcast.torrent_items) == 2
    assert [i.guid for i in podcast.torrent_items] == [
        "https://tracker.example/torrents/1",
        "https://tracker.example/torrents/3",
    ]


def test_malformed_feed_raises():
    with pytest.raises(ValueError, match="Failed to parse feed"):
        parse_unit3d_feed("this is not xml")


# ---------------------------------------------------------------------------
# State preservation
# ---------------------------------------------------------------------------

def test_existing_item_state_preserved(tmp_path):
    podcast_dir = tmp_path / "tracker-my-podcast"
    existing = TorrentItem(
        guid="https://tracker.example/torrents/1",
        title="My Podcast - Episode 1",
        published="Tue, 05 May 2026 12:00:00 +0000",
        description="Description for episode 1",
        torrent_url="https://tracker.example/download/1.rsskey",
        info_hash="abc123",
        episode_guids=["ep-guid-1", "ep-guid-2"],
        fetched_at="2026-05-06T00:00:00",
    )
    existing.save(podcast_dir)

    podcast = parse_unit3d_feed(_rss(_item(1), _item(2)), output_dir=tmp_path)

    first = podcast.torrent_items[0]
    assert first.info_hash == "abc123"
    assert first.episode_guids == ["ep-guid-1", "ep-guid-2"]
    assert first.fetched_at == "2026-05-06T00:00:00"

    second = podcast.torrent_items[1]
    assert second.info_hash is None
    assert second.episode_guids == []
    assert second.fetched_at is None


def test_orphaned_item_excluded(tmp_path):
    podcast_dir = tmp_path / "tracker-my-podcast"
    orphan = TorrentItem(
        guid="https://tracker.example/torrents/99",
        title="Deleted torrent",
        published=None,
        description=None,
        torrent_url="https://tracker.example/download/99.rsskey",
        info_hash="deadbeef",
    )
    orphan.save(podcast_dir)

    podcast = parse_unit3d_feed(_rss(_item(1)), output_dir=tmp_path)

    guids = [i.guid for i in podcast.torrent_items]
    assert "https://tracker.example/torrents/99" not in guids
    assert guids == ["https://tracker.example/torrents/1"]


def test_all_on_disk_episodes_loaded(tmp_path):
    podcast_dir = tmp_path / "tracker-my-podcast"
    episode = Episode(
        title="Episode 1",
        guid="ep-guid-1",
        published="Tue, 05 May 2026 12:00:00 +0000",
        audio_url=None,
        duration=None,
        description="An episode spawned from a torrent",
        slug="episode-1",
    )
    episode.save(podcast_dir, "t")

    podcast = parse_unit3d_feed(_rss(_item(1)), output_dir=tmp_path)

    assert len(podcast.episodes) == 1
    assert podcast.episodes[0].guid == "ep-guid-1"
    assert podcast.episodes[0].title == "Episode 1"


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------

def test_blacklist_blanks_matching_description():
    xml = _rss(_item(1, description="Episode brought to you by Ben Smith"))
    podcast = parse_unit3d_feed(xml, blacklist=["Ben Smith"])

    assert podcast.torrent_items[0].description is None


def test_blacklist_no_match_preserves_description():
    xml = _rss(_item(1, description="A normal description"))
    podcast = parse_unit3d_feed(xml, blacklist=["secret"])

    assert podcast.torrent_items[0].description == "A normal description"


def test_no_blacklist_by_default():
    xml = _rss(_item(1, description="Contains Ben Smith name"))
    podcast = parse_unit3d_feed(xml)

    assert podcast.torrent_items[0].description == "Contains Ben Smith name"
