# GUID-Based Episode JSON Storage

## Problem

Episode JSON files are named after the cleaned episode title (`episode_basename()`). If a title changes in the RSS feed or title cleaning config changes, `Episode.save()` writes a new JSON file alongside the old one. `Podcast.load()` globs all `*.json` files and loads both, creating duplicate episodes in memory. The pipeline may reprocess already-completed episodes and produce duplicate output files.

## Decision

Change episode JSON filenames from title-based to GUID-based. Audio and other output files remain title-based and human-readable (unchanged).

## Filename Format

```
{date}-{raw-title-slug}-{guid-hash}.json
```

- **date**: `yyyy-mm-dd` from `published`, or `unknown-date`
- **raw-title-slug**: `slugify()` applied to the original RSS title (before any `clean_title()` processing), truncated to ~60 chars at a word boundary
- **guid-hash**: first 8 characters of SHA256 hex digest of the GUID

Example: `2025-03-01-my-great-episode-title-a1b2c3d4.json`

The hash suffix ensures uniqueness even if two episodes share the same date and similar titles. The date and raw title prefix keeps files easy to identify when browsing on disk.

## Changes

### `models.py`

- New function `episode_json_filename(guid, raw_title, published)` — produces the stable GUID-based filename (no extension).
- `Episode` gets a new optional field `raw_title: str | None = None` to store the original RSS title before cleaning.
- `Episode.to_dict()` / `Episode.from_dict()` serialize/deserialize `raw_title`.
- `Episode.save()` calls `episode_json_filename()` instead of `episode_basename()` for the JSON filename.
- `Podcast.load()` deduplicates episodes by GUID after loading all JSON files. When duplicates exist, keeps the episode with more completed steps and deletes the stale JSON file from disk.

### `feed.py`

- `parse_feed()` captures the original RSS title into `raw_title` before applying `clean_title()`.
- When merging with existing on-disk episodes, `raw_title` is preserved from the existing episode if the freshly parsed one has none (shouldn't happen, but defensive).

### `cli.py`

- New `migrate` command with required `--feed` flag.
- Scans `output/<podcast>/episodes/*.json`, loads each episode, computes the new GUID-based filename, and renames.
- Files already in the new format are skipped.
- If a new-format file already exists for the same GUID (duplicate), keeps the one with more completed steps and deletes the other.
- Reports each rename and a summary count.

### No changes to pipeline steps

Download, tag, detect_ads, strip_ads, stage, torrent, seed, upload, and audiobookshelf steps are untouched. Audio file naming continues to use `episode_basename()`.

## GUID Deduplication in `Podcast.load()`

Since the Docker container may fetch a feed (writing new-format JSON) before `migrate` is run, both old-format and new-format files for the same episode can coexist. `Podcast.load()` handles this:

1. Load all `*.json` files from the episodes directory.
2. Group by `episode.guid`.
3. For each GUID with multiple files: keep the episode with the most completed steps, delete the other file(s) from disk.
4. Log a warning when duplicates are cleaned up.

This makes the system self-healing regardless of migration timing.

## Migration

```
podcast-etl migrate --feed my-podcast
```

- Requires `--feed` (name or URL).
- Loads each episode JSON from the feed's output directory.
- Computes the new GUID-based filename using `episode_json_filename()`.
- Renames files that don't match the new pattern.
- Handles duplicates (same GUID, different filenames) by keeping the most-complete one.
- Reports results to stdout.

## Edge Cases

- **No `raw_title` in existing JSON files**: Migration and `Podcast.load()` fall back to `episode.title` (the cleaned title) when `raw_title` is absent. This is slightly less stable than using the true raw title, but acceptable for pre-migration data.
- **GUID missing from a JSON file**: Skip with a warning (shouldn't happen — GUID is a required field).
- **Duplicate GUIDs on disk**: Keep the episode with more completed steps. On tie, keep the newer file (by mtime).
- **Truncation collisions**: Two episodes with the same date and similar long titles could have the same truncated slug, but the GUID hash suffix differentiates them.

## What Does NOT Change

- Audio filenames (`episode_basename()` in download, tag, etc.)
- Step result paths stored inside episode JSON
- `podcast.json` format
- Slug generation or deduplication in `parse_feed()`
- Any pipeline step behavior
