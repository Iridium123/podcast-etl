"""Eval runner: label episodes with production's classify, then score datasets.

This is the orchestration layer. The primitives live elsewhere:
``eval.label`` (classify + assemble Labels), ``eval.score`` (segment matching),
``eval.datasets`` (load a directory of Labels). ``run_eval`` ties them together
into the convenience matrix exposed as ``podcast-etl eval run``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from podcast_etl.detectors.transcription import (
    build_llm_client,
    normalize_whisper_config,
    transcribe,
)
from podcast_etl.labels import EpisodeRef

from eval.datasets import load_dataset, ref_key
from eval.label import classify_to_segments, make_labels
from eval.resolve import ResolvedEpisode, resolve_episode
from eval.score import AggregateScore, aggregate_scores, score_episode

logger = logging.getLogger(__name__)


@dataclass
class EvalConfig:
    name: str
    whisper: dict[str, Any]
    llm: dict[str, Any]
    prompt: str  # name of prompt file in prompts/


@dataclass
class RunConfig:
    output_dir: str
    gold: str
    configs: list[EvalConfig]
    # Which gold annotators count as gold. Default ["human"] avoids circular
    # scoring against model-bootstrapped labels; [] accepts all annotators.
    allowed_annotators: list[str] | None = None


def load_run_config(path: Path) -> RunConfig:
    """Load an eval run config from YAML."""
    data = yaml.safe_load(path.read_text())
    configs = [
        EvalConfig(
            name=c["name"],
            whisper=c.get("whisper", {}),
            llm=c.get("llm", {}),
            prompt=c.get("prompt", "default"),
        )
        for c in data.get("configs", [])
    ]
    return RunConfig(
        output_dir=data.get("output_dir", "./output"),
        gold=data.get("gold", "gold"),
        configs=configs,
        allowed_annotators=data.get("allowed_annotators", ["human"]),
    )


def _audio_duration(audio_path: Path) -> float:
    """Audio duration in seconds (mutagen), matching production's detect_ads."""
    from mutagen.mp3 import MP3

    audio = MP3(audio_path)
    return audio.info.length if audio.info is not None else 0.0


# ---------------------------------------------------------------------------
# transcript acquisition + reuse
# ---------------------------------------------------------------------------

def _whisper_config_key(whisper: dict[str, Any]) -> str:
    """Stable hash of the content-affecting whisper fields, for transcript reuse."""
    serialized = json.dumps(normalize_whisper_config(whisper), sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:12]


def group_configs_by_whisper(configs: list[EvalConfig]) -> dict[str, list[EvalConfig]]:
    """Group configs by whisper settings so a transcript is computed once each."""
    groups: dict[str, list[EvalConfig]] = {}
    for config in configs:
        groups.setdefault(_whisper_config_key(config.whisper), []).append(config)
    return groups


