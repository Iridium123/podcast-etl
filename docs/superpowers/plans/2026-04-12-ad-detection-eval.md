# Ad Detection Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an evaluation harness to measure ad detection quality against human-annotated gold-standard episodes and compare across model/prompt/whisper configurations.

**Architecture:** Standalone `eval/` directory that imports from `podcast_etl` for episode resolution and transcription, but is otherwise self-contained. Annotation files are version-controlled JSON. The runner loads a YAML config matrix, transcribes (with reuse), classifies via LLM with swappable prompts, and scores against annotations. Scoring uses pluggable segment matching with overlap-based default.

**Tech Stack:** Python 3.13, pytest, PyYAML (already a dependency), podcast_etl internals (models, detectors, pipeline)

---

## File Structure

```
eval/
├── __init__.py               # empty
├── models.py                 # Annotation, EpisodeRef dataclasses (load/save)
├── resolve.py                # resolve EpisodeRef -> audio path, transcript, episode
├── score.py                  # segment matching, per-episode + aggregate scoring
├── annotate.py               # bootstrap annotations from episode status or blank
├── review.py                 # display transcript with annotation highlights
├── validate.py               # check annotation files for consistency
├── run.py                    # eval runner: config matrix -> transcribe -> classify -> score -> report
├── classify.py               # LLM classification adapter that accepts custom prompts
├── prompts/
│   └── default.txt           # current hardcoded prompt, extracted
├── annotations/
│   └── .gitkeep
├── results/
│   └── .gitignore
└── transcripts/
    └── .gitignore
tests/
└── test_eval/
    ├── __init__.py
    ├── test_eval_models.py
    ├── test_resolve.py
    ├── test_score.py
    ├── test_annotate.py
    ├── test_review.py
    ├── test_validate.py
    ├── test_classify.py
    └── test_run.py
```

**Responsibilities:**
- `models.py` -- `EpisodeRef` and `Annotation` dataclasses with `to_dict`/`from_dict`/`load`/`save`. No business logic.
- `resolve.py` -- Given an `EpisodeRef` and `output_dir`, locate the podcast dir, episode JSON, audio file, and transcript on disk. Single function: `resolve_episode(ref, output_dir) -> ResolvedEpisode`.
- `score.py` -- `match_segments(predicted, gold, matcher)` returns matched pairs + unmatched. `score_episode(predicted, gold, transcript)` returns `EpisodeScore`. `aggregate_scores(scores)` returns `AggregateScore`. `format_report(results)` prints the comparison table.
- `annotate.py` -- `bootstrap_from_episode(episode, ref, annotator)` reads detect_ads status and writes annotation JSON. `create_blank(ref, audio_duration)` writes a blank annotation.
- `review.py` -- `format_review(annotation, transcript, audio_path)` renders transcript lines with ad segments highlighted. `review_annotation(path, output_dir)` is the CLI entry point.
- `validate.py` -- `validate_annotation(ann)` checks a single annotation. `validate_annotations(dir)` checks all files in a directory.
- `classify.py` -- `classify_with_prompt(transcript, prompt_text, config)` calls the Anthropic API with a custom prompt string instead of the hardcoded one.
- `run.py` -- CLI entry point. Loads `eval_config.yaml`, groups configs by whisper settings, runs transcription + classification, scores, reports.

---

### Task 1: Project scaffolding and gitignore

**Files:**
- Create: `eval/__init__.py`
- Create: `eval/annotations/.gitkeep`
- Create: `eval/results/.gitignore`
- Create: `eval/transcripts/.gitignore`
- Create: `tests/test_eval/__init__.py`
- Create: `eval/prompts/default.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p eval/annotations eval/results eval/transcripts eval/prompts
mkdir -p tests/test_eval
touch eval/__init__.py tests/test_eval/__init__.py eval/annotations/.gitkeep
```

- [ ] **Step 2: Create gitignore files**

`eval/results/.gitignore`:
```
*
!.gitignore
```

`eval/transcripts/.gitignore`:
```
*
!.gitignore
```

- [ ] **Step 3: Add eval ignores to root .gitignore**

Append to `.gitignore`:
```
# Eval outputs
eval/results/
eval/transcripts/
```

- [ ] **Step 4: Extract default prompt**

Copy the prompt text from `src/podcast_etl/detectors/transcription.py` lines 15-35 (the `_CLASSIFY_PROMPT` string) into `eval/prompts/default.txt`. Include everything up to and including "Transcript:\n" -- the runner will append the formatted transcript at runtime.

`eval/prompts/default.txt`:
```
You are an ad-segment detector for podcast audio. You will receive a timestamped transcript of a podcast episode. Identify all advertisement segments, including:
- Programmatic ads (dynamically inserted, often with abrupt topic changes)
- Burned-in ads (pre-recorded by advertisers)
- Host-read ads (hosts reading ad copy / sponsor mentions)

For each ad segment, return the start and end timestamps (in seconds) and a short label describing the ad (e.g. "Pre-roll ad for Squarespace").

Return ONLY valid JSON -- no markdown fences, no commentary. Use this exact schema:
{
  "segments": [
    {"start": 0.0, "end": 45.2, "confidence": 0.9, "label": "Pre-roll ad for Squarespace"}
  ]
}

If there are no ads, return: {"segments": []}

Transcript:
```

- [ ] **Step 5: Commit**

```bash
git add eval/ tests/test_eval/ .gitignore
git commit -m "feat: scaffold eval directory structure and extract default prompt"
```

---

### Task 2: Annotation data model

**Files:**
- Create: `eval/models.py`
- Create: `tests/test_eval/test_eval_models.py`

- [ ] **Step 1: Write failing tests for EpisodeRef and Annotation**

`tests/test_eval/test_eval_models.py`:
```python
"""Tests for eval annotation data model."""

import json
from datetime import datetime

import pytest

from eval.models import Annotation, EpisodeRef


class TestEpisodeRef:
    def test_to_dict(self):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="2024-01-15-ep-one-a1b2c3d4.json")
        assert ref.to_dict() == {
            "podcast_slug": "my-podcast",
            "episode_json": "2024-01-15-ep-one-a1b2c3d4.json",
        }

    def test_from_dict(self):
        data = {"podcast_slug": "my-podcast", "episode_json": "2024-01-15-ep-one-a1b2c3d4.json"}
        ref = EpisodeRef.from_dict(data)
        assert ref.podcast_slug == "my-podcast"
        assert ref.episode_json == "2024-01-15-ep-one-a1b2c3d4.json"


class TestAnnotation:
    def _sample_annotation(self) -> Annotation:
        return Annotation(
            episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="2024-01-15-ep-a1b2c3d4.json"),
            audio_duration=3600.0,
            segments=[
                {"start": 0.0, "end": 43.5, "label": "Pre-roll ad", "notes": ""},
                {"start": 1820.0, "end": 1892.0, "label": "Mid-roll", "notes": "programmatic"},
            ],
            annotator="human",
            created_at="2026-04-12T10:00:00",
        )

    def test_to_dict_roundtrip(self):
        ann = self._sample_annotation()
        data = ann.to_dict()
        restored = Annotation.from_dict(data)
        assert restored.episode_ref.podcast_slug == "my-podcast"
        assert restored.audio_duration == 3600.0
        assert len(restored.segments) == 2
        assert restored.segments[0]["start"] == 0.0
        assert restored.annotator == "human"

    def test_save_and_load(self, tmp_path):
        ann = self._sample_annotation()
        path = tmp_path / "test-annotation.json"
        ann.save(path)

        loaded = Annotation.load(path)
        assert loaded.episode_ref.episode_json == ann.episode_ref.episode_json
        assert loaded.audio_duration == ann.audio_duration
        assert len(loaded.segments) == 2
        assert loaded.annotator == "human"

    def test_save_creates_parent_dirs(self, tmp_path):
        ann = self._sample_annotation()
        path = tmp_path / "subdir" / "test.json"
        ann.save(path)
        assert path.exists()

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Annotation.load(tmp_path / "missing.json")

    def test_segments_as_ad_segments(self):
        ann = self._sample_annotation()
        ad_segs = ann.segments_as_ad_segments()
        assert len(ad_segs) == 2
        assert ad_segs[0].start == 0.0
        assert ad_segs[0].end == 43.5
        assert ad_segs[0].label == "Pre-roll ad"
        assert ad_segs[0].confidence == 1.0
        assert ad_segs[0].detector == "gold"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval/test_eval_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.models'`

