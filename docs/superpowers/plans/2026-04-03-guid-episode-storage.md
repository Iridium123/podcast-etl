# GUID-Based Episode JSON Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch episode JSON filenames from title-based to GUID-based, preventing duplicates when RSS titles change.

**Architecture:** Add `episode_json_filename()` to produce stable `{date}-{slug}-{hash}.json` names using the raw RSS title and GUID hash. `Episode` gains a `raw_title` field set by `parse_feed()` before title cleaning. `Episode.save()` writes the new filename and deletes any stale title-based file. `Podcast.load()` deduplicates by GUID. A `migrate` CLI command renames existing files.

**Tech Stack:** Python, hashlib (SHA256), pytest

**Spec:** `docs/superpowers/specs/2026-04-03-guid-episode-storage-design.md`

---

### Task 1: `episode_json_filename()` function

**Files:**
- Modify: `src/podcast_etl/models.py:1-10` (imports + new function)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_models.py`:

```python
from podcast_etl.models import episode_json_filename


def test_episode_json_filename_basic():
    result = episode_json_filename("guid-123", "My Episode Title", "Mon, 01 Jan 2024 00:00:00 +0000")
    # date-slug-hash format
    assert result.startswith("2024-01-01-my-episode-title-")
    assert len(result.split("-")[-1]) == 8  # 8-char hex hash


def test_episode_json_filename_no_published():
    result = episode_json_filename("guid-123", "Title", None)
    assert result.startswith("unknown-date-title-")


def test_episode_json_filename_raw_title_none_uses_empty():
    result = episode_json_filename("guid-123", None, None)
    # With no title, slug is empty, so format is unknown-date-{hash}
    assert result.startswith("unknown-date-")
    assert len(result) == len("unknown-date-") + 8  # 8-char hash


def test_episode_json_filename_truncates_long_slug():
    long_title = "A " + "very " * 30 + "long title"
    result = episode_json_filename("guid-123", long_title, "Mon, 01 Jan 2024 00:00:00 +0000")
    # Remove date prefix and hash suffix to get slug portion
    # Format: 2024-01-01-{slug}-{hash}
    without_date = result[len("2024-01-01-"):]
    slug = without_date[:-(8 + 1)]  # remove -hash
    assert len(slug) <= 60


def test_episode_json_filename_truncates_at_word_boundary():
    # Build a title whose slug is exactly long enough to need truncation
    # "abcdefgh" repeated = 8 chars per word, slug uses dashes
    title = " ".join(["abcdefgh"] * 10)  # slug: "abcdefgh-abcdefgh-..." = 89 chars
    result = episode_json_filename("guid-123", title, "Mon, 01 Jan 2024 00:00:00 +0000")
    without_date = result[len("2024-01-01-"):]
    slug = without_date[:-(8 + 1)]  # remove -hash
    assert len(slug) <= 60
    assert not slug.endswith("-")


def test_episode_json_filename_same_guid_same_hash():
    a = episode_json_filename("guid-123", "Title A", None)
    b = episode_json_filename("guid-123", "Title B", None)
    assert a.split("-")[-1] == b.split("-")[-1]


