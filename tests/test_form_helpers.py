"""Tests for the shared form helpers used by feeds and defaults routes."""
from __future__ import annotations

import pytest

from podcast_etl.web.form_helpers import (
    apply_bool_field,
    apply_int_field,
    apply_pipeline,
    apply_text_field,
    apply_title_cleaning,
    parse_form_section,
    parse_pipeline_checkboxes,
    parse_title_cleaning_checkboxes,
    parse_yaml_base,
)


# ---------------------------------------------------------------------------
# parse_yaml_base
# ---------------------------------------------------------------------------

def test_parse_yaml_base_empty_returns_empty_dict():
    base, error = parse_yaml_base("", "Feed YAML")
    assert base == {}
    assert error is None


def test_parse_yaml_base_whitespace_only_returns_empty_dict():
    base, error = parse_yaml_base("   \n  \n", "Feed YAML")
    assert base == {}
    assert error is None


def test_parse_yaml_base_valid_mapping():
    base, error = parse_yaml_base("key: value\nnum: 5", "Feed YAML")
    assert base == {"key": "value", "num": 5}
    assert error is None


def test_parse_yaml_base_invalid_yaml_returns_error():
    base, error = parse_yaml_base("key: [unclosed", "Feed YAML")
    assert base == {}
    assert error is not None
    assert "Invalid YAML" in error


def test_parse_yaml_base_non_mapping_returns_error():
    base, error = parse_yaml_base("- item1\n- item2", "Feed YAML")
    assert base == {}
    assert error is not None
    assert "Feed YAML must be a YAML mapping" in error


def test_parse_yaml_base_null_returns_empty_dict():
    """YAML 'null' or just whitespace should not be an error."""
    base, error = parse_yaml_base("null", "Feed YAML")
    assert base == {}
    assert error is None


# ---------------------------------------------------------------------------
# apply_text_field
# ---------------------------------------------------------------------------

def test_apply_text_field_sets_non_empty():
    base = {}
    apply_text_field(base, "name", "hello")
    assert base == {"name": "hello"}


def test_apply_text_field_strips_whitespace():
    base = {}
    apply_text_field(base, "name", "  hello  ")
    assert base == {"name": "hello"}


def test_apply_text_field_deletes_when_cleared():
    base = {"name": "existing"}
    apply_text_field(base, "name", "")
    assert base == {}


def test_apply_text_field_deletes_when_whitespace():
    base = {"name": "existing"}
    apply_text_field(base, "name", "   ")
    assert base == {}


def test_apply_text_field_empty_on_empty_base_noop():
    base = {}
    apply_text_field(base, "name", "")
    assert base == {}


# ---------------------------------------------------------------------------
# apply_int_field
# ---------------------------------------------------------------------------

def test_apply_int_field_parses_valid_int():
    base = {}
    apply_int_field(base, "last", "10")
    assert base == {"last": 10}


def test_apply_int_field_raises_on_invalid_string():
    """Bad input must raise rather than silently storing the raw string.

    A string in an int field would later crash downstream code (e.g. upload
    step using category_id, filter_episodes using last) far from the form.
    """
    base = {}
    with pytest.raises(ValueError, match="category_id"):
        apply_int_field(base, "category_id", "abc")
    assert base == {}


def test_apply_int_field_raises_includes_field_name():
    """The raised error should name the offending field so users can fix it."""
    base = {}
    with pytest.raises(ValueError, match="last"):
        apply_int_field(base, "last", "not-a-number")


def test_apply_int_field_deletes_when_cleared():
    base = {"last": 5}
    apply_int_field(base, "last", "")
    assert base == {}


def test_apply_int_field_strips_whitespace():
    base = {}
    apply_int_field(base, "last", "  5  ")
    assert base == {"last": 5}


# ---------------------------------------------------------------------------
# apply_pipeline
# ---------------------------------------------------------------------------

def test_apply_pipeline_sets_list():
    base = {}
    apply_pipeline(base, ["download", "tag"])
    assert base == {"pipeline": ["download", "tag"]}


def test_apply_pipeline_removes_empty():
    base = {"pipeline": ["download"]}
    apply_pipeline(base, [])
    assert base == {}


