from __future__ import annotations

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from podcast_etl.web import templates

router = APIRouter()


@router.get("/defaults", response_class=HTMLResponse)
async def defaults_edit_form(request: Request):
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import (
        KNOWN_DEFAULTS_FIELDS,
        load_config,
        split_config_fields,
    )

    config = load_config(request.app.state.config_path)
    defaults = config.get("defaults", {})
    poll_interval = config.get("poll_interval", 3600)
    all_steps = list(STEP_REGISTRY.keys())

    _, extra = split_config_fields(defaults, KNOWN_DEFAULTS_FIELDS)
    extra_yaml = yaml.dump(extra, default_flow_style=False, sort_keys=False) if extra else ""

    return templates.TemplateResponse(
        request,
        "defaults/edit.html",
        {
            "defaults": defaults,
            "poll_interval": poll_interval,
            "extra_yaml": extra_yaml,
            "all_steps": all_steps,
            "error": None,
        },
    )


@router.post("/defaults", response_class=HTMLResponse)
async def defaults_save(request: Request):
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import (
        KNOWN_DEFAULTS_FIELDS,
        load_config,
        merge_config_fields,
        save_config,
        split_config_fields,
        validate_config,
    )

    config = load_config(request.app.state.config_path)
    existing_defaults = config.get("defaults", {})
    form_data = await request.form()
    all_steps = list(STEP_REGISTRY.keys())

    # Parse poll_interval (top-level key)
    poll_interval_raw = form_data.get("poll_interval", "")
    poll_interval: int | None = None
    if str(poll_interval_raw).strip():
        try:
            poll_interval = int(str(poll_interval_raw).strip())
        except ValueError:
            pass

    # Parse known fields
    known: dict = {}
    output_dir = str(form_data.get("output_dir", "")).strip()
    if output_dir:
        known["output_dir"] = output_dir

    torrent_data_dir = str(form_data.get("torrent_data_dir", "")).strip()
    if torrent_data_dir:
        known["torrent_data_dir"] = torrent_data_dir

    pipeline = [step for step in all_steps if form_data.get(f"pipeline_{step}") == "on"]
    if pipeline:
        known["pipeline"] = pipeline

    title_cleaning = {
        "strip_date": form_data.get("title_strip_date") == "on",
        "reorder_parts": form_data.get("title_reorder_parts") == "on",
        "prepend_episode_number": form_data.get("title_prepend_episode_number") == "on",
        "sanitize": form_data.get("title_sanitize") == "on",
    }
    if any(title_cleaning.values()):
        known["title_cleaning"] = title_cleaning

    # Parse extra YAML
    extra_yaml = str(form_data.get("extra_yaml", ""))
    extra: dict = {}
    if extra_yaml.strip():
        try:
            parsed = yaml.safe_load(extra_yaml)
            if parsed is not None:
                if not isinstance(parsed, dict):
                    raise ValueError("Extra YAML must be a mapping")
                extra = parsed
        except (yaml.YAMLError, ValueError) as exc:
            _, extra_orig = split_config_fields(existing_defaults, KNOWN_DEFAULTS_FIELDS)
            extra_yaml_orig = yaml.dump(extra_orig, default_flow_style=False, sort_keys=False) if extra_orig else ""
            return templates.TemplateResponse(
                request,
                "defaults/edit.html",
                {
                    "defaults": existing_defaults,
                    "poll_interval": config.get("poll_interval", 3600),
                    "extra_yaml": extra_yaml,
                    "all_steps": all_steps,
                    "error": f"Invalid YAML: {exc}",
                },
                status_code=200,
            )

    updated_defaults = merge_config_fields(known, extra)

    # Preserve blacklist from existing defaults if not overridden by extra
    if "blacklist" in existing_defaults and "blacklist" not in updated_defaults:
        updated_defaults["blacklist"] = existing_defaults["blacklist"]

    config["defaults"] = updated_defaults
    if poll_interval is not None:
        config["poll_interval"] = poll_interval

    try:
        validate_config(config)
    except SystemExit as exc:
        _, extra_orig = split_config_fields(existing_defaults, KNOWN_DEFAULTS_FIELDS)
        extra_yaml_orig = yaml.dump(extra_orig, default_flow_style=False, sort_keys=False) if extra_orig else ""
        return templates.TemplateResponse(
            request,
            "defaults/edit.html",
            {
                "defaults": existing_defaults,
                "poll_interval": config.get("poll_interval", 3600),
                "extra_yaml": extra_yaml,
                "all_steps": all_steps,
                "error": str(exc),
            },
            status_code=200,
        )

    save_config(config, request.app.state.config_path)
    return RedirectResponse(url="/defaults", status_code=303)