def test_episode_json_filename_different_guid_different_hash():
    a = episode_json_filename("guid-1", "Same Title", None)
    b = episode_json_filename("guid-2", "Same Title", None)
    assert a.split("-")[-1] != b.split("-")[-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::test_episode_json_filename_basic -v`
Expected: FAIL with `ImportError: cannot import name 'episode_json_filename'`

- [ ] **Step 3: Implement `episode_json_filename()`**

Add to `src/podcast_etl/models.py` after the existing imports, add `import hashlib`. Then add the function after `episode_basename()`:

```python
def episode_json_filename(guid: str, raw_title: str | None, published: str | None) -> str:
    """Return the base filename (no extension) for an episode's JSON state file.

    Uses a GUID hash for stability — the filename does not change when titles
    are cleaned or modified in the RSS feed.
    """
    date_prefix = format_date(published) or "unknown-date"
    slug = slugify(raw_title or "")
    if len(slug) > 60:
        cut = slug.rfind("-", 0, 61)
        slug = slug[:cut] if cut > 0 else slug[:60]
    guid_hash = hashlib.sha256(guid.encode()).hexdigest()[:8]
    if slug:
        return f"{date_prefix}-{slug}-{guid_hash}"
    return f"{date_prefix}-{guid_hash}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -k "episode_json_filename" -v`
Expected: All 8 new tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/models.py tests/test_models.py
git commit -m "feat: add episode_json_filename() for GUID-based JSON naming"
```

---

### Task 2: `raw_title` field on Episode

**Files:**
- Modify: `src/podcast_etl/models.py:66-109` (Episode dataclass)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_models.py`:

```python
def test_episode_dict_roundtrip_with_raw_title():
    ep = _make_episode(raw_title="Original RSS Title")
    roundtripped = Episode.from_dict(ep.to_dict())
    assert roundtripped.raw_title == "Original RSS Title"


def test_episode_dict_roundtrip_without_raw_title():
    ep = _make_episode()
    roundtripped = Episode.from_dict(ep.to_dict())
    assert roundtripped.raw_title is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::test_episode_dict_roundtrip_with_raw_title -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'raw_title'`

- [ ] **Step 3: Add `raw_title` field to Episode**

In `src/podcast_etl/models.py`, add `raw_title` field to the `Episode` dataclass after `image_url`:

```python
raw_title: str | None = None
```

Add `"raw_title"` to `to_dict()` return dict (after `"image_url"`):

```python
"raw_title": self.raw_title,
```

Add `raw_title` to `from_dict()`:

```python
raw_title=data.get("raw_title"),
```

Also update `_make_episode` in `tests/test_models.py` to accept `raw_title`:
The existing `_make_episode` uses `**kwargs` with `defaults.update(kwargs)`, so it already supports arbitrary keyword args — no change needed to the helper.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All tests PASS (new and existing)

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/models.py tests/test_models.py
git commit -m "feat: add raw_title field to Episode for stable JSON naming"
```

---

### Task 3: Update `Episode.save()` to use GUID-based filenames

**Depends on:** Task 1 (`episode_json_filename`), Task 2 (`raw_title` field)

**Files:**
- Modify: `src/podcast_etl/models.py:111-116` (`Episode.save()`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_models.py`:

```python
def test_episode_save_uses_guid_based_filename(tmp_path: Path):
    ep = _make_episode(raw_title="My Raw Title")
    ep.save(tmp_path, "My Podcast")
    # Should NOT use the old title-based pattern
    old_pattern = tmp_path / "episodes" / "My Podcast - 2024-01-01 - Test Episode.json"
    assert not old_pattern.exists()
    # Should use GUID-based pattern
    files = list((tmp_path / "episodes").glob("*.json"))
    assert len(files) == 1
    assert "guid-123" not in files[0].name  # raw guid not in name
    assert files[0].name.startswith("2024-01-01-my-raw-title-")


def test_episode_save_deletes_stale_title_based_file(tmp_path: Path):
    ep = _make_episode(raw_title="My Raw Title")
    # Create a stale title-based file (simulating pre-migration state)
    episodes_dir = tmp_path / "episodes"
    episodes_dir.mkdir(parents=True)
    stale_file = episodes_dir / "My Podcast - 2024-01-01 - Test Episode.json"
    stale_file.write_text("{}")
    # Save with new naming — should delete the stale file
    ep.save(tmp_path, "My Podcast")
    assert not stale_file.exists()
    files = list(episodes_dir.glob("*.json"))
    assert len(files) == 1


def test_episode_save_no_raw_title_falls_back_to_title(tmp_path: Path):
    ep = _make_episode()  # raw_title is None
    ep.save(tmp_path, "My Podcast")
    files = list((tmp_path / "episodes").glob("*.json"))
    assert len(files) == 1
    # Falls back to cleaned title for slug
    assert files[0].name.startswith("2024-01-01-test-episode-")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::test_episode_save_uses_guid_based_filename -v`
Expected: FAIL — old code still writes title-based filename

- [ ] **Step 3: Update `Episode.save()`**

Replace the `save` method in `src/podcast_etl/models.py`:

```python
def save(self, podcast_dir: Path, podcast_title: str) -> None:
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    filename = episode_json_filename(self.guid, self.raw_title or self.title, self.published) + ".json"
    path = episodes_dir / filename
    path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
    # Clean up stale title-based file from pre-migration
    old_filename = episode_basename(podcast_title, self.title, self.published) + ".json"
    if old_filename != filename:
        old_path = episodes_dir / old_filename
        if old_path.exists():
            old_path.unlink()
```

- [ ] **Step 4: Update existing tests that assert old filename pattern and run all tests**

The existing tests `test_episode_save_and_load` and `test_episode_save_creates_directory` assert the old title-based filename. Update them alongside the implementation:

```python
def test_episode_save_and_load(tmp_path: Path):
    ep = _make_episode()
    ep.save(tmp_path, "My Podcast")
    files = list((tmp_path / "episodes").glob("*.json"))
    assert len(files) == 1
    loaded = Episode.load(files[0])
    assert loaded == ep


def test_episode_save_creates_directory(tmp_path: Path):
    ep = _make_episode()
    ep.save(tmp_path, "My Podcast")
    assert (tmp_path / "episodes").exists()
    assert len(list((tmp_path / "episodes").glob("*.json"))) == 1
```

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS. Some tests in `test_feed.py` (`test_parse_feed_preserves_existing_status`, `test_parse_feed_title_cleaning_preserves_status_despite_slug_change`) may need adjustment — see Task 4.

- [ ] **Step 6: Commit**

```bash
git add src/podcast_etl/models.py tests/test_models.py
git commit -m "feat: Episode.save() uses GUID-based filenames, cleans up stale files"
```

---

### Task 4: Update `feed.py` to set `raw_title`

**Files:**
- Modify: `src/podcast_etl/feed.py:49-97`
- Test: `tests/test_feed.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_feed.py`:

```python
def test_parse_feed_sets_raw_title_before_cleaning():
    entry = _Entry(title="Guest Name (3_19_26)", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", title_cleaning={"strip_date": True})
    # title is cleaned, raw_title is the original
    assert podcast.episodes[0].title == "Guest Name"
    assert podcast.episodes[0].raw_title == "Guest Name (3_19_26)"


def test_parse_feed_raw_title_set_without_cleaning():
    entry = _Entry(title="Normal Title", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry])
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml")
    assert podcast.episodes[0].raw_title == "Normal Title"


def test_parse_feed_preserves_raw_title_from_rss_over_disk(tmp_path: Path):
    """Fresh RSS raw_title takes precedence over whatever is on disk."""
    existing_ep = Episode(
        title="Old Cleaned",
        guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration=None,
        description=None,
        slug="old-cleaned",
        raw_title="Old Raw Title",
        status={"download": StepStatus(completed_at="2024-01-01T00:00:00", result={})},
    )
    podcast_dir = tmp_path / "test-podcast"
    existing_ep.save(podcast_dir, "Test Podcast")

    entry = _Entry(title="New Raw Title", guid="guid-1", links=[_audio_link()])
    feed = _make_parsed_feed(entries=[entry], feed=_FeedMeta(title="Test Podcast"))
    with patch("podcast_etl.feed.feedparser.parse", return_value=feed):
        podcast = parse_feed("https://example.com/feed.xml", output_dir=tmp_path)

    ep = podcast.episodes[0]
    assert ep.raw_title == "New Raw Title"
    assert "download" in ep.status  # status still preserved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_feed.py::test_parse_feed_sets_raw_title_before_cleaning -v`
Expected: FAIL — `raw_title` not set by `parse_feed()`

- [ ] **Step 3: Update `parse_feed()` to capture `raw_title`**

In `src/podcast_etl/feed.py`, modify the episode construction loop. Change:

```python
        title = entry.get("title", "Untitled")
        title = clean_title(title, title_cleaning, published=entry.get("published"), all_entries=feed.entries)
```

to:

```python
        raw_title = entry.get("title", "Untitled")
        title = clean_title(raw_title, title_cleaning, published=entry.get("published"), all_entries=feed.entries)
```

And add `raw_title=raw_title` to the `Episode(...)` constructor call:

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
            raw_title=raw_title,
        )
