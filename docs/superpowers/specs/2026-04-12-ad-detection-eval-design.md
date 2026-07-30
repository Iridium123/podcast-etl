# Ad Detection Evaluation Harness

## Problem

The LLM-based ad detection pipeline produces imprecise segment boundaries — typically a few seconds of ad audio leaks through at the start of each detected segment. There is no way to measure detection quality, compare model/prompt configurations, or track improvement over time. We need an evaluation workflow to build a gold-standard test set and score detection accuracy against it.

## Goals

- Build a test set of 20-50 human annotated episodes across multiple podcasts plus potentially more labeled with more expensive models or methods. 
- Compare ad detection quality across different whisper configs, LLM models, and prompt variants in a single run
- Measure segment detection accuracy (precision/recall) with emphasis on avoiding false positives (content incorrectly removed)
- Measure boundary precision in both seconds and word-level accuracy
- Keep the eval decoupled from the main pipeline — imports shared code but doesn't modify it

## Non-goals

- Interactive annotation UI (plain JSON editing for now)
- Listening quality evaluation of stripped audio
- CI integration
- Changes to the main pipeline (tracked separately in `docs/ad-detection-improvements.md`)

## Annotation format

Each annotated episode is a JSON file in `eval/annotations/`. All annotations — whether bootstrapped from a model or created by a human — use the same format.

```json
{
  "episode_ref": {
    "podcast_slug": "my-podcast",
    "episode_json": "2024-01-15-episode-one-a1b2c3d4.json"
  },
  "audio_duration": 3600.0,
  "segments": [
    {
      "start": 0.0,
      "end": 43.5,
      "label": "Pre-roll ad for Squarespace",
      "notes": "Host-read, blends into intro music"
    },
    {
      "start": 1820.0,
      "end": 1892.0,
      "label": "Mid-roll programmatic ad",
      "notes": ""
    }
  ],
  "annotator": "human",
  "created_at": "2026-04-12T10:00:00"
}
```

Key design choices:

- `episode_ref` points to the podcast slug and episode JSON filename — enough to resolve the audio file, transcript, and episode metadata given an output directory.
- `annotator` is a freeform string. Model-bootstrapped annotations use the model name (e.g., `claude-sonnet-4-20250514`). Human-corrected annotations change this to `human` or another identifier. The scorer treats all annotations identically; the field is metadata for filtering and bookkeeping.
- Segments have `start`/`end` at whatever precision the annotator wants. No `confidence` field — these are ground truth.
- `notes` is optional freeform text for the annotator's context (e.g., "ambiguous boundary — music transition").
- The annotation file references the episode but does not embed transcripts or audio paths. These are resolved at runtime.

## Annotation workflow

**Bootstrap from a model run:** Run a high-quality model config (e.g., Opus with word-level timestamps) against target episodes. The bootstrap script writes standard annotation files with `annotator` set to the model name and segments from the model's predictions.

**Human correction:** Open the bootstrapped annotation JSON in an editor. Adjust timestamps, add/remove segments, update labels, and change `annotator` to `human`. This is the same file — no format conversion needed.

**Manual creation:** For episodes with no prior detection, a script creates a blank annotation file with the episode reference and audio duration filled in.

**Review tool:** A `review` command that displays the timestamped transcript with gold-standard segments highlighted alongside the audio file path, so the annotator can read the transcript and listen to the audio when correcting boundaries.

```
$ uv run python eval/review.py eval/annotations/my-podcast-ep1.json

Audio: /output/my-podcast/audio/My Podcast - 2024-01-15 - Episode One.mp3

  [0.0s - 4.2s]   Welcome to the show
  [4.2s - 8.1s]   Before we begin, a word from our sponsor
▌ [8.1s - 42.3s]  Squarespace is the all-in-one platform...   ◀ AD [8.0 - 43.5]
▌ [42.3s - 43.8s] Visit squarespace.com/myshow for 10% off    ◀ AD [8.0 - 43.5]
  [43.8s - 51.0s]  Alright, today we're talking about...
```

**Validation:** A script that verifies all annotation files reference episodes that exist on disk, segments don't overlap, start < end, and times fall within audio duration.

## Eval run config

A YAML config specifies which model/prompt/whisper combinations to test. Multiple configs can be compared in a single run.

```yaml
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
    prompt: word-boundary
    min_confidence: 0.5
```

- `prompt` refers to a named file in `eval/prompts/`. `default` maps to the current hardcoded prompt, extracted to a file.
- Configs that share the same whisper settings reuse the same transcript — transcribe once, classify multiple times. This saves the most expensive operation.

