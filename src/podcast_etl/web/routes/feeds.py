from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from podcast_etl.web import templates
from podcast_etl.web.form_helpers import (
    check_origin,
    compute_yaml_diff,
    parse_form_section,
    pop_pending_config_payload,
    pop_pending_delete,
    store_pending_change,
    store_pending_delete,
    validate_or_400,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds")


@router.get("", response_class=HTMLResponse)
async def feeds_list(request: Request):
    from podcast_etl.service import get_feed_status, get_output_dir, load_config

    config = load_config(request.app.state.config_path)
    output_dir = get_output_dir(config)
    feed_status = get_feed_status(output_dir, config)

    # Build a lookup from url -> status entry
    status_by_url = {s["url"]: s for s in feed_status}

    feeds = []
    for feed in config.get("feeds", []):
        url = feed.get("url", "")
        name = feed.get("name")
        status = status_by_url.get(url, {})
        feeds.append({
            "name": name or url,
            "has_name": bool(name),
            "url": url,
            "enabled": feed.get("enabled", False),
            "episode_count": status.get("episode_count", 0),
        })

    return templates.TemplateResponse(
        request,
        "feeds/list.html",
        {"feeds": feeds},
    )


@router.get("/add", response_class=HTMLResponse)
async def feed_add_form(request: Request):
    from podcast_etl.pipeline import STEP_REGISTRY

    all_steps = list(STEP_REGISTRY.keys())

    return templates.TemplateResponse(
        request,
        "feeds/form.html",
        {
            "feed": {},
            "extra_yaml": "",
            "all_steps": all_steps,
            "error": None,
        },
    )


@router.post("/add", response_class=HTMLResponse, dependencies=[Depends(check_origin)])
async def feed_add(request: Request):
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import load_config, save_config

    form_data = await request.form()
    all_steps = list(STEP_REGISTRY.keys())

    parsed, error = _parse_feed_form(form_data, all_steps)
    if error:
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": dict(form_data),
                "extra_yaml": str(form_data.get("extra_yaml", "")),
                "all_steps": all_steps,
                "error": error,
            },
            status_code=200,
        )

    url = parsed.get("url", "").strip()
    feed_name = parsed.get("name", "").strip()

    if not url:
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": parsed,
                "extra_yaml": str(form_data.get("extra_yaml", "")),
                "all_steps": all_steps,
                "error": "URL is required.",
            },
            status_code=200,
        )

    if not feed_name:
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": parsed,
                "extra_yaml": str(form_data.get("extra_yaml", "")),
                "all_steps": all_steps,
                "error": "Name is required.",
            },
            status_code=200,
        )

    config = load_config(request.app.state.config_path)
    existing_urls = [f.get("url", "") for f in config.get("feeds", [])]
    if url in existing_urls:
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": parsed,
                "extra_yaml": str(form_data.get("extra_yaml", "")),
                "all_steps": all_steps,
                "error": f"Feed with URL {url!r} already exists.",
            },
            status_code=200,
        )

    config.setdefault("feeds", []).append(parsed)
    save_config(config, request.app.state.config_path)

    return RedirectResponse(url=f"/feeds/{feed_name}", status_code=303)


@router.get("/{name}/delete", response_class=HTMLResponse)
async def feed_delete_confirm(request: Request, name: str):
    from podcast_etl.service import find_feed_config, load_config

    config = load_config(request.app.state.config_path)
    feed = find_feed_config(config, name)

    if feed is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Feed {name!r} not found.")

    token = store_pending_delete(request, name)

    return templates.TemplateResponse(
        request,
        "feeds/delete_confirm.html",
        {"feed": feed, "name": name, "token": token},
    )


@router.post("/{name}/delete", response_class=HTMLResponse, dependencies=[Depends(check_origin)])
async def feed_delete(request: Request, name: str):
    from podcast_etl.service import delete_feed, find_feed_config, load_config

    form_data = await request.form()
    token = str(form_data.get("token", ""))
    expected_name = pop_pending_delete(request, token)
    if not expected_name or expected_name != name:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid or expired delete token.")

    config = load_config(request.app.state.config_path)
    feed = find_feed_config(config, name)

    if feed is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Feed {name!r} not found.")

    delete_feed(config, request.app.state.config_path, name)

    return RedirectResponse(url="/feeds", status_code=303)