```

Also fix the GUID fallback on line 62 to use `raw_title` instead of `title`, so the GUID remains stable even when title cleaning changes:

```python
        guid = entry.get("id", entry.get("link", raw_title))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_feed.py -v`
Expected: All tests PASS

- [ ] **Step 5: Fix any tests that broke due to filename changes**

The test `test_parse_feed_preserves_existing_status` saves an episode with `Episode.save()` (which now writes GUID-based filenames) and then expects `parse_feed()` to find it. Since `parse_feed()` loads existing episodes by globbing `*.json`, the new filenames are picked up — no change needed. Verify by running:

Run: `uv run pytest tests/test_feed.py::test_parse_feed_preserves_existing_status -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/podcast_etl/feed.py tests/test_feed.py
git commit -m "feat: parse_feed() captures raw_title from RSS before cleaning"
```

---

### Task 5: GUID deduplication in `Podcast.load()`

**Files:**
- Modify: `src/podcast_etl/models.py:167-175` (`Podcast.load()`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_models.py`:

```python
def test_podcast_load_deduplicates_by_guid(tmp_path: Path):
    """When two JSON files have the same GUID, keep the one with more completed steps."""
    podcast_dir = tmp_path / "my-podcast"
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir(parents=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "My Podcast", "url": "https://example.com/feed.xml",
        "description": None, "image_url": None, "slug": "my-podcast",
    }))

    # Episode A: 2 completed steps (old-format filename)
    ep_a = _make_episode(
        raw_title="Test Episode",
        status={
            "download": StepStatus(completed_at="2024-01-01T00:00:00", result={"path": "audio/ep.mp3"}),
            "tag": StepStatus(completed_at="2024-01-01T00:00:00", result={}),
        },
    )
    (episodes_dir / "old-format-name.json").write_text(json.dumps(ep_a.to_dict(), indent=2))

    # Episode B: same GUID, 1 completed step (new-format filename)
    ep_b = _make_episode(
        raw_title="Test Episode",
        status={"download": StepStatus(completed_at="2024-01-02T00:00:00", result={"path": "audio/ep.mp3"})},
    )
    (episodes_dir / "new-format-name.json").write_text(json.dumps(ep_b.to_dict(), indent=2))

    loaded = Podcast.load(podcast_dir)
    assert len(loaded.episodes) == 1
    # Should keep the one with more steps
    assert len(loaded.episodes[0].status) == 2
    # Stale file should be deleted
    assert not (episodes_dir / "new-format-name.json").exists()
    assert (episodes_dir / "old-format-name.json").exists()


def test_podcast_load_no_duplicates_unchanged(tmp_path: Path):
    """Normal load with unique GUIDs is unaffected."""
    ep = _make_episode(raw_title="Test Episode")
    p = _make_podcast()
    p.episodes = [ep]
    p.save(tmp_path)

    loaded = Podcast.load(tmp_path / "my-podcast")
    assert len(loaded.episodes) == 1
    assert loaded.episodes[0].guid == "guid-123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::test_podcast_load_deduplicates_by_guid -v`