- [ ] **Step 3: Implement the data model**

`eval/models.py`:
```python
"""Annotation data model for ad detection evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from podcast_etl.detectors import AdSegment


@dataclass
class EpisodeRef:
    podcast_slug: str
    episode_json: str  # e.g. "2024-01-15-episode-one-a1b2c3d4.json"

    def to_dict(self) -> dict[str, Any]:
        return {"podcast_slug": self.podcast_slug, "episode_json": self.episode_json}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeRef:
        return cls(podcast_slug=data["podcast_slug"], episode_json=data["episode_json"])


@dataclass
class Annotation:
    episode_ref: EpisodeRef
    audio_duration: float
    segments: list[dict[str, Any]]  # [{start, end, label, notes}]
    annotator: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_ref": self.episode_ref.to_dict(),
            "audio_duration": self.audio_duration,
            "segments": self.segments,
            "annotator": self.annotator,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Annotation:
        return cls(
            episode_ref=EpisodeRef.from_dict(data["episode_ref"]),
            audio_duration=data["audio_duration"],
            segments=data["segments"],
            annotator=data["annotator"],
            created_at=data["created_at"],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> Annotation:
        data = json.loads(path.read_text())
        return cls.from_dict(data)

    def segments_as_ad_segments(self) -> list[AdSegment]:
        """Convert annotation segments to AdSegment objects for scoring."""
        return [
            AdSegment(
                start=seg["start"],
                end=seg["end"],
                confidence=1.0,
                detector="gold",
                label=seg.get("label", ""),
            )
            for seg in self.segments
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval/test_eval_models.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add eval/models.py tests/test_eval/test_eval_models.py
git commit -m "feat: add annotation data model with load/save"
```

---

### Task 3: Episode resolution

**Files:**
- Create: `eval/resolve.py`
- Create: `tests/test_eval/test_resolve.py`

- [ ] **Step 1: Write failing tests for episode resolution**

The resolver takes an `EpisodeRef` and `output_dir`, and returns a `ResolvedEpisode` with paths to the podcast dir, episode JSON, audio file, and transcript (if it exists). It uses `Episode.load()` to load the episode, then derives file paths from the episode's download status.

`tests/test_eval/test_resolve.py`:
```python
"""Tests for episode resolution from EpisodeRef."""

import json

import pytest

from eval.models import EpisodeRef
from eval.resolve import ResolvedEpisode, resolve_episode


def _write_podcast(podcast_dir):
    podcast_dir.mkdir(parents=True, exist_ok=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "My Podcast",
        "url": "https://example.com/feed.xml",
        "description": "A podcast",
        "image_url": None,
        "slug": "my-podcast",
    }, indent=2))


def _write_episode(podcast_dir, episode_json, audio_filename="episode.mp3"):
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    (episodes_dir / episode_json).write_text(json.dumps({
        "title": "Episode One",
        "guid": "guid-1",
        "published": "2024-01-15",
        "audio_url": "https://example.com/ep.mp3",
        "duration": "3600",
        "description": "An episode",
        "slug": "episode-one",
        "status": {
            "download": {
                "completed_at": "2024-01-15T10:00:00",
                "result": {"path": f"audio/{audio_filename}", "size_bytes": 1024},
            },
        },
    }, indent=2))
    audio_dir = podcast_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / audio_filename).write_bytes(b"fake audio")


class TestResolveEpisode:
    def test_resolves_audio_path(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        _write_episode(podcast_dir, episode_json)

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        resolved = resolve_episode(ref, tmp_path)

        assert resolved.podcast_dir == podcast_dir
        assert resolved.audio_path == podcast_dir / "audio" / "episode.mp3"
        assert resolved.audio_path.exists()

    def test_resolves_transcript_when_exists(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        _write_episode(podcast_dir, episode_json)

        transcripts_dir = podcast_dir / "transcripts"
        transcripts_dir.mkdir()
        (transcripts_dir / "episode.json").write_text('[{"start": 0, "end": 10, "text": "hi"}]')

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        resolved = resolve_episode(ref, tmp_path)

        assert resolved.transcript_path == transcripts_dir / "episode.json"

    def test_transcript_path_none_when_missing(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        _write_episode(podcast_dir, episode_json)

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        resolved = resolve_episode(ref, tmp_path)

        assert resolved.transcript_path is None

    def test_raises_when_podcast_dir_missing(self, tmp_path):
        ref = EpisodeRef(podcast_slug="nonexistent", episode_json="ep.json")
        with pytest.raises(FileNotFoundError, match="Podcast directory not found"):
            resolve_episode(ref, tmp_path)

    def test_raises_when_episode_json_missing(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        _write_podcast(podcast_dir)

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="missing.json")
        with pytest.raises(FileNotFoundError, match="Episode file not found"):
            resolve_episode(ref, tmp_path)

    def test_raises_when_audio_missing(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        # Write episode JSON but don't create the audio file
        episodes_dir = podcast_dir / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        (episodes_dir / episode_json).write_text(json.dumps({
            "title": "Episode One",
            "guid": "guid-1",
            "published": "2024-01-15",
            "audio_url": "https://example.com/ep.mp3",
            "duration": "3600",
            "description": "An episode",
            "slug": "episode-one",
            "status": {
                "download": {
                    "completed_at": "2024-01-15T10:00:00",
                    "result": {"path": "audio/episode.mp3"},
                },
            },
        }, indent=2))

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            resolve_episode(ref, tmp_path)

    def test_exposes_episode_object(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        _write_episode(podcast_dir, episode_json)

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        resolved = resolve_episode(ref, tmp_path)

        assert resolved.episode.title == "Episode One"
        assert resolved.episode.guid == "guid-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval/test_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.resolve'`

- [ ] **Step 3: Implement episode resolution**

`eval/resolve.py`:
```python
"""Resolve an EpisodeRef to concrete file paths on disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from podcast_etl.models import Episode

from eval.models import EpisodeRef


@dataclass
class ResolvedEpisode:
    podcast_dir: Path
    episode: Episode
    audio_path: Path
    transcript_path: Path | None  # None if no transcript on disk


def resolve_episode(ref: EpisodeRef, output_dir: Path) -> ResolvedEpisode:
    """Resolve an EpisodeRef to paths on disk.

    Raises FileNotFoundError if the podcast dir, episode JSON, or audio file
    cannot be found.
    """
    podcast_dir = output_dir / ref.podcast_slug
    if not podcast_dir.exists():
        raise FileNotFoundError(f"Podcast directory not found: {podcast_dir}")

    episode_path = podcast_dir / "episodes" / ref.episode_json
    if not episode_path.exists():
        raise FileNotFoundError(f"Episode file not found: {episode_path}")

    episode = Episode.load(episode_path)

    # Derive audio path from download status
    download_status = episode.status.get("download")
    if not download_status:
        raise FileNotFoundError(f"Episode {ref.episode_json} has no download status")
    relative_path = download_status.result.get("path", "")
    audio_path = podcast_dir / relative_path
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Check for transcript
    transcript_path = podcast_dir / "transcripts" / (audio_path.stem + ".json")
    if not transcript_path.exists():
        transcript_path = None

    return ResolvedEpisode(
        podcast_dir=podcast_dir,
        episode=episode,
        audio_path=audio_path,
        transcript_path=transcript_path,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval/test_resolve.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add eval/resolve.py tests/test_eval/test_resolve.py
git commit -m "feat: add episode resolution from EpisodeRef"
```

