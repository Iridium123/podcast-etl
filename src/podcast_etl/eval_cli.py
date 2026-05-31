"""CLI subcommands for the ad-detection eval harness.

The eval package lives at the project root (sibling of ``src/podcast_etl/``) and
is not part of the installed wheel — these commands work in dev / from-source
invocations. Imports happen lazily inside each command so the cost (and the
project-root ``sys.path`` insertion) is only paid when an eval command runs.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import click

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _ensure_eval_importable() -> None:
    """Add the project root to sys.path so ``from eval.X import ...`` resolves."""
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))


@click.group(name="eval")
def eval_group() -> None:
    """Ad-detection evaluation harness (labeling, annotation, scoring)."""


@eval_group.command(name="validate")
@click.argument("dataset", type=click.Path(path_type=Path))
def validate_cmd(dataset: Path) -> None:
    """Structurally validate every Labels file under a dataset directory."""
    _ensure_eval_importable()
    from eval.datasets import resolve_dataset_path
    from eval.validate import validate_dataset

    root = resolve_dataset_path(str(dataset))
    if not root.exists():
        raise click.ClickException(f"Dataset not found: {root}")

    results = validate_dataset(root)
    if not results:
        click.echo(f"No label files found under {root}")
        return

    error_count = 0
    for name, errors in results.items():
        if errors:
            error_count += len(errors)
            click.echo(f"{name}: {len(errors)} error(s)")
            for err in errors:
                click.echo(f"  - {err}")
        else:
            click.echo(f"{name}: OK")
    if error_count:
        raise click.ClickException(f"{error_count} validation error(s) across {len(results)} file(s)")


@eval_group.command(name="score")
@click.option("--predictions", "predictions", multiple=True, required=True,
              help="Predictions dataset (name or path). Repeatable for batch comparison.")
@click.option("--gold", "gold", required=True, help="Gold dataset (name or path)")
@click.option("--allowed-annotators", default="human", show_default=True,
              help="Comma-separated gold annotators to score; empty string accepts all")
@click.option("--results-dir", type=click.Path(path_type=Path), default=Path("eval/results"),
              show_default=True, help="Where to write the results JSON")
def score_cmd(predictions: tuple[str, ...], gold: str, allowed_annotators: str, results_dir: Path) -> None:
    """Score one or more prediction datasets against a gold dataset."""
    _ensure_eval_importable()
    from eval.datasets import resolve_dataset_path
    from eval.run import score_datasets
    from eval.score import format_report

    allowed = [a.strip() for a in allowed_annotators.split(",") if a.strip()]
    gold_dir = resolve_dataset_path(gold)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    results_dir.mkdir(parents=True, exist_ok=True)

    report_input = {}
    for pred in predictions:
        pred_dir = resolve_dataset_path(pred)
        agg = score_datasets(pred_dir, gold_dir, allowed_annotators=allowed)
        report_input[pred_dir.name] = agg
        out = results_dir / f"{timestamp}-{pred_dir.name}-vs-{gold_dir.name}.json"
        out.write_text(json.dumps(
            {"predictions": str(pred_dir), "gold": str(gold_dir), "timestamp": timestamp, **asdict(agg)},
            indent=2,
        ) + "\n")

    click.echo(format_report(report_input))


@eval_group.command(name="annotate")
@click.argument("podcast_slug")
@click.argument("episode_json")
@click.option("--dataset", default="gold", show_default=True, help="Target dataset name")
@click.option("--blank", is_flag=True, help="Create an empty skeleton instead of bootstrapping")
@click.option("--bootstrap-from", "bootstrap_from", default=None,
              help="Source dataset to copy from (defaults to --output-dir, i.e. production)")
@click.option("--annotator", default=None, help="Override the annotator tag")
@click.option("--output-dir", type=click.Path(path_type=Path), default=Path("output"), show_default=True)
@click.option("--datasets-dir", type=click.Path(path_type=Path), default=Path("eval/datasets"), show_default=True)
def annotate_cmd(podcast_slug: str, episode_json: str, dataset: str, blank: bool,
                 bootstrap_from: str | None, annotator: str | None,
                 output_dir: Path, datasets_dir: Path) -> None:
    """Create a Labels file for hand correction (bootstrapped or blank)."""
    _ensure_eval_importable()
    from eval.annotate import bootstrap_labels, create_blank
    from eval.datasets import resolve_dataset_path
    from eval.resolve import resolve_episode
    from eval.run import _audio_duration
    from podcast_etl.labels import EpisodeRef, Labels

    ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
    resolved = resolve_episode(ref, output_dir)
    stem = resolved.audio_path.stem
    target = datasets_dir / dataset / podcast_slug / "labels" / f"{stem}.json"

    if blank:
        labels = create_blank(ref, audio_duration=round(_audio_duration(resolved.audio_path), 2))
    else:
        source_root = resolve_dataset_path(bootstrap_from) if bootstrap_from else output_dir
        source_path = source_root / podcast_slug / "labels" / f"{stem}.json"
        if not source_path.exists():
            raise click.ClickException(
                f"No source labels at {source_path} to bootstrap from "
                "(run detect_ads first, or pass --blank)"
            )
        labels = bootstrap_labels(Labels.load(source_path), annotator=annotator)

    labels.save(target)
    click.echo(f"Wrote {target} (annotator={labels.provenance.annotator or '<empty>'}, "
               f"segments={len(labels.segments)})")


@eval_group.command(name="label")
@click.argument("dataset")
@click.option("--podcast", "podcast_slug", required=True, help="Podcast slug")
@click.option("--episode", "episodes", multiple=True, required=True,
              help="Episode JSON filename (repeatable)")
@click.option("--model", default=None, help="LLM model (defaults to provider default)")
@click.option("--prompt", default="default", show_default=True, help="Prompt name in prompts/")
@click.option("--whisper-model", default="base", show_default=True)
@click.option("--language", default="en", show_default=True)
@click.option("--output-dir", type=click.Path(path_type=Path), default=Path("output"), show_default=True)
@click.option("--datasets-dir", type=click.Path(path_type=Path), default=Path("eval/datasets"), show_default=True)
@click.option("--prompts-dir", type=click.Path(path_type=Path), default=Path("prompts"), show_default=True)
def label_cmd(dataset: str, podcast_slug: str, episodes: tuple[str, ...], model: str | None,
              prompt: str, whisper_model: str, language: str,
              output_dir: Path, datasets_dir: Path, prompts_dir: Path) -> None:
    """Run production's classifier over episodes and write a predictions dataset."""
    _ensure_eval_importable()
    from podcast_etl.detectors.transcription import build_llm_client
    from podcast_etl.labels import EpisodeRef
    from eval.run import EvalConfig, label_dataset

    prompt_path = prompts_dir / f"{prompt}.txt"
    if not prompt_path.exists():
        raise click.ClickException(f"Prompt not found: {prompt_path}")

    llm: dict[str, object] = {"provider": "anthropic"}
    if model:
        llm["model"] = model
    config = EvalConfig(
        name=dataset,
        whisper={"model": whisper_model, "language": language},
        llm=llm,
        prompt=prompt,
    )
    refs = [EpisodeRef(podcast_slug=podcast_slug, episode_json=ep) for ep in episodes]
    client = build_llm_client(llm)
    written = label_dataset(
        config, refs, output_dir, datasets_dir / dataset,
        prompt_text=prompt_path.read_text(), client=client,
    )
    click.echo(f"Wrote {len(written)} label file(s) to {datasets_dir / dataset}")


