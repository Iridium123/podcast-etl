# Ad Detection Improvements

Planned improvements to the LLM-based ad detection and stripping pipeline.

## Transcription precision

1. **Word-level timestamps from whisper** — Enable `word_timestamps=True` for local faster-whisper and `timestamp_granularities[]=word` for the remote API. Currently using sentence-level segments, which limits boundary precision to wherever whisper decided to chunk.

2. **Transcript versioning** — Don't silently overwrite transcripts when whisper config changes (model, language, word_timestamps). Either namespace by config or track which config produced a transcript.

## LLM classification

3. **Record provenance on results** — Store which model, prompt version, and whisper config produced each set of ad segments in the episode status. Currently segments are saved with no record of how they were generated.

4. **Make the LLM prompt configurable** — The classification prompt is hardcoded in `transcription.py`. Allow prompt variants to be swapped without code changes, supporting A/B experimentation.

5. **Finer segment boundary placement** — Post-processing step that uses word-level timestamps to refine LLM-reported boundaries. The LLM picks approximate times; snap to the nearest word boundary when it makes sense.

## Stripping quality

6. **Configurable buffer/padding on ad boundaries** — Allow padding before/after detected ad segments to trim conservatively and protect content. Currently cuts exactly at the LLM-reported boundary.

## Evaluation

7. **Scoring with configurable tolerance** — When comparing predicted vs. gold-standard boundaries, use a configurable window (e.g., 1s, 2s, 5s) for fuzzy matching rather than requiring exact alignment.

8. **Asymmetric scoring** — Penalize false positives (content incorrectly removed) more heavily than false negatives (ads left in). Removing real podcast content is worse than leaving an ad.