---

### Task 4: Segment matching and per-episode scoring

**Files:**
- Create: `eval/score.py`
- Create: `tests/test_eval/test_score.py`

- [ ] **Step 1: Write failing tests for segment matching**

`tests/test_eval/test_score.py`:
```python
"""Tests for segment matching and scoring."""

import pytest

from podcast_etl.detectors import AdSegment

from eval.score import (
    AggregateScore,
    EpisodeScore,
    MatchedPair,
    MatchResult,
    aggregate_scores,
    format_report,
    match_segments,
    overlap_fraction_matcher,
    score_episode,
)


def _gold(start, end, label="Ad"):
    return AdSegment(start=start, end=end, confidence=1.0, detector="gold", label=label)


def _pred(start, end, label="Ad"):
    return AdSegment(start=start, end=end, confidence=0.9, detector="transcription", label=label)


# ---------------------------------------------------------------------------
# overlap_fraction_matcher
# ---------------------------------------------------------------------------

class TestOverlapFractionMatcher:
    def test_full_overlap_matches(self):
        assert overlap_fraction_matcher(_pred(0, 30), _gold(0, 30), threshold=0.5) is True

    def test_sufficient_overlap_matches(self):
        # Pred covers 20 of 30 gold seconds = 66%
        assert overlap_fraction_matcher(_pred(10, 40), _gold(0, 30), threshold=0.5) is True

    def test_insufficient_overlap_no_match(self):
        # Pred covers 5 of 30 gold seconds = 16%
        assert overlap_fraction_matcher(_pred(25, 50), _gold(0, 30), threshold=0.5) is False

    def test_no_overlap(self):
        assert overlap_fraction_matcher(_pred(100, 130), _gold(0, 30), threshold=0.5) is False

    def test_zero_duration_gold(self):
        assert overlap_fraction_matcher(_pred(0, 10), _gold(5, 5), threshold=0.5) is False


# ---------------------------------------------------------------------------
# match_segments
# ---------------------------------------------------------------------------

class TestMatchSegments:
    def test_perfect_match(self):
        gold = [_gold(0, 30), _gold(100, 130)]
        pred = [_pred(0, 30), _pred(100, 130)]
        result = match_segments(pred, gold)

        assert len(result.matched) == 2
        assert len(result.false_positives) == 0
        assert len(result.false_negatives) == 0

    def test_false_positive(self):
        gold = [_gold(0, 30)]
        pred = [_pred(0, 30), _pred(200, 230)]
        result = match_segments(pred, gold)

        assert len(result.matched) == 1
        assert len(result.false_positives) == 1
        assert result.false_positives[0].start == 200

    def test_false_negative(self):
        gold = [_gold(0, 30), _gold(100, 130)]
        pred = [_pred(0, 30)]
        result = match_segments(pred, gold)

        assert len(result.matched) == 1
        assert len(result.false_negatives) == 1
        assert result.false_negatives[0].start == 100

    def test_empty_predictions(self):
        gold = [_gold(0, 30)]
        result = match_segments([], gold)
        assert len(result.false_negatives) == 1
        assert len(result.matched) == 0

    def test_empty_gold(self):
        pred = [_pred(0, 30)]
        result = match_segments(pred, [])
        assert len(result.false_positives) == 1
        assert len(result.matched) == 0

    def test_both_empty(self):
        result = match_segments([], [])
        assert len(result.matched) == 0
        assert len(result.false_positives) == 0
        assert len(result.false_negatives) == 0

    def test_best_overlap_wins(self):
        # Two predictions overlap same gold -- best overlap wins
        gold = [_gold(0, 30)]
        pred = [_pred(20, 50), _pred(0, 30)]
        result = match_segments(pred, gold)

        assert len(result.matched) == 1
        assert result.matched[0].predicted.start == 0  # exact match wins
        assert len(result.false_positives) == 1


# ---------------------------------------------------------------------------
# score_episode
# ---------------------------------------------------------------------------

class TestScoreEpisode:
    def test_perfect_detection(self):
        gold = [_gold(0, 30)]
        pred = [_pred(0, 30)]
        score = score_episode(pred, gold)

        assert score.true_positives == 1
        assert score.false_positives == 0
        assert score.false_negatives == 0
        assert score.precision == 1.0
        assert score.recall == 1.0

    def test_boundary_errors_computed(self):
        gold = [_gold(10, 50)]
        pred = [_pred(12, 48)]  # starts 2s late, ends 2s early
        score = score_episode(pred, gold)

        assert score.true_positives == 1
        assert len(score.start_errors) == 1
        assert score.start_errors[0] == pytest.approx(2.0)   # pred - gold
        assert score.end_errors[0] == pytest.approx(-2.0)     # pred - gold

    def test_content_lost_from_false_positives(self):
        gold = []
        pred = [_pred(100, 115)]  # 15s of content falsely flagged
        score = score_episode(pred, gold)

        assert score.false_positives == 1
        assert score.content_lost_seconds == pytest.approx(15.0)

    def test_ads_missed_from_false_negatives(self):
        gold = [_gold(0, 30)]
        pred = []
        score = score_episode(pred, gold)

        assert score.false_negatives == 1
        assert score.ads_missed_seconds == pytest.approx(30.0)

    def test_precision_recall_with_mixed_results(self):
        gold = [_gold(0, 30), _gold(100, 130)]
        pred = [_pred(0, 30), _pred(200, 230)]  # 1 TP, 1 FP, 1 FN
        score = score_episode(pred, gold)

        assert score.true_positives == 1
        assert score.false_positives == 1
        assert score.false_negatives == 1
        assert score.precision == pytest.approx(0.5)
        assert score.recall == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# aggregate_scores
# ---------------------------------------------------------------------------

class TestAggregateScores:
    def test_aggregates_multiple_episodes(self):
        scores = [
            EpisodeScore(
                true_positives=2, false_positives=1, false_negatives=0,
                start_errors=[1.0, -0.5], end_errors=[-1.0, 0.5],
                content_lost_seconds=10.0, ads_missed_seconds=0.0,
            ),
            EpisodeScore(
                true_positives=1, false_positives=0, false_negatives=1,
                start_errors=[2.0], end_errors=[-1.5],
                content_lost_seconds=0.0, ads_missed_seconds=30.0,
            ),
        ]
        agg = aggregate_scores(scores)

        assert agg.total_tp == 3
        assert agg.total_fp == 1
        assert agg.total_fn == 1
        assert agg.precision == pytest.approx(3 / 4)
        assert agg.recall == pytest.approx(3 / 4)
        assert agg.total_content_lost == pytest.approx(10.0)
        assert agg.total_ads_missed == pytest.approx(30.0)

    def test_empty_scores(self):
        agg = aggregate_scores([])
        assert agg.total_tp == 0
        assert agg.precision == 1.0  # no predictions = vacuously precise
        assert agg.start_error_mean == 0.0

    def test_boundary_errors_use_absolute_values(self):
        scores = [
            EpisodeScore(
                true_positives=2, false_positives=0, false_negatives=0,
                start_errors=[-3.0, 3.0], end_errors=[1.0, -1.0],
                content_lost_seconds=0.0, ads_missed_seconds=0.0,
            ),
        ]
        agg = aggregate_scores(scores)
        assert agg.start_error_mean == pytest.approx(3.0)
        assert agg.start_error_median == pytest.approx(3.0)
        assert agg.end_error_mean == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_formats_table(self):
        results = {
            "config-a": AggregateScore(
                total_tp=10, total_fp=2, total_fn=1,
                precision=0.83, recall=0.91, f1=0.87,
                start_error_mean=1.5, start_error_median=1.2, start_error_p95=3.0,
                end_error_mean=0.8, end_error_median=0.6, end_error_p95=2.0,
                total_content_lost=12.5, total_ads_missed=30.0,
            ),
        }
        report = format_report(results)
        assert "config-a" in report
        assert "0.83" in report
        assert "0.91" in report

    def test_handles_empty_results(self):
        report = format_report({})
        assert "Config" in report  # header still present
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval/test_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.score'`

