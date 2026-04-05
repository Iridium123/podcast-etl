"""Tests for images.py: download, resolve, and convert helpers."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from podcast_etl.images import download_image, resolve_episode_image
from podcast_etl.models import Episode, Podcast
from podcast_etl.pipeline import PipelineContext


class TestDownloadImage:
    def test_downloads_to_output_dir(self, tmp_path):
        mock_response = MagicMock()
        mock_response.content = b"fake-image-data"
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.images.httpx.get", return_value=mock_response):
            result = download_image(
                "https://example.com/image.jpg", tmp_path, "episode-1"
            )

        assert result == tmp_path / "episode-1.jpg"
        assert result.read_bytes() == b"fake-image-data"

    def test_returns_cached_path_if_exists(self, tmp_path):
        cached = tmp_path / "episode-1.jpg"
        cached.write_bytes(b"existing")

        with patch("podcast_etl.images.httpx.get") as mock_get:
            result = download_image(
                "https://example.com/image.jpg", tmp_path, "episode-1"
            )

        mock_get.assert_not_called()
        assert result == cached

    def test_strips_query_params_for_extension(self, tmp_path):
        mock_response = MagicMock()
        mock_response.content = b"data"
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.images.httpx.get", return_value=mock_response):
            result = download_image(
                "https://cdn.example.com/img.png?token=abc&size=Large",
                tmp_path,
                "episode-1",
            )

        assert result.suffix == ".png"

    def test_falls_back_to_jpg_when_no_extension(self, tmp_path):
        mock_response = MagicMock()
        mock_response.content = b"data"
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.images.httpx.get", return_value=mock_response):
            result = download_image(
                "https://cdn.example.com/images/abc123",
                tmp_path,
                "episode-1",
            )

        assert result.suffix == ".jpg"


from PIL import Image

from podcast_etl.images import convert_image


class TestConvertImage:
    def _make_test_image(self, path: Path, size=(800, 800), mode="RGB") -> Path:
        img = Image.new(mode, size, color="red")
        img.save(path)
        return path

    def test_converts_to_jpeg(self, tmp_path):
        source = self._make_test_image(tmp_path / "source.png")
        dest = tmp_path / "out.jpg"

        result = convert_image(source, dest, max_size=(600, 600))

        assert result == dest
        img = Image.open(dest)
        assert img.format == "JPEG"

    def test_resizes_to_fit_within_max_size(self, tmp_path):
        source = self._make_test_image(tmp_path / "source.png", size=(1000, 500))
        dest = tmp_path / "out.jpg"

        convert_image(source, dest, max_size=(600, 600))

        img = Image.open(dest)
        assert img.width == 600
        assert img.height == 300

    def test_no_upscale(self, tmp_path):
        source = self._make_test_image(tmp_path / "source.png", size=(200, 200))
        dest = tmp_path / "out.jpg"

        convert_image(source, dest, max_size=(600, 600))

        img = Image.open(dest)
        assert img.width == 200
        assert img.height == 200

    def test_converts_rgba_to_rgb(self, tmp_path):
        source = self._make_test_image(tmp_path / "source.png", mode="RGBA")
        dest = tmp_path / "out.jpg"

        convert_image(source, dest, max_size=(600, 600))

        img = Image.open(dest)
        assert img.mode == "RGB"


def _make_context(tmp_path, podcast=None):
    if podcast is None:
        podcast = Podcast(
            title="Test Podcast",
            url="https://example.com/feed.xml",
            description=None,
            image_url="https://example.com/feed-cover.jpg",
            slug="test-podcast",
        )
    return PipelineContext(output_dir=tmp_path, podcast=podcast)


def make_episode(**kwargs):
    defaults = dict(
        title="Episode 1",
        guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration=None,
        description=None,
        slug="ep-1",
    )
    defaults.update(kwargs)
    return Episode(**defaults)


class TestResolveEpisodeImage:
    def test_downloads_episode_image(self, tmp_path, make_episode):
        ep = make_episode(image_url="https://example.com/ep1.jpg")
        ctx = _make_context(tmp_path)
        mock_response = MagicMock()
        mock_response.content = b"ep-image"
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.images.httpx.get", return_value=mock_response):
            result = resolve_episode_image(ep, ctx)

        assert result is not None
        assert result.exists()
        assert result.read_bytes() == b"ep-image"

    def test_returns_none_when_no_image_url(self, tmp_path, make_episode):
        ep = make_episode()
        ctx = _make_context(tmp_path)

        result = resolve_episode_image(ep, ctx)

        assert result is None

    def test_skips_episode_image_matching_feed_image(self, tmp_path, make_episode):
        ep = make_episode(image_url="https://example.com/feed-cover.jpg")
        ctx = _make_context(tmp_path)

        result = resolve_episode_image(ep, ctx)

        assert result is None

    def test_falls_back_to_feed_image_when_allowed(self, tmp_path, make_episode):
        ep = make_episode()  # no episode image
        ctx = _make_context(tmp_path)
        mock_response = MagicMock()
        mock_response.content = b"feed-image"
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.images.httpx.get", return_value=mock_response):
            result = resolve_episode_image(ep, ctx, allow_feed_fallback=True)

        assert result is not None
        assert result.read_bytes() == b"feed-image"

    def test_no_fallback_by_default(self, tmp_path, make_episode):
        ep = make_episode()  # no episode image
        ctx = _make_context(tmp_path)

        result = resolve_episode_image(ep, ctx)

        assert result is None

    def test_returns_none_on_download_failure(self, tmp_path, make_episode):
        ep = make_episode(image_url="https://example.com/broken.jpg")
        ctx = _make_context(tmp_path)

        with patch("podcast_etl.images.httpx.get", side_effect=httpx.HTTPError("fail")):
            result = resolve_episode_image(ep, ctx)

        assert result is None

    def test_no_fallback_when_feed_has_no_image(self, tmp_path, make_episode):
        podcast = Podcast(
            title="Test Podcast", url="https://example.com/feed.xml",
            description=None, image_url=None, slug="test-podcast",
        )
        ep = make_episode()
        ctx = _make_context(tmp_path, podcast=podcast)

        result = resolve_episode_image(ep, ctx, allow_feed_fallback=True)

        assert result is None
