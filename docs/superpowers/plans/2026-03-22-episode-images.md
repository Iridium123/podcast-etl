# Episode Image Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract episode images from RSS feeds, download/cache/convert them, embed as ID3 album art, and use as tracker cover image overrides.

**Architecture:** New `images.py` module provides download, resolve, and convert helpers. Tag step and upload step each call these helpers independently. Episode model gains `image_url` field parsed from feedparser.

**Tech Stack:** feedparser (existing), httpx (existing), Pillow (new), mutagen (existing)

**Spec:** `docs/superpowers/specs/2026-03-22-episode-images-design.md`

---

### Task 1: Add Pillow dependency

**Files:**
- Modify: `pyproject.toml:6-15`

- [ ] **Step 1: Add Pillow to dependencies**

In `pyproject.toml`, add `"Pillow>=10.0",` to the `dependencies` list:

```toml
dependencies = [
    "feedparser>=6.0",
    "httpx>=0.28",
    "click>=8.0",
    "pyyaml>=6.0",
    "mutagen>=1.47",
    "torf>=4.3.1",
    "anthropic>=0.40",
    "faster-whisper>=1.1",
    "Pillow>=10.0",
]
```

- [ ] **Step 2: Install**

Run: `uv sync`
Expected: installs Pillow successfully

- [ ] **Step 3: Verify import**

Run: `uv run python -c "from PIL import Image; print('OK')"`
Expected: prints `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Add Pillow dependency for image processing"
```

---

### Task 2: Add `image_url` to Episode model

**Files:**
- Modify: `src/podcast_etl/models.py:66-106`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing test for Episode with image_url**

Add to `tests/test_models.py`:

```python
def test_episode_dict_roundtrip_with_image_url():
    ep = _make_episode(image_url="https://example.com/ep1.jpg")
    assert Episode.from_dict(ep.to_dict()) == ep
    assert ep.to_dict()["image_url"] == "https://example.com/ep1.jpg"


def test_episode_dict_roundtrip_without_image_url():
    ep = _make_episode()
    assert ep.image_url is None
    roundtripped = Episode.from_dict(ep.to_dict())
    assert roundtripped.image_url is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_episode_dict_roundtrip_with_image_url -v`
Expected: FAIL — `_make_episode` doesn't accept `image_url`, `Episode` has no `image_url` field

- [ ] **Step 3: Add image_url field to Episode**

In `src/podcast_etl/models.py`, add `image_url: str | None` to the `Episode` dataclass after `description` (line 73). It must come before `slug` because `slug` has no default and `image_url` needs a default of `None`:

Actually, looking at the field order — all fields before `slug` can be None, and `slug` is required. `image_url` should go after `slug` but before `status` (which has a default). Place it at line 75:

```python
@dataclass
class Episode:
    title: str
    guid: str
    published: str | None
    audio_url: str | None
    duration: str | None
    description: str | None
    slug: str
    image_url: str | None = None
    status: dict[str, StepStatus | None] = field(default_factory=dict)
```

Update `to_dict` to include `image_url`:

```python
    def to_dict(self) -> dict[str, Any]:
        status_dict = {}
        for step_name, step_status in self.status.items():
            status_dict[step_name] = step_status.to_dict() if step_status else None
        return {
            "title": self.title,
            "guid": self.guid,
            "published": self.published,
            "audio_url": self.audio_url,
            "duration": self.duration,
            "description": self.description,
            "slug": self.slug,
            "image_url": self.image_url,
            "status": status_dict,
        }
```

Update `from_dict` to read `image_url`:

```python
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Episode:
        status = {}
        for step_name, step_data in data.get("status", {}).items():
            status[step_name] = StepStatus.from_dict(step_data) if step_data else None
        return cls(
            title=data["title"],
            guid=data["guid"],
            published=data.get("published"),
            audio_url=data.get("audio_url"),
            duration=data.get("duration"),
            description=data.get("description"),
            slug=data["slug"],
            image_url=data.get("image_url"),
            status=status,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: all pass, including the two new tests

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/models.py tests/test_models.py
git commit -m "Add image_url field to Episode model"
```

---

### Task 3: Extract episode image_url in feed.py

**Files:**
- Modify: `src/podcast_etl/feed.py:75-83`
- Test: `tests/test_feed.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_feed.py`. First update `_Entry` to support an `image` field:

In the `_Entry.__init__` method, add an `image` parameter:

```python
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
```

