"""Tests for feed.py: parse_feed."""
from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_etl.feed import parse_feed
from podcast_etl.models import Episode, StepStatus


# ---------------------------------------------------------------------------
# Minimal feedparser mock helpers
# ---------------------------------------------------------------------------

class _FeedMeta:
    """Mimics feedparser's feed.feed object."""

    def __init__(self, title="Test Podcast", subtitle="A great podcast", image_href=None):
        self._data = {"title": title, "subtitle": subtitle}
        if image_href:
            self.image = {"href": image_href}

    def get(self, key, default=None):
        return self._data.get(key, default)


class _Entry:
    """Mimics a single feedparser entry."""

    def __init__(
        self,
        title="Episode 1",
        guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        links=None,
        enclosures=None,
        summary="Episode summary",
        itunes_duration="1:00:00",
        image=None,
        itunes_episode=None,
    ):
        self._data = {
            "title": title,
            "id": guid,
            "published": published,
            "links": links if links is not None else [],
            "enclosures": enclosures if enclosures is not None else [],
            "summary": summary,
            "itunes_duration": itunes_duration,
        }
        if image is not None:
            self._data["image"] = image
        if itunes_episode is not None:
            self._data["itunes_episode"] = itunes_episode

    def get(self, key, default=None):
        return self._data.get(key, default)


class _ParsedFeed:
    """Mimics the top-level feedparser result."""

    def __init__(self, entries=None, bozo=False, bozo_exception=None, feed=None):
        self.entries = entries or []
        self.bozo = bozo
        self.bozo_exception = bozo_exception
        self.feed = feed or _FeedMeta()


def _audio_link(url="https://example.com/ep.mp3"):
    return {"type": "audio/mpeg", "href": url, "rel": "enclosure"}


def _make_parsed_feed(**kwargs):
    return _ParsedFeed(**kwargs)


# ---------------------------------------------------------------------------
# Success cases
# ---------------------------------------------------------------------------

def test_parse_feed_returns_podcast_with_correct_metadata():
    feed = _make_parsed_feed(
        entries=[_Entry()],
        feed=_FeedMeta(title="My Podcast", subtitle="The subtitle"),
    )
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.title == "My Podcast"
    assert podcast.url == "https://example.com/feed.xml"
    assert podcast.description == "The subtitle"
    assert podcast.slug == "my-podcast"


def test_parse_feed_episode_fields_populated():
    entry = _Entry(
        title="Episode 1",
        guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        links=[_audio_link("https://example.com/ep.mp3")],
        summary="Great episode",
        itunes_duration="45:00",
    )
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    ep = podcast.episodes[0]
    assert ep.title == "Episode 1"
    assert ep.guid == "guid-1"
    assert ep.published == "Mon, 01 Jan 2024 00:00:00 +0000"
    assert ep.audio_url == "https://example.com/ep.mp3"
    assert ep.description == "Great episode"
    assert ep.duration == "45:00"
    assert ep.slug == "episode-1"


def test_parse_feed_image_url_extracted():
    feed = _make_parsed_feed(feed=_FeedMeta(image_href="https://example.com/cover.jpg"))
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.image_url == "https://example.com/cover.jpg"


def test_parse_feed_no_image_url():
    feed = _make_parsed_feed(feed=_FeedMeta())  # no image attribute
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.image_url is None


def test_parse_feed_audio_from_links():
    entry = _Entry(links=[_audio_link("https://example.com/ep.mp3")], enclosures=[])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].audio_url == "https://example.com/ep.mp3"


def test_parse_feed_audio_from_enclosures_fallback():
    """If no audio in links, fall back to enclosures."""
    entry = _Entry(
        links=[{"type": "text/html", "href": "https://example.com", "rel": "alternate"}],
        enclosures=[{"href": "https://example.com/ep.mp3"}],
    )
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].audio_url == "https://example.com/ep.mp3"


def test_parse_feed_no_audio_url_gives_none():
    entry = _Entry(links=[], enclosures=[])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].audio_url is None


def test_parse_feed_slug_deduplication():
    """Two episodes with the same title get distinct slugs."""
    e1 = _Entry(title="Episode 1", guid="guid-1")
    e2 = _Entry(title="Episode 1", guid="guid-2")
    feed = _make_parsed_feed(entries=[e1, e2])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    slugs = [ep.slug for ep in podcast.episodes]
    assert len(set(slugs)) == 2
    assert slugs[0] == "episode-1"
    assert slugs[1] == "episode-1-2"


