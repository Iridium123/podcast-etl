"""Shared request-handling helpers for the feeds and defaults edit routes.

Both the feeds edit form and the defaults edit form use the same pattern:
a "full YAML" textarea as the base, with structured form fields overlaid
on top. They also share a token-based confirm flow for save previews.
These helpers keep that logic in one place, along with the Origin/Referer
CSRF check applied to state-changing POST endpoints.
"""
from __future__ import annotations

import difflib
import secrets
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import HTTPException, Request

from podcast_etl.service import load_config, validate_config


def check_origin(request: Request) -> None:
    """Reject cross-origin state-changing requests via Origin/Referer check.

    Used as a FastAPI dependency on state-changing POST endpoints to block
    CSRF from malicious pages the user might visit in another browser tab.
    A request is accepted if any of the following hold:

    1. No ``Origin`` or ``Referer`` header is present — non-browser
       clients (curl, tests, HTTPie) never send these.
    2. The ``Origin`` scheme+host matches the request's ``Host`` header —
       the default same-origin case for direct / LAN / Tailscale
       deployments where the Host header is preserved end-to-end.
    3. The ``Origin`` matches an entry in ``web.trusted_origins`` in the
       config — for deployments behind a reverse proxy that may rewrite
       the Host header (e.g. Cloudflare Tunnel with default
       ``httpHostHeader``). Matching is case-insensitive and tolerant of
       a trailing slash in the config value.

    Otherwise raises ``HTTPException(400)``.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is None:
        return

    parsed = urlparse(origin)
    if not parsed.netloc:
        return  # malformed Origin; no host to compare against

    # Same-origin: Origin scheme+host matches the request's Host header.
    host = request.headers.get("host", "")
    if host and parsed.netloc.lower() == host.lower():
        return

    # Explicit whitelist for reverse-proxy / tunnel deployments where
    # the Host header reaching the app does not match the public URL.
    origin_normalized = f"{parsed.scheme}://{parsed.netloc}".lower()
    config = load_config(request.app.state.config_path)
    trusted = config.get("web", {}).get("trusted_origins", [])
    for entry in trusted:
        if origin_normalized == str(entry).strip().rstrip("/").lower():
            return

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
    At most one eviction per call since we only ever insert one entry.
    """
    token = secrets.token_urlsafe(16)
    store[token] = value
    if len(store) > MAX_PENDING:
        del store[next(iter(store))]
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


def compute_yaml_diff(old: dict, new: dict) -> list[str]:
    """Return unified diff lines between two YAML-serialized dicts.

    Used by the preview handlers to show a before/after diff of config
    changes on the confirm page. Both inputs are dumped with the same
    options so only real content changes appear in the diff.
    """
    old_yaml = yaml.dump(old, default_flow_style=False, sort_keys=False)
    new_yaml = yaml.dump(new, default_flow_style=False, sort_keys=False)
    return list(difflib.unified_diff(
        old_yaml.splitlines(),
        new_yaml.splitlines(),
        fromfile="current",
        tofile="updated",
        lineterm="",
    ))


def pop_pending_config_payload(request: Request, token: str) -> dict:
    """Pop a pending-change token and parse its payload as a YAML mapping.

    Raises ``HTTPException(400)`` when the token is missing or expired,
    when the payload is not valid YAML, or when the parsed payload is not
    a mapping. Used by the confirm handlers to consume a previously-stored
    preview payload before writing it to disk.
    """
    new_config_yaml = pop_pending_change(request, token)
    if not new_config_yaml:
        raise HTTPException(status_code=400, detail="Invalid or expired change token.")
    try:
        payload = yaml.safe_load(new_config_yaml)
        if not isinstance(payload, dict):
            raise ValueError("Config must be a YAML mapping")
    except (yaml.YAMLError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config data: {exc}")
    return payload


def validate_or_400(config: dict) -> None:
    """Run :func:`validate_config`; re-raise ``SystemExit`` as ``HTTPException(400)``.

    Used by the confirm handlers, which want a 400 JSON error rather than
    a form re-render when validation fails (validation on confirm should
    be rare — it was already checked on preview, so failure here means
    something changed between preview and confirm).
    """
    try:
        validate_config(config)
    except SystemExit as exc:
        raise HTTPException(status_code=400, detail=f"Config validation failed: {exc}")
