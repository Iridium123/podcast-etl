from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from podcast_etl.web import templates

router = APIRouter()


@router.get("/")
async def dashboard(request: Request):
    from podcast_etl.service import load_config, get_output_dir, get_feed_status

    config = load_config(request.app.state.config_path)
    output_dir = get_output_dir(config)
    feed_status = get_feed_status(output_dir, config)

    active_feeds = sum(1 for f in config.get("feeds", []) if f.get("enabled", False))
    total_completed = sum(f["completed_count"] for f in feed_status)
    total_pending = sum(f["pending_count"] for f in feed_status)

    poll_control = getattr(request.app.state, "poll_control", None)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "config": config,
            "active_feeds": active_feeds,
            "total_completed": total_completed,
            "total_pending": total_pending,
            "poll_control": poll_control,
        },
    )


@router.post("/poll/pause")
async def poll_pause(request: Request):
    poll_control = getattr(request.app.state, "poll_control", None)
    if poll_control is not None:
        poll_control.paused = True
    return templates.TemplateResponse(
        request,
        "fragments/poll_status.html",
        {"poll_control": poll_control},
    )


@router.post("/poll/resume")
async def poll_resume(request: Request):
    poll_control = getattr(request.app.state, "poll_control", None)
    if poll_control is not None:
        poll_control.paused = False
    return templates.TemplateResponse(
        request,
        "fragments/poll_status.html",
        {"poll_control": poll_control},
    )


@router.post("/poll/run-now")
async def poll_run_now(request: Request):
    poll_control = getattr(request.app.state, "poll_control", None)
    if poll_control is not None:
        poll_control.run_now.set()
    return templates.TemplateResponse(
        request,
        "fragments/poll_status.html",
        {"poll_control": poll_control},
    )


@router.get("/log-tail", response_class=HTMLResponse)
async def log_tail(request: Request):
    from podcast_etl.service import load_config, get_output_dir

    config = load_config(request.app.state.config_path)
    output_dir = get_output_dir(config)
    log_file = output_dir / "podcast-etl.log"

    lines: list[str] = []
    if log_file.exists():
        text = log_file.read_text(errors="replace")
        lines = text.splitlines()[-100:]

    return templates.TemplateResponse(
        request,
        "fragments/log_tail.html",
        {"lines": lines},
    )