- [ ] **Step 3: Implement scoring**

`eval/score.py`:
```python
"""Segment matching and scoring for ad detection evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from podcast_etl.detectors import AdSegment


# ---------------------------------------------------------------------------
# Segment matching
# ---------------------------------------------------------------------------

MatcherFunc = Callable[[AdSegment, AdSegment, float], bool]


def overlap_fraction_matcher(predicted: AdSegment, gold: AdSegment, threshold: float = 0.5) -> bool:
    """Return True if the overlap between predicted and gold exceeds threshold
    fraction of the gold segment's duration."""
    gold_duration = gold.end - gold.start
    if gold_duration <= 0:
        return False
    overlap_start = max(predicted.start, gold.start)
    overlap_end = min(predicted.end, gold.end)
    overlap = max(0.0, overlap_end - overlap_start)
    return (overlap / gold_duration) >= threshold


def _compute_overlap(a: AdSegment, b: AdSegment) -> float:
    """Compute overlap duration between two segments."""
    start = max(a.start, b.start)
    end = min(a.end, b.end)
    return max(0.0, end - start)


@dataclass
class MatchedPair:
    predicted: AdSegment
    gold: AdSegment


@dataclass
class MatchResult:
    matched: list[MatchedPair]
    false_positives: list[AdSegment]   # predicted with no gold match
    false_negatives: list[AdSegment]   # gold with no prediction match


def match_segments(
    predicted: list[AdSegment],
    gold: list[AdSegment],
    matcher: MatcherFunc = overlap_fraction_matcher,
    threshold: float = 0.5,
) -> MatchResult:
    """Match predicted segments to gold segments.

    Each gold segment matches at most one prediction (best overlap),
    and each prediction matches at most one gold segment.
    """
    if not predicted and not gold:
        return MatchResult(matched=[], false_positives=[], false_negatives=[])

    # Build a list of all valid (pred_idx, gold_idx, overlap) triples
    candidates: list[tuple[int, int, float]] = []
    for pi, p in enumerate(predicted):
        for gi, g in enumerate(gold):
            if matcher(p, g, threshold):
                candidates.append((pi, gi, _compute_overlap(p, g)))

    # Greedy assignment: best overlap first
    candidates.sort(key=lambda c: c[2], reverse=True)
    matched_pred: set[int] = set()
    matched_gold: set[int] = set()
    matched: list[MatchedPair] = []

    for pi, gi, _overlap in candidates:
        if pi not in matched_pred and gi not in matched_gold:
            matched.append(MatchedPair(predicted=predicted[pi], gold=gold[gi]))
            matched_pred.add(pi)
            matched_gold.add(gi)

    false_positives = [p for i, p in enumerate(predicted) if i not in matched_pred]
    false_negatives = [g for i, g in enumerate(gold) if i not in matched_gold]

    return MatchResult(matched=matched, false_positives=false_positives, false_negatives=false_negatives)


# ---------------------------------------------------------------------------
# Per-episode scoring
# ---------------------------------------------------------------------------

@dataclass
class EpisodeScore:
    true_positives: int
    false_positives: int
    false_negatives: int
    start_errors: list[float]      # predicted.start - gold.start for each TP
    end_errors: list[float]        # predicted.end - gold.end for each TP
    content_lost_seconds: float    # total duration of false positives
    ads_missed_seconds: float      # total duration of false negatives

    @property
    def precision(self) -> float:
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total > 0 else 1.0

    @property
    def recall(self) -> float:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total > 0 else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def score_episode(
    predicted: list[AdSegment],
    gold: list[AdSegment],
    matcher: MatcherFunc = overlap_fraction_matcher,
    threshold: float = 0.5,
) -> EpisodeScore:
    """Score predicted segments against gold standard for a single episode."""
    result = match_segments(predicted, gold, matcher, threshold)

    start_errors = [m.predicted.start - m.gold.start for m in result.matched]
    end_errors = [m.predicted.end - m.gold.end for m in result.matched]
    content_lost = sum(p.end - p.start for p in result.false_positives)
    ads_missed = sum(g.end - g.start for g in result.false_negatives)

    return EpisodeScore(
        true_positives=len(result.matched),
        false_positives=len(result.false_positives),
        false_negatives=len(result.false_negatives),
        start_errors=start_errors,
        end_errors=end_errors,
        content_lost_seconds=content_lost,
        ads_missed_seconds=ads_missed,
    )


# ---------------------------------------------------------------------------
# Aggregate scoring
# ---------------------------------------------------------------------------

@dataclass
class AggregateScore:
    total_tp: int
    total_fp: int
    total_fn: int
    precision: float
    recall: float
    f1: float
    start_error_mean: float
    start_error_median: float
    start_error_p95: float
    end_error_mean: float
    end_error_median: float
    end_error_p95: float
    total_content_lost: float
    total_ads_missed: float


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    return s[f] + (k - f) * (s[c] - s[f])


def aggregate_scores(scores: list[EpisodeScore]) -> AggregateScore:
    """Aggregate per-episode scores into summary metrics."""
    total_tp = sum(s.true_positives for s in scores)
    total_fp = sum(s.false_positives for s in scores)
    total_fn = sum(s.false_negatives for s in scores)

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    all_start = [e for s in scores for e in s.start_errors]
    all_end = [e for s in scores for e in s.end_errors]

    # Use absolute values for mean/median/p95 so we measure magnitude
    abs_start = [abs(e) for e in all_start]
    abs_end = [abs(e) for e in all_end]

    return AggregateScore(
        total_tp=total_tp,
        total_fp=total_fp,
        total_fn=total_fn,
        precision=precision,
        recall=recall,
        f1=f1,
        start_error_mean=sum(abs_start) / len(abs_start) if abs_start else 0.0,
        start_error_median=_median(abs_start),
        start_error_p95=_percentile(abs_start, 95),
        end_error_mean=sum(abs_end) / len(abs_end) if abs_end else 0.0,
        end_error_median=_median(abs_end),
        end_error_p95=_percentile(abs_end, 95),
        total_content_lost=sum(s.content_lost_seconds for s in scores),
        total_ads_missed=sum(s.ads_missed_seconds for s in scores),
    )


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(results: dict[str, AggregateScore]) -> str:
    """Format a comparison table of aggregate scores across configs."""
    header = f"{'Config':<30} {'Prec':>6} {'Rec':>6} {'F1':>6} {'Start(med)':>11} {'End(med)':>11} {'Content-lost':>13} {'Ads-missed':>11}"
    lines = [header, "-" * len(header)]

    for name, agg in results.items():
        start_med = f"{agg.start_error_median:+.1f}s"
        end_med = f"{agg.end_error_median:+.1f}s"
        lines.append(
            f"{name:<30} {agg.precision:>6.2f} {agg.recall:>6.2f} {agg.f1:>6.2f} "
            f"{start_med:>11} {end_med:>11} {agg.total_content_lost:>12.1f}s {agg.total_ads_missed:>10.1f}s"
        )

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval/test_score.py -v`
Expected: All 22 tests PASS

