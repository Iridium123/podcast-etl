"""Shared request-handling helpers for the feeds and defaults edit routes.

Both the feeds edit form and the defaults edit form use the same pattern:
a "full YAML" textarea as the base, with structured form fields overlaid
on top. They also share a token-based confirm flow for save previews.
These helpers keep that logic in one place, along with the Origin/Referer
CSRF check applied to state-changing POST endpoints.
"""
from __future__ import annotations

import secrets
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import HTTPException, Request


def check_origin(request: Request) -> None:
    """Reject cross-origin state-changing requests via Origin/Referer check.

    Raises ``HTTPException(400)`` when the request's ``Origin`` (or
    ``Referer`` when ``Origin`` is absent) points to a host different from
    the request's ``Host`` header. Requests with neither header are allowed
    so that non-browser clients (curl, tests, HTTPie) continue to work —
    browsers always send at least one of the two for cross-origin POSTs.

    Used as a FastAPI dependency on state-changing POST endpoints to block
    CSRF / DNS-rebinding attacks on the localhost web UI.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is None:
        return
    origin_netloc = urlparse(origin).netloc
    host = request.headers.get("host", "")
    if origin_netloc and origin_netloc.lower() != host.lower():
        raise HTTPException(status_code=400, detail="Cross-origin request rejected.")


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
    """Set ``base[key]`` to int(value). Delete the key when input is empty.

    Raises ``ValueError`` with the field name when the input is present but
    not parseable as an integer. Callers are expected to catch this and
    surface the error to the user rather than letting a bad value land in
    the YAML config and crash later code far from the form.
    """
    stripped = value.strip()
    if not stripped:
        if key in base:
            del base[key]
        return
    try:
        base[key] = int(stripped)
    except ValueError:
        raise ValueError(f"{key}: must be a number")


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
    text_fields: Sequence[str] = (),
    int_fields: Sequence[str] = (),
    bool_fields: Sequence[str] = (),
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
        try:
            apply_int_field(base, field, str(form_data.get(field, "")))
        except ValueError as exc:
            return {}, str(exc)
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


MAX_PENDING = 32
"""Maximum number of unconfirmed pending changes/deletes kept in memory.

Prevents unbounded memory growth if a user (or attacker via the origin
check bypass) submits many previews without confirming. When the store is
full, the oldest entry is evicted. 32 is more than any legitimate single
user would have open at once.
"""


def _store_bounded(store: dict, value: Any) -> str:
    """Insert ``value`` under a new random token, evicting the oldest entry
    when the store exceeds :data:`MAX_PENDING`. Returns the token.

    Relies on dict insertion-order preservation (guaranteed in Python 3.7+).
    """
    token = secrets.token_urlsafe(16)
    store[token] = value
    while len(store) > MAX_PENDING:
        oldest = next(iter(store))
        del store[oldest]
    return token


def store_pending_change(request: Request, payload: str) -> str:
    """Store a pending change payload keyed by a random token.

    Returns the token. Payload lives in ``request.app.state.pending_changes``
    (a simple in-memory dict) until consumed by :func:`pop_pending_change`.
    The store is bounded to :data:`MAX_PENDING` entries — the oldest is
    evicted when the limit is reached.
    """
    if not hasattr(request.app.state, "pending_changes"):
        request.app.state.pending_changes = {}
    return _store_bounded(request.app.state.pending_changes, payload)


def pop_pending_change(request: Request, token: str) -> str | None:
    """Retrieve and consume a pending change by token."""
    pending = getattr(request.app.state, "pending_changes", {})
    return pending.pop(token, None)


def store_pending_delete(request: Request, feed_name: str) -> str:
    """Store a pending feed-delete keyed by a random token.

    Same bounded semantics as :func:`store_pending_change`. Used by the
    delete confirmation flow so that the POST handler can verify the user
    just visited the GET confirmation page.
    """
    if not hasattr(request.app.state, "pending_deletes"):
        request.app.state.pending_deletes = {}
    return _store_bounded(request.app.state.pending_deletes, feed_name)


def pop_pending_delete(request: Request, token: str) -> str | None:
    """Retrieve and consume a pending feed-delete by token."""
    pending = getattr(request.app.state, "pending_deletes", {})
    return pending.pop(token, None)
