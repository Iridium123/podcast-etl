from __future__ import annotations

import difflib

import yaml
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from podcast_etl.web import templates

router = APIRouter()


@router.get("/defaults", response_class=HTMLResponse)
async def defaults_edit_form(request: Request):
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import load_config

    config = load_config(request.app.state.config_path)
    defaults = config.get("defaults", {})
    poll_interval = config.get("poll_interval", 3600)
    all_steps = list(STEP_REGISTRY.keys())

    full_defaults_yaml = yaml.dump(defaults, default_flow_style=False, sort_keys=False) if defaults else ""

    return templates.TemplateResponse(
        request,
        "defaults/edit.html",
        {
            "defaults": defaults,
            "poll_interval": poll_interval,
            "full_defaults_yaml": full_defaults_yaml,
            "all_steps": all_steps,
            "error": None,
        },
    )


def _parse_defaults_form(form_data, all_steps: list[str]) -> tuple[dict, int | None, str | None]:
    """Parse defaults edit form data into (updated_defaults_dict, poll_interval_or_None, error_or_None).

    The full defaults YAML textarea is the base; form fields overlay on top (form fields win).
    Returns (defaults_dict, poll_interval, None) on success, or ({}, None, error_message) on parse failure.
    """
    poll_interval_raw = str(form_data.get("poll_interval", "")).strip()
    poll_interval: int | None = None
    if poll_interval_raw:
        try:
            poll_interval = int(poll_interval_raw)
        except ValueError:
            pass

    output_dir = str(form_data.get("output_dir", "")).strip()
    torrent_data_dir = str(form_data.get("torrent_data_dir", "")).strip()
    pipeline = [step for step in all_steps if form_data.get(f"pipeline_{step}") == "on"]
    title_cleaning = {
        "strip_date": form_data.get("title_strip_date") == "on",
        "reorder_parts": form_data.get("title_reorder_parts") == "on",
        "prepend_episode_number": form_data.get("title_prepend_episode_number") == "on",
        "sanitize": form_data.get("title_sanitize") == "on",
    }
    extra_yaml = str(form_data.get("extra_yaml", ""))

    # Start from the full YAML textarea as base
    base: dict = {}
    if extra_yaml.strip():
        try:
            parsed = yaml.safe_load(extra_yaml)
            if parsed is not None:
                if not isinstance(parsed, dict):
                    raise ValueError("Full defaults YAML must be a mapping")
                base = parsed
        except (yaml.YAMLError, ValueError) as exc:
            return {}, None, f"Invalid YAML: {exc}"

    # Overlay form field values on top (form fields always win)
    if output_dir:
        base["output_dir"] = output_dir
    if torrent_data_dir:
        base["torrent_data_dir"] = torrent_data_dir
    if pipeline:
        base["pipeline"] = pipeline
    if any(title_cleaning.values()):
        base["title_cleaning"] = title_cleaning

    return base, poll_interval, None


@router.post("/defaults", response_class=HTMLResponse)
async def defaults_save(request: Request):
    """Direct save (legacy route — kept for backward compatibility with existing tests)."""
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import (
        load_config,
        save_config,
        validate_config,
    )

    config = load_config(request.app.state.config_path)
    existing_defaults = config.get("defaults", {})
    form_data = await request.form()
    all_steps = list(STEP_REGISTRY.keys())
    extra_yaml_raw = str(form_data.get("extra_yaml", ""))

    updated_defaults, poll_interval, error = _parse_defaults_form(form_data, all_steps)
    if error:
        return templates.TemplateResponse(
            request,
            "defaults/edit.html",
            {
                "defaults": existing_defaults,
                "poll_interval": config.get("poll_interval", 3600),
                "full_defaults_yaml": extra_yaml_raw,
                "all_steps": all_steps,
                "error": error,
            },
            status_code=200,
        )

    # Preserve blacklist from existing defaults if not overridden
    if "blacklist" in existing_defaults and "blacklist" not in updated_defaults:
        updated_defaults["blacklist"] = existing_defaults["blacklist"]

    config["defaults"] = updated_defaults
    if poll_interval is not None:
        config["poll_interval"] = poll_interval

    try:
        validate_config(config)
    except SystemExit as exc:
        return templates.TemplateResponse(
            request,
            "defaults/edit.html",
            {
                "defaults": existing_defaults,
                "poll_interval": config.get("poll_interval", 3600),
                "full_defaults_yaml": extra_yaml_raw,
                "all_steps": all_steps,
                "error": str(exc),
            },
            status_code=200,
        )

    save_config(config, request.app.state.config_path)
    return RedirectResponse(url="/defaults", status_code=303)