Then add the tests:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_feed.py::test_parse_feed_episode_image_url_extracted -v`
Expected: FAIL — Episode constructor doesn't receive `image_url`

- [ ] **Step 3: Extract image_url in parse_feed**

In `src/podcast_etl/feed.py`, inside the `for entry in feed.entries` loop, extract the image URL and pass it to the Episode constructor. Add before the `Episode(...)` call (around line 75):

```python
        ep_image_url = None
        ep_image = entry.get("image")
        if ep_image:
            ep_image_url = ep_image.get("href")
```

Then add `image_url=ep_image_url` to the `Episode(...)` constructor call:

```python
        episode = Episode(
            title=title,
            guid=guid,
            published=entry.get("published"),
            audio_url=audio_url,
            duration=entry.get("itunes_duration"),
            description=description,
            slug=ep_slug,
            image_url=ep_image_url,
        )
```

- [ ] **Step 4: Run all feed tests**

Run: `uv run pytest tests/test_feed.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/feed.py tests/test_feed.py
git commit -m "Extract episode image_url from RSS feed"
```

---

### Task 4: Create images.py — download_image

**Files:**
- Create: `src/podcast_etl/images.py`
- Create: `tests/test_images.py`

- [ ] **Step 1: Write failing tests for download_image**

Create `tests/test_images.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_images.py::TestDownloadImage -v`
Expected: FAIL — `podcast_etl.images` module does not exist

- [ ] **Step 3: Implement download_image**

Create `src/podcast_etl/images.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path

import httpx

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_images.py::TestDownloadImage -v`
Expected: all 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/images.py tests/test_images.py
git commit -m "Add download_image helper"
```

---

### Task 5: Add convert_image to images.py

**Files:**
- Modify: `src/podcast_etl/images.py`
- Modify: `tests/test_images.py`

- [ ] **Step 1: Write failing tests for convert_image**

Add to `tests/test_images.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_images.py::TestConvertImage -v`
Expected: FAIL — `convert_image` not yet defined

- [ ] **Step 3: Implement convert_image**

Add to `src/podcast_etl/images.py`:

```python
from PIL import Image


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
```

Note: `Image.thumbnail()` resizes in-place, preserves aspect ratio, and does not upscale — exactly what we need.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_images.py::TestConvertImage -v`
Expected: all 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/images.py tests/test_images.py
git commit -m "Add convert_image helper"
```

---

### Task 6: Add resolve_episode_image to images.py

**Files:**
- Modify: `src/podcast_etl/images.py`
- Modify: `tests/test_images.py`

- [ ] **Step 1: Write failing tests for resolve_episode_image**

Add to `tests/test_images.py`:

```python
from podcast_etl.images import resolve_episode_image
from podcast_etl.models import Episode, Podcast
from podcast_etl.pipeline import PipelineContext


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


def _make_episode(**kwargs):
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
    def test_downloads_episode_image(self, tmp_path):
        ep = _make_episode(image_url="https://example.com/ep1.jpg")
        ctx = _make_context(tmp_path)
        mock_response = MagicMock()
        mock_response.content = b"ep-image"
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.images.httpx.get", return_value=mock_response):
            result = resolve_episode_image(ep, ctx)

        assert result is not None
        assert result.exists()
        assert result.read_bytes() == b"ep-image"

    def test_returns_none_when_no_image_url(self, tmp_path):
        ep = _make_episode()
        ctx = _make_context(tmp_path)

        result = resolve_episode_image(ep, ctx)

        assert result is None

    def test_skips_episode_image_matching_feed_image(self, tmp_path):
        ep = _make_episode(image_url="https://example.com/feed-cover.jpg")
        ctx = _make_context(tmp_path)

        result = resolve_episode_image(ep, ctx)

        assert result is None

    def test_falls_back_to_feed_image_when_allowed(self, tmp_path):
        ep = _make_episode()  # no episode image
        ctx = _make_context(tmp_path)
        mock_response = MagicMock()
        mock_response.content = b"feed-image"
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.images.httpx.get", return_value=mock_response):
            result = resolve_episode_image(ep, ctx, allow_feed_fallback=True)

        assert result is not None
        assert result.read_bytes() == b"feed-image"

    def test_no_fallback_by_default(self, tmp_path):
        ep = _make_episode()  # no episode image
        ctx = _make_context(tmp_path)

        result = resolve_episode_image(ep, ctx)

        assert result is None

    def test_returns_none_on_download_failure(self, tmp_path):
        ep = _make_episode(image_url="https://example.com/broken.jpg")
        ctx = _make_context(tmp_path)

        with patch("podcast_etl.images.httpx.get", side_effect=httpx.HTTPError("fail")):
            result = resolve_episode_image(ep, ctx)

        assert result is None

    def test_no_fallback_when_feed_has_no_image(self, tmp_path):
        podcast = Podcast(
            title="Test Podcast", url="https://example.com/feed.xml",
            description=None, image_url=None, slug="test-podcast",
        )
        ep = _make_episode()
        ctx = _make_context(tmp_path, podcast=podcast)

        result = resolve_episode_image(ep, ctx, allow_feed_fallback=True)

        assert result is None
```