def test_parse_feed_preserves_existing_status(tmp_path: Path):
    """Existing step status on disk is merged into freshly parsed episodes."""
    from podcast_etl.models import Podcast

    # Write an existing episode with completed 'download' status to disk
    existing_ep = Episode(
        title="Episode 1",
        guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration=None,
        description=None,
        slug="episode-1",
        status={"download": StepStatus(completed_at="2024-01-01T00:00:00", result={"size_bytes": 100})},
    )
    podcast_dir = tmp_path / "test-podcast"
    existing_ep.save(podcast_dir, "Test Podcast")

    # Now parse the feed — should pick up the existing status
    entry = _Entry(title="Episode 1", guid="guid-1", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry], feed=_FeedMeta(title="Test Podcast"))
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", output_dir=tmp_path)

    ep = podcast.episodes[0]
    assert "download" in ep.status
    assert ep.status["download"].result["size_bytes"] == 100


def test_parse_feed_no_status_for_new_episodes(tmp_path: Path):
    """Episodes not yet on disk have an empty status dict."""
    entry = _Entry(title="Brand New Episode", guid="guid-new", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", output_dir=tmp_path)

    assert podcast.episodes[0].status == {}


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------

def test_parse_feed_bozo_with_no_entries_raises():
    feed = _make_parsed_feed(bozo=True, bozo_exception=Exception("bad xml"), entries=[])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        with pytest.raises(ValueError, match="Failed to parse feed"):
            parse_feed("https://example.com/feed.xml")


def test_parse_feed_bozo_with_entries_succeeds():
    """A bozo feed that still has entries is treated as valid (partial parse)."""
    entry = _Entry(links=[_audio_link()])
    feed = _make_parsed_feed(bozo=True, bozo_exception=Exception("minor"), entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert len(podcast.episodes) == 1


# ---------------------------------------------------------------------------
# Description cleaning
# ---------------------------------------------------------------------------

def test_parse_feed_cleans_html_description():
    """HTML in episode descriptions is stripped to plain text."""
    entry = _Entry(
        summary="<p>Hello <a href='https://example.com'>world</a></p>",
        links=[_audio_link()],
    )
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].description == "Hello world"


def test_parse_feed_cleans_entity_encoded_description():
    """Entity-encoded HTML (Patreon-style) is decoded and stripped."""
    entry = _Entry(
        summary="&lt;p&gt;Content here&lt;/p&gt;",
        links=[_audio_link()],
    )
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].description == "Content here"


def test_parse_feed_cleans_podcast_description():
    """Podcast-level description is also cleaned."""
    feed = _make_parsed_feed(
        entries=[_Entry(links=[_audio_link()])],
        feed=_FeedMeta(subtitle="<p>About the <b>show</b></p>"),
    )
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.description == "About the show"


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------

def test_parse_feed_blacklist_blanks_matching_description():
    entry = _Entry(
        summary="Episode brought to you by Ben Smith",
        links=[_audio_link()],
    )
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", blacklist=["Ben Smith"])

    assert podcast.episodes[0].description is None


def test_parse_feed_blacklist_no_match_preserves_description():
    entry = _Entry(
        summary="A normal description",
        links=[_audio_link()],
    )
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", blacklist=["secret"])

    assert podcast.episodes[0].description == "A normal description"


def test_parse_feed_no_blacklist_by_default():
    entry = _Entry(
        summary="Contains Ben Smith name",
        links=[_audio_link()],
    )
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].description == "Contains Ben Smith name"


# ---------------------------------------------------------------------------
# Episode image_url
# ---------------------------------------------------------------------------

def test_parse_feed_episode_image_url_extracted():
    entry = _Entry(
        links=[_audio_link()],
        image={"href": "https://example.com/ep1-cover.jpg"},
    )
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].image_url == "https://example.com/ep1-cover.jpg"


def test_parse_feed_episode_no_image_gives_none():
    entry = _Entry(links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].image_url is None


# ---------------------------------------------------------------------------
# Title cleaning
# ---------------------------------------------------------------------------

def test_parse_feed_applies_strip_date():
    entry = _Entry(title="Guest Name (3_19_26)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"strip_date": True})
    assert podcast.episodes[0].title == "Guest Name"


def test_parse_feed_applies_reorder_parts_with_siblings():
    """Two same-day episodes with parts get reordered using common prefix."""
    e1 = _Entry(title="World War II - D-Day (Part 1)", guid="guid-1", links=[_audio_link()])
    e2 = _Entry(title="World War II - Normandy (Part 2)", guid="guid-2", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[e1, e2])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"reorder_parts": True})
    assert podcast.episodes[0].title == "World War II - Part 1 - D-Day"
    assert podcast.episodes[1].title == "World War II - Part 2 - Normandy"