- [ ] **Step 5: Commit**

```bash
git add eval/score.py tests/test_eval/test_score.py
git commit -m "feat: add segment matching and scoring with pluggable matchers"
```

---

### Task 5: LLM classification adapter

**Files:**
- Create: `eval/classify.py`
- Create: `tests/test_eval/test_classify.py`

- [ ] **Step 1: Write failing tests for custom prompt classification**

`tests/test_eval/test_classify.py`:
```python
"""Tests for LLM classification adapter with custom prompts."""

import json
from unittest.mock import MagicMock, patch

from eval.classify import classify_with_prompt


SAMPLE_TRANSCRIPT = [
    {"start": 0.0, "end": 10.0, "text": "This episode brought to you by Acme"},
    {"start": 10.0, "end": 30.0, "text": "Welcome to the show"},
]

CUSTOM_PROMPT = "Find the ads.\n\nTranscript:\n"


class TestClassifyWithPrompt:
    def test_uses_custom_prompt(self):
        llm_response = json.dumps({"segments": [
            {"start": 0.0, "end": 10.0, "confidence": 0.9, "label": "Ad"},
        ]})
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        config = {"llm": {"model": "claude-haiku-4-5-20251001"}}

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = classify_with_prompt(SAMPLE_TRANSCRIPT, CUSTOM_PROMPT, config)

        # Verify the prompt sent starts with our custom prompt, not the default
        call_kwargs = mock_client.messages.create.call_args.kwargs
        sent_prompt = call_kwargs["messages"][0]["content"]
        assert sent_prompt.startswith("Find the ads.")
        assert "[0.0s - 10.0s]" in sent_prompt  # transcript appended

        assert len(result) == 1
        assert result[0].start == 0.0

    def test_filters_by_min_confidence(self):
        llm_response = json.dumps({"segments": [
            {"start": 0.0, "end": 10.0, "confidence": 0.3, "label": "Maybe ad"},
            {"start": 50.0, "end": 60.0, "confidence": 0.9, "label": "Definite ad"},
        ]})
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        config = {"llm": {"model": "claude-haiku-4-5-20251001"}, "min_confidence": 0.5}

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = classify_with_prompt(SAMPLE_TRANSCRIPT, CUSTOM_PROMPT, config)

        assert len(result) == 1
        assert result[0].start == 50.0

    def test_uses_configured_model(self):
        llm_response = json.dumps({"segments": []})
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        config = {"llm": {"model": "claude-sonnet-4-20250514"}}

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            classify_with_prompt(SAMPLE_TRANSCRIPT, CUSTOM_PROMPT, config)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval/test_classify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.classify'`

- [ ] **Step 3: Implement the classification adapter**

`eval/classify.py`:
```python
"""LLM classification adapter that accepts custom prompts."""

from __future__ import annotations

from typing import Any

from podcast_etl.detectors import AdSegment
from podcast_etl.detectors.transcription import _format_transcript, _parse_llm_response


def classify_with_prompt(
    transcript: list[dict[str, Any]],
    prompt_text: str,
    config: dict[str, Any],
) -> list[AdSegment]:
    """Classify transcript segments using a custom prompt.

    Like AnthropicProvider.classify_ads but with a caller-supplied prompt
    instead of the hardcoded _CLASSIFY_PROMPT.
    """
    import anthropic

    llm_config = config.get("llm", {})
    api_key = llm_config.get("api_key") or None
    model = llm_config.get("model", "claude-haiku-4-5-20251001")
    min_confidence = config.get("min_confidence", 0.5)

    client = anthropic.Anthropic(api_key=api_key)

    formatted = _format_transcript(transcript)
    full_prompt = prompt_text + formatted

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": full_prompt}],
    )

    if not message.content or not hasattr(message.content[0], "text"):
        raise ValueError(f"Unexpected Anthropic response: {message.content!r}")

    segments = _parse_llm_response(message.content[0].text)
    return [s for s in segments if s.confidence >= min_confidence]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval/test_classify.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add eval/classify.py tests/test_eval/test_classify.py
git commit -m "feat: add LLM classification adapter with custom prompt support"
```

---

### Task 6: Annotation bootstrap and creation tooling

**Files:**
- Create: `eval/annotate.py`
- Create: `tests/test_eval/test_annotate.py`

- [ ] **Step 1: Write failing tests for annotation creation**

`tests/test_eval/test_annotate.py`:
```python
"""Tests for annotation bootstrap and creation."""

import json
from datetime import datetime

import pytest

from podcast_etl.models import Episode, StepStatus

from eval.annotate import bootstrap_from_episode, create_blank
from eval.models import Annotation, EpisodeRef


def _episode_with_ads():
    """Create an episode with detect_ads status."""
    return Episode(
        title="Episode One",
        guid="guid-1",
        published="Mon, 15 Jan 2024 10:00:00 GMT",
        audio_url="https://example.com/ep.mp3",
        duration="3600",
        description="An episode",
        slug="episode-one",
        raw_title="Episode One",
        status={
            "download": StepStatus(
                completed_at="2024-01-15T10:00:00",
                result={"path": "audio/episode.mp3", "size_bytes": 1024},
            ),
            "detect_ads": StepStatus(
                completed_at="2024-01-15T10:05:00",
                result={
                    "segments": [
                        {"start": 0.0, "end": 30.0, "confidence": 0.9,
                         "detector": "transcription", "label": "Pre-roll ad"},
                        {"start": 1800.0, "end": 1860.0, "confidence": 0.85,
                         "detector": "transcription", "label": "Mid-roll ad"},
                    ],
                    "audio_duration": 3600.0,
                    "detectors_used": ["transcription"],
                },
            ),
        },
    )


class TestBootstrapFromEpisode:
    def test_creates_annotation_from_detect_ads(self):
        ep = _episode_with_ads()
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        ann = bootstrap_from_episode(ep, ref, annotator="claude-sonnet-4-20250514")

        assert ann.audio_duration == 3600.0
        assert len(ann.segments) == 2
        assert ann.segments[0]["start"] == 0.0
        assert ann.segments[0]["end"] == 30.0
        assert ann.segments[0]["label"] == "Pre-roll ad"
        assert ann.annotator == "claude-sonnet-4-20250514"
        assert ann.episode_ref.podcast_slug == "my-podcast"

    def test_raises_when_no_detect_ads(self):
        ep = Episode(
            title="Ep", guid="g", published=None, audio_url=None,
            duration=None, description=None, slug="ep",
            status={},
        )
        ref = EpisodeRef(podcast_slug="p", episode_json="ep.json")
        with pytest.raises(ValueError, match="no detect_ads"):
            bootstrap_from_episode(ep, ref, annotator="test")

    def test_segments_have_empty_notes(self):
        ep = _episode_with_ads()
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        ann = bootstrap_from_episode(ep, ref, annotator="model")

        for seg in ann.segments:
            assert "notes" in seg
            assert seg["notes"] == ""


class TestCreateBlank:
    def test_creates_blank_annotation(self):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        ann = create_blank(ref, audio_duration=1800.0)

        assert ann.episode_ref == ref
        assert ann.audio_duration == 1800.0
        assert ann.segments == []
        assert ann.annotator == ""

    def test_blank_save_and_load(self, tmp_path):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        ann = create_blank(ref, audio_duration=1800.0)
        path = tmp_path / "blank.json"
        ann.save(path)

        loaded = Annotation.load(path)
        assert loaded.segments == []
        assert loaded.audio_duration == 1800.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval/test_annotate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.annotate'`

