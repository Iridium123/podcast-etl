"""Tests for images.py: download, resolve, and convert helpers."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.images import download_image


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
