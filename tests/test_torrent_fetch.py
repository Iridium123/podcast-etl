"""Tests for the torrent-source feed fetch phase (torrent_fetch.py)."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.id3 import COMM, ID3, TDRC, TIT2, TRCK

from podcast_etl.clients import TorrentFileInfo
from podcast_etl.models import (
    Episode,
    Podcast,
    StepStatus,
    TorrentItem,
    episode_basename,
    guid_hash,
)
from podcast_etl.torrent_fetch import (
    _build_episode,
    _destination_filenames,
    _read_id3,
    fetch_torrent_item,
    fetch_torrents,
    to_rfc2822,
)


class FakeTorrentClient:
    def __init__(self):
        self.torrents: set[str] = set()
        self.complete: set[str] = set()
        self.files: dict[str, list[TorrentFileInfo]] = {}
        self.added: list[tuple[Path, str]] = []

    def has_torrent(self, h):
        return h in self.torrents

    def is_complete(self, h):
        return h in self.complete

    def get_files(self, h):
        return self.files[h]

    def add_torrent(self, path, save_path):
        self.added.append((Path(path), save_path))
        return "added"


class RaisingClient:
    """A client whose every method call is an error (must never be touched)."""

    def has_torrent(self, h):
        raise AssertionError("client touched")

    def is_complete(self, h):
        raise AssertionError("client touched")

    def get_files(self, h):
        raise AssertionError("client touched")

    def add_torrent(self, path, save_path):
        raise AssertionError("client touched")


def make_mp3(path, title=None, date=None, track=None, comment=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 256)
    tags = ID3()
    if title:
        tags.add(TIT2(encoding=3, text=[title]))
    if date:
        tags.add(TDRC(encoding=3, text=[date]))
    if track:
        tags.add(TRCK(encoding=3, text=[track]))
    if comment:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=[comment]))
    tags.save(path)


def make_podcast(**overrides) -> Podcast:
    defaults = dict(
        title="My Pod",
        url="http://example.com/feed",
        description=None,
        image_url=None,
        slug="my-pod",
    )
    defaults.update(overrides)
    return Podcast(**defaults)


def make_item(**overrides) -> TorrentItem:
    defaults = dict(
        guid="item-guid-1",
        title="Item Title",
        published="Mon, 04 May 2026 10:00:00 +0000",
        description="item description",
        torrent_url="http://tracker.example/t1.torrent",
    )
    defaults.update(overrides)
    return TorrentItem(**defaults)


def make_config(**overrides) -> dict:
    config = {"client": {"save_path": "/data"}, "blacklist": [], "title_cleaning": None}
    config.update(overrides)
    return config


def make_fileinfo(absolute: Path, relative: str) -> TorrentFileInfo:
    return TorrentFileInfo(absolute_path=absolute, relative_path=Path(relative))


class TestToRfc2822:
    def test_rfc2822_roundtrip(self):
        original = "Mon, 04 May 2026 10:00:00 +0000"
        result = to_rfc2822(original)
        assert result is not None
        assert parsedate_to_datetime(result) == parsedate_to_datetime(original)

    def test_iso_date_converted(self):
        result = to_rfc2822("2026-05-05")
        assert result is not None
        parsed = parsedate_to_datetime(result)
        assert (parsed.year, parsed.month, parsed.day) == (2026, 5, 5)

    def test_iso_datetime_converted(self):
        result = to_rfc2822("2026-05-05T14:30:00+02:00")
        assert result is not None
        assert parsedate_to_datetime(result) == datetime.fromisoformat(
            "2026-05-05T14:30:00+02:00"
        )

    def test_garbage_returns_none(self):
        assert to_rfc2822("not a date at all") is None

    def test_none_returns_none(self):
        assert to_rfc2822(None) is None


class TestReadId3:
    def test_full_tags_extracted(self, tmp_path):
        path = tmp_path / "ep.mp3"
        make_mp3(path, title="My Episode", date="2026-05-05", track="3", comment="Notes")
        result = _read_id3(path)
        assert result["title"] == "My Episode"
        assert result["date"] == "2026-05-05"
        assert result["track"] == 3
        assert result["comment"] == "Notes"

    def test_track_with_total(self, tmp_path):
        path = tmp_path / "ep.mp3"
        make_mp3(path, track="3/10")
        assert _read_id3(path)["track"] == 3

    def test_non_numeric_track_omitted(self, tmp_path):
        path = tmp_path / "ep.mp3"
        make_mp3(path, title="T", track="abc")
        result = _read_id3(path)
        assert "track" not in result
        assert result["title"] == "T"

    def test_missing_tags_keys_absent(self, tmp_path):
        path = tmp_path / "ep.mp3"
        make_mp3(path, title="Only Title")
        result = _read_id3(path)
        assert result == {"title": "Only Title"}

    def test_no_id3_header_returns_empty(self, tmp_path):
        path = tmp_path / "garbage.mp3"
        path.write_bytes(b"\x12\x34garbage no header here" * 10)
        assert _read_id3(path) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert _read_id3(tmp_path / "nope.mp3") == {}


class TestBuildEpisode:
    def test_id3_metadata_used(self, tmp_path):
        path = tmp_path / "save" / "file.mp3"
        make_mp3(path, title="Tagged Title", date="2026-05-05", track="7", comment="From COMM")
        item = make_item(info_hash="hash1")
        ep = _build_episode(
            make_fileinfo(path, "dir/file.mp3"), item, make_podcast(), make_config()
        )
        assert ep.title == "Tagged Title"
        assert ep.raw_title == "Tagged Title"
        assert ep.guid == "hash1:dir/file.mp3"
        assert ep.episode_number == 7
        assert ep.description == "From COMM"
        assert ep.audio_url is None
        assert ep.duration is None
        # ID3 ISO date normalized to RFC 2822
        parsed = parsedate_to_datetime(ep.published)
        assert (parsed.year, parsed.month, parsed.day) == (2026, 5, 5)

    def test_title_falls_back_to_filename_stem(self, tmp_path):
        path = tmp_path / "save" / "Great Episode Name.mp3"
        make_mp3(path)
        item = make_item(info_hash="hash1")
        ep = _build_episode(
            make_fileinfo(path, "dir/Great Episode Name.mp3"),
            item,
            make_podcast(),
            make_config(),
        )
        assert ep.raw_title == "Great Episode Name"
        assert ep.title == "Great Episode Name"

    def test_published_from_filename_beats_item(self, tmp_path):
        path = tmp_path / "Show - 2025.10.02 - Sapiens [2025_MP3-96 kbps].mp3"
        make_mp3(path, title="T")  # no ID3 date
        item = make_item(info_hash="hash1", published="Mon, 04 May 2026 10:00:00 +0000")
        ep = _build_episode(
            make_fileinfo(path, path.name), item, make_podcast(), make_config()
        )
        parsed = parsedate_to_datetime(ep.published)
        assert (parsed.year, parsed.month, parsed.day) == (2025, 10, 2)

    def test_id3_date_beats_filename(self, tmp_path):
        path = tmp_path / "Show - 2025.10.02 - Sapiens.mp3"
        make_mp3(path, title="T", date="2026-05-05")
        item = make_item(info_hash="hash1")
        ep = _build_episode(
            make_fileinfo(path, path.name), item, make_podcast(), make_config()
        )
        parsed = parsedate_to_datetime(ep.published)
        assert (parsed.year, parsed.month, parsed.day) == (2026, 5, 5)

    def test_dateless_filename_falls_back_to_item(self, tmp_path):
        path = tmp_path / "Show - Sapiens.mp3"
        make_mp3(path, title="T")
        item = make_item(info_hash="hash1", published="Mon, 04 May 2026 10:00:00 +0000")
        ep = _build_episode(
            make_fileinfo(path, path.name), item, make_podcast(), make_config()
        )
        parsed = parsedate_to_datetime(ep.published)
        assert (parsed.year, parsed.month, parsed.day) == (2026, 5, 4)

    def test_published_falls_back_to_item(self, tmp_path):
        path = tmp_path / "file.mp3"
        make_mp3(path, title="T")
        item = make_item(info_hash="hash1", published="Mon, 04 May 2026 10:00:00 +0000")
        ep = _build_episode(
            make_fileinfo(path, "file.mp3"), item, make_podcast(), make_config()
        )
        assert parsedate_to_datetime(ep.published) == parsedate_to_datetime(
            "Mon, 04 May 2026 10:00:00 +0000"
        )

    def test_published_falls_back_to_mtime(self, tmp_path):
        path = tmp_path / "file.mp3"
        make_mp3(path, title="T")
        item = make_item(info_hash="hash1", published=None)
        ep = _build_episode(
            make_fileinfo(path, "file.mp3"), item, make_podcast(), make_config()
        )
        assert ep.published is not None
        # Must be parseable by the downstream TagStep parser
        parsed = parsedate_to_datetime(ep.published)
        assert abs(parsed.timestamp() - path.stat().st_mtime) < 2

    def test_description_falls_back_to_item(self, tmp_path):
        path = tmp_path / "file.mp3"
        make_mp3(path, title="T")
        item = make_item(info_hash="hash1", description="item description")
        ep = _build_episode(
            make_fileinfo(path, "file.mp3"), item, make_podcast(), make_config()
        )
        assert ep.description == "item description"

    def test_blacklist_applied(self, tmp_path):
        path = tmp_path / "file.mp3"
        make_mp3(path, title="T", comment="Sponsored by John Doe")
        item = make_item(info_hash="hash1")
        ep = _build_episode(
            make_fileinfo(path, "file.mp3"),
            item,
            make_podcast(),
            make_config(blacklist=["john doe"]),
        )
        assert ep.description is None

    def test_slug_dedup_for_identical_titles(self, tmp_path):
        podcast = make_podcast()
        item = make_item(info_hash="hash1")
        config = make_config()
        p1 = tmp_path / "a" / "same.mp3"
        p2 = tmp_path / "b" / "same.mp3"
        make_mp3(p1, title="Same Title")
        make_mp3(p2, title="Same Title")
        ep1 = _build_episode(make_fileinfo(p1, "a/same.mp3"), item, podcast, config)
        podcast.episodes.append(ep1)
        ep2 = _build_episode(make_fileinfo(p2, "b/same.mp3"), item, podcast, config)
        assert ep1.slug == "same-title"
        assert ep2.slug == "same-title-2"

    def test_clean_title_wiring(self, tmp_path):
        path = tmp_path / "file.mp3"
        make_mp3(path, title="Raw Tagged Title", date="2026-05-05", track="4")
        item = make_item(info_hash="hash1")
        config = make_config(title_cleaning={"sanitize": True})
        with patch(
            "podcast_etl.torrent_fetch.clean_title", return_value="CLEANED"
        ) as mock_clean:
            ep = _build_episode(
                make_fileinfo(path, "file.mp3"), item, make_podcast(), config
            )
        assert ep.title == "CLEANED"
        assert ep.raw_title == "Raw Tagged Title"
        args, kwargs = mock_clean.call_args
        assert args[0] == "Raw Tagged Title"
        assert args[1] == {"sanitize": True}
        assert kwargs["episode_number"] == 4


class TestDestinationFilenames:
    def _episode(self, title: str) -> Episode:
        return Episode(
            title=title,
            guid=f"g:{title}",
            published="Mon, 04 May 2026 10:00:00 +0000",
            audio_url=None,
            duration=None,
            description=None,
            slug="s",
        )

    def test_distinct_titles_plain_basenames(self):
        episodes = [self._episode("One"), self._episode("Two")]
        fileinfos = [
            make_fileinfo(Path("/x/a.mp3"), "a.mp3"),
            make_fileinfo(Path("/x/b.mp3"), "b.mp3"),
        ]
        names = _destination_filenames(episodes, fileinfos, "My Pod")
        expected = [
            episode_basename("My Pod", ep.title, ep.published) + ".mp3"
            for ep in episodes
        ]
        assert names == expected

    def test_identical_titles_get_hash_suffixes(self):
        episodes = [self._episode("Same"), self._episode("Same")]
        fileinfos = [
            make_fileinfo(Path("/x/a.mp3"), "dir/a.mp3"),
            make_fileinfo(Path("/x/b.mp3"), "dir/b.mp3"),
        ]
        names = _destination_filenames(episodes, fileinfos, "My Pod")
        assert names[0] != names[1]
        base = episode_basename("My Pod", "Same", episodes[0].published)
        for name in names:
            assert name.startswith(base + "-")
            suffix = name[len(base) + 1 : -len(".mp3")]
            assert len(suffix) == 8
            int(suffix, 16)  # 8-hex chars

    def test_deterministic_across_calls(self):
        episodes = [self._episode("Same"), self._episode("Same")]
        fileinfos = [
            make_fileinfo(Path("/x/a.mp3"), "dir/a.mp3"),
            make_fileinfo(Path("/x/b.mp3"), "dir/b.mp3"),
        ]
        first = _destination_filenames(episodes, fileinfos, "My Pod")
        second = _destination_filenames(episodes, fileinfos, "My Pod")
        assert first == second


class TestStateMachine:
    def test_state1_fetches_blob_and_falls_through_to_add(self, tmp_path):
        podcast_dir = tmp_path / "podcast"
        item = make_item()
        client = FakeTorrentClient()
        with patch(
            "podcast_etl.torrent_fetch._fetch_blob", return_value=b"blob-bytes"
        ), patch("podcast_etl.torrent_fetch.read_info_hash", return_value="hash1"):
            fetch_torrent_item(item, make_podcast(), podcast_dir, make_config(), client)

        blob_path = podcast_dir / "torrent_files" / f"{guid_hash(item.guid)}.torrent"
        assert blob_path.read_bytes() == b"blob-bytes"
        assert item.info_hash == "hash1"
        # info_hash persisted to disk
        item_json = podcast_dir / "torrents" / f"{guid_hash(item.guid)}.json"
        assert json.loads(item_json.read_text())["info_hash"] == "hash1"
        # Fall-through: add_torrent called in the SAME invocation
        assert client.added == [(blob_path, "/data")]
        assert item.fetched_at is None

    def test_state2_readds_missing_torrent(self, tmp_path):
        podcast_dir = tmp_path / "podcast"
        item = make_item(info_hash="hash1")
        blob_path = podcast_dir / "torrent_files" / f"{guid_hash(item.guid)}.torrent"
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(b"existing-blob")
        client = FakeTorrentClient()
        podcast = make_podcast()
        fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)
        assert client.added == [(blob_path, "/data")]
        assert podcast.episodes == []
        assert item.fetched_at is None

    def test_state2_refetches_missing_blob(self, tmp_path):
        podcast_dir = tmp_path / "podcast"
        item = make_item(info_hash="hash1")
        blob_path = podcast_dir / "torrent_files" / f"{guid_hash(item.guid)}.torrent"
        client = FakeTorrentClient()
        with patch(
            "podcast_etl.torrent_fetch._fetch_blob", return_value=b"blob-bytes"
        ) as mock_fetch:
            fetch_torrent_item(item, make_podcast(), podcast_dir, make_config(), client)
        mock_fetch.assert_called_once_with(item.torrent_url)
        assert blob_path.read_bytes() == b"blob-bytes"
        assert client.added == [(blob_path, "/data")]

    def test_state2_waits_while_downloading(self, tmp_path):
        podcast_dir = tmp_path / "podcast"
        item = make_item(info_hash="hash1")
        client = FakeTorrentClient()
        client.torrents.add("hash1")
        podcast = make_podcast()
        fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)
        assert client.added == []
        assert podcast.episodes == []
        assert item.fetched_at is None

    def _complete_client(self, save_dir: Path) -> FakeTorrentClient:
        make_mp3(save_dir / "torrent1" / "ep1.mp3", title="Episode One", date="2026-01-01")
        make_mp3(save_dir / "torrent1" / "ep2.mp3", title="Episode Two", date="2026-01-02")
        (save_dir / "torrent1" / "cover.jpg").write_bytes(b"jpegdata")
        client = FakeTorrentClient()
        client.torrents.add("hash1")
        client.complete.add("hash1")
        client.files["hash1"] = [
            make_fileinfo(save_dir / "torrent1" / "ep1.mp3", "torrent1/ep1.mp3"),
            make_fileinfo(save_dir / "torrent1" / "ep2.mp3", "torrent1/ep2.mp3"),
            make_fileinfo(save_dir / "torrent1" / "cover.jpg", "torrent1/cover.jpg"),
        ]
        return client

    def test_state3_spawns_episodes(self, tmp_path):
        podcast_dir = tmp_path / "podcast"
        save_dir = tmp_path / "downloads"
        item = make_item(info_hash="hash1")
        client = self._complete_client(save_dir)
        podcast = make_podcast()

        fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)

        assert len(podcast.episodes) == 2
        guids = [ep.guid for ep in podcast.episodes]
        assert guids == ["hash1:torrent1/ep1.mp3", "hash1:torrent1/ep2.mp3"]
        for ep in podcast.episodes:
            download = ep.status["download"]
            filename = episode_basename("My Pod", ep.title, ep.published) + ".mp3"
            copied = podcast_dir / "audio" / filename
            assert copied.exists()
            assert download.result == {
                "path": f"audio/{filename}",
                "size_bytes": copied.stat().st_size,
            }
        assert item.fetched_at is not None
        assert item.episode_guids == guids
        # item state persisted
        item_json = podcast_dir / "torrents" / f"{guid_hash(item.guid)}.json"
        assert json.loads(item_json.read_text())["fetched_at"] == item.fetched_at

    def test_no_mp3s_marks_fetched_with_zero_episodes(self, tmp_path, caplog):
        podcast_dir = tmp_path / "podcast"
        save_dir = tmp_path / "downloads"
        (save_dir / "torrent1").mkdir(parents=True)
        (save_dir / "torrent1" / "cover.jpg").write_bytes(b"jpegdata")
        item = make_item(info_hash="hash1")
        client = FakeTorrentClient()
        client.torrents.add("hash1")
        client.complete.add("hash1")
        client.files["hash1"] = [
            make_fileinfo(save_dir / "torrent1" / "cover.jpg", "torrent1/cover.jpg")
        ]
        podcast = make_podcast()
        with caplog.at_level("WARNING"):
            fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)
        assert "contains no MP3 files" in caplog.text
        assert item.fetched_at is not None
        assert item.episode_guids == []
        assert podcast.episodes == []

    def test_missing_source_logged_and_skipped(self, tmp_path, caplog):
        """A source file this process cannot see (unmounted path, e.g. a
        pre-existing torrent saved outside client.save_path) must produce a
        clear actionable error, not a FileNotFoundError traceback, and must
        not spawn any episodes."""
        podcast_dir = tmp_path / "podcast"
        save_dir = tmp_path / "downloads"
        item = make_item(info_hash="hash1")
        client = self._complete_client(save_dir)
        missing_path = save_dir / "torrent1" / "ep2.mp3"
        missing_path.unlink()
        podcast = make_podcast()

        with caplog.at_level("ERROR"):
            fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)

        assert "not visible to this process" in caplog.text
        assert str(missing_path) in caplog.text
        assert podcast.episodes == []
        assert item.fetched_at is None  # retried next cycle

    def test_copy_failure_does_not_leak_episodes(self, tmp_path):
        """A copy failure mid-torrent must not leave audio-less episodes in
        podcast.episodes (observed live: unreachable save_path caused 178
        half-built episodes to hit the pipeline and fail tagging)."""
        podcast_dir = tmp_path / "podcast"
        save_dir = tmp_path / "downloads"
        item = make_item(info_hash="hash1")
        client = self._complete_client(save_dir)
        podcast = make_podcast()

        # Second MP3's copy fails mid-loop (sources exist, so the upfront
        # visibility check passes)
        real_copyfile = shutil.copyfile

        def failing_copyfile(src, dest, **kwargs):
            if Path(src).name == "ep2.mp3":
                raise OSError("disk full")
            return real_copyfile(src, dest, **kwargs)

        with patch("podcast_etl.torrent_fetch.shutil.copyfile", failing_copyfile):
            with pytest.raises(OSError):
                fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)

        # Only the successfully-copied episode joined the podcast
        assert [ep.guid for ep in podcast.episodes] == ["hash1:torrent1/ep1.mp3"]
        assert (podcast_dir / "audio").exists()
        assert item.fetched_at is None  # retried next cycle

    def test_partial_spawn_idempotency(self, tmp_path):
        podcast_dir = tmp_path / "podcast"
        save_dir = tmp_path / "downloads"
        item = make_item(info_hash="hash1")
        client = self._complete_client(save_dir)
        podcast = make_podcast()

        fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)
        assert len(podcast.episodes) == 2
        statuses = [ep.status["download"] for ep in podcast.episodes]
        paths = [s.result["path"] for s in statuses]
        audio_files = [podcast_dir / p for p in paths]
        mtimes = [f.stat().st_mtime_ns for f in audio_files]

        # Simulate a crash between spawning and marking fetched
        item.fetched_at = None
        fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)

        assert len(podcast.episodes) == 2
        for ep, status, path in zip(podcast.episodes, statuses, paths):
            assert ep.status["download"] is status  # same object preserved
            assert ep.status["download"].result["path"] == path
        # No re-copy: audio untouched
        assert [f.stat().st_mtime_ns for f in audio_files] == mtimes
        assert item.fetched_at is not None


class TestFetchTorrents:
    def test_skips_fetched_items_and_client_construction(self, tmp_path):
        podcast = make_podcast()
        items = [
            make_item(guid="g1", fetched_at="2026-01-01T00:00:00"),
            make_item(guid="g2", fetched_at="2026-01-02T00:00:00"),
        ]
        with patch(
            "podcast_etl.torrent_fetch.get_torrent_client",
            side_effect=AssertionError("client constructed"),
        ):
            fetch_torrents(items, podcast, tmp_path, make_config())

    def test_only_pending_items_processed(self, tmp_path):
        podcast = make_podcast()
        fetched = make_item(guid="g1", fetched_at="2026-01-01T00:00:00")
        pending = make_item(guid="g2")
        client = RaisingClient()
        with patch(
            "podcast_etl.torrent_fetch.get_torrent_client", return_value=client
        ), patch("podcast_etl.torrent_fetch.fetch_torrent_item") as mock_fetch:
            fetch_torrents([fetched, pending], podcast, tmp_path, make_config())
        assert mock_fetch.call_count == 1
        assert mock_fetch.call_args[0][0] is pending

    def test_per_item_isolation(self, tmp_path):
        podcast = make_podcast()
        item1 = make_item(guid="g1", torrent_url="http://tracker.example/bad.torrent")
        item2 = make_item(guid="g2", torrent_url="http://tracker.example/good.torrent")
        client = FakeTorrentClient()

        def fetch_blob(url):
            if url == item1.torrent_url:
                raise RuntimeError("boom")
            return b"blob-bytes"

        with patch(
            "podcast_etl.torrent_fetch.get_torrent_client", return_value=client
        ), patch(
            "podcast_etl.torrent_fetch._fetch_blob", side_effect=fetch_blob
        ), patch(
            "podcast_etl.torrent_fetch.read_info_hash", return_value="hash2"
        ):
            fetch_torrents([item1, item2], podcast, tmp_path, make_config())

        # item1 failed but item2 was still processed (blob fetched + added)
        assert item1.info_hash is None
        assert item2.info_hash == "hash2"
        assert len(client.added) == 1


class TestToLocalPath:
    def test_rebases_save_path_onto_torrent_data_dir(self):
        from podcast_etl.torrent_fetch import _to_local_path

        config = {"client": {"save_path": "/data"}, "torrent_data_dir": "/torrent-data"}
        assert _to_local_path(Path("/data/Show/ep.mp3"), config) == Path("/torrent-data/Show/ep.mp3")

    def test_path_outside_save_path_unchanged(self):
        from podcast_etl.torrent_fetch import _to_local_path

        config = {"client": {"save_path": "/data"}, "torrent_data_dir": "/torrent-data"}
        assert _to_local_path(Path("/media/other/ep.mp3"), config) == Path("/media/other/ep.mp3")

    def test_missing_config_is_noop(self):
        from podcast_etl.torrent_fetch import _to_local_path

        assert _to_local_path(Path("/data/ep.mp3"), {}) == Path("/data/ep.mp3")
        assert _to_local_path(Path("/data/ep.mp3"), {"client": {"save_path": "/data"}}) == Path("/data/ep.mp3")

    def test_state3_reads_files_at_rebased_path(self, tmp_path):
        """Client reports container-side paths; audio is read from the local mount."""
        local_mount = tmp_path / "torrent-data"
        make_mp3(local_mount / "t1" / "ep.mp3", title="Rebased")
        item = make_item(info_hash="hash1")
        client = FakeTorrentClient()
        client.torrents.add("hash1")
        client.complete.add("hash1")
        # absolute_path as the qBittorrent container would report it
        client.files["hash1"] = [
            TorrentFileInfo(absolute_path=Path("/data/t1/ep.mp3"), relative_path=Path("t1/ep.mp3"))
        ]
        podcast = make_podcast()
        config = {
            "client": {"save_path": "/data"},
            "torrent_data_dir": str(local_mount),
        }
        fetch_torrent_item(item, podcast, tmp_path / "podcast", config, client)

        assert len(podcast.episodes) == 1
        assert podcast.episodes[0].title == "Rebased"
        assert item.fetched_at is not None


class TestCrossTorrentFilenames:
    def test_same_title_in_second_torrent_gets_suffix(self, tmp_path):
        """A same-titled episode from a different torrent must not clobber or
        silently reuse the first torrent's audio file."""
        podcast_dir = tmp_path / "podcast"
        save_dir = tmp_path / "downloads"
        podcast = make_podcast()

        make_mp3(save_dir / "t1" / "ep.mp3", title="Same Title", date="2026-01-01")
        client = FakeTorrentClient()
        client.torrents.update({"hash1", "hash2"})
        client.complete.update({"hash1", "hash2"})
        client.files["hash1"] = [make_fileinfo(save_dir / "t1" / "ep.mp3", "t1/ep.mp3")]
        item1 = make_item(guid="g1", info_hash="hash1")
        fetch_torrent_item(item1, podcast, podcast_dir, make_config(), client)

        make_mp3(save_dir / "t2" / "ep.mp3", title="Same Title", date="2026-01-01")
        client.files["hash2"] = [make_fileinfo(save_dir / "t2" / "ep.mp3", "t2/ep.mp3")]
        item2 = make_item(guid="g2", info_hash="hash2")
        fetch_torrent_item(item2, podcast, podcast_dir, make_config(), client)

        paths = [ep.status["download"].result["path"] for ep in podcast.episodes]
        assert len(paths) == len(set(paths)), f"filename collision across torrents: {paths}"
        for p in paths:
            assert (podcast_dir / p).exists()


