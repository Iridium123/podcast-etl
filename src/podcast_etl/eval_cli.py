"""CLI subcommands for the ad-detection eval harness.

The eval package lives at the project root (sibling of src/podcast_etl/) and
is not part of the installed wheel — these commands work in dev / from-source
invocations. Imports happen lazily inside each command so the cost is only
paid when the user actually runs an eval command.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _ensure_eval_importable() -> None:
    """Add project root to sys.path so `from eval.X import ...` works."""
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))


def _resolve_blank_duration(episode, podcast_dir: Path, override: float | None) -> float:
    """Pick the audio duration for a blank annotation.

    Order: explicit --duration > read from the on-disk audio file via mutagen >
    fail with a clear message asking for --duration.
    """
    if override is not None:
        return override
    download = episode.status.get("download")
    if download and download.result.get("path"):
        audio_path = podcast_dir / download.result["path"]
        if audio_path.exists():
            from mutagen.mp3 import MP3
            audio = MP3(audio_path)
            if audio.info is not None:
                return float(audio.info.length)
    raise click.ClickException(
        f"Cannot determine audio duration for {episode.slug} — pass --duration <seconds> "
        "(no downloaded audio file available to probe)",
    )


@click.group(name="eval")
def eval_group() -> None:
    """Ad-detection evaluation harness (annotation + scoring tooling)."""


@eval_group.command(name="run")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=Path("eval/eval_config.yaml"),
    show_default=True,
    help="Path to the eval config YAML",
)
def run_cmd(config_path: Path) -> None:
    """Run the eval matrix and print a comparison report."""
    _ensure_eval_importable()
    from eval.run import load_run_config, run_eval
    from eval.score import format_report

    if not config_path.exists():
        raise click.ClickException(f"Config not found: {config_path}")

    eval_dir = config_path.parent
    run_config = load_run_config(config_path)
    results = run_eval(
        configs=run_config.configs,
        annotations_dir=eval_dir / "annotations",
        output_dir=Path(run_config.output_dir),
        prompts_dir=eval_dir / "prompts",
        results_dir=eval_dir / "results",
        allowed_annotators=run_config.allowed_annotators,
    )
    click.echo(format_report(results))


@eval_group.command(name="annotate")
@click.argument("podcast_slug")
@click.argument("episode_json")
@click.option("--blank", is_flag=True, help="Create an empty annotation instead of bootstrapping from detect_ads")
@click.option("--annotator", default=None, help="Override the annotator tag (defaults to recorded llm.model)")
@click.option(
    "--duration",
    type=float,
    default=None,
    help="Audio duration in seconds (used with --blank; auto-detected from the audio file if omitted)",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("output"),
    show_default=True,
    help="Production output directory holding the podcast/episode",
)
@click.option(
    "--annotations-dir",
    type=click.Path(path_type=Path),
    default=Path("eval/annotations"),
    show_default=True,
    help="Where to write the new annotation",
)
def annotate_cmd(
    podcast_slug: str,
    episode_json: str,
    blank: bool,
    annotator: str | None,
    duration: float | None,
    output_dir: Path,
    annotations_dir: Path,
) -> None:
    """Create an annotation file for an episode (bootstrap or blank)."""
    _ensure_eval_importable()
    from eval.annotate import bootstrap_from_episode, create_blank
    from eval.models import EpisodeRef
    from podcast_etl.models import Episode

    ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
    episode_path = output_dir / podcast_slug / "episodes" / episode_json
    if not episode_path.exists():
        raise click.ClickException(f"Episode file not found: {episode_path}")

    episode = Episode.load(episode_path)

    if blank:
        ann = create_blank(ref, audio_duration=_resolve_blank_duration(episode, output_dir / podcast_slug, duration))
    else:
        ann = bootstrap_from_episode(episode, ref, annotator=annotator)

    annotations_dir.mkdir(parents=True, exist_ok=True)
    out_path = annotations_dir / f"{podcast_slug}-{episode.slug}.json"
    ann.save(out_path)
    click.echo(f"Wrote {out_path} (annotator={ann.annotator or '<empty>'}, segments={len(ann.segments)})")


@eval_group.command(name="validate")
@click.argument(
    "path",
    type=click.Path(path_type=Path, exists=True),
    default=Path("eval/annotations"),
    required=False,
)
def validate_cmd(path: Path) -> None:
    """Validate one annotation file, or all *.json under a directory."""
    _ensure_eval_importable()
    from eval.models import Annotation
    from eval.validate import validate_annotation

    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not files:
        click.echo(f"No annotation files found under {path}")
        return

    errors_found = 0
    for f in files:
        ann = Annotation.load(f)
        errors = validate_annotation(ann)
        if errors:
            errors_found += len(errors)
            click.echo(f"{f}: {len(errors)} error(s)")
            for err in errors:
                click.echo(f"  - {err}")
        else:
            click.echo(f"{f}: OK")

    if errors_found:
        raise click.ClickException(f"{errors_found} validation error(s) across {len(files)} file(s)")


@eval_group.command(name="review")
@click.argument("annotation_path", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("output"),
    show_default=True,
    help="Production output directory holding the podcast/episode",
)
def review_cmd(annotation_path: Path, output_dir: Path) -> None:
    """Display transcript with ad segments highlighted for review."""
    _ensure_eval_importable()
    from eval.review import review_annotation

    click.echo(review_annotation(annotation_path, output_dir))