@router.get("/{name}", response_class=HTMLResponse)
async def feed_detail(request: Request, name: str):
    from podcast_etl.service import (
        KNOWN_FEED_FIELDS,
        find_feed_config,
        get_output_dir,
        get_resolved_config_with_sources,
        load_config,
        split_config_fields,
    )

    config = load_config(request.app.state.config_path)
    feed = find_feed_config(config, name)

    if feed is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Feed {name!r} not found.")

    defaults = config.get("defaults", {})
    known, extra = split_config_fields(feed, KNOWN_FEED_FIELDS)
    extra_yaml = yaml.dump(extra, default_flow_style=False, sort_keys=False) if extra else ""

    resolved, source_map = get_resolved_config_with_sources(defaults, feed)
    resolved_yaml = yaml.dump(resolved, default_flow_style=False, sort_keys=False)

    # Build episodes grid from disk data if available
    output_dir = get_output_dir(config)
    episodes = []
    step_names: list[str] = resolved.get("pipeline") or ["download"]
    podcast_slug = None
    if output_dir.exists():
        from podcast_etl.models import Podcast as PodcastModel, format_date

        url = feed.get("url", "")
        for podcast_dir in sorted(output_dir.iterdir()):
            if not podcast_dir.is_dir():
                continue
            podcast_json = podcast_dir / "podcast.json"
            if not podcast_json.exists():
                continue
            try:
                podcast = PodcastModel.load(podcast_dir)
            except Exception:
                continue
            if podcast.url != url:
                continue
            podcast_slug = podcast.slug
            for ep in podcast.episodes:
                ep_statuses = {}
                for step_name in step_names:
                    if ep.status.get(step_name) is not None:
                        ep_statuses[step_name] = "done"
                    else:
                        ep_statuses[step_name] = "pending"
                episodes.append({
                    "title": ep.title,
                    "statuses": ep_statuses,
                    "published": format_date(ep.published),
                    "_published_raw": ep.published or "",
                })
            break

    # Disk order is oldest-first (date-prefixed filenames sort ascending).
    # Reverse to show newest first, matching RSS feed order and `last N` behavior.
    episodes.reverse()

    # Build directory paths if we have a podcast slug
    dirs = None
    if podcast_slug is not None:
        torrent_data_dir = resolved.get("torrent_data_dir", "/torrent-data")
        abs_output = output_dir.resolve()
        dirs = {
            "audio": str(abs_output / podcast_slug / "audio") + "/",
            "cleaned": str(abs_output / podcast_slug / "cleaned") + "/",
            "transcripts": str(abs_output / podcast_slug / "transcripts") + "/",
            "torrents": str(abs_output / podcast_slug / "torrents") + "/",
            "staged": str(Path(torrent_data_dir).resolve()).rstrip("/") + "/" + podcast_slug + "/",
        }

    return templates.TemplateResponse(
        request,
        "feeds/detail.html",
        {
            "feed": feed,
            "known": known,
            "extra_yaml": extra_yaml,
            "resolved_yaml": resolved_yaml,
            "source_map": source_map,
            "step_names": step_names,
            "episodes": episodes,
            "dirs": dirs,
        },
    )


@router.get("/{name}/edit", response_class=HTMLResponse)
async def feed_edit_form(request: Request, name: str):
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import (
        KNOWN_FEED_FIELDS,
        find_feed_config,
        load_config,
        split_config_fields,
    )

    config = load_config(request.app.state.config_path)
    feed = find_feed_config(config, name)

    if feed is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Feed {name!r} not found.")

    full_feed_yaml = yaml.dump(feed, default_flow_style=False, sort_keys=False)

    all_steps = list(STEP_REGISTRY.keys())

    return templates.TemplateResponse(
        request,
        "feeds/form.html",
        {
            "feed": feed,
            "full_feed_yaml": full_feed_yaml,
            "all_steps": all_steps,
            "error": None,
        },
    )