class TestClientConstructionGuard:
    def test_missing_client_config_logged_not_raised(self, tmp_path, caplog):
        podcast = make_podcast()
        items = [make_item(guid="g1")]
        with caplog.at_level("ERROR"):
            fetch_torrents(items, podcast, tmp_path, {})  # no client config
        assert "Cannot build torrent client" in caplog.text
        assert items[0].info_hash is None  # untouched, retried next cycle


class TestPathTraversalGuard:
    def test_suspicious_relative_paths_skipped(self, tmp_path, caplog):
        """File entries with traversal segments must never be read or spawned."""
        podcast_dir = tmp_path / "podcast"
        save_dir = tmp_path / "downloads"
        make_mp3(save_dir / "t1" / "good.mp3", title="Good")
        client = FakeTorrentClient()
        client.torrents.add("hash1")
        client.complete.add("hash1")
        client.files["hash1"] = [
            make_fileinfo(save_dir / "t1" / "good.mp3", "t1/good.mp3"),
            make_fileinfo(save_dir / ".." / "evil.mp3", "../evil.mp3"),
            TorrentFileInfo(absolute_path=Path("/etc/evil.mp3"), relative_path=Path("/etc/evil.mp3")),
        ]
        item = make_item(info_hash="hash1")
        podcast = make_podcast()
        with caplog.at_level("WARNING"):
            fetch_torrent_item(item, podcast, podcast_dir, make_config(), client)
        assert "suspicious path" in caplog.text
        assert [ep.guid for ep in podcast.episodes] == ["hash1:t1/good.mp3"]
        assert item.fetched_at is not None
