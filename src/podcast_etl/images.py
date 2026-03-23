from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from PIL import Image

if TYPE_CHECKING:
    from podcast_etl.models import Episode
    from podcast_etl.pipeline import PipelineContext

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


def resolve_episode_image(
    episode: Episode,
    context: PipelineContext,
    *,
    allow_feed_fallback: bool = False,
) -> Path | None:
    """Download and cache the episode image. Returns path or None.

    If the episode has no image (or its image matches the feed image),
    falls back to the feed image only when allow_feed_fallback is True.
    Download/conversion failures are non-fatal: logs warning, returns None.
    """
    from podcast_etl.models import episode_basename

    images_dir = context.podcast_dir / "images"
    podcast = context.podcast

    # Try episode-specific image (skip if same as feed image)
    if episode.image_url and episode.image_url != podcast.image_url:
        basename = episode_basename(
            context.effective_title, episode.title, episode.published
        )
        try:
            return download_image(episode.image_url, images_dir, basename)
        except Exception:
            logger.warning("Failed to download episode image for %s", episode.slug, exc_info=True)
            return None

    # Fall back to feed-level image
    if allow_feed_fallback and podcast.image_url:
        try:
            return download_image(podcast.image_url, images_dir, "feed-image")
        except Exception:
            logger.warning("Failed to download feed image", exc_info=True)
            return None

    return None


def convert_image(source: Path, dest: Path, *, max_size: tuple[int, int]) -> Path:
    """Convert an image to JPEG, resizing to fit within max_size. No upscaling."""
    with Image.open(source) as img:
        # Resize to fit within max_size, preserving aspect ratio, no upscale
        img.thumbnail(max_size, Image.LANCZOS)

        # Convert RGBA/P/LA to RGB for JPEG
        if img.mode != "RGB":
            img = img.convert("RGB")

        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=85)
    return dest
