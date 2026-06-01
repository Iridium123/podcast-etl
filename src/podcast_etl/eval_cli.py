"""The ``podcast-etl eval`` CLI surface — a dev tool for the ad-detection eval harness.

The eval harness (``eval/`` package) lives at the PROJECT ROOT, not under
``src/``, so it is NOT part of the installed ``podcast_etl`` package. When this
CLI runs via the console entry point (``podcast-etl eval ...``), the current
working directory is not automatically on ``sys.path``, so ``import eval.X`` can
fail at runtime even though it works under pytest (which sets
``pythonpath = ["."]``). The ``_ensure_cwd_importable()`` guard below inserts the
CWD so the harness is importable when run from the repo root.

Because the eval harness is a dev tool, the ``eval.*`` imports are done lazily
INSIDE each command callback (after the guard). This keeps merely importing this
module — and therefore ``cli.py`` — from hard-failing in deployments where the
``eval/`` directory is absent (e.g. the Docker image).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import click
import yaml

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("./output")
DEFAULT_DATASETS_DIR = Path("eval/datasets")
DEFAULT_RESULTS_DIR = Path("eval/results")
DEFAULT_EVAL_CONFIG = Path("eval/eval_config.yaml")


def _ensure_cwd_importable() -> None:
    """Ensure the current working directory is on ``sys.path``.

    The ``eval/`` package lives at the repo root, outside the installed
    ``podcast_etl`` package; the console entry point does not put CWD on the
    path. Call this before any ``import eval.X`` in a command callback.
    """
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


# ---------------------------------------------------------------------------
# Reusable option decorators
# ---------------------------------------------------------------------------

def _output_dir_option(fn):
    return click.option(
        "--output-dir",
        type=click.Path(path_type=Path),
        default=DEFAULT_OUTPUT_DIR,
        show_default=True,
        help="Production output directory (episode JSON + audio).",
    )(fn)


def _datasets_dir_option(fn):
    return click.option(
        "--datasets-dir",
        type=click.Path(path_type=Path),
        default=DEFAULT_DATASETS_DIR,
        show_default=True,
        help="Directory holding named eval datasets.",
    )(fn)


def _results_dir_option(fn):
    return click.option(
        "--results-dir",
        type=click.Path(path_type=Path),
        default=DEFAULT_RESULTS_DIR,
        show_default=True,
        help="Directory to write per-run results JSON into.",
    )(fn)


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------

@click.group(name="eval")
def eval_group() -> None:
    """Ad-detection eval harness — label, annotate, validate, score, run.

    A development tool, intended to be run from the repository root (the
    ``eval/`` package lives there, not in the installed package).
    """


# ---------------------------------------------------------------------------
# eval label
# ---------------------------------------------------------------------------

def _build_ad_config_from_yaml(config_path: Path | None):
    """Build an ad_config dict from an optional YAML file, applying defaults.

    Imports DEFAULT_LLM_MODEL lazily so this helper doesn't pull in production
    deps at module import. A top-level ``prompt`` in the YAML is folded into
    ``ad_config["llm"]["prompt"]``.
    """
    from podcast_etl.detectors.transcription import DEFAULT_LLM_MODEL

    ad_config: dict = {
        "whisper": {"model": "base", "language": "en"},
        "llm": {"provider": "anthropic", "model": DEFAULT_LLM_MODEL, "prompt": "default"},
        "min_confidence": 0.5,
    }
    if config_path is None:
        return ad_config

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if "whisper" in raw:
        ad_config["whisper"] = {**ad_config["whisper"], **raw["whisper"]}
    if "llm" in raw:
        ad_config["llm"] = {**ad_config["llm"], **raw["llm"]}
    if "prompt" in raw:
        ad_config["llm"]["prompt"] = raw["prompt"]
    if "min_confidence" in raw:
        ad_config["min_confidence"] = raw["min_confidence"]
    return ad_config


@eval_group.command(name="label")
@click.argument("dataset_name")
@click.option("--podcast", "podcast_slug", default=None, help="Only label this podcast slug.")
@click.option("--episodes", "episodes_regex", default=None, help="Regex filter on episode JSON filenames.")
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None, help="YAML ad-detection config (whisper/llm/prompt/min_confidence).")
@_output_dir_option
@_datasets_dir_option
def label_cmd(
    dataset_name: str,
    podcast_slug: str | None,
    episodes_regex: str | None,
    config_path: Path | None,
    output_dir: Path,
    datasets_dir: Path,
) -> None:
    """Generate predicted Labels for episodes into a named dataset."""
    _ensure_cwd_importable()
    from eval.label import iter_episode_refs, label_dataset

    ad_config = _build_ad_config_from_yaml(config_path)

    if podcast_slug:
        try:
            refs = iter_episode_refs(output_dir, podcast_slug, episodes_regex)
        except FileNotFoundError:
            click.echo(
                f"no episodes found for podcast '{podcast_slug}' under {output_dir}",
                err=True,
            )
            raise SystemExit(1)
    else:
        # Enumerate every podcast dir (subdir with an episodes/ dir) and concat.
        refs = []
        if output_dir.exists():
            for podcast_dir in sorted(output_dir.iterdir()):
                if podcast_dir.is_dir() and (podcast_dir / "episodes").is_dir():
                    refs.extend(
                        iter_episode_refs(output_dir, podcast_dir.name, episodes_regex)
                    )

    if not refs:
        click.echo("No episodes found to label.")
        return

    dataset_root = datasets_dir / dataset_name
    paths = label_dataset(refs, ad_config, output_dir, dataset_root)
    click.echo(f"Wrote {len(paths)} label file(s) to {dataset_root}")


# ---------------------------------------------------------------------------
# eval annotate
# ---------------------------------------------------------------------------

@eval_group.command(name="annotate")
@click.argument("podcast")
@click.argument("episode_stem")
@click.option("--dataset", default="gold", show_default=True, help="Target dataset to write the annotation into.")
@click.option("--blank", "blank", is_flag=True, help="Create an empty annotation skeleton (annotator=human).")
@click.option("--bootstrap-from", "bootstrap_from", default=None, help="Source dataset name/path to copy an existing Labels from.")
@_output_dir_option
@_datasets_dir_option
def annotate_cmd(
    podcast: str,
    episode_stem: str,
    dataset: str,
    blank: bool,
    bootstrap_from: str | None,
    output_dir: Path,
    datasets_dir: Path,
) -> None:
    """Create a gold-standard annotation, blank or bootstrapped from another dataset."""
    _ensure_cwd_importable()
    from podcast_etl.labels import EpisodeRef

    from eval.annotate import bootstrap_from_dataset, create_blank
    from eval.datasets import label_file_path, resolve_dataset_root

    if blank and bootstrap_from:
        raise click.UsageError("--blank and --bootstrap-from are mutually exclusive.")

    episode_json = episode_stem if episode_stem.endswith(".json") else f"{episode_stem}.json"
    stem = episode_json.removesuffix(".json")
    ref = EpisodeRef(podcast_slug=podcast, episode_json=episode_json)

    bootstrapped = False
    if blank:
        from eval.resolve import resolve_episode

        resolved = resolve_episode(ref, output_dir)
        duration = 0.0
        try:
            from mutagen.mp3 import MP3

            duration = MP3(resolved.audio_path).info.length
        except Exception as exc:  # noqa: BLE001 — duration is best-effort metadata
            logger.warning("Could not read audio duration for %s: %s", resolved.audio_path, exc)
        labels = create_blank(ref, duration)
    else:
        # Default (neither flag) bootstraps from production output.
        src = bootstrap_from if bootstrap_from is not None else "output"
        source_root = resolve_dataset_root(src, output_dir, datasets_dir)
        labels = bootstrap_from_dataset(ref, source_root)
        bootstrapped = True

    path = label_file_path(datasets_dir / dataset, podcast, stem)
    labels.save(path)
    click.echo(f"Wrote annotation to {path}")
    if bootstrapped:
        click.echo(
            "Reminder: after correcting segments, set provenance.annotator to "
            '"human" so the eval scorer treats it as gold.'
        )


# ---------------------------------------------------------------------------
# eval validate
# ---------------------------------------------------------------------------

@eval_group.command(name="validate")
@click.argument("dataset_name")
@_output_dir_option
@_datasets_dir_option
def validate_cmd(dataset_name: str, output_dir: Path, datasets_dir: Path) -> None:
    """Check every Labels file in a dataset for consistency; exit non-zero on errors."""
    _ensure_cwd_importable()
    from eval.datasets import resolve_dataset_root
    from eval.validate import validate_dataset

    root = resolve_dataset_root(dataset_name, output_dir, datasets_dir)
    if not root.exists():
        click.echo(f"dataset not found: {root}", err=True)
        raise SystemExit(1)
    results = validate_dataset(root)

    any_errors = False
    for name, errors in results.items():
        if errors:
            any_errors = True
            click.echo(f"{name}:")
            for err in errors:
                click.echo(f"  - {err}")
        else:
            click.echo(f"{name}: OK")

    if any_errors:
        raise SystemExit(1)
    click.echo(f"{len(results)} file(s), all valid")


# ---------------------------------------------------------------------------
# eval score
# ---------------------------------------------------------------------------

@eval_group.command(name="score")
@click.option("--predictions", "predictions", multiple=True, required=True, help="Predictions dataset name/path (repeatable).")
@click.option("--gold", "gold", required=True, help="Gold dataset name/path.")
@click.option("--allowed-annotators", "allowed_annotators", multiple=True, default=("human",), show_default=True, help="Score only gold whose provenance.annotator is in this set (repeatable).")
@_output_dir_option
@_datasets_dir_option
@_results_dir_option
def score_cmd(
    predictions: tuple[str, ...],
    gold: str,
    allowed_annotators: tuple[str, ...],
    output_dir: Path,
    datasets_dir: Path,
    results_dir: Path,
) -> None:
    """Score one or more predictions datasets against a gold dataset."""
    _ensure_cwd_importable()
    from eval.datasets import load_dataset, resolve_dataset_root
    from eval.score import aggregate_scores, format_report, score_episode

    gold_root = resolve_dataset_root(gold, output_dir, datasets_dir)
    try:
        gold_dataset = load_dataset(gold_root)
    except FileNotFoundError:
        click.echo(f"gold dataset not found: {gold_root}", err=True)
        raise SystemExit(1)

    allowed = set(allowed_annotators)
    if allowed:
        before = len(gold_dataset)
        gold_dataset = {
            k: v for k, v in gold_dataset.items() if v.provenance.annotator in allowed
        }
        skipped = before - len(gold_dataset)
        if skipped:
            click.echo(
                f"Skipped {skipped} gold annotation(s) not in allowed annotators {sorted(allowed)}",
                err=True,
            )

    results: dict = {}
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    results_dir.mkdir(parents=True, exist_ok=True)

    for pred_name in predictions:
        pred_root = resolve_dataset_root(pred_name, output_dir, datasets_dir)
        try:
            pred_dataset = load_dataset(pred_root)
        except FileNotFoundError:
            click.echo(f"predictions dataset not found: {pred_root}", err=True)
            raise SystemExit(1)
        scored_keys = [k for k in gold_dataset if k in pred_dataset]
        unscored_keys = [k for k in gold_dataset if k not in pred_dataset]
        if unscored_keys:
            click.echo(
                f"Scored {len(scored_keys)}/{len(gold_dataset)} gold episodes for"
                f" {pred_name!r}; {len(unscored_keys)} had no prediction and were"
                f" excluded: {sorted(unscored_keys)}",
                err=True,
            )
        scores = [
            score_episode(pred_dataset[k].segments, gold_dataset[k].segments)
            for k in scored_keys
        ]
        agg = aggregate_scores(scores)
        results[pred_name] = agg

        safe_pred = pred_name.replace("/", "-")
        safe_gold = gold.replace("/", "-")
        out_path = results_dir / f"{timestamp}-{safe_pred}-vs-{safe_gold}.json"
        out_path.write_text(
            json.dumps(
                {
                    "config": pred_name,
                    "gold": gold,
                    "timestamp": timestamp,
                    "gold_episode_count": len(gold_dataset),
                    "scored_episode_count": len(scored_keys),
                    "aggregate": dataclasses.asdict(agg),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    click.echo(format_report(results))


# ---------------------------------------------------------------------------
# eval run
# ---------------------------------------------------------------------------

@eval_group.command(name="run")
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=DEFAULT_EVAL_CONFIG, show_default=True, help="Eval matrix config YAML.")
@_output_dir_option
@_datasets_dir_option
@_results_dir_option
def run_cmd(config_path: Path, output_dir: Path, datasets_dir: Path, results_dir: Path) -> None:
    """Run the full eval matrix from a config file and print a comparison report."""
    _ensure_cwd_importable()
    from eval.run import run_eval
    from eval.score import format_report

    results = run_eval(config_path, output_dir, datasets_dir, results_dir)
    click.echo(format_report(results))
