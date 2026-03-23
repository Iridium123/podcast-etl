from __future__ import annotations

import logging
from pathlib import Path

import httpx
from PIL import Image

logger = logging.getLogger(__name__)


def _extract_extension(url: str) -> str:
    """Extract file extension from a URL, stripping query params. Falls back to .jpg."""
    path = url.split("?")[0]
    filename = path.split("/")[-1]
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ".jpg"


def download_image(url: str, output_dir: Path, basename: str) -> Path:
    """Download an image from a URL, caching to output_dir/<basename>.<ext>.

    Returns the cached path if the file already exists on disk.
    """
    ext = _extract_extension(url)
    dest = output_dir / f"{basename}{ext}"

    if dest.exists():
        logger.debug("Image already cached: %s", dest)
        return dest

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading image %s -> %s", url, dest)
    response = httpx.get(url, follow_redirects=True, timeout=60)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest


def convert_image(source: Path, dest: Path, *, max_size: tuple[int, int]) -> Path:
    """Convert an image to JPEG, resizing to fit within max_size. No upscaling."""
    img = Image.open(source)

    # Resize to fit within max_size, preserving aspect ratio, no upscale
    img.thumbnail(max_size, Image.LANCZOS)

    # Convert RGBA/P/LA to RGB for JPEG
    if img.mode not in ("RGB",):
        img = img.convert("RGB")

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=85)
    return dest