- [ ] **Step 3: Implement annotation tooling**

`eval/annotate.py`:
```python
"""Bootstrap and create gold-standard annotation files."""

from __future__ import annotations

from datetime import datetime

from podcast_etl.models import Episode

from eval.models import Annotation, EpisodeRef


def bootstrap_from_episode(
    episode: Episode,
    ref: EpisodeRef,
    annotator: str,
) -> Annotation:
    """Create an annotation pre-populated from an episode's detect_ads results.

    The resulting annotation can be saved to disk and then manually corrected.
    """
    detect_status = episode.status.get("detect_ads")
    if not detect_status:
        raise ValueError(f"Episode {episode.slug} has no detect_ads status")

    result = detect_status.result
    raw_segments = result.get("segments", [])
    audio_duration = result.get("audio_duration", 0.0)

    segments = [
        {
            "start": seg["start"],
            "end": seg["end"],
            "label": seg.get("label", ""),
            "notes": "",
        }
        for seg in raw_segments
    ]

    return Annotation(
        episode_ref=ref,
        audio_duration=audio_duration,
        segments=segments,
        annotator=annotator,
        created_at=datetime.now().isoformat(),
    )


def create_blank(ref: EpisodeRef, audio_duration: float) -> Annotation:
    """Create a blank annotation for manual labeling."""
    return Annotation(
        episode_ref=ref,
        audio_duration=audio_duration,
        segments=[],
        annotator="",
        created_at=datetime.now().isoformat(),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval/test_annotate.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add eval/annotate.py tests/test_eval/test_annotate.py
git commit -m "feat: add annotation bootstrap from episode status and blank creation"
```

---

### Task 7: Annotation validation

**Files:**
- Create: `eval/validate.py`
- Create: `tests/test_eval/test_validate.py`

- [ ] **Step 1: Write failing tests for validation**

`tests/test_eval/test_validate.py`:
```python
"""Tests for annotation validation."""

import json

import pytest

from eval.models import Annotation, EpisodeRef
from eval.validate import validate_annotation, validate_annotations


def _valid_annotation() -> Annotation:
    return Annotation(
        episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json"),
        audio_duration=3600.0,
        segments=[
            {"start": 0.0, "end": 30.0, "label": "Ad 1", "notes": ""},
            {"start": 100.0, "end": 130.0, "label": "Ad 2", "notes": ""},
        ],
        annotator="human",
        created_at="2026-04-12T10:00:00",
    )


class TestValidateAnnotation:
    def test_valid_annotation_passes(self):
        errors = validate_annotation(_valid_annotation())
        assert errors == []

    def test_segment_start_after_end(self):
        ann = _valid_annotation()
        ann.segments[0]["start"] = 40.0
        ann.segments[0]["end"] = 30.0
        errors = validate_annotation(ann)
        assert any("start >= end" in e for e in errors)

    def test_segment_exceeds_duration(self):
        ann = _valid_annotation()
        ann.segments[1]["end"] = 4000.0
        errors = validate_annotation(ann)
        assert any("exceeds audio duration" in e for e in errors)

    def test_overlapping_segments(self):
        ann = _valid_annotation()
        ann.segments[1]["start"] = 20.0  # overlaps with segment 0 (0-30)
        ann.segments[1]["end"] = 50.0
        errors = validate_annotation(ann)
        assert any("overlaps" in e for e in errors)

    def test_negative_start(self):
        ann = _valid_annotation()
        ann.segments[0]["start"] = -1.0
        errors = validate_annotation(ann)
        assert any("negative" in e for e in errors)

    def test_empty_segments_valid(self):
        ann = _valid_annotation()
        ann.segments = []
        errors = validate_annotation(ann)
        assert errors == []

    def test_missing_required_segment_fields(self):
        ann = _valid_annotation()
        ann.segments[0] = {"label": "Ad"}  # missing start and end
        errors = validate_annotation(ann)
        assert any("start" in e for e in errors)


class TestValidateAnnotations:
    def test_validates_all_files(self, tmp_path):
        ann_dir = tmp_path / "annotations"
        ann_dir.mkdir()

        good = _valid_annotation()
        good.save(ann_dir / "good.json")

        bad = _valid_annotation()
        bad.segments[0]["start"] = 50.0  # start > end (end is 30)
        bad.save(ann_dir / "bad.json")

        results = validate_annotations(ann_dir)
        assert "good.json" in results
        assert results["good.json"] == []
        assert "bad.json" in results
        assert len(results["bad.json"]) > 0

    def test_skips_non_json_files(self, tmp_path):
        ann_dir = tmp_path / "annotations"
        ann_dir.mkdir()
        (ann_dir / ".gitkeep").touch()
        (ann_dir / "readme.txt").write_text("not json")

        results = validate_annotations(ann_dir)
        assert len(results) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.validate'`

- [ ] **Step 3: Implement validation**

`eval/validate.py`:
```python
"""Validate annotation files for consistency."""

from __future__ import annotations

from pathlib import Path

from eval.models import Annotation


def validate_annotation(ann: Annotation) -> list[str]:
    """Check a single annotation for consistency. Returns list of error messages."""
    errors: list[str] = []

    sorted_segs = sorted(ann.segments, key=lambda s: s.get("start", 0))
    for i, seg in enumerate(sorted_segs):
        if "start" not in seg or "end" not in seg:
            errors.append(f"Segment {i}: missing 'start' or 'end' field")
            continue

        start, end = seg["start"], seg["end"]

        if start < 0 or end < 0:
            errors.append(f"Segment {i}: negative timestamp (start={start}, end={end})")

        if start >= end:
            errors.append(f"Segment {i}: start >= end ({start} >= {end})")

        if end > ann.audio_duration:
            errors.append(f"Segment {i}: end ({end}) exceeds audio duration ({ann.audio_duration})")

        # Check overlap with next segment
        if i + 1 < len(sorted_segs) and "start" in sorted_segs[i + 1]:
            next_start = sorted_segs[i + 1]["start"]
            if end > next_start:
                errors.append(f"Segment {i} (end={end}) overlaps with segment {i + 1} (start={next_start})")

    return errors


def validate_annotations(annotations_dir: Path) -> dict[str, list[str]]:
    """Validate all annotation JSON files in a directory.

    Returns a dict of filename -> list of error messages.
    Only processes .json files.
    """
    results: dict[str, list[str]] = {}

    for path in sorted(annotations_dir.iterdir()):
        if path.suffix != ".json":
            continue
        ann = Annotation.load(path)
        results[path.name] = validate_annotation(ann)

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval/test_validate.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add eval/validate.py tests/test_eval/test_validate.py
git commit -m "feat: add annotation validation"
```

---

### Task 8: Review tool

**Files:**
- Create: `eval/review.py`
- Create: `tests/test_eval/test_review.py`

- [ ] **Step 1: Write failing tests for the review display**

