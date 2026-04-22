from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from podcast_etl.web import templates
from podcast_etl.web.form_helpers import check_origin
from podcast_etl.web.log_stream import read_tail_lines, tail_log_events

router = APIRouter()

INITIAL_LOG_LINES = 100


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
    # Discard the offset: the SSE generator picks its own start at connect time.
    # Lines written between this render and the SSE connect (sub-100ms gap) are
    # missed in the live tail; a refresh shows them in the historical batch.
    lines, _ = read_tail_lines(output_dir / "podcast-etl.log", INITIAL_LOG_LINES)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "config": config,
            "active_feeds": active_feeds,
            "total_completed": total_completed,
            "total_pending": total_pending,
            "poll_control": poll_control,
            "lines": lines,
        },
    )


@router.post("/poll/pause", dependencies=[Depends(check_origin)])
async def poll_pause(request: Request):
    poll_control = getattr(request.app.state, "poll_control", None)
    if poll_control is not None:
        poll_control.paused = True
    return templates.TemplateResponse(
        request,
        "fragments/poll_status.html",
        {"poll_control": poll_control},
    )


@router.post("/poll/resume", dependencies=[Depends(check_origin)])
async def poll_resume(request: Request):
    poll_control = getattr(request.app.state, "poll_control", None)
    if poll_control is not None:
        poll_control.paused = False
    return templates.TemplateResponse(
        request,
        "fragments/poll_status.html",
        {"poll_control": poll_control},
    )


@router.post("/poll/run-now", dependencies=[Depends(check_origin)])
async def poll_run_now(request: Request):
    poll_control = getattr(request.app.state, "poll_control", None)
    if poll_control is not None:
        poll_control.run_now.set()
    return templates.TemplateResponse(
        request,
        "fragments/poll_status.html",
        {"poll_control": poll_control},
    )


@router.get("/log-stream")
async def log_stream(request: Request) -> StreamingResponse:
    from podcast_etl.service import load_config, get_output_dir

    config = load_config(request.app.state.config_path)
    log_file = get_output_dir(config) / "podcast-etl.log"

    return StreamingResponse(
        tail_log_events(log_file),
        media_type="text/event-stream",
        # Disable proxy buffering (nginx) so events flush immediately.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