@router.post("/defaults/preview", response_class=HTMLResponse)
async def defaults_save_preview(request: Request):
    """Show diff preview before saving. If valid, display confirm page."""
    from podcast_etl.pipeline import STEP_REGISTRY
    from podcast_etl.service import (
        load_config,
        validate_config,
    )

    config = load_config(request.app.state.config_path)
    existing_defaults = config.get("defaults", {})
    form_data = await request.form()
    all_steps = list(STEP_REGISTRY.keys())
    extra_yaml_raw = str(form_data.get("extra_yaml", ""))

    updated_defaults, poll_interval, error = _parse_defaults_form(form_data, all_steps)
    if error:
        return templates.TemplateResponse(
            request,
            "defaults/edit.html",
            {
                "defaults": existing_defaults,
                "poll_interval": config.get("poll_interval", 3600),
                "full_defaults_yaml": extra_yaml_raw,
                "all_steps": all_steps,
                "error": error,
            },
            status_code=200,
        )

    # Preserve blacklist from existing defaults if not overridden
    if "blacklist" in existing_defaults and "blacklist" not in updated_defaults:
        updated_defaults["blacklist"] = existing_defaults["blacklist"]

    # Build candidate config for validation
    candidate_config = dict(config)
    candidate_config["defaults"] = updated_defaults
    if poll_interval is not None:
        candidate_config["poll_interval"] = poll_interval

    try:
        validate_config(candidate_config)
    except SystemExit as exc:
        return templates.TemplateResponse(
            request,
            "defaults/edit.html",
            {
                "defaults": existing_defaults,
                "poll_interval": config.get("poll_interval", 3600),
                "full_defaults_yaml": extra_yaml_raw,
                "all_steps": all_steps,
                "error": str(exc),
            },
            status_code=200,
        )

    old_yaml = yaml.dump(existing_defaults, default_flow_style=False, sort_keys=False)
    new_yaml = yaml.dump(updated_defaults, default_flow_style=False, sort_keys=False)

    diff_lines = list(difflib.unified_diff(
        old_yaml.splitlines(),
        new_yaml.splitlines(),
        fromfile="current",
        tofile="updated",
        lineterm="",
    ))

    # Encode poll_interval into the new config YAML payload so confirm can use it
    new_config_payload = {
        "defaults": updated_defaults,
        "poll_interval": poll_interval if poll_interval is not None else config.get("poll_interval", 3600),
    }
    new_config_yaml = yaml.dump(new_config_payload, default_flow_style=False, sort_keys=False)

    return templates.TemplateResponse(
        request,
        "defaults/confirm.html",
        {
            "diff_lines": diff_lines,
            "new_config_yaml": new_config_yaml,
        },
    )


@router.post("/defaults/confirm", response_class=HTMLResponse)
async def defaults_save_confirm(
    request: Request,
    new_config_yaml: str = Form(""),
):
    """Deserialize the confirmed new defaults YAML and write it to disk."""
    from podcast_etl.service import (
        load_config,
        save_config,
        validate_config,
    )

    if not new_config_yaml.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="No config data submitted.")

    try:
        payload = yaml.safe_load(new_config_yaml)
        if not isinstance(payload, dict):
            raise ValueError("Config must be a YAML mapping")
    except (yaml.YAMLError, ValueError) as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid config data: {exc}")

    updated_defaults = payload.get("defaults", {})
    poll_interval = payload.get("poll_interval")

    config = load_config(request.app.state.config_path)
    config["defaults"] = updated_defaults
    if poll_interval is not None:
        config["poll_interval"] = poll_interval

    try:
        validate_config(config)
    except SystemExit as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Config validation failed: {exc}")

    save_config(config, request.app.state.config_path)
    return RedirectResponse(url="/defaults", status_code=303)