## Eval runner

The runner:

1. Loads all annotation files from `eval/annotations/`.
2. For each annotation, resolves the episode's audio file and existing transcript via `episode_ref` + `output_dir`.
3. Groups configs by whisper settings. For each whisper group, transcribes once (or reuses an existing transcript if available and config matches).
4. For each config, runs LLM classification using the specified model and prompt variant.
5. Passes predicted segments and gold-standard segments to the scorer.
6. Saves per-config results to `eval/results/<timestamp>-<config-name>.json`.
7. Prints a comparison table to stdout.

The runner calls `transcribe()` and `classify_transcript()` from `podcast_etl.detectors.transcription` directly. It does not go through the pipeline step machinery.

**Eval transcript storage:** Eval transcripts are stored in `eval/transcripts/<podcast_slug>/` keyed by whisper config hash and episode, so they never overwrite production transcripts in the output directory. If an eval whisper config matches what's already in the output directory, the runner can read (but not write) the existing transcript.

Prompt variants are loaded from `eval/prompts/<name>.txt`. The runner substitutes the prompt text before calling the LLM provider, overriding the hardcoded default. This requires the classify path to accept a prompt parameter — a small adapter in the eval code, not a change to the main codebase.

## Scoring

### Segment matching

A predicted segment matches a gold segment if their overlap exceeds a threshold fraction of the gold segment's duration (default: 50%). The matching strategy is pluggable — the scorer accepts a matching function, so it can be swapped for IoU-based, time-window-based, or other approaches later without changing the rest of the scoring code.

Each gold segment matches at most one prediction (best overlap), and each prediction matches at most one gold segment.

### Per-episode metrics

**Segment detection:**
- True positives — predicted segments that match a gold segment
- False positives — predicted segments with no gold match (content incorrectly flagged)
- False negatives — gold segments with no matching prediction (missed ads)

**Boundary error (for true positives):**
- Signed start error: predicted start minus gold start (positive = late, negative = early)
- Signed end error: predicted end minus gold end (positive = extends past ad, negative = cuts short)
- Reported in seconds — always available regardless of whisper config

**Word-level boundary accuracy (when word timestamps are available):**
- For each true positive, look at the gap between the predicted and gold boundaries. Count the words that fall in that gap — these are the words that were misclassified (content words incorrectly included in an ad, or ad words incorrectly left as content).
- Reports like "3 content words incorrectly cut" — more intuitive than seconds for understanding impact
- Only available when the transcript includes word-level timestamps; omitted otherwise

### Aggregate metrics

- Precision, recall, F1 on segment detection
- Mean, median, p95 boundary error for start and end separately
- Total content incorrectly removed (seconds) — false positive impact
- Total ad duration missed (seconds) — false negative impact
- Word-level accuracy stats when available
- All metrics can be filtered by annotator type (e.g., human-only vs. all)

### Output

Per-run JSON results files in `eval/results/`. Human-readable comparison table to stdout:

```
Config                  Prec   Rec    F1    Start-err(med)  End-err(med)  Content-lost  Ads-missed
haiku-sentence-level    0.92   0.85   0.88  +2.1s           -0.8s         12.3s         45.2s
sonnet-word-level       0.95   0.88   0.91  +0.4s           -0.3s         3.1s          38.0s
```

## File layout

```
eval/
├── annotations/              # gold-standard JSON files (version controlled)
│   ├── my-podcast-ep1.json
│   └── another-podcast-ep3.json
├── prompts/                  # named prompt variants
│   ├── default.txt           # current hardcoded prompt, extracted
│   └── word-boundary.txt     # variant using word-level timestamps
├── results/                  # run outputs (gitignored)
│   └── ...
├── transcripts/              # eval-specific transcripts (gitignored)
│   └── <podcast-slug>/       # keyed by whisper config hash + episode
├── run.py                    # eval runner
├── score.py                  # scorer (matching + metrics)
├── annotate.py               # bootstrap/create annotation files
├── review.py                 # transcript + annotation display
├── validate.py               # annotation consistency checks
└── eval_config.yaml          # run config
```

### Imports from main project

The eval scripts import from `podcast_etl` for:
- `models.Episode.load()`, `models.Podcast.load()` — resolving episode references
- `detectors.transcription.transcribe()`, `TranscriptionDetector.classify_transcript()` — running detection
- `detectors.AdSegment` — segment dataclass
- `pipeline.deep_merge()` — config merging

Everything else (scoring, matching, annotation management, reporting) is self-contained in `eval/`.