def _reuse_production_transcript(
    resolved: ResolvedEpisode, whisper: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Return the on-disk production transcript if its whisper provenance matches.

    detect_ads records the whisper config that produced the transcript. When it
    matches the eval whisper config (after normalization) we skip re-transcribing.
    Returns None when there's no transcript, no recorded provenance, or a mismatch.
    """
    if resolved.transcript_path is None:
        return None
    detect_status = resolved.episode.status.get("detect_ads")
    if not detect_status:
        return None
    recorded = detect_status.result.get("whisper")
    if recorded is None or recorded != normalize_whisper_config(whisper):
        return None
    return json.loads(resolved.transcript_path.read_text())


# ---------------------------------------------------------------------------
# labeling a dataset
# ---------------------------------------------------------------------------

def label_dataset(
    config: EvalConfig,
    refs: list[EpisodeRef],
    output_dir: Path,
    dataset_dir: Path,
    prompt_text: str,
    client: Any | None = None,
    transcript_cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> list[Path]:
    """Run production classify for each ref and write Labels into *dataset_dir*.

    Writes ``dataset_dir/<slug>/labels/<audio-stem>.json`` (same layout and
    filename convention as production), so the result is a valid dataset.
    Returns the paths written. Episodes that fail to resolve are skipped with
    a warning rather than aborting the whole run.
    """
    if transcript_cache is None:
        transcript_cache = {}
    written: list[Path] = []
    for ref in refs:
        try:
            resolved = resolve_episode(ref, output_dir)
        except FileNotFoundError as e:
            logger.warning("Skipping %s: %s", ref_key(ref), e)
            continue

        cache_key = (_whisper_config_key(config.whisper), ref_key(ref))
        if cache_key in transcript_cache:
            transcript = transcript_cache[cache_key]
        else:
            reused = _reuse_production_transcript(resolved, config.whisper)
            if reused is not None:
                logger.info("Reusing production transcript for %s", ref_key(ref))
                transcript = reused
            else:
                transcript = transcribe(resolved.audio_path, {"whisper": config.whisper})
            transcript_cache[cache_key] = transcript

        segments = classify_to_segments(transcript, config.llm, prompt_text, client=client)
        labels = make_labels(
            ref=ref,
            audio_duration=round(_audio_duration(resolved.audio_path), 2),
            segments=segments,
            whisper=config.whisper,
            llm_config=config.llm,
            prompt_name=config.prompt,
        )
        out_path = dataset_dir / ref.podcast_slug / "labels" / f"{resolved.audio_path.stem}.json"
        labels.save(out_path)
        written.append(out_path)
    return written


# ---------------------------------------------------------------------------
# scoring two datasets
# ---------------------------------------------------------------------------

def score_datasets(
    predictions_dir: Path,
    gold_dir: Path,
    allowed_annotators: list[str] | None = None,
    threshold: float = 0.5,
) -> AggregateScore:
    """Score a predictions dataset against a gold dataset.

    Only episodes present in both are scored. Gold labels are filtered by
    ``allowed_annotators`` (default ``["human"]``; ``[]`` accepts all). Gold
    episodes lacking a prediction are logged, not silently dropped.
    """
    if allowed_annotators is None:
        allowed_annotators = ["human"]

    predictions = load_dataset(predictions_dir)
    gold = load_dataset(gold_dir)

    if allowed_annotators:
        allowed = set(allowed_annotators)
        kept = {k: v for k, v in gold.items() if v.provenance.annotator in allowed}
        skipped = len(gold) - len(kept)
        if skipped:
            logger.warning(
                "Skipping %d gold label(s) whose annotator is not in %s. "
                "Set allowed_annotators=[] to include all.",
                skipped, sorted(allowed),
            )
        gold = kept
    else:
        logger.info("allowed_annotators=[] — accepting all gold annotators")

    scores = []
    missing: list[str] = []
    for key, gold_labels in gold.items():
        pred = predictions.get(key)
        if pred is None:
            missing.append(key)
            continue
        scores.append(score_episode(pred.segments, gold_labels.segments, threshold=threshold))
    if missing:
        logger.warning(
            "%d gold episode(s) had no prediction and were not scored: %s",
            len(missing), sorted(missing),
        )
    return aggregate_scores(scores)


# ---------------------------------------------------------------------------
# the matrix runner
# ---------------------------------------------------------------------------

def run_eval(
    configs: list[EvalConfig],
    output_dir: Path,
    gold_dir: Path,
    datasets_dir: Path,
    prompts_dir: Path,
    results_dir: Path,
    timestamp: str,
    allowed_annotators: list[str] | None = None,
    client: Any | None = None,
) -> dict[str, AggregateScore]:
    """Label each config into its own dataset, score it against gold, save results.

    A single transcript cache and Anthropic client are shared across configs, so
    configs with matching whisper settings transcribe once and the cacheable
    prompt is reused across classify calls.
    """
    names = [c.name for c in configs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Duplicate config names: {duplicates}")

    gold = load_dataset(gold_dir)
    refs = [labels.episode_ref for labels in gold.values()]

    prompt_cache: dict[str, str] = {}
    for config in configs:
        if config.prompt not in prompt_cache:
            prompt_cache[config.prompt] = (prompts_dir / f"{config.prompt}.txt").read_text()

    if client is None:
        api_key = next((c.llm.get("api_key") for c in configs if c.llm.get("api_key")), None)
        client = build_llm_client({"provider": "anthropic", "api_key": api_key})

    transcript_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    results: dict[str, AggregateScore] = {}

    for config in configs:
        predictions_dir = datasets_dir / config.name
        label_dataset(
            config, refs, output_dir, predictions_dir,
            prompt_text=prompt_cache[config.prompt],
            client=client, transcript_cache=transcript_cache,
        )
        agg = score_datasets(predictions_dir, gold_dir, allowed_annotators=allowed_annotators)
        results[config.name] = agg

    results_dir.mkdir(parents=True, exist_ok=True)
    for config_name, agg in results.items():
        result_path = results_dir / f"{timestamp}-{config_name}.json"
        result_path.write_text(
            json.dumps({"config": config_name, "timestamp": timestamp, **asdict(agg)}, indent=2) + "\n"
        )

    return results


def main() -> None:
    """CLI entry point retained for `uv run python eval/run.py`."""
    import sys

    from eval.datasets import resolve_dataset_path
    from eval.score import format_report

    logging.basicConfig(level=logging.INFO)
    eval_dir = Path(__file__).parent
    config_path = eval_dir / "eval_config.yaml"
    if not config_path.exists():
        print(f"No config found at {config_path}", file=sys.stderr)
        sys.exit(1)

    run_config = load_run_config(config_path)
    results = run_eval(
        configs=run_config.configs,
        output_dir=Path(run_config.output_dir),
        gold_dir=resolve_dataset_path(run_config.gold),
        datasets_dir=eval_dir / "datasets",
        prompts_dir=Path("prompts"),
        results_dir=eval_dir / "results",
        timestamp=datetime.now().strftime("%Y-%m-%dT%H-%M-%S"),
        allowed_annotators=run_config.allowed_annotators,
    )
    print(format_report(results))


if __name__ == "__main__":
    main()