Expected: FAIL — `len(loaded.episodes)` is 2 (no dedup)

- [ ] **Step 3: Update `Podcast.load()` with GUID deduplication**

Replace `Podcast.load()` in `src/podcast_etl/models.py`:

```python
@classmethod
def load(cls, podcast_dir: Path) -> Podcast:
    data = json.loads((podcast_dir / "podcast.json").read_text())
    podcast = cls.from_dict(data)
    episodes_dir = podcast_dir / "episodes"
    if episodes_dir.exists():
        # Load all episodes, tracking source file paths
        episodes_by_guid: dict[str, list[tuple[Path, Episode]]] = {}
        for ep_path in sorted(episodes_dir.glob("*.json")):
            ep = Episode.load(ep_path)
            episodes_by_guid.setdefault(ep.guid, []).append((ep_path, ep))
        # Deduplicate: keep the episode with the most completed steps
        for guid, entries in episodes_by_guid.items():
            if len(entries) > 1:
                entries.sort(key=lambda e: (-len(e[1].status), -e[0].stat().st_mtime))
                keeper_path, keeper = entries[0]
                for stale_path, _ in entries[1:]:
                    logger.warning("Removing duplicate episode file %s (GUID %s)", stale_path.name, guid)
                    stale_path.unlink()
                podcast.episodes.append(keeper)
            else:
                podcast.episodes.append(entries[0][1])
    return podcast
```