def test_parse_feed_reorder_parts_solo_unchanged():
    """A single episode with a part indicator is left unchanged."""
    entry = _Entry(title="The Show (Part 1)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"reorder_parts": True})
    assert podcast.episodes[0].title == "The Show (Part 1)"


def test_parse_feed_no_title_cleaning_by_default():
    entry = _Entry(title="Guest Name (3_19_26)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")
    assert podcast.episodes[0].title == "Guest Name (3_19_26)"


def test_parse_feed_title_cleaning_affects_slug():
    """Cleaned titles should produce slugs from the cleaned version."""
    entry = _Entry(title="Guest Name (3_19_26)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"strip_date": True})
    assert podcast.episodes[0].slug == "guest-name"


def test_parse_feed_title_cleaning_preserves_status_despite_slug_change(tmp_path: Path):
    """Status is preserved via GUID even when title cleaning changes the slug."""
    # Save an episode with the old (uncleaned) title
    existing_ep = Episode(
        title="Guest Name (3_19_26)",
        guid="guid-dated",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration=None,
        description=None,
        slug="guest-name-3_19_26",
        status={"download": StepStatus(completed_at="2024-01-01T00:00:00", result={"size_bytes": 200})},
    )
    podcast_dir = tmp_path / "test-podcast"
    existing_ep.save(podcast_dir, "Test Podcast")

    # Re-parse with title cleaning enabled — slug will change
    entry = _Entry(title="Guest Name (3_19_26)", guid="guid-dated", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry], feed=_FeedMeta(title="Test Podcast"))
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", output_dir=tmp_path, title_cleaning={"strip_date": True})

    ep = podcast.episodes[0]
    assert ep.title == "Guest Name"
    assert ep.slug == "guest-name"
    assert "download" in ep.status
    assert ep.status["download"].result["size_bytes"] == 200


# ---------------------------------------------------------------------------
# Episode number
# ---------------------------------------------------------------------------

def test_parse_feed_episode_number_parsed():
    entry = _Entry(links=[_audio_link()], itunes_episode="42")
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].episode_number == 42


def test_parse_feed_episode_number_missing_gives_none():
    entry = _Entry(links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].episode_number is None


def test_parse_feed_episode_number_non_numeric_gives_none():
    entry = _Entry(links=[_audio_link()], itunes_episode="bonus")
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].episode_number is None


def test_parse_feed_episode_number_float_string_parsed():
    """Some RSS generators emit episode numbers as floats like '42.0'."""
    entry = _Entry(links=[_audio_link()], itunes_episode="42.0")
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].episode_number == 42


def test_parse_feed_episode_number_prepended_to_title():
    entry = _Entry(title="Rise of the Mongols", links=[_audio_link()], itunes_episode="123")
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed(
            "https://example.com/feed.xml",
            title_cleaning={"prepend_episode_number": True},
        )

    assert podcast.episodes[0].title == "123 - Rise of the Mongols"


def test_parse_feed_episode_number_not_prepended_without_config():
    entry = _Entry(title="Rise of the Mongols", links=[_audio_link()], itunes_episode="123")
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")

    assert podcast.episodes[0].title == "Rise of the Mongols"


# ---------------------------------------------------------------------------
# raw_title
# ---------------------------------------------------------------------------

def test_parse_feed_sets_raw_title_before_cleaning():
    entry = _Entry(title="Guest Name (3_19_26)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"strip_date": True})
    # title is cleaned, raw_title is the original
    assert podcast.episodes[0].title == "Guest Name"
    assert podcast.episodes[0].raw_title == "Guest Name (3_19_26)"


def test_parse_feed_raw_title_set_without_cleaning():
    entry = _Entry(title="Normal Title", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")
    assert podcast.episodes[0].raw_title == "Normal Title"


def test_parse_feed_preserves_raw_title_from_rss_over_disk(tmp_path: Path):
    """Fresh RSS raw_title takes precedence over whatever is on disk."""
    existing_ep = Episode(
        title="Old Cleaned",
        guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration=None,
        description=None,
        slug="old-cleaned",
        raw_title="Old Raw Title",
        status={"download": StepStatus(completed_at="2024-01-01T00:00:00", result={})},
    )
    podcast_dir = tmp_path / "test-podcast"
    existing_ep.save(podcast_dir, "Test Podcast")

    entry = _Entry(title="New Raw Title", guid="guid-1", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry], feed=_FeedMeta(title="Test Podcast"))
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", output_dir=tmp_path)

    ep = podcast.episodes[0]
    assert ep.raw_title == "New Raw Title"
    assert "download" in ep.status  # status still preserved
