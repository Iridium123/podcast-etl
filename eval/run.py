"""Eval matrix runner — label each config into its own dataset, score vs one gold.

``run_eval`` reads a matrix config (see ``eval/eval_config.yaml.example``),
labels every config's predictions into ``<datasets_dir>/_runs/<name>``, scores
each against a single gold dataset, and returns a ``dict[name -> AggregateScore]``.
The gold dataset defines the episode set: only episodes present in gold are
scored, and only those gold entries whose ``provenance.annotator`` is in
``allowed_annotators`` (default ``["human"]``).

A single in-memory ``transcript_cache`` is threaded through every config so that
configs sharing identical whisper settings transcribe each episode only once.

``main()`` lets ``uv run python eval/run.py`` work standalone.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path

import yaml

from eval.datasets import load_dataset, resolve_dataset_root
from eval.label import label_dataset
from eval.score import AggregateScore, aggregate_scores, format_report, score_episode

logger = logging.getLogger(__name__)


def run_eval(
    config_path: Path,
    output_dir: Path,
    datasets_dir: Path,
    results_dir: Path,
) -> dict[str, AggregateScore]:
    """Run the eval matrix described by *config_path*.

    Args:
        config_path: Path to the matrix config YAML.
        output_dir: Production output directory (overridable via config's
            ``output_dir`` key).
        datasets_dir: Directory holding named datasets (gold + per-run predictions).
        results_dir: Directory to write per-config result JSON into.

    Returns:
        Mapping of config name to its :class:`~eval.score.AggregateScore`.

    Raises:
        ValueError: If two configs share the same ``name``.
    """
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    output_dir = Path(cfg.get("output_dir", output_dir))

    gold_root = resolve_dataset_root(cfg["gold"], output_dir, datasets_dir)
    gold = load_dataset(gold_root)

    allowed = cfg.get("allowed_annotators", ["human"])
    if allowed:
        allowed_set = set(allowed)
        before = len(gold)
        gold = {k: v for k, v in gold.items() if v.provenance.annotator in allowed_set}
        skipped = before - len(gold)
        if skipped:
            logger.warning(
                "Skipped %d gold annotation(s) not in allowed annotators %s",
                skipped, sorted(allowed_set),
            )

    refs = [v.episode_ref for v in gold.values()]

    configs = cfg.get("configs", [])
    seen: set[str] = set()
    for c in configs:
        name = c["name"]
        if name in seen:
            raise ValueError(f"Duplicate config name in eval config: {name!r}")
        seen.add(name)

    # Shared across all configs so identical whisper settings reuse transcripts.
    transcript_cache: dict = {}
    results: dict[str, AggregateScore] = {}
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    results_dir.mkdir(parents=True, exist_ok=True)

    for c in configs:
        name = c["name"]
        prompt = c.get("prompt", c.get("llm", {}).get("prompt", "default"))
        ad_config = {
            "whisper": c.get("whisper", {}),
            "llm": {**c.get("llm", {}), "prompt": prompt},
            "min_confidence": c.get("min_confidence", 0.5),
        }
        pred_root = datasets_dir / "_runs" / name
        label_dataset(
            refs, ad_config, output_dir, pred_root, transcript_cache=transcript_cache
        )
        # label_dataset writes nothing (so pred_root may not exist) when refs is
        # empty — e.g. the gold annotator filter removed every episode. Treat an
        # absent predictions dir as an empty dataset rather than erroring.
        pred = load_dataset(pred_root) if pred_root.exists() else {}
        scored_keys = [k for k in gold if k in pred]
        unscored_keys = [k for k in gold if k not in pred]
        if unscored_keys:
            logger.warning(
                "Scored %d/%d gold episodes for %r; %d had no prediction and were excluded: %s",
                len(scored_keys), len(gold), name, len(unscored_keys), sorted(unscored_keys),
            )
        scores = [
            score_episode(pred[k].segments, gold[k].segments)
            for k in scored_keys
        ]
        agg = aggregate_scores(scores)
        results[name] = agg

        out_path = results_dir / f"{timestamp}-{name}.json"
        out_path.write_text(
            json.dumps(
                {
                    "config": name,
                    "gold": cfg["gold"],
                    "timestamp": timestamp,
                    "gold_episode_count": len(gold),
                    "scored_episode_count": len(scored_keys),
                    "aggregate": dataclasses.asdict(agg),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return results


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config_path = Path("eval/eval_config.yaml")
    results = run_eval(
        config_path,
        output_dir=Path("./output"),
        datasets_dir=Path("eval/datasets"),
        results_dir=Path("eval/results"),
    )
    print(format_report(results))


if __name__ == "__main__":
    main()