def _parse_feed_form(form_data, all_steps: list[str]) -> tuple[dict, str | None]:
    """Parse feed edit form data into (updated_feed_dict, error_str_or_None).

    The full-feed YAML textarea is the base; form fields overlay on top (form fields win).
    Returns (feed_dict, None) on success, or ({}, error_message) on parse failure.
    """
    return parse_form_section(
        form_data,
        all_steps,
        "Full feed YAML",
        text_fields=["url", "name", "title_override", "episode_filter"],
        int_fields=["last", "category_id", "type_id"],
        bool_fields=["enabled"],
    )


@router.post("/{name}/preview", response_class=HTMLResponse, dependencies=[Depends(check_origin)])
async def feed_save_preview(request: Request, name: str):
    """Show diff preview before saving. If valid, display confirm page."""
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import (
        find_feed_config,
        load_config,
        replace_feed,
        validate_config,
    )

    config = load_config(request.app.state.config_path)
    existing_feed = find_feed_config(config, name)

    if existing_feed is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Feed {name!r} not found.")

    form_data = await request.form()
    all_steps = list(STEP_REGISTRY.keys())
    extra_yaml_raw = str(form_data.get("extra_yaml", ""))

    updated_feed, error = _parse_feed_form(form_data, all_steps)
    if error:
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": existing_feed,
                "full_feed_yaml": extra_yaml_raw,
                "all_steps": all_steps,
                "error": error,
            },
            status_code=200,
        )

    # Preserve cover_image / banner_image
    for preserve_key in ("cover_image", "banner_image"):
        if preserve_key in existing_feed and preserve_key not in updated_feed:
            updated_feed[preserve_key] = existing_feed[preserve_key]

    # Build candidate config for validation
    candidate_config = dict(config)
    candidate_config["feeds"] = replace_feed(config.get("feeds", []), name, updated_feed)

    try:
        validate_config(candidate_config)
    except SystemExit as exc:
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": existing_feed,
                "full_feed_yaml": extra_yaml_raw,
                "all_steps": all_steps,
                "error": str(exc),
            },
            status_code=200,
        )

    diff_lines = compute_yaml_diff(existing_feed, updated_feed)
    new_yaml = yaml.dump(updated_feed, default_flow_style=False, sort_keys=False)
    token = store_pending_change(request, new_yaml)

    return templates.TemplateResponse(
        request,
        "feeds/confirm.html",
        {
            "feed_name": name,
            "diff_lines": diff_lines,
            "token": token,
        },
    )


@router.post("/{name}/confirm", response_class=HTMLResponse, dependencies=[Depends(check_origin)])
async def feed_save_confirm(
    request: Request,
    name: str,
    token: str = Form(""),
):
    """Look up pending change by token and write it to disk."""
    from podcast_etl.service import (
        find_feed_config,
        load_config,
        replace_feed,
        save_config,
    )

    updated_feed = pop_pending_config_payload(request, token)

    config = load_config(request.app.state.config_path)
    existing_feed = find_feed_config(config, name)

    if existing_feed is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Feed {name!r} not found.")

    config["feeds"] = replace_feed(config.get("feeds", []), name, updated_feed)

    validate_or_400(config)
    save_config(config, request.app.state.config_path)

    redirect_name = updated_feed.get("name") or updated_feed.get("url", name)
    return RedirectResponse(url=f"/feeds/{redirect_name}", status_code=303)


@router.post("/{name}/run", response_class=HTMLResponse, dependencies=[Depends(check_origin)])
async def feed_run(request: Request, name: str):
    from podcast_etl.service import fetch_feed, load_config, run_pipeline, get_output_dir, find_feed_config

    config = load_config(request.app.state.config_path)
    feed = find_feed_config(config, name)

    if feed is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Feed {name!r} not found.")

    async def _run() -> None:
        from podcast_etl.pipeline import resolve_feed_config

        resolved = resolve_feed_config(config.get("defaults", {}), feed)
        output_dir = get_output_dir(config)
        podcast = await asyncio.to_thread(fetch_feed, feed["url"], output_dir, resolved)
        await asyncio.to_thread(run_pipeline, podcast, output_dir, resolved)

    task = asyncio.create_task(_run())
    task.add_done_callback(
        lambda t: logger.error("feed_run failed: %s", t.exception()) if not t.cancelled() and t.exception() else None
    )
    return HTMLResponse('<span class="text-blue-400">Running...</span>')
