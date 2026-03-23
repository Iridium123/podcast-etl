# Episode Image Support

## Goal

Use episode-level artwork from RSS feeds as ID3 album art and tracker cover images, preferring per-episode images over feed-level defaults.

## Background

RSS feeds provide images at two levels via `<itunes:image href="...">`:

- **Feed-level**: always present, square (1400x1400 to 3000x3000). Already extracted as `Podcast.image_url`.
- **Episode-level**: optional per `<item>`. feedparser exposes as `entry["image"]["href"]`. Patreon feeds reliably provide unique per-episode images. Some feeds (Omny) repeat the feed image for every episode. Some (Libsyn) have episode images in raw XML that feedparser doesn't expose — accepted as a known limitation.

No standard exists for banner/wide images. `banner_image` stays config-only.

## Design

### 1. Episode model

Add `image_url: str | None` to the `Episode` dataclass. Parsed from `entry.get("image", {}).get("href")` in `feed.py`. Serialized in episode JSON via `to_dict`/`from_dict`.

### 2. Image download helper — `src/podcast_etl/images.py`

**`download_image(url, output_dir, basename) -> Path`**

- Downloads an image from a URL to `output_dir/<basename>.<ext>`.
- Strips query parameters from URL before extracting extension. Falls back to `.jpg` if no extension is present.
- Returns the cached path if the file already exists on disk (skip re-download).
- Raises on download failure.

**`resolve_episode_image(episode, context, *, allow_feed_fallback=False) -> Path | None`**

- Takes `episode` and `context` (gets `podcast` from `context.podcast`, matching existing helper conventions like `_resolve_audio_path`).
- If `episode.image_url` is set **and differs from `context.podcast.image_url`**, downloads to `output/<podcast>/images/` using `episode_basename()` from models.py.
- If episode image URL equals feed image URL, treats it as no episode image (avoids redundant downloads for feeds like Omny that repeat the feed art).
- If no episode image and `allow_feed_fallback=True`, downloads `context.podcast.image_url` instead. Cached once per podcast (e.g. `feed-image.<ext>`).
- Returns `None` if no image is available.
- Image download/conversion failures are **non-fatal**: logs a warning and returns `None`. The consuming step proceeds without an image.

**`convert_image(source, dest, *, max_size) -> Path`**

- Opens the source image with Pillow.
- Resizes to fit within `max_size` (tuple of width, height), preserving aspect ratio. No upscaling.
- Converts to RGB (drop alpha channel) and saves as JPEG at `dest`.
- Returns `dest`.

### 3. Tag step changes

After tagging metadata, call:

```python
raw = resolve_episode_image(episode, context, allow_feed_fallback=True)
if raw:
    converted = convert_image(raw, <images_dir>/<basename>-embed.jpg, max_size=(600, 600))
    # embed as APIC frame via mutagen
```

Embed the JPEG as an `APIC` (attached picture) ID3 frame with picture type "Cover (front)". Record the image path in the step result.

### 4. Upload step changes

Before calling `tracker.upload()`, call:

```python
raw = resolve_episode_image(episode, context, allow_feed_fallback=False)
if raw:
    converted = convert_image(raw, <images_dir>/<basename>-cover.jpg, max_size=(1400, 1400))
    # pass to tracker as cover override
```

If a converted episode image exists, pass it to the tracker, taking priority over `feed_config["cover_image"]`. If `None`, current behavior is unchanged.

The tracker's `upload()` method gains an optional `cover_image_override: Path | None` parameter. When set, it is used instead of `feed_config["cover_image"]`.

### 5. Tracker changes

`ModifiedUnit3dTracker.upload()` accepts a new `cover_image_override: Path | None = None` parameter. The cover image resolution order becomes:

1. `cover_image_override` (episode image, passed by upload step)
2. `feed_config["cover_image"]` (static config file, existing behavior)
3. No cover image

### 6. Dependencies

Add `Pillow` to project dependencies.

## Fallback summary

| Consumer | Episode image | Feed image | Config `cover_image` |
|----------|:---:|:---:|:---:|
| ID3 embed (tag step) | 1st | 2nd | N/A |
| Tracker cover (upload step) | 1st | No | 2nd |

## File changes

| File | Change |
|------|--------|
| `src/podcast_etl/models.py` | Add `image_url` to `Episode` |
| `src/podcast_etl/feed.py` | Extract episode `image_url` from feedparser |
| `src/podcast_etl/images.py` | New — download, resolve, convert helpers |
| `src/podcast_etl/steps/tag.py` | Download + convert + embed APIC |
| `src/podcast_etl/steps/upload.py` | Download + convert + pass override |
| `src/podcast_etl/trackers/unit3d.py` | Accept `cover_image_override` param |
| `pyproject.toml` | Add Pillow dependency |
| `tests/test_models.py` | Update Episode dict roundtrip tests |
| `tests/test_feed.py` | Test episode image_url extraction |
| `tests/test_images.py` | New — download, resolve, convert tests |
| `tests/test_tag_step.py` | Test APIC embedding |
| `tests/test_upload_step.py` | Test cover override passing |
| `tests/test_unit3d_tracker.py` | Test cover override precedence |
| `CLAUDE.md` | Document `images.py` module, `image_url` field |
| `README.md` | Document episode image behavior |

## Error handling

Image download and conversion failures are **non-fatal**. If an image can't be fetched or Pillow can't process it, the step logs a warning and continues without an image. Images are supplementary — they should never block core audio processing.

## Out of scope

- Manual XML parsing fallback for feedparser gaps (Libsyn). Can revisit if needed.
- Banner images from RSS (no standard exists).
- Image support for audiobookshelf step.