def test_apply_pipeline_empty_on_empty_base_noop():
    base = {}
    apply_pipeline(base, [])
    assert base == {}


# ---------------------------------------------------------------------------
# apply_title_cleaning
# ---------------------------------------------------------------------------

def test_apply_title_cleaning_sets_when_any_true():
    base = {}
    apply_title_cleaning(base, {"strip_date": True, "sanitize": False})
    assert base == {"title_cleaning": {"strip_date": True, "sanitize": False}}


def test_apply_title_cleaning_removes_when_all_false():
    base = {"title_cleaning": {"strip_date": True}}
    apply_title_cleaning(base, {"strip_date": False, "sanitize": False})
    assert base == {}


def test_apply_title_cleaning_all_false_on_empty_base_noop():
    base = {}
    apply_title_cleaning(base, {"strip_date": False})
    assert base == {}


# ---------------------------------------------------------------------------
# parse_pipeline_checkboxes
# ---------------------------------------------------------------------------

def test_parse_pipeline_checkboxes_picks_checked():
    form = {"pipeline_download": "on", "pipeline_tag": "on", "pipeline_upload": "off"}
    result = parse_pipeline_checkboxes(form, ["download", "tag", "upload", "seed"])
    assert result == ["download", "tag"]


def test_parse_pipeline_checkboxes_preserves_order():
    form = {"pipeline_a": "on", "pipeline_b": "on", "pipeline_c": "on"}
    result = parse_pipeline_checkboxes(form, ["c", "a", "b"])
    assert result == ["c", "a", "b"]


def test_parse_pipeline_checkboxes_empty_when_none_checked():
    form = {}
    result = parse_pipeline_checkboxes(form, ["download", "tag"])
    assert result == []


# ---------------------------------------------------------------------------
# parse_title_cleaning_checkboxes
# ---------------------------------------------------------------------------

def test_parse_title_cleaning_checkboxes_all_on():
    form = {
        "title_strip_date": "on",
        "title_reorder_parts": "on",
        "title_prepend_episode_number": "on",
        "title_sanitize": "on",
    }
    result = parse_title_cleaning_checkboxes(form)
    assert result == {
        "strip_date": True,
        "reorder_parts": True,
        "prepend_episode_number": True,
        "sanitize": True,
    }


def test_parse_title_cleaning_checkboxes_all_off():
    form = {}
    result = parse_title_cleaning_checkboxes(form)
    assert result == {
        "strip_date": False,
        "reorder_parts": False,
        "prepend_episode_number": False,
        "sanitize": False,
    }


# ---------------------------------------------------------------------------
# store_pending_change / pop_pending_change
# ---------------------------------------------------------------------------

def _fake_request():
    class FakeState:
        pass

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()

    return FakeRequest()


def test_store_and_pop_pending_change():
    from podcast_etl.web.form_helpers import pop_pending_change, store_pending_change

    request = _fake_request()
    token = store_pending_change(request, "some yaml content")
    assert token
    assert len(token) >= 16

    popped = pop_pending_change(request, token)
    assert popped == "some yaml content"

    # Token is single-use
    assert pop_pending_change(request, token) is None


def test_store_pending_change_evicts_oldest_over_max_size():
    """Unbounded growth would let a never-confirmed preview accumulate forever.

    When the store is full, the oldest entry must be evicted so memory stays
    bounded. Evicted tokens must no longer be retrievable.
    """
    from podcast_etl.web.form_helpers import (
        MAX_PENDING,
        pop_pending_change,
        store_pending_change,
    )

    request = _fake_request()
    first_token = store_pending_change(request, "payload-0")
    for i in range(1, MAX_PENDING + 1):
        store_pending_change(request, f"payload-{i}")

    # Store should be at max size, not larger
    assert len(request.app.state.pending_changes) == MAX_PENDING
    # The first token should have been evicted
    assert pop_pending_change(request, first_token) is None


def test_store_and_pop_pending_delete():
    from podcast_etl.web.form_helpers import pop_pending_delete, store_pending_delete

    request = _fake_request()
    token = store_pending_delete(request, "show-a")
    assert token
    assert pop_pending_delete(request, token) == "show-a"
    # Single-use
    assert pop_pending_delete(request, token) is None


