# Episode Number Support

## Summary

Parse `itunes:episode` from RSS feeds, write it as an ID3 track number tag, and provide an opt-in title cleaning rule to prepend it to episode titles.

## Changes

### 1. New field: `Episode.episode_number: int | None`

- `models.py`: add `episode_number` field (default `None`) with `to_dict`/`from_dict` serialization
- `feed.py`: read `entry.get("itunes_episode")`, parse to `int`, store on Episode; drop non-numeric values silently

### 2. Track number tag (unconditional)

- `steps/tag.py`: import `TRCK` from mutagen; when `episode.episode_number is not None`, write `TRCK` frame with the episode number string

### 3. Title prepend (opt-in)

- `title_clean.py`: new `prepend_episode_number(title, episode_number)` function that returns `"{number} - {title}"`
- `clean_title()` chain order: `strip_date` -> `reorder_parts` -> `prepend_episode_number` -> `sanitize`
- `clean_title()` gains an `episode_number: int | None = None` parameter
- `feed.py`: passes `episode_number` (parsed from `itunes_episode`) to `clean_title()`
- Config key: `title_cleaning.prepend_episode_number: true` (default `false`)

### 4. Config / docs

- `CLAUDE.md` and `README.md`: document `prepend_episode_number` in `title_cleaning` block and `episode_number` on Episode model
- `feeds.yaml` example: add `prepend_episode_number: false` to `title_cleaning` defaults

## Title cleaning chain

```
strip_date -> reorder_parts -> prepend_episode_number -> sanitize
```

Example: `Rise of the Mongols (Part 3) (3/19/26)` with episode number 123:
1. strip_date: `Rise of the Mongols (Part 3)`
2. reorder_parts: `Part 3 - Rise of the Mongols` (with sibling context)
3. prepend_episode_number: `123 - Part 3 - Rise of the Mongols`
4. sanitize: `123 - Part 3 - Rise of the Mongols` (no-op here)

## Testing

- `test_feed.py`: episode_number parsed from itunes_episode, non-numeric ignored, missing gives None
- `test_title_clean.py`: prepend_episode_number function, clean_title integration with new ordering
- `test_tag_step.py`: TRCK tag written when episode_number set, omitted when None
- `test_models.py`: episode_number round-trips through to_dict/from_dict and JSON serialization