`tests/test_eval/test_review.py`:
```python
"""Tests for annotation review display."""

from eval.models import Annotation, EpisodeRef
from eval.review import format_review


TRANSCRIPT = [
    {"start": 0.0, "end": 4.2, "text": "Welcome to the show"},
    {"start": 4.2, "end": 8.1, "text": "Before we begin, a word from our sponsor"},
    {"start": 8.1, "end": 42.3, "text": "Squarespace is the all-in-one platform"},
    {"start": 42.3, "end": 43.8, "text": "Visit squarespace.com/myshow for 10% off"},
    {"start": 43.8, "end": 51.0, "text": "Alright, today we're talking about"},
]


def _annotation_with_ad():
    return Annotation(
        episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json"),
        audio_duration=3600.0,
        segments=[{"start": 8.0, "end": 43.5, "label": "Pre-roll ad", "notes": ""}],
        annotator="human",
        created_at="2026-04-12T10:00:00",
    )


class TestFormatReview:
    def test_marks_ad_segments(self):
        output = format_review(_annotation_with_ad(), TRANSCRIPT, audio_path="/output/audio/ep.mp3")
        lines = output.split("\n")

        # Non-ad lines should not have the marker
        assert any("Welcome to the show" in line and "\u258c" not in line for line in lines)
        # Ad lines should have the marker
        assert any("Squarespace" in line and "\u258c" in line for line in lines)
        assert any("squarespace.com" in line and "\u258c" in line for line in lines)
        # Post-ad line should not have marker
        assert any("talking about" in line and "\u258c" not in line for line in lines)

    def test_shows_audio_path(self):
        output = format_review(_annotation_with_ad(), TRANSCRIPT, audio_path="/output/audio/ep.mp3")
        assert "/output/audio/ep.mp3" in output

    def test_no_ads(self):
        ann = _annotation_with_ad()
        ann.segments = []
        output = format_review(ann, TRANSCRIPT, audio_path="/output/audio/ep.mp3")
        assert "\u258c" not in output

    def test_empty_transcript(self):
        output = format_review(_annotation_with_ad(), [], audio_path="/output/audio/ep.mp3")
        assert "/output/audio/ep.mp3" in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval/test_review.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.review'`

- [ ] **Step 3: Implement the review tool**

`eval/review.py`:
```python
"""Display transcript with annotation highlights for review."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.models import Annotation
from eval.resolve import resolve_episode


def _is_in_ad(time: float, segments: list[dict[str, Any]]) -> tuple[bool, dict[str, Any] | None]:
    """Check if a timestamp falls within any annotated ad segment."""
    for seg in segments:
        if seg["start"] <= time < seg["end"]:
            return True, seg
    return False, None


def format_review(
    annotation: Annotation,
    transcript: list[dict[str, Any]],
    audio_path: str,
) -> str:
    """Format transcript lines with ad segments highlighted."""
    lines = [f"\nAudio: {audio_path}\n"]

    for seg in transcript:
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()

        in_ad, ad_seg = _is_in_ad(start, annotation.segments)
        if in_ad and ad_seg is not None:
            ad_range = f"[{ad_seg['start']:.1f} - {ad_seg['end']:.1f}]"
            lines.append(f"\u258c [{start:.1f}s - {end:.1f}s]  {text:<50}  \u25c0 AD {ad_range}")
        else:
            lines.append(f"  [{start:.1f}s - {end:.1f}s]  {text}")

    return "\n".join(lines)


def review_annotation(annotation_path: Path, output_dir: Path) -> str:
    """Load an annotation and its transcript, then format for review.

    This is the main entry point for the review CLI.
    """
    annotation = Annotation.load(annotation_path)
    resolved = resolve_episode(annotation.episode_ref, output_dir)

    transcript: list[dict[str, Any]] = []
    if resolved.transcript_path and resolved.transcript_path.exists():
        transcript = json.loads(resolved.transcript_path.read_text())

    return format_review(annotation, transcript, str(resolved.audio_path))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval/test_review.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add eval/review.py tests/test_eval/test_review.py
git commit -m "feat: add annotation review tool with transcript highlighting"
```

---

### Task 9: Eval runner

**Files:**
- Create: `eval/run.py`
- Create: `tests/test_eval/test_run.py`

- [ ] **Step 1: Write failing tests for the runner**

`tests/test_eval/test_run.py`:
```python
"""Tests for the eval runner."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.detectors import AdSegment

from eval.models import Annotation, EpisodeRef
from eval.run import (
    EvalConfig,
    RunConfig,
    group_configs_by_whisper,
    load_prompt,
    load_run_config,
    run_eval,
)


def _setup_annotation(tmp_path):
    """Create a minimal annotation file and matching episode on disk."""
    # Annotation
    ann_dir = tmp_path / "annotations"
    ann_dir.mkdir()
    ann = Annotation(
        episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json"),
        audio_duration=120.0,
        segments=[{"start": 0.0, "end": 30.0, "label": "Pre-roll", "notes": ""}],
        annotator="human",
        created_at="2026-04-12T10:00:00",
    )
    ann.save(ann_dir / "ep-ann.json")

    # Episode on disk
    output_dir = tmp_path / "output"
    podcast_dir = output_dir / "my-podcast"
    podcast_dir.mkdir(parents=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "My Podcast", "url": "https://example.com",
        "description": None, "image_url": None, "slug": "my-podcast",
    }))
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir()
    (episodes_dir / "ep.json").write_text(json.dumps({
        "title": "Ep 1", "guid": "g1", "published": "2024-01-15",
        "audio_url": "https://example.com/ep.mp3", "duration": "120",
        "description": "ep", "slug": "ep-1",
        "status": {"download": {"completed_at": "2024-01-15T10:00:00",
                                 "result": {"path": "audio/ep.mp3"}}},
    }))
    audio_dir = podcast_dir / "audio"
    audio_dir.mkdir()
    (audio_dir / "ep.mp3").write_bytes(b"fake audio")

    return ann_dir, output_dir


class TestGroupConfigsByWhisper:
    def test_groups_by_whisper_settings(self):
        configs = [
            EvalConfig(name="a", whisper={"model": "base"}, llm={}, prompt="default", min_confidence=0.5),
            EvalConfig(name="b", whisper={"model": "base"}, llm={}, prompt="alt", min_confidence=0.5),
            EvalConfig(name="c", whisper={"model": "large"}, llm={}, prompt="default", min_confidence=0.5),
        ]
        groups = group_configs_by_whisper(configs)
        # "a" and "b" share whisper config, "c" is separate
        assert len(groups) == 2
        group_sizes = sorted(len(v) for v in groups.values())
        assert group_sizes == [1, 2]


class TestLoadPrompt:
    def test_loads_prompt_file(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "custom.txt").write_text("Find the ads.\n\nTranscript:\n")

        text = load_prompt("custom", prompts_dir)
        assert text == "Find the ads.\n\nTranscript:\n"

    def test_raises_on_missing_prompt(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="Prompt file not found"):
            load_prompt("missing", prompts_dir)


class TestLoadRunConfig:
    def test_loads_yaml(self, tmp_path):
        config_path = tmp_path / "eval_config.yaml"
        config_path.write_text("""
output_dir: ./output
configs:
  - name: test-config
    whisper:
      model: base
      language: en
    llm:
      provider: anthropic
      model: claude-haiku-4-5-20251001
    prompt: default
    min_confidence: 0.5
""")
        run_config = load_run_config(config_path)
        assert run_config.output_dir == "./output"
        assert len(run_config.configs) == 1
        assert run_config.configs[0].name == "test-config"


class TestRunEval:
    def test_runs_eval_and_returns_scores(self, tmp_path):
        ann_dir, output_dir = _setup_annotation(tmp_path)

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "default.txt").write_text("Find ads.\n\nTranscript:\n")

        transcript_segments = [
            {"start": 0.0, "end": 10.0, "text": "Ad content"},
            {"start": 10.0, "end": 30.0, "text": "More ad"},
            {"start": 30.0, "end": 120.0, "text": "Main content"},
        ]
        predicted_ads = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Pre-roll"),
        ]

        configs = [
            EvalConfig(name="test", whisper={"model": "base"}, llm={"provider": "anthropic", "model": "test"},
                       prompt="default", min_confidence=0.5),
        ]

        with patch("eval.run.transcribe", return_value=transcript_segments):
            with patch("eval.run.classify_with_prompt", return_value=predicted_ads):
                results = run_eval(
                    configs=configs,
                    annotations_dir=ann_dir,
                    output_dir=output_dir,
                    prompts_dir=prompts_dir,
                    results_dir=tmp_path / "results",
                )

        assert "test" in results
        assert results["test"].total_tp == 1
        assert results["test"].total_fp == 0
        assert results["test"].total_fn == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval/test_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.run'`

