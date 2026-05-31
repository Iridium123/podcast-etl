# Labels-as-standalone-output + Eval-as-thin-scorer refactor

**Date:** 2026-05-31
**Supersedes:** large portions of PR #56 (the original ad-detection-eval harness)
**Strategy:** Two sequential PRs off main, both fresh branches.

---

## Why this exists

PR #56 added an evaluation harness for the LLM-based ad detector. Along the way it accumulated:

1. A parallel classify implementation in `eval/classify.py` that drifted from production's `AnthropicProvider.classify_ads` — different prompt-handling, different caching behavior, different client lifecycle.
2. An eval-only `Annotation` dataclass that mirrors `detect_ads.result` with a different schema, requiring schema-conversion code at boundaries.
3. Coupling between eval and the *internals* of production (private helpers, embedded segments inside episode JSONs).

The result is two ways of producing labeled episodes, two schemas for representing them, and an eval workflow that can't be trusted to predict production behavior because the code paths diverge.

This plan unifies all that around two ideas:

- **Production owns the classify code path.** Eval is a configuration + orchestration layer on top.
- **Ad labels are first-class on-disk artifacts**, parallel to transcripts. Both production and eval write them in the same format. Eval scoring just compares two directories of label files.

Once this lands, "does prompt X beat prompt Y?" is answerable by running production-the-tool twice with different `prompt:` config values and pointing the eval scorer at the two output directories.

---

## Architecture: before / after

### Before

```
production:
  output/<slug>/episodes/<file>.json    # contains detect_ads.result.segments inline
  output/<slug>/transcripts/<file>.json

eval:
  eval/annotations/<file>.json          # Annotation dataclass (different schema)
  eval/classify.py                      # parallel classify implementation
  eval/run.py                           # combined generate + score
  eval/results/<ts>-<config>.json       # aggregate scores
```

### After

```
production:
  output/<slug>/episodes/<file>.json    # detect_ads.result records {labels_path, provenance}; NO segments
  output/<slug>/transcripts/<file>.json
  output/<slug>/labels/<file>.json      # NEW — Labels written here, parallel to transcripts/
  prompts/default.txt                   # NEW — extracted from _CLASSIFY_PROMPT

eval:
  eval/datasets/<name>/<slug>/labels/<file>.json  # same Labels shape as production
  eval/results/<ts>-...json                       # scoring output only
  (no eval/classify.py, no Annotation dataclass)
```

The `Labels` dataclass and the `classify()` function live in production code (`podcast_etl/`). Eval imports them.

---

## The `Labels` type

Defined in production (`src/podcast_etl/labels.py` or alongside `AdSegment`):

```python
@dataclass
class Provenance:
    whisper: dict[str, Any]              # normalized whisper config
    llm: dict[str, str]                  # {provider, model, prompt}
    annotator: str                       # = llm.model unless human-corrected
    created_at: str

@dataclass
class Labels:
    episode_ref: EpisodeRef              # {podcast_slug, episode_json}
    audio_duration: float                # for validation
    segments: list[AdSegment]            # AdSegment gains optional notes: str = ""
    provenance: Provenance

    def save(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> Labels: ...
```

`EpisodeRef` moves from `eval/models.py` into production (`podcast_etl/labels.py` or similar).

On-disk JSON shape:

```json
{
    "episode_ref": {"podcast_slug": "...", "episode_json": "..."},
    "audio_duration": 1944.0,
    "segments": [
        {"start": 0.0, "end": 27.9, "label": "Pre-roll ad for X",
         "confidence": 0.95, "detector": "transcription", "notes": ""}
    ],
    "provenance": {
        "whisper": {"model": "base", "language": "en"},
        "llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "prompt": "default"},
        "annotator": "claude-haiku-4-5-20251001",
        "created_at": "2026-05-31T..."
    }
}
```

---

## PR-A — Production refactor

Branch: fresh off main. No eval changes (eval/ untouched).

### A1. Extract prompt to a file

