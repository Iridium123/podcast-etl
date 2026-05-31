# Ad Detection Improvements

Planned improvements to the LLM-based ad detection and stripping pipeline.

## Transcription precision

1. **Word-level timestamps from whisper** — Enable `word_timestamps=True` for local faster-whisper and `timestamp_granularities[]=word` for the remote API. Currently using sentence-level segments, which limits boundary precision to wherever whisper decided to chunk.

2. ~~**Transcript versioning** — Don't silently overwrite transcripts when whisper config changes (model, language, word_timestamps).~~ **Done.** `detect_ads` records the normalized whisper config that produced each transcript; on subsequent runs it compares the recorded config to the current one and re-transcribes on mismatch instead of silently reusing stale data.

## LLM classification

3. ~~**Record provenance on results** — Store which model, prompt version, and whisper config produced each set of ad segments in the episode status.~~ **Partially done.** `detect_ads.result` now records `whisper` (normalized) and `llm` (provider + model). Prompt version not yet tracked.

4. **Make the LLM prompt configurable** — The classification prompt is hardcoded in `transcription.py`. Allow prompt variants to be swapped without code changes, supporting A/B experimentation.

5. **Finer segment boundary placement** — Post-processing step that uses word-level timestamps to refine LLM-reported boundaries. The LLM picks approximate times; snap to the nearest word boundary when it makes sense.

## Stripping quality

6. **Configurable buffer/padding on ad boundaries** — Allow padding before/after detected ad segments to trim conservatively and protect content. Currently cuts exactly at the LLM-reported boundary.

## Evaluation

7. **Scoring with configurable tolerance** — When comparing predicted vs. gold-standard boundaries, use a configurable window (e.g., 1s, 2s, 5s) for fuzzy matching rather than requiring exact alignment.

8. **Asymmetric scoring** — Penalize false positives (content incorrectly removed) more heavily than false negatives (ads left in). Removing real podcast content is worse than leaving an ad.

9. ~~**Reuse existing production transcripts in eval** — The `run_eval` runner currently always re-transcribes audio for each whisper config, even when an existing transcript is on disk in the production output directory under `<podcast_dir>/transcripts/`.~~ **Done.** The runner now consults `detect_ads.result['whisper']`; if it matches the eval's normalized whisper config, it loads the on-disk transcript instead of re-transcribing.

10. **Persistent eval transcript cache** — Beyond reusing production transcripts, eval-only whisper configs (e.g., `word_timestamps=true` when production isn't using it) should cache to `eval/transcripts/<podcast-slug>/<whisper-hash>/<episode>.json` so subsequent eval runs against the same configurations don't re-pay the transcription cost.