Add `import logging` and `logger = logging.getLogger(__name__)` to the top of `models.py` if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/podcast_etl/models.py tests/test_models.py
git commit -m "feat: Podcast.load() deduplicates episodes by GUID"
```

---

### Task 6: `migrate` CLI command

**Files:**
- Modify: `src/podcast_etl/cli.py` (add new command)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
import json
from podcast_etl.models import Episode, StepStatus, episode_basename, episode_json_filename


def test_migrate_renames_old_format_files(tmp_path: Path):
    """migrate --feed renames title-based JSON files to GUID-based."""
    # Set up config
    cfg_path = tmp_path / "feeds.yaml"
    cfg_path.write_text(yaml.dump({
        "feeds": [{"url": "https://example.com/rss", "name": "my-show"}],
        "defaults": {"output_dir": str(tmp_path / "output")},
    }))
    # Create a podcast with an episode using old-format filename
    output_dir = tmp_path / "output"
    podcast_dir = output_dir / "test-podcast"
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir(parents=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "Test Podcast", "url": "https://example.com/rss",
        "description": None, "image_url": None, "slug": "test-podcast",
    }))
    ep = Episode(
        title="Episode 1", guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration=None, description=None, slug="episode-1",
        status={"download": StepStatus(completed_at="2024-01-01T00:00:00", result={})},
    )
    old_filename = episode_basename("Test Podcast", ep.title, ep.published) + ".json"
    (episodes_dir / old_filename).write_text(json.dumps(ep.to_dict(), indent=2))

    from click.testing import CliRunner
    from podcast_etl.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "migrate", "--feed", "my-show"])

    assert result.exit_code == 0
    # Old file should be gone
    assert not (episodes_dir / old_filename).exists()
    # New GUID-based file should exist
    new_filename = episode_json_filename("guid-1", "Episode 1", ep.published) + ".json"
    assert (episodes_dir / new_filename).exists()
    # Content should have raw_title backfilled
    data = json.loads((episodes_dir / new_filename).read_text())
    assert data["raw_title"] == "Episode 1"
    assert "Renamed 1" in result.output or "renamed" in result.output.lower()
```

Also add a test for the deduplication path in migrate:

```python
def test_migrate_deduplicates_same_guid(tmp_path: Path):
    """migrate --feed removes duplicate JSON files for the same GUID."""
    cfg_path = tmp_path / "feeds.yaml"
    cfg_path.write_text(yaml.dump({
        "feeds": [{"url": "https://example.com/rss", "name": "my-show"}],
        "defaults": {"output_dir": str(tmp_path / "output")},
    }))
    output_dir = tmp_path / "output"
    podcast_dir = output_dir / "test-podcast"
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir(parents=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "Test Podcast", "url": "https://example.com/rss",
        "description": None, "image_url": None, "slug": "test-podcast",
    }))

    # Two files with the same GUID but different step counts
    ep_more = Episode(
        title="Episode 1", guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url=None, duration=None, description=None, slug="episode-1",
        status={
            "download": StepStatus(completed_at="2024-01-01T00:00:00", result={}),
            "tag": StepStatus(completed_at="2024-01-01T00:00:00", result={}),
        },
    )
    ep_less = Episode(
        title="Episode 1", guid="guid-1",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        audio_url=None, duration=None, description=None, slug="episode-1",
        status={"download": StepStatus(completed_at="2024-01-01T00:00:00", result={})},
    )
    (episodes_dir / "old-name.json").write_text(json.dumps(ep_more.to_dict(), indent=2))
    (episodes_dir / "other-name.json").write_text(json.dumps(ep_less.to_dict(), indent=2))

    from click.testing import CliRunner
    from podcast_etl.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["-c", str(cfg_path), "migrate", "--feed", "my-show"])

    assert result.exit_code == 0
    files = list(episodes_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert len(data["status"]) == 2  # kept the one with more steps
    assert "duplicate" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_migrate_renames_old_format_files -v`
