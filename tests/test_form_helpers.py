"""Tests for the shared form helpers used by feeds and defaults routes."""
from __future__ import annotations

from podcast_etl.web.form_helpers import (
    apply_int_field,
    apply_pipeline,
    apply_text_field,
    apply_title_cleaning,
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


def test_apply_int_field_preserves_invalid_string():
    base = {}
    apply_int_field(base, "category_id", "abc")
    assert base == {"category_id": "abc"}


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

def test_store_and_pop_pending_change():
    from podcast_etl.web.form_helpers import pop_pending_change, store_pending_change

    # Fake request with app.state
    class FakeState:
        pass

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()

    request = FakeRequest()
    token = store_pending_change(request, "some yaml content")
    assert token
    assert len(token) >= 16

    popped = pop_pending_change(request, token)
    assert popped == "some yaml content"

    # Token is single-use
    assert pop_pending_change(request, token) is None


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