- [ ] **Step 3: Implement the runner**

`eval/run.py`:
```python
"""Eval runner: load configs, transcribe, classify, score, report."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from podcast_etl.detectors.transcription import transcribe

from eval.classify import classify_with_prompt
from eval.models import Annotation
from eval.resolve import resolve_episode
from eval.score import AggregateScore, aggregate_scores, format_report, score_episode

logger = logging.getLogger(__name__)


@dataclass
class EvalConfig:
    name: str
    whisper: dict[str, Any]
    llm: dict[str, Any]
    prompt: str  # name of prompt file in prompts/
    min_confidence: float


@dataclass
class RunConfig:
    output_dir: str
    configs: list[EvalConfig]


def load_run_config(path: Path) -> RunConfig:
    """Load an eval run config from YAML."""
    data = yaml.safe_load(path.read_text())
    configs = [
        EvalConfig(
            name=c["name"],
            whisper=c.get("whisper", {}),
            llm=c.get("llm", {}),
            prompt=c.get("prompt", "default"),
            min_confidence=c.get("min_confidence", 0.5),
        )
        for c in data.get("configs", [])
    ]
    return RunConfig(output_dir=data.get("output_dir", "./output"), configs=configs)


def load_prompt(name: str, prompts_dir: Path) -> str:
    """Load a named prompt from the prompts directory."""
    path = prompts_dir / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text()


def _whisper_config_key(whisper: dict[str, Any]) -> str:
    """Stable hash key for a whisper config, for transcript reuse."""
    serialized = json.dumps(whisper, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:12]


def group_configs_by_whisper(configs: list[EvalConfig]) -> dict[str, list[EvalConfig]]:
    """Group eval configs by whisper settings for transcript reuse."""
    groups: dict[str, list[EvalConfig]] = {}
    for config in configs:
        key = _whisper_config_key(config.whisper)
        groups.setdefault(key, []).append(config)
    return groups


def _load_annotations(annotations_dir: Path) -> list[Annotation]:
    """Load all annotation JSON files from the annotations directory."""
    annotations = []
    for path in sorted(annotations_dir.glob("*.json")):
        annotations.append(Annotation.load(path))
    return annotations


def run_eval(
    configs: list[EvalConfig],
    annotations_dir: Path,
    output_dir: Path,
    prompts_dir: Path,
    results_dir: Path,
) -> dict[str, AggregateScore]:
    """Run the eval matrix and return aggregate scores per config."""
    annotations = _load_annotations(annotations_dir)
    if not annotations:
        logger.warning("No annotations found in %s", annotations_dir)
        return {}

    # Load prompts
    prompt_cache: dict[str, str] = {}
    for config in configs:
        if config.prompt not in prompt_cache:
            prompt_cache[config.prompt] = load_prompt(config.prompt, prompts_dir)

    # Group configs by whisper settings for transcript reuse
    whisper_groups = group_configs_by_whisper(configs)

    # Transcribe once per whisper config per episode
    # Key: (whisper_key, episode_ref_key) -> transcript segments
    transcript_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    # Collect per-config per-episode scores
    config_scores: dict[str, list] = {c.name: [] for c in configs}

    for ann in annotations:
        try:
            resolved = resolve_episode(ann.episode_ref, output_dir)
        except FileNotFoundError as e:
            logger.warning("Skipping annotation: %s", e)
            continue

        gold = ann.segments_as_ad_segments()
        ref_key = f"{ann.episode_ref.podcast_slug}/{ann.episode_ref.episode_json}"

        for whisper_key, group in whisper_groups.items():
            cache_key = (whisper_key, ref_key)

            if cache_key not in transcript_cache:
                ad_config = {"whisper": group[0].whisper}
                transcript_cache[cache_key] = transcribe(resolved.audio_path, ad_config)

            transcript = transcript_cache[cache_key]

            for config in group:
                prompt_text = prompt_cache[config.prompt]
                ad_config = {
                    "whisper": config.whisper,
                    "llm": config.llm,
                    "min_confidence": config.min_confidence,
                }
                predicted = classify_with_prompt(transcript, prompt_text, ad_config)
                episode_score = score_episode(predicted, gold)
                config_scores[config.name].append(episode_score)

    # Aggregate
    results: dict[str, AggregateScore] = {}
    for config_name, scores in config_scores.items():
        results[config_name] = aggregate_scores(scores)

    # Save results
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M")
    for config_name, agg in results.items():
        result_path = results_dir / f"{timestamp}-{config_name}.json"
        result_data = {
            "config": config_name,
            "timestamp": timestamp,
            "total_tp": agg.total_tp,
            "total_fp": agg.total_fp,
            "total_fn": agg.total_fn,
            "precision": agg.precision,
            "recall": agg.recall,
            "f1": agg.f1,
            "start_error_median": agg.start_error_median,
            "end_error_median": agg.end_error_median,
            "total_content_lost": agg.total_content_lost,
            "total_ads_missed": agg.total_ads_missed,
        }
        result_path.write_text(json.dumps(result_data, indent=2) + "\n")

    return results


def main() -> None:
    """CLI entry point for the eval runner."""
    import sys

    logging.basicConfig(level=logging.INFO)

    eval_dir = Path(__file__).parent
    config_path = eval_dir / "eval_config.yaml"
    if not config_path.exists():
        print(f"No config found at {config_path}", file=sys.stderr)
        sys.exit(1)

    run_config = load_run_config(config_path)
    output_dir = Path(run_config.output_dir)

    results = run_eval(
        configs=run_config.configs,
        annotations_dir=eval_dir / "annotations",
        output_dir=output_dir,
        prompts_dir=eval_dir / "prompts",
        results_dir=eval_dir / "results",
    )

    print(format_report(results))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval/test_run.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add eval/run.py tests/test_eval/test_run.py
git commit -m "feat: add eval runner with config matrix and transcript reuse"
```

---

### Task 10: Example eval config and full test suite

**Files:**
- Create: `eval/eval_config.yaml.example`

- [ ] **Step 1: Create example eval config**

`eval/eval_config.yaml.example`:
```yaml
# Eval run configuration
# Copy to eval_config.yaml and customize for your setup.

output_dir: ./output

configs:
  - name: haiku-sentence-level
    whisper:
      model: base
      language: en
    llm:
      provider: anthropic
      model: claude-haiku-4-5-20251001
    prompt: default
    min_confidence: 0.5

  - name: sonnet-word-level
    whisper:
      model: base
      language: en
      word_timestamps: true
    llm:
      provider: anthropic
      model: claude-sonnet-4-20250514
    prompt: default
    min_confidence: 0.5
```

- [ ] **Step 2: Run all eval tests**

Run: `uv run pytest tests/test_eval/ -v`
Expected: All tests PASS

- [ ] **Step 3: Run full project test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS (both existing and new eval tests)

- [ ] **Step 4: Commit**

```bash
git add eval/eval_config.yaml.example
git commit -m "feat: add example eval config"
```