Expected: FAIL with `No such command 'migrate'`

- [ ] **Step 3: Implement the `migrate` command**

Add to `src/podcast_etl/cli.py`, before the `if __name__` block or at the end of the command definitions:

```python
@main.command()
@click.option("--feed", "feed_identifier", required=True, help="Feed name or URL to migrate")
@click.pass_context
def migrate(ctx: click.Context, feed_identifier: str) -> None:
    """Migrate episode JSON files from title-based to GUID-based filenames."""
    from podcast_etl.models import episode_basename, episode_json_filename

    config = ctx.obj["config"]
    output_dir = get_output_dir(config)

    feed_config = find_feed_config(config, feed_identifier)
    if not feed_config:
        click.echo(f"Feed not found: {feed_identifier}")
        sys.exit(1)

    resolved_url = feed_config["url"]

    # Find the podcast directory
    podcast_dir = None
    if output_dir.exists():
        for d in output_dir.iterdir():
            if not d.is_dir() or not (d / "podcast.json").exists():
                continue
            podcast = Podcast.load(d)
            if podcast.url == resolved_url:
                podcast_dir = d
                break

    if not podcast_dir:
        click.echo(f"No data found for feed: {feed_identifier}")
        return

    episodes_dir = podcast_dir / "episodes"
    if not episodes_dir.exists():
        click.echo("No episodes directory found.")
        return

    # Load all episodes, group by GUID
    episodes_by_guid: dict[str, list[tuple[Path, Episode]]] = {}
    for ep_path in sorted(episodes_dir.glob("*.json")):
        try:
            ep = Episode.load(ep_path)
        except Exception as exc:
            click.echo(f"  Skipping {ep_path.name}: {exc}")
            continue
        episodes_by_guid.setdefault(ep.guid, []).append((ep_path, ep))

    renamed = 0
    deduped = 0
    for guid, entries in episodes_by_guid.items():
        # Deduplicate: keep the one with most completed steps
        if len(entries) > 1:
            entries.sort(key=lambda e: (-len(e[1].status), -e[0].stat().st_mtime))
            for stale_path, _ in entries[1:]:
                click.echo(f"  Removing duplicate: {stale_path.name}")
                stale_path.unlink()
                deduped += 1
            entries = [entries[0]]

        ep_path, ep = entries[0]
        # Backfill raw_title if missing
        if ep.raw_title is None:
            ep.raw_title = ep.title
        new_filename = episode_json_filename(ep.guid, ep.raw_title, ep.published) + ".json"
        new_path = episodes_dir / new_filename

        if ep_path.name == new_filename:
            continue  # Already migrated

        # Write updated content (with raw_title) to new path
        new_path.write_text(json.dumps(ep.to_dict(), indent=2) + "\n")
        ep_path.unlink()
        click.echo(f"  Renamed: {ep_path.name} -> {new_filename}")
        renamed += 1

    click.echo(f"Migration complete: {renamed} renamed, {deduped} duplicates removed.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py::test_migrate_renames_old_format_files -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/podcast_etl/cli.py tests/test_cli.py
git commit -m "feat: add migrate command for GUID-based episode filenames"
```

---

### Task 7: Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md**

Add `migrate` to the Commands section:

```
uv run podcast-etl migrate --feed NAME  # migrate episode JSON to GUID-based filenames
```

Add to the Architecture section under `models.py`:

```
`Episode.raw_title` stores the original RSS title before cleaning; `episode_json_filename()` produces stable GUID-based filenames for episode JSON state files
```

Add `test_models.py` entry update to mention `episode_json_filename` tests.

Add to the `migrate` description under Pipeline steps (or as a new CLI command section).

- [ ] **Step 2: Update README.md**

Add a section about the `migrate` command and when to use it (after upgrading to GUID-based filenames).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document migrate command and GUID-based episode storage"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run Docker test build**

Run: `docker build --target test -t podcast-etl-test . && docker run --rm podcast-etl-test`
Expected: All tests PASS in Docker

- [ ] **Step 3: Manual smoke test**

Run: `uv run podcast-etl --help`
Verify `migrate` appears in the command list.

Run: `uv run podcast-etl migrate --help`
Verify `--feed` is documented as required.
