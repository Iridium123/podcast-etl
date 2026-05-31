"""Eval runner: load configs, transcribe, classify, score, report."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from podcast_etl.detectors.transcription import normalize_whisper_config, transcribe

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
    # Annotation filter: only score annotations whose annotator is in this list.
    # Empty list or None means accept all annotators. Default ["human"] guards against
    # accidentally evaluating a model against its own bootstrapped predictions.
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
            min_confidence=c.get("min_confidence", 0.5),
        )
        for c in data.get("configs", [])
    ]
    allowed = data.get("allowed_annotators", ["human"])
    return RunConfig(
        output_dir=data.get("output_dir", "./output"),
        configs=configs,
        allowed_annotators=allowed,
    )


def load_prompt(name: str, prompts_dir: Path) -> str:
    """Load a named prompt from the prompts directory."""
    path = prompts_dir / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text()


def _whisper_config_key(whisper: dict[str, Any]) -> str:
    """Stable hash key for a whisper config, for transcript reuse.

    Only considers fields that actually affect transcript content
    (see normalize_whisper_config), so unrelated knobs (api_key, etc.) or
    no-op typos do not fragment the cache.
    """
    serialized = json.dumps(normalize_whisper_config(whisper), sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:12]


def group_configs_by_whisper(configs: list[EvalConfig]) -> dict[str, list[EvalConfig]]:
    """Group eval configs by whisper settings for transcript reuse."""
    groups: dict[str, list[EvalConfig]] = {}
    for config in configs:
        key = _whisper_config_key(config.whisper)
        groups.setdefault(key, []).append(config)
    return groups


def _reuse_production_transcript(
    resolved, whisper: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Return the on-disk production transcript if its whisper provenance matches.

    The detect_ads step records the normalized whisper config that produced
    the transcript. If that matches the current eval whisper config (after
    normalization), we can skip re-transcribing — saving minutes per episode.

    Returns None when there's no transcript, no recorded provenance, or the
    recorded config differs from the eval config.
    """
    if resolved.transcript_path is None:
        return None
    detect_status = resolved.episode.status.get("detect_ads")
    if not detect_status:
        return None
    recorded = detect_status.result.get("whisper")
    if recorded is None:
        return None
    if recorded != normalize_whisper_config(whisper):
        return None
    return json.loads(resolved.transcript_path.read_text())


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
    allowed_annotators: list[str] | None = None,
) -> dict[str, AggregateScore]:
    """Run the eval matrix and return aggregate scores per config.

    `allowed_annotators` filters which annotations are scored. Default
    (None) accepts only `human`-annotated entries — this prevents
    accidentally evaluating a model against its own bootstrapped
    predictions. Pass an empty list to accept all annotators, or a list
    of specific annotator strings (e.g. ["human", "claude-opus-4-7"]).
    """
    names = [c.name for c in configs]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ValueError(f"Duplicate config names: {sorted(duplicates)}")

    annotations = _load_annotations(annotations_dir)
    if not annotations:
        logger.warning("No annotations found in %s", annotations_dir)
        return {}

    if allowed_annotators is None:
        allowed_annotators = ["human"]
    if allowed_annotators:
        allowed_set = set(allowed_annotators)
        kept = [a for a in annotations if a.annotator in allowed_set]
        skipped = len(annotations) - len(kept)
        if skipped:
            skipped_annotators = sorted({a.annotator for a in annotations if a.annotator not in allowed_set})
            logger.warning(
                "Skipping %d annotation(s) whose annotator is not in %s (skipped annotators: %s). "
                "Set allowed_annotators=[] in eval_config.yaml to include all.",
                skipped, sorted(allowed_set), skipped_annotators,
            )
        annotations = kept
        if not annotations:
            logger.warning("No annotations remained after annotator filter")
            return {c.name: aggregate_scores([]) for c in configs}
    else:
        logger.info(
            "allowed_annotators=[] — accepting all annotators as gold (default 'human'-only filter bypassed)",
        )

    # Load prompts
    prompt_cache: dict[str, str] = {}
    for config in configs:
        if config.prompt not in prompt_cache:
            prompt_cache[config.prompt] = load_prompt(config.prompt, prompts_dir)

    # Group configs by whisper settings for transcript reuse
    whisper_groups = group_configs_by_whisper(configs)

    # Construct a single Anthropic client and share it across all classify calls.
    # Lazy import so test runs that mock anthropic via sys.modules still work.
    import anthropic
    api_key = next((c.llm.get("api_key") for c in configs if c.llm.get("api_key")), None)
    anthropic_client = anthropic.Anthropic(api_key=api_key)

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
                whisper = group[0].whisper
                reused = _reuse_production_transcript(resolved, whisper)
                if reused is not None:
                    logger.info(
                        "Reusing production transcript for %s (whisper match)", ref_key,
                    )
                    transcript_cache[cache_key] = reused
                else:
                    ad_config = {"whisper": whisper}
                    try:
                        transcript_cache[cache_key] = transcribe(resolved.audio_path, ad_config)
                    except Exception as e:
                        logger.warning(
                            "Transcription failed for %s with whisper config %s: %s",
                            ref_key, whisper_key, e,
                        )
                        continue

            transcript = transcript_cache[cache_key]

            for config in group:
                prompt_text = prompt_cache[config.prompt]
                ad_config = {
                    "whisper": config.whisper,
                    "llm": config.llm,
                    "min_confidence": config.min_confidence,
                }
                try:
                    predicted = classify_with_prompt(transcript, prompt_text, ad_config, client=anthropic_client)
                except Exception as e:
                    logger.warning(
                        "Classification failed for %s with config %s: %s",
                        ref_key, config.name, e,
                    )
                    continue
                episode_score = score_episode(predicted, gold)
                config_scores[config.name].append(episode_score)

    # Aggregate
    results: dict[str, AggregateScore] = {}
    for config_name, scores in config_scores.items():
        results[config_name] = aggregate_scores(scores)

    # Save results
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    for config_name, agg in results.items():
        result_path = results_dir / f"{timestamp}-{config_name}.json"
        result_data = {
            "config": config_name,
            "timestamp": timestamp,
            **asdict(agg),
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
        allowed_annotators=run_config.allowed_annotators,
    )

    print(format_report(results))


if __name__ == "__main__":
    main()