Also add `import httpx` at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_images.py::TestResolveEpisodeImage -v`
Expected: FAIL — `resolve_episode_image` not yet defined

- [ ] **Step 3: Implement resolve_episode_image**

Add to `src/podcast_etl/images.py`:

```python
from podcast_etl.models import Episode, episode_basename
from podcast_etl.pipeline import PipelineContext


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_images.py -v`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/images.py tests/test_images.py
git commit -m "Add resolve_episode_image helper"
```

---

### Task 7: Embed APIC in tag step

**Files:**
- Modify: `src/podcast_etl/steps/tag.py:1-71`
- Modify: `tests/test_tag_step.py`

- [ ] **Step 1: Write failing tests for APIC embedding**

Add to `tests/test_tag_step.py`:

```python
from unittest.mock import MagicMock, patch
from mutagen.id3 import APIC


def test_tag_step_embeds_episode_image(tmp_path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".mp3")
    ep = _make_episode(
        status=_download_status("audio/ep-1.mp3"),
        image_url="https://example.com/ep1.jpg",
    )

    # Create a fake raw image for resolve_episode_image to return
    images_dir = ctx.podcast_dir / "images"
    images_dir.mkdir(parents=True)
    raw_image = images_dir / "raw.jpg"
    # Create a minimal valid JPEG-like file via Pillow
    from PIL import Image as PILImage
    PILImage.new("RGB", (100, 100), "red").save(raw_image, "JPEG")

    with patch("podcast_etl.steps.tag.resolve_episode_image", return_value=raw_image):
        result = TagStep().process(ep, ctx)

    audio_path = ctx.podcast_dir / result.data["path"]
    tags = ID3(audio_path)
    apic_frames = tags.getall("APIC")
    assert len(apic_frames) == 1
    assert apic_frames[0].mime == "image/jpeg"


def test_tag_step_no_image_skips_apic(tmp_path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".mp3")
    ep = _make_episode(status=_download_status("audio/ep-1.mp3"))

    with patch("podcast_etl.steps.tag.resolve_episode_image", return_value=None):
        result = TagStep().process(ep, ctx)

    audio_path = ctx.podcast_dir / result.data["path"]
    tags = ID3(audio_path)
    assert tags.getall("APIC") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tag_step.py::test_tag_step_embeds_episode_image -v`
Expected: FAIL — `resolve_episode_image` not imported in tag step, no APIC embedding logic

- [ ] **Step 3: Implement APIC embedding in tag step**

In `src/podcast_etl/steps/tag.py`:

Add import at the top:

```python
from mutagen.id3 import APIC, COMM, ID3, ID3NoHeaderError, TDRC, TDRL, TIT2, TPE1

from podcast_etl.images import convert_image, resolve_episode_image
from podcast_etl.models import Episode, episode_basename
```

In the `process` method, after the `self._tag_mp3(...)` call and before the `logger.info(...)` line, add:

```python
        # Embed episode image as album art
        raw_image = resolve_episode_image(episode, context, allow_feed_fallback=True)
        if raw_image:
            images_dir = context.podcast_dir / "images"
            basename = episode_basename(context.effective_title, episode.title, episode.published)
            embed_path = images_dir / f"{basename}-embed.jpg"
            try:
                convert_image(raw_image, embed_path, max_size=(600, 600))
                self._embed_cover(audio_path, embed_path)
            except Exception:
                logger.warning("Failed to embed cover image for %s", episode.slug, exc_info=True)
```

Add the `_embed_cover` method to the `TagStep` class:

