"""Shared form-handling helpers for the feeds and defaults edit routes.

Both the feeds edit form and the defaults edit form use the same pattern:
a "full YAML" textarea as the base, with structured form fields overlaid
on top. They also share a token-based confirm flow for save previews.
These helpers keep that logic in one place.
"""
from __future__ import annotations

import secrets
from typing import Any

import yaml
from fastapi import Request


def parse_yaml_base(extra_yaml: str, what: str) -> tuple[dict, str | None]:
    """Parse the full-YAML textarea into a base dict.

    Returns (base_dict, None) on success or ({}, error_message) on failure.
    Empty input returns ({}, None) — an empty textarea is valid.
    """
    if not extra_yaml.strip():
        return {}, None
    try:
        parsed = yaml.safe_load(extra_yaml)
    except yaml.YAMLError as exc:
        return {}, f"Invalid YAML: {exc}"
    if parsed is None:
        return {}, None
    if not isinstance(parsed, dict):
        return {}, f"{what} must be a YAML mapping"
    return parsed, None


def apply_text_field(base: dict, key: str, value: str) -> None:
    """Set ``base[key]`` to ``value`` if non-empty, else delete the key."""
    stripped = value.strip()
    if stripped:
        base[key] = stripped
    elif key in base:
        del base[key]


def apply_int_field(base: dict, key: str, value: str) -> None:
    """Set ``base[key]`` to int(value) if parseable, else leave as-is.

    Empty input deletes the key. Unparseable input keeps the original
    string (callers may want to preserve user typos rather than silently
    drop them).
    """
    stripped = value.strip()
    if not stripped:
        if key in base:
            del base[key]
        return
    try:
        base[key] = int(stripped)
    except ValueError:
        base[key] = stripped


def apply_bool_field(base: dict, key: str, value: str) -> None:
    """Set ``base[key]`` to True if value == 'on', else False.

    Always writes the key (unlike text/int, which delete when cleared),
    since a checkbox always has a definite true/false state.
    """
    base[key] = value == "on"


def parse_form_section(
    form_data: Any,
    all_steps: list[str],
    what: str,
    *,
    text_fields: list[str] = (),
    int_fields: list[str] = (),
    bool_fields: list[str] = (),
) -> tuple[dict, str | None]:
    """Parse a config section from form data.

    Both the feed edit form and the defaults edit form follow the same
    pattern: a full-YAML textarea provides the base, structured form
    fields overlay on top, and pipeline/title_cleaning are handled
    uniformly. The only thing that differs is which fields each form has.

    Returns ``(merged_dict, None)`` on success or ``({}, error)`` on
    invalid YAML input.
    """
    extra_yaml = str(form_data.get("extra_yaml", ""))
    base, error = parse_yaml_base(extra_yaml, what)
    if error:
        return {}, error

    for field in text_fields:
        apply_text_field(base, field, str(form_data.get(field, "")))
    for field in int_fields:
        apply_int_field(base, field, str(form_data.get(field, "")))
    for field in bool_fields:
        apply_bool_field(base, field, str(form_data.get(field, "")))

    apply_pipeline(base, parse_pipeline_checkboxes(form_data, all_steps))
    apply_title_cleaning(base, parse_title_cleaning_checkboxes(form_data))

    return base, None


def apply_pipeline(base: dict, pipeline: list[str]) -> None:
    """Set or clear the pipeline key. Empty list removes the override."""
    if pipeline:
        base["pipeline"] = pipeline
    elif "pipeline" in base:
        del base["pipeline"]


def apply_title_cleaning(base: dict, title_cleaning: dict[str, bool]) -> None:
    """Set title_cleaning only if any flag is true, else remove the key."""
    if any(title_cleaning.values()):
        base["title_cleaning"] = title_cleaning
    elif "title_cleaning" in base:
        del base["title_cleaning"]


def parse_pipeline_checkboxes(form_data: Any, all_steps: list[str]) -> list[str]:
    """Collect pipeline step names from checkbox form data."""
    return [step for step in all_steps if form_data.get(f"pipeline_{step}") == "on"]


def parse_title_cleaning_checkboxes(form_data: Any) -> dict[str, bool]:
    """Collect title_cleaning flags from checkbox form data."""
    return {
        "strip_date": form_data.get("title_strip_date") == "on",
        "reorder_parts": form_data.get("title_reorder_parts") == "on",
        "prepend_episode_number": form_data.get("title_prepend_episode_number") == "on",
        "sanitize": form_data.get("title_sanitize") == "on",
    }


def store_pending_change(request: Request, payload: str) -> str:
    """Store a pending change payload keyed by a random token.

    Returns the token. Payload lives in ``request.app.state.pending_changes``
    (a simple in-memory dict) until consumed by ``pop_pending_change``.
    """
    token = secrets.token_urlsafe(16)
    if not hasattr(request.app.state, "pending_changes"):
        request.app.state.pending_changes = {}
    request.app.state.pending_changes[token] = payload
    return token


def pop_pending_change(request: Request, token: str) -> str | None:
    """Retrieve and consume a pending change by token."""
    pending = getattr(request.app.state, "pending_changes", {})
    return pending.pop(token, None)