@eval_group.command(name="review")
@click.argument("labels_path", type=click.Path(path_type=Path, exists=True))
@click.option("--output-dir", type=click.Path(path_type=Path), default=Path("output"), show_default=True)
def review_cmd(labels_path: Path, output_dir: Path) -> None:
    """Print a transcript with the Labels file's ad segments highlighted."""
    _ensure_eval_importable()
    from eval.review import review_labels_file

    click.echo(review_labels_file(labels_path, output_dir))


@eval_group.command(name="run")
@click.option("--config", "config_path", type=click.Path(path_type=Path),
              default=Path("eval/eval_config.yaml"), show_default=True)
@click.option("--prompts-dir", type=click.Path(path_type=Path), default=Path("prompts"), show_default=True,
              help="Directory holding <prompt>.txt files")
def run_cmd(config_path: Path, prompts_dir: Path) -> None:
    """Run the eval matrix from a config file and print a comparison report."""
    _ensure_eval_importable()
    from eval.datasets import resolve_dataset_path
    from eval.run import load_run_config, run_eval
    from eval.score import format_report

    if not config_path.exists():
        raise click.ClickException(f"Config not found: {config_path}")
    # Surface a missing prompts dir here rather than deep inside run_eval (where
    # it would only fail when the first prompt file is read).
    if not prompts_dir.exists():
        raise click.ClickException(f"Prompts directory not found: {prompts_dir}")

    eval_dir = config_path.parent
    rc = load_run_config(config_path)
    results = run_eval(
        configs=rc.configs,
        output_dir=Path(rc.output_dir),
        gold_dir=resolve_dataset_path(rc.gold, datasets_dir=eval_dir / "datasets"),
        datasets_dir=eval_dir / "datasets",
        prompts_dir=prompts_dir,
        results_dir=eval_dir / "results",
        timestamp=datetime.now().strftime("%Y-%m-%dT%H-%M-%S"),
        allowed_annotators=rc.allowed_annotators,
    )
    click.echo(format_report(results))