```python
    def _embed_cover(self, audio_path: Path, image_path: Path) -> None:
        try:
            tags = ID3(audio_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,  # Cover (front)
            desc="Cover",
            data=image_path.read_bytes(),
        ))
        tags.save(audio_path)
```

- [ ] **Step 4: Run all tag step tests**

Run: `uv run pytest tests/test_tag_step.py -v`
Expected: all pass. Existing tests still pass because `resolve_episode_image` returns `None` when episode has no `image_url` and podcast has no `image_url` (the test helper `_make_podcast` sets `image_url=None`).

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/steps/tag.py tests/test_tag_step.py
git commit -m "Embed episode image as APIC album art in tag step"
```

---

### Task 8: Add cover_image_override to tracker

**Files:**
- Modify: `src/podcast_etl/trackers/unit3d.py:83-90,130-138`
- Modify: `tests/test_unit3d_tracker.py`

- [ ] **Step 1: Write failing tests for cover_image_override**

Add to `tests/test_unit3d_tracker.py`, inside the `TestUpload` class:

```python
    def test_cover_image_override_takes_priority(self, torrent_path, feed_config, tmp_path):
        override_cover = tmp_path / "override.jpg"
        override_cover.write_bytes(b"override-jpeg")
        config_cover = tmp_path / "config-cover.jpg"
        config_cover.write_bytes(b"config-jpeg")
        feed_with_cover = {**feed_config, "cover_image": str(config_cover)}

        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(
                torrent_path, episode, podcast, feed_with_cover,
                cover_image_override=override_cover,
            )

        upload_call = client.post.call_args_list[1]
        files = upload_call.kwargs["files"]
        cover_entries = [(name, data) for name, data in files if name == "torrent-cover"]
        assert len(cover_entries) == 1
        assert cover_entries[0][1][1] == b"override-jpeg"

    def test_no_override_uses_feed_config_cover(self, torrent_path, feed_config, tmp_path):
        config_cover = tmp_path / "config-cover.jpg"
        config_cover.write_bytes(b"config-jpeg")
        feed_with_cover = {**feed_config, "cover_image": str(config_cover)}

        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_with_cover)

        upload_call = client.post.call_args_list[1]
        files = upload_call.kwargs["files"]
        cover_entries = [(name, data) for name, data in files if name == "torrent-cover"]
        assert len(cover_entries) == 1
        assert cover_entries[0][1][1] == b"config-jpeg"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_unit3d_tracker.py::TestUpload::test_cover_image_override_takes_priority -v`
Expected: FAIL — `upload()` doesn't accept `cover_image_override`

- [ ] **Step 3: Add cover_image_override parameter to upload()**

In `src/podcast_etl/trackers/unit3d.py`, update the `upload` method signature (line 83-90):

```python
    def upload(
        self,
        torrent_path: Path,
        episode: Episode,
        podcast: Podcast,
        feed_config: dict[str, Any],
        audio_path: Path | None = None,
        cover_image_override: Path | None = None,
    ) -> dict[str, Any]:
```

Replace the cover image block (lines 134-138) with:

```python
            cover_path = cover_image_override or (
                Path(feed_config["cover_image"]) if feed_config.get("cover_image") else None
            )
            if cover_path:
                mime = mimetypes.guess_type(cover_path.name)[0] or "image/jpeg"
                files.append(("torrent-cover", (cover_path.name, cover_path.read_bytes(), mime)))