def test_store_pending_delete_evicts_oldest_over_max_size():
    from podcast_etl.web.form_helpers import (
        MAX_PENDING,
        pop_pending_delete,
        store_pending_delete,
    )

    request = _fake_request()
    first_token = store_pending_delete(request, "show-0")
    for i in range(1, MAX_PENDING + 1):
        store_pending_delete(request, f"show-{i}")

    assert len(request.app.state.pending_deletes) == MAX_PENDING
    assert pop_pending_delete(request, first_token) is None


# ---------------------------------------------------------------------------
# apply_bool_field
# ---------------------------------------------------------------------------

def test_apply_bool_field_checkbox_on():
    base = {}
    apply_bool_field(base, "enabled", "on")
    assert base == {"enabled": True}


def test_apply_bool_field_checkbox_off():
    base = {"enabled": True}
    apply_bool_field(base, "enabled", "")
    assert base == {"enabled": False}


# ---------------------------------------------------------------------------
# parse_form_section
# ---------------------------------------------------------------------------

def test_parse_form_section_empty_form():
    form = {}
    base, error = parse_form_section(
        form, ["download", "tag"], "Feed",
        text_fields=["url", "name"],
        int_fields=["last"],
        bool_fields=["enabled"],
    )
    assert error is None
    # bool fields always set; empty text/int fields leave no key
    assert base == {"enabled": False}


def test_parse_form_section_all_fields():
    form = {
        "extra_yaml": "",
        "url": "http://a.com/rss",
        "name": "show-a",
        "last": "5",
        "enabled": "on",
        "pipeline_download": "on",
        "pipeline_tag": "on",
        "title_strip_date": "on",
    }
    base, error = parse_form_section(
        form, ["download", "tag", "upload"], "Feed",
        text_fields=["url", "name"],
        int_fields=["last"],
        bool_fields=["enabled"],
    )
    assert error is None
    assert base["url"] == "http://a.com/rss"
    assert base["name"] == "show-a"
    assert base["last"] == 5
    assert base["enabled"] is True
    assert base["pipeline"] == ["download", "tag"]
    assert base["title_cleaning"]["strip_date"] is True


def test_parse_form_section_yaml_base_with_overlay():
    """Form fields overlay on top of the YAML base."""
    form = {
        "extra_yaml": "url: http://old.com/rss\ntracker:\n  mod_queue_opt_in: 1\n",
        "url": "http://new.com/rss",
        "name": "show",
    }
    base, error = parse_form_section(
        form, [], "Feed",
        text_fields=["url", "name"],
    )
    assert error is None
    assert base["url"] == "http://new.com/rss"  # form wins over YAML
    assert base["name"] == "show"
    assert base["tracker"] == {"mod_queue_opt_in": 1}  # preserved from YAML


def test_parse_form_section_invalid_yaml():
    form = {"extra_yaml": "key: [unclosed"}
    base, error = parse_form_section(form, [], "Feed")
    assert base == {}
    assert error is not None
    assert "Invalid YAML" in error


def test_parse_form_section_bad_int_returns_error():
    """An unparseable int field should surface as an error to the caller."""
    form = {"extra_yaml": "", "last": "abc"}
    base, error = parse_form_section(
        form, [], "Feed",
        int_fields=["last"],
    )
    assert base == {}
    assert error is not None
    assert "last" in error


def test_parse_form_section_clears_fields_not_in_form():
    """Form-cleared fields override YAML."""
    form = {
        "extra_yaml": "last: 10\nepisode_filter: 'Part'\n",
        # last and episode_filter both cleared in form
    }
    base, error = parse_form_section(
        form, [], "Feed",
        text_fields=["episode_filter"],
        int_fields=["last"],
    )
    assert error is None
    assert "last" not in base
    assert "episode_filter" not in base


def test_pop_pending_change_invalid_token():
    from podcast_etl.web.form_helpers import pop_pending_change

    class FakeState:
        pass

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()

    result = pop_pending_change(FakeRequest(), "nonexistent")
    assert result is None