- Create `prompts/default.txt` at project root containing the current `_CLASSIFY_PROMPT` text.
- Add config field `ad_detection.llm.prompt: str = "default"`.
- `AnthropicProvider.classify_ads` resolves `prompts/<name>.txt`, reads at call time.
- Validation: unknown prompt name raises a clear error early in pipeline setup.

### A2. Prompt caching + shared client in production

- `AnthropicProvider.classify_ads` puts prompt in `system` with `cache_control: ephemeral`.
- Accept optional `client: anthropic.Anthropic` parameter.
- Pipeline construction (or detect_ads step) instantiates one client per pipeline run and threads it through. Eliminates per-call client construction in production.

### A3. Collapse classify implementations

- New public `classify(transcript, prompt_text, llm_config, client=None) -> list[AdSegment]` in `transcription.py`.
- `AnthropicProvider.classify_ads` becomes a thin call site.
- Eval will use this directly in PR-B (so this is the seam).

### A4. `Labels` dataclass + standalone output files

- New `podcast_etl/labels.py` (or extend `detectors/__init__.py`) defining `Labels`, `Provenance`, `EpisodeRef`.
- Add optional `notes: str = ""` to `AdSegment`.
- `detect_ads` step:
  - Computes the existing detection result.
  - Constructs a `Labels` object.
  - Writes to `output/<slug>/labels/<episode-stem>.json`.
  - Records `{labels_path, transcript_path, completed_at, whisper, llm}` in `episode.status['detect_ads'].result`. **No more `segments` or `audio_duration` in the result dict.**
- Any consumer that needs the segments (e.g., the `strip_ads` step) reads them from the labels file via the recorded path.

### A5. Migration script

`scripts/migrate_labels.py` (standalone — slated for deletion after the one-time migration runs):

```
uv run python scripts/migrate_labels.py --output-dir output/
```

For each episode JSON containing `detect_ads.result['segments']`:
1. Construct `Labels` from the embedded data (segments, audio_duration, recorded whisper/llm).
2. Write to `output/<slug>/labels/<file>.json`.
3. Rewrite `detect_ads.result` to drop `segments`/`audio_duration` and add `labels_path`.
4. Save the episode JSON.

Idempotent. Logs each conversion. Dry-run flag.

### A6. Tests + docs

- Update `test_detect_ads_step.py` for new label-file output (drop the in-episode-segments assertions, add labels-file assertions).
- Update `test_transcription_detector.py` for the new `classify()` signature.
- New `tests/test_labels.py` for `Labels.save`/`load`/roundtrip.
- New `tests/test_migrate_labels.py` for the migration script.
- Update CLAUDE.md (architecture, test inventory).
- Update README.md (config format with `prompt:` field, mention of `labels/` directory).

### Out of scope for PR-A

- All eval code stays as-is (still has its own `Annotation`, still has `eval/classify.py`). It will continue to work against the new production output because the embedded-segments path is gone — eval reads from disk anyway via `Episode.load`, but `eval/run.py`'s current `_reuse_production_transcript` already inspects `detect_ads.result` not `Episode.segments` directly, so the only changes needed in eval to keep tests green are minor.
- Actually: need to spot-check `eval/run.py:_reuse_production_transcript` since it reads `detect_ads.result['whisper']`. That still works since we keep whisper in the result. ✅

---

## PR-B — Eval rebuild as thin scorer

Branch: fresh off main. Depends on PR-A being merged.

### B1. Eval datasets = production label layout

- A "dataset" is a directory: `eval/datasets/<dataset-name>/<podcast-slug>/labels/<episode-stem>.json`.
- Each file is a production-format `Labels` JSON.
- Production's `output/<slug>/labels/` is a valid dataset out of the box (`--gold output` works directly).

### B2. Delete eval-only constructs

- Remove `Annotation` dataclass (replaced by `Labels`).
- Remove `eval/classify.py` (replaced by `from podcast_etl.detectors.transcription import classify`).
- Update `eval/models.py` to drop `Annotation` (and `EpisodeRef` since it moved to production).
- Update `eval/score.py` if it constructs `AdSegment` from `Annotation.segments` — read from `Labels.segments` directly.

