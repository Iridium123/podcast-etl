"""Integration tests that run against real podcast feeds.

These tests make real HTTP requests: parse an RSS feed, download an episode,
tag the MP3, and stage it for seeding.  Run with ``pytest -m ''`` to include.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from mutagen.id3 import ID3

from podcast_etl.feed import parse_feed
from podcast_etl.models import StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.download import DownloadStep
from podcast_etl.steps.stage import StageStep
from podcast_etl.steps.tag import TagStep

pytestmark = pytest.mark.integration

# Stable, well-known public feed (NPR Planet Money — ~20 min episodes).
# If this feed changes and breaks CI, swap in another public MP3 podcast feed.
FEED_URL = "https://feeds.npr.org/510289/podcast.xml"


def _record_status(episode, step_name, result):
    """Write a StepStatus entry so downstream steps can find prior output."""
    episode.status[step_name] = StepStatus(
        completed_at=datetime.now().isoformat(),
        result=result.data,
    )


def test_download_tag_stage(tmp_path):
    """Parse a real feed, download one episode, tag it, and stage it."""
    # --- Parse feed --------------------------------------------------------
    podcast = parse_feed(FEED_URL, output_dir=tmp_path)
    assert podcast.episodes, "Feed returned no episodes"
    assert podcast.title
    assert podcast.slug

    episode = next((ep for ep in podcast.episodes if ep.audio_url), None)
    assert episode is not None, "No episode with audio URL found"
    podcast.episodes = [episode]

    config = {"torrent_data_dir": str(tmp_path / "torrent-data")}
    context = PipelineContext(output_dir=tmp_path, podcast=podcast, config=config)

    # --- Download ----------------------------------------------------------
    dl_result = DownloadStep().process(episode, context)
    _record_status(episode, "download", dl_result)

    audio_path = context.podcast_dir / dl_result.data["path"]
    assert audio_path.exists()
    assert audio_path.suffix == ".mp3"
    assert dl_result.data["size_bytes"] > 0

    # --- Tag ---------------------------------------------------------------
    tag_result = TagStep().process(episode, context)
    _record_status(episode, "tag", tag_result)

    assert tag_result.data["release_date"]

    tags = ID3(audio_path)
    assert tags["TIT2"].text[0] == episode.title
    assert "TDRL" in tags
    assert "TPE1" in tags

    # --- Stage -------------------------------------------------------------
    stage_result = StageStep().process(episode, context)
    _record_status(episode, "stage", stage_result)

    staged = Path(stage_result.data["local_path"])
    assert staged.exists()
    assert staged.stat().st_size == audio_path.stat().st_size


def test_torrent_fetch_spawns_episode_from_real_torrent(tmp_path):
    """Fetch phase end-to-end with a real .torrent (torf) and real ID3 (mutagen).

    Only the torrent client is faked; the MP3, the .torrent blob, the ID3
    read, the audio copy, and the episode JSON are all real.
    """
    from mutagen.id3 import TDRC, TIT2, TRCK
    from torf import Torrent

    from podcast_etl.clients import TorrentFileInfo
    from podcast_etl.models import Podcast
    from podcast_etl.torrent_fetch import fetch_torrent_item
    from podcast_etl.unit3d_feed import parse_unit3d_feed

    # --- Build a real MP3-ish file with real ID3 tags ----------------------
    save_dir = tmp_path / "qbt-save"
    mp3_path = save_dir / "My Show" / "episode-one.mp3"
    mp3_path.parent.mkdir(parents=True)
    mp3_path.write_bytes(b"\x00" * 4096)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["Episode One"]))
    tags.add(TDRC(encoding=3, text=["2026-05-05"]))
    tags.add(TRCK(encoding=3, text=["1/10"]))
    tags.save(mp3_path)

    # --- Build a real .torrent for it via torf -----------------------------
    torrent = Torrent(path=mp3_path.parent, trackers=["http://tracker.example/announce"])
    torrent.generate()
    blob_path = tmp_path / "fixture.torrent"
    torrent.write(blob_path)
    info_hash = str(torrent.infohash)

    # --- Parse a minimal UNIT3D-style feed ---------------------------------
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Tracker - My Show</title>
<item>
  <title>My Show - Episode One</title>
  <guid>https://tracker.example/torrents/1</guid>
  <pubDate>Tue, 05 May 2026 12:00:00 +0000</pubDate>
  <enclosure url="https://tracker.example/download/1" length="1" type="application/x-bittorrent"/>
</item>
</channel></rss>"""
    output_dir = tmp_path / "output"
    podcast = parse_unit3d_feed(rss, output_dir=output_dir)
    podcast.save(output_dir)  # what service.fetch_feed does after parsing
    assert len(podcast.torrent_items) == 1
    item = podcast.torrent_items[0]

    class FakeClient:
        def has_torrent(self, h):
            return h == info_hash

        def is_complete(self, h):
            return True

        def get_files(self, h):
            return [
                TorrentFileInfo(
                    absolute_path=mp3_path,
                    relative_path=Path("My Show/episode-one.mp3"),
                )
            ]

        def add_torrent(self, path, save_path):
            raise AssertionError("should not add: client already has the torrent")

    # Blob already on disk with the real info hash recorded (post-State-1)
    podcast_dir = podcast.podcast_dir(output_dir)
    item.info_hash = info_hash

    config = {"client": {"save_path": str(save_dir)}}
    fetch_torrent_item(item, podcast, podcast_dir, config, FakeClient())

    # --- Torrent item marked fetched ---------------------------------------
    assert item.fetched_at
    assert item.episode_guids == [f"{info_hash}:My Show/episode-one.mp3"]

    # --- Episode spawned with real ID3 metadata ----------------------------
    assert len(podcast.episodes) == 1
    episode = podcast.episodes[0]
    assert episode.guid == f"{info_hash}:My Show/episode-one.mp3"
    assert episode.title == "Episode One"
    assert episode.raw_title == "Episode One"
    assert episode.episode_number == 1
    assert episode.slug == "episode-one"
    # Date normalized to RFC 2822 (TagStep-compatible)
    from email.utils import parsedate_to_datetime

    assert parsedate_to_datetime(episode.published).year == 2026

    # --- Audio copied, synthesized download status points at it ------------
    download = episode.status["download"]
    audio_path = podcast_dir / download.result["path"]
    assert audio_path.exists()
    assert audio_path.stat().st_size == mp3_path.stat().st_size
    assert download.result["size_bytes"] == mp3_path.stat().st_size

    # --- Episode JSON persisted and reloadable -----------------------------
    reloaded = Podcast.load(podcast_dir)
    assert [ep.guid for ep in reloaded.episodes] == [episode.guid]
    assert reloaded.torrent_items[0].fetched_at
