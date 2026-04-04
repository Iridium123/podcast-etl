from __future__ import annotations

import asyncio

import yaml
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from podcast_etl.web import templates

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


@router.post("/add", response_class=HTMLResponse)
async def feed_add(
    request: Request,
    url: str = Form(""),
    feed_name: str = Form("", alias="name"),
):
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import load_config, save_config

    if not url.strip():
        all_steps = list(STEP_REGISTRY.keys())
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": {"name": feed_name},
                "extra_yaml": "",
                "all_steps": all_steps,
                "error": "URL is required.",
            },
            status_code=200,
        )

    if not feed_name.strip():
        all_steps = list(STEP_REGISTRY.keys())
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": {"url": url},
                "extra_yaml": "",
                "all_steps": all_steps,
                "error": "Name is required.",
            },
            status_code=200,
        )

    config = load_config(request.app.state.config_path)
    existing_urls = [f.get("url", "") for f in config.get("feeds", [])]
    if url.strip() in existing_urls:
        all_steps = list(STEP_REGISTRY.keys())
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": {"url": url, "name": feed_name},
                "extra_yaml": "",
                "all_steps": all_steps,
                "error": f"Feed with URL {url!r} already exists.",
            },
            status_code=200,
        )

    entry: dict = {"url": url.strip(), "name": feed_name.strip()}

    config.setdefault("feeds", []).append(entry)
    save_config(config, request.app.state.config_path)

    return RedirectResponse(url=f"/feeds/{entry['name']}", status_code=303)


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
    if output_dir.exists():
        from podcast_etl.models import Podcast as PodcastModel

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
            for ep in podcast.episodes:
                ep_statuses = {}
                for step_name in step_names:
                    if ep.status.get(step_name) is not None:
                        ep_statuses[step_name] = "done"
                    else:
                        ep_statuses[step_name] = "pending"
                episodes.append({"title": ep.title, "statuses": ep_statuses})
            break

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

    _, extra = split_config_fields(feed, KNOWN_FEED_FIELDS)
    extra_yaml = yaml.dump(extra, default_flow_style=False, sort_keys=False) if extra else ""

    all_steps = list(STEP_REGISTRY.keys())

    return templates.TemplateResponse(
        request,
        "feeds/form.html",
        {
            "feed": feed,
            "extra_yaml": extra_yaml,
            "all_steps": all_steps,
            "error": None,
        },
    )


@router.post("/{name}", response_class=HTMLResponse)
async def feed_save(
    request: Request,
    name: str,
    feed_name: str = Form("", alias="name"),
    url: str = Form(""),
    enabled: str = Form(""),
    last: str = Form(""),
    episode_filter: str = Form(""),
    category_id: str = Form(""),
    type_id: str = Form(""),
    extra_yaml: str = Form(""),
):
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import (
        find_feed_config,
        load_config,
        merge_config_fields,
        save_config,
        validate_config,
    )

    config = load_config(request.app.state.config_path)
    existing_feed = find_feed_config(config, name)

    if existing_feed is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Feed {name!r} not found.")

    # Parse form data
    form_data = await request.form()
    all_steps = list(STEP_REGISTRY.keys())

    pipeline = [step for step in all_steps if form_data.get(f"pipeline_{step}") == "on"]
    title_cleaning = {
        "strip_date": form_data.get("title_strip_date") == "on",
        "reorder_parts": form_data.get("title_reorder_parts") == "on",
        "prepend_episode_number": form_data.get("title_prepend_episode_number") == "on",
        "sanitize": form_data.get("title_sanitize") == "on",
    }

    known: dict = {"url": url}
    if feed_name:
        known["name"] = feed_name
    known["enabled"] = enabled == "on"
    if last.strip():
        try:
            known["last"] = int(last.strip())
        except ValueError:
            pass
    if episode_filter.strip():
        known["episode_filter"] = episode_filter.strip()
    if category_id.strip():
        try:
            known["category_id"] = int(category_id.strip())
        except ValueError:
            known["category_id"] = category_id.strip()
    if type_id.strip():
        try:
            known["type_id"] = int(type_id.strip())
        except ValueError:
            known["type_id"] = type_id.strip()
    if pipeline:
        known["pipeline"] = pipeline
    if any(title_cleaning.values()):
        known["title_cleaning"] = title_cleaning

    # Parse extra YAML
    extra: dict = {}
    if extra_yaml.strip():
        try:
            parsed = yaml.safe_load(extra_yaml)
            if parsed is not None:
                if not isinstance(parsed, dict):
                    raise ValueError("Extra YAML must be a mapping")
                extra = parsed
        except (yaml.YAMLError, ValueError) as exc:
            return templates.TemplateResponse(
                request,
                "feeds/form.html",
                {
                    "feed": existing_feed,
                    "extra_yaml": extra_yaml,
                    "all_steps": all_steps,
                    "error": f"Invalid YAML: {exc}",
                },
                status_code=200,
            )

    updated_feed = merge_config_fields(known, extra)

    # Preserve cover_image / banner_image from existing feed if not in form
    for preserve_key in ("cover_image", "banner_image"):
        if preserve_key in existing_feed and preserve_key not in updated_feed:
            updated_feed[preserve_key] = existing_feed[preserve_key]

    # Replace feed in config
    new_feeds = []
    replaced = False
    for f in config.get("feeds", []):
        if f.get("name") == name or f.get("url") == name:
            new_feeds.append(updated_feed)
            replaced = True
        else:
            new_feeds.append(f)
    if not replaced:
        new_feeds.append(updated_feed)
    config["feeds"] = new_feeds

    # Validate
    try:
        validate_config(config)
    except SystemExit as exc:
        return templates.TemplateResponse(
            request,
            "feeds/form.html",
            {
                "feed": existing_feed,
                "extra_yaml": extra_yaml,
                "all_steps": all_steps,
                "error": str(exc),
            },
            status_code=200,
        )

    save_config(config, request.app.state.config_path)

    redirect_name = updated_feed.get("name") or updated_feed.get("url", name)
    return RedirectResponse(url=f"/feeds/{redirect_name}", status_code=303)


@router.post("/{name}/run", response_class=HTMLResponse)
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

    asyncio.create_task(_run())
    return HTMLResponse('<span class="text-blue-400">Running...</span>')