```

- [ ] **Step 4: Run all tracker tests**

Run: `uv run pytest tests/test_unit3d_tracker.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/trackers/unit3d.py tests/test_unit3d_tracker.py
git commit -m "Add cover_image_override parameter to tracker upload"
```

---

### Task 9: Pass cover override from upload step

**Files:**
- Modify: `src/podcast_etl/steps/upload.py:1-55`
- Modify: `tests/test_upload_step.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_upload_step.py`:

```python
class TestUploadStepCoverOverride:
    def test_passes_episode_image_as_cover_override(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        # Create a fake resolved image
        images_dir = context.podcast_dir / "images"
        images_dir.mkdir(parents=True)
        raw_image = images_dir / "raw.jpg"
        raw_image.write_bytes(b"raw-data")
        converted = images_dir / "cover.jpg"
        converted.write_bytes(b"converted-data")

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with (
            patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker),
            patch("podcast_etl.steps.upload.resolve_episode_image", return_value=raw_image),
            patch("podcast_etl.steps.upload.convert_image", return_value=converted),
        ):
            UploadStep().process(episode, context)

        call_kwargs = mock_tracker.upload.call_args.kwargs
        assert call_kwargs["cover_image_override"] == converted

    def test_no_episode_image_passes_none(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with (
            patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker),
            patch("podcast_etl.steps.upload.resolve_episode_image", return_value=None),
        ):
            UploadStep().process(episode, context)

        call_kwargs = mock_tracker.upload.call_args.kwargs
        assert call_kwargs.get("cover_image_override") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_upload_step.py::TestUploadStepCoverOverride -v`
Expected: FAIL — `resolve_episode_image` not imported in upload step

- [ ] **Step 3: Implement cover override logic in upload step**

In `src/podcast_etl/steps/upload.py`, add imports:

```python
from podcast_etl.images import convert_image, resolve_episode_image
from podcast_etl.models import Episode, episode_basename
```

In the `process` method, after `audio_path = _resolve_audio_path(episode)` (line 40), add:

```python
        # Resolve episode cover image (no feed fallback for tracker)
        cover_override = None
        raw_image = resolve_episode_image(episode, context, allow_feed_fallback=False)
        if raw_image:
            images_dir = context.podcast_dir / "images"
            basename = episode_basename(
                context.effective_title, episode.title, episode.published
            )
            cover_path = images_dir / f"{basename}-cover.jpg"
            try:
                cover_override = convert_image(raw_image, cover_path, max_size=(500, 500))
            except Exception:
                logger.warning("Failed to convert cover image for %s", episode.slug, exc_info=True)
```

Update the `tracker.upload()` call to pass the override:

```python
        upload_result = tracker.upload(
            torrent_path=Path(torrent_path),
            episode=episode,
            podcast=context.podcast,
            feed_config=context.feed_config,
            audio_path=audio_path,
            cover_image_override=cover_override,
        )
```

- [ ] **Step 4: Fix existing test_calls_tracker_upload assertion**

The existing `test_calls_tracker_upload` in `tests/test_upload_step.py` (line 79-85) asserts on the exact kwargs passed to `tracker.upload()`. Since we now pass `cover_image_override`, update it:

```python
    def test_calls_tracker_upload(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with (
            patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker),
            patch("podcast_etl.steps.upload.resolve_episode_image", return_value=None),
        ):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_called_once_with(
            torrent_path=Path(TORRENT_PATH),
            episode=episode,
            podcast=context.podcast,
            feed_config=context.feed_config,
            audio_path=None,
            cover_image_override=None,
        )
        assert result.data["torrent_id"] == 42
        assert result.data["url"] == "https://tracker.example.com/torrents/42"
```

- [ ] **Step 5: Run all upload step tests**

Run: `uv run pytest tests/test_upload_step.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/podcast_etl/steps/upload.py tests/test_upload_step.py
git commit -m "Pass episode cover image override to tracker in upload step"
```

---

### Task 10: Run full test suite and fix any issues

**Files:**
- All modified files from prior tasks

- [ ] **Step 1: Run entire test suite**

Run: `uv run pytest tests/ -v`
Expected: all pass

- [ ] **Step 2: Fix any failures**

If any existing tests fail due to the new `image_url` field or the new `resolve_episode_image` calls, fix them. Common fixes:
- Tests that construct `Episode` directly may need to verify they still work (the `image_url` field has a default of `None`, so they should)
- Upload step tests that assert on `tracker.upload.call_args` need to account for the new `cover_image_override` kwarg

- [ ] **Step 3: Commit fixes if needed**

```bash
git add -u
git commit -m "Fix test compatibility with episode image changes"
```

---

### Task 11: Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md**

In the Architecture section, add `images.py` to the module list:

```
9.5. `images.py` — `download_image` (URL download with caching), `resolve_episode_image` (episode/feed image resolution with fallback), `convert_image` (Pillow resize + JPEG conversion)
```

In the `Episode` model description, mention `image_url`.

Add `test_images.py` to the test list:

```
- `test_images.py` — `download_image` (caching, extension extraction, fallback), `resolve_episode_image` (episode/feed fallback, dedup, error handling), `convert_image` (resize, format conversion, no upscale)
```

- [ ] **Step 2: Update README.md**

Add a brief note about episode images in the pipeline description. Mention that `tag` embeds episode artwork as ID3 album art when available, and `upload` uses episode artwork as tracker cover when available (falling back to `cover_image` config).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "Document episode image support in CLAUDE.md and README.md"
```