### B3. CLI commands (all under `podcast-etl eval`)

```
podcast-etl eval label <dataset-name> [--podcast SLUG] [--episodes ...] [--config <yaml>]
  Runs production's classify() for each episode with the configured prompt/model/whisper.
  Writes Labels files to eval/datasets/<dataset-name>/<slug>/labels/<file>.json.
  Uses production transcripts when whisper config matches (reuses existing logic).

podcast-etl eval annotate <podcast> <episode-stem> [--dataset gold] [--blank|--bootstrap-from <dataset>]
  Creates a Labels file for hand correction.
  Bootstrap copies from another dataset's Labels (defaults to copying from production).
  After human edits, the file's annotator should be set to "human".

podcast-etl eval validate <dataset-name>
  Same structural checks as today.

podcast-etl eval score --predictions <dataset|path> --gold <dataset|path> [--allowed-annotators ...]
  Compares two directories of Labels files. Episodes in both get scored.
  Writes results to eval/results/<ts>-<predictions>-vs-<gold>.json + prints table.
  Multiple --predictions allowed for batch comparison vs one gold.

podcast-etl eval run  (convenience wrapper, retained)
  Reads eval_config.yaml matrix; for each entry calls `label`, then `score`
  against the configured gold dataset. Same UX as today, built on the new primitives.
```

### B4. Migrate the 3 sonnet annotations

`eval/annotations/money-stuff-the-podcast-*.json` → `eval/datasets/sonnet-4-6-bootstrap/money-stuff-the-podcast/labels/<file>.json`. Schema convert (drop old `Annotation` fields, write `Labels` with `provenance.annotator = "claude-sonnet-4-6"`). Delete `eval/annotations/` and the `.gitkeep`.

### B5. Tests + docs

- Drop `test_classify.py` (no eval-side classify to test).
- Update `test_eval/test_run.py` for new label/score split.
- New `test_eval/test_score_cmd.py` covering the score command directly.
- Update `test_annotate.py` for new `Labels` output.
- Update `test_eval_cli.py` for the new subcommand surface.
- README: rewrite eval section around datasets-as-directories.
- CLAUDE.md: update test inventory + architecture.
- `docs/ad-detection-improvements.md`: mark roadmap items done (#1, #2, #3, #9 should all be fully resolved).

---

## Decisions (resolved)

| # | Decision |
|---|---|
| Branch strategy | Fresh branches off main, both PRs |
| Prompts location | `prompts/` at project root (shared) |
| `AdSegment.notes` | Add as `notes: str = ""` (production ignores; eval uses) |
| Annotator field | `provenance.annotator = llm.model` unless human-corrected |
| `Annotation` dataclass | Drop entirely; `Labels` replaces it |
| `EpisodeRef` location | Move from `eval/models.py` to production |
| `eval run` convenience wrapper | Keep |
| Dataset layout | `eval/datasets/<name>/<slug>/labels/<file>.json` (mirrors production) |
| Dataclass name | `Labels` |
| Migration tool | Standalone `scripts/migrate_labels.py`; delete after running |
| Embedded-segments fallback | None — remove fully once migrated |
| PR-B branch base | Off main (not off PR-A's branch) |
| Anthropic client lifecycle | Constructed per step invocation (one per `detect_ads.process(episode)`) — keeps step boundary clean, avoids introducing client state into `PipelineContext` |
| Migration script implementation | Uses the `Labels` dataclass for serialization — harder to corrupt data, slightly slower to develop |
| Post PR-A sequencing | Rebase PR #56 onto new main, see what's left, likely close it |

---

## Sequencing

1. Land PR-A (production refactor + migration script).
2. Run `scripts/migrate_labels.py` on personal/production data.
3. Rebase PR #56 onto new main. Most of it (eval improvements 1/2/4/5, sonnet annotations) will conflict. Likely close it.
4. Open PR-B (eval rebuild) off fresh main.
5. After PR-B merges, delete `scripts/migrate_labels.py`.
