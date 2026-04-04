from __future__ import annotations

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

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
        status = status_by_url.get(url, {})
        feeds.append({
            "name": feed.get("name") or url,
            "url": url,
            "enabled": feed.get("enabled", False),
            "episode_count": status.get("episode_count", 0),
        })

    return templates.TemplateResponse(
        request,
        "feeds/list.html",
        {"feeds": feeds},
    )


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
