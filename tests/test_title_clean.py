"""Tests for title_clean.py: strip_date, reorder_parts, clean_title, resolve_title_cleaning."""
from podcast_etl.pipeline import resolve_title_cleaning
from podcast_etl.title_clean import clean_title, reorder_parts, strip_date


class TestStripDate:
    # Parentheses
    def test_numeric_underscore_parens(self):
        assert strip_date("Natasha Lennard (3_19_26)") == "Natasha Lennard"

    def test_numeric_slash_parens(self):
        assert strip_date("Guest Name (03/22/2026)") == "Guest Name"

    def test_numeric_dash_parens(self):
        assert strip_date("Guest Name (3-22-26)") == "Guest Name"

    def test_iso_date_parens(self):
        assert strip_date("Guest Name (2026-03-22)") == "Guest Name"

    def test_month_name_comma_parens(self):
        assert strip_date("Guest Name (March 22, 2026)") == "Guest Name"

    def test_short_month_no_comma_parens(self):
        assert strip_date("Guest Name (Mar 22 2026)") == "Guest Name"

    # Brackets
    def test_numeric_brackets(self):
        assert strip_date("Guest Name [3_19_26]") == "Guest Name"

    # Braces
    def test_numeric_braces(self):
        assert strip_date("Guest Name {3_19_26}") == "Guest Name"

    # Date at start
    def test_date_at_start(self):
        assert strip_date("(3_19_26) Guest Name") == "Guest Name"

    # Date in middle with separators
    def test_date_in_middle(self):
        assert strip_date("Show - (3_19_26) - Guest") == "Show - Guest"

    # Cleanup of trailing separator
    def test_trailing_dash_cleaned(self):
        assert strip_date("Guest Name - (3_19_26)") == "Guest Name"

    # No match cases
    def test_bare_date_not_stripped(self):
        assert strip_date("Guest Name 3_19_26") == "Guest Name 3_19_26"

    def test_no_date_unchanged(self):
        assert strip_date("Just a Normal Title") == "Just a Normal Title"

    def test_empty_string(self):
        assert strip_date("") == ""

    # Safety: don't return empty
    def test_date_only_returns_original(self):
        assert strip_date("(3_19_26)") == "(3_19_26)"

    # Multiple dates — only bracketed dates are removed, connectors like "and" remain
    def test_multiple_dates_all_stripped(self):
        assert strip_date("Ep (1/2/26) and (3/4/26)") == "Ep and"


def _same_day_entries(*titles_and_dates: tuple[str, str]) -> list[dict]:
    """Helper to build a list of fake feed entries with title and published."""
    return [{"title": t, "published": d} for t, d in titles_and_dates]


_PUB = "Mon, 01 Jan 2024 00:00:00 +0000"
_PUB_OTHER = "Tue, 02 Jan 2024 00:00:00 +0000"


class TestReorderParts:
    # --- No siblings: title unchanged ---

    def test_no_entries_unchanged(self):
        """Without sibling context, part titles are left alone."""
        assert reorder_parts("The Great Episode (Part 1)") == "The Great Episode (Part 1)"

    def test_no_published_date_unchanged(self):
        entries = _same_day_entries(
            ("Series - Ep A (Part 1)", _PUB),
            ("Series - Ep B (Part 2)", _PUB),
        )
        assert reorder_parts("Series - Ep A (Part 1)", published=None, all_entries=entries) == "Series - Ep A (Part 1)"

    def test_solo_episode_unchanged(self):
        """Only one episode on the date — no reorder."""
        entries = _same_day_entries(("Solo Episode (Part 1)", _PUB))
        assert reorder_parts("Solo Episode (Part 1)", _PUB, entries) == "Solo Episode (Part 1)"

    def test_no_part_indicator_unchanged(self):
        assert reorder_parts("Just a Normal Title") == "Just a Normal Title"

    def test_bare_part_not_matched(self):
        assert reorder_parts("Episode Part 1") == "Episode Part 1"

    def test_empty_string(self):
        assert reorder_parts("") == ""

    # --- Siblings with common prefix ---

    def test_common_prefix_inserts_part_after_prefix(self):
        entries = _same_day_entries(
            ("World War II - D-Day (Part 3)", _PUB),
            ("World War II - Battle of the Bulge (Part 4)", _PUB),
        )
        assert reorder_parts("World War II - D-Day (Part 3)", _PUB, entries) == "World War II - Part 3 - D-Day"
        assert reorder_parts("World War II - Battle of the Bulge (Part 4)", _PUB, entries) == "World War II - Part 4 - Battle of the Bulge"

    def test_common_prefix_no_separator(self):
        """Common prefix without a dash separator snaps to word boundary."""
        entries = _same_day_entries(
            ("History Hour Alpha (Part 1)", _PUB),
            ("History Hour Beta (Part 2)", _PUB),
        )
        assert reorder_parts("History Hour Alpha (Part 1)", _PUB, entries) == "History Hour - Part 1 - Alpha"

    def test_different_bracket_types(self):
        entries = _same_day_entries(
            ("Series - Alpha [Part 1]", _PUB),
            ("Series - Beta [Part 2]", _PUB),
        )
        assert reorder_parts("Series - Alpha [Part 1]", _PUB, entries) == "Series - Part 1 - Alpha"

    def test_pt_dot_variant(self):
        entries = _same_day_entries(
            ("Series - Alpha (Pt. 1)", _PUB),
            ("Series - Beta (Pt. 2)", _PUB),
        )
        assert reorder_parts("Series - Alpha (Pt. 1)", _PUB, entries) == "Series - Pt. 1 - Alpha"

    def test_case_insensitive_preserves_case(self):
        entries = _same_day_entries(
            ("Series - Alpha (PART 1)", _PUB),
            ("Series - Beta (PART 2)", _PUB),
        )
        assert reorder_parts("Series - Alpha (PART 1)", _PUB, entries) == "Series - PART 1 - Alpha"

    def test_ignores_different_day_siblings(self):
        """Episodes on a different day are not considered siblings."""
        entries = _same_day_entries(
            ("Series - Alpha (Part 1)", _PUB),
            ("Series - Beta (Part 2)", _PUB_OTHER),
        )
        assert reorder_parts("Series - Alpha (Part 1)", _PUB, entries) == "Series - Alpha (Part 1)"

    # --- Short prefix: prepend ---

    def test_short_prefix_prepends(self):
        """When common prefix is < 5 chars, fall back to prepending."""
        entries = _same_day_entries(
            ("Go Alpha (Part 1)", _PUB),
            ("Go Beta (Part 2)", _PUB),
        )
        # Common prefix is "Go" (2 chars) — too short
        assert reorder_parts("Go Alpha (Part 1)", _PUB, entries) == "Part 1 - Go Alpha"

    def test_no_common_prefix_prepends(self):
        entries = _same_day_entries(
            ("Alpha Story (Part 1)", _PUB),
            ("Beta Story (Part 2)", _PUB),
        )
        assert reorder_parts("Alpha Story (Part 1)", _PUB, entries) == "Part 1 - Alpha Story"

    # --- Siblings without part indicators don't count ---

    def test_non_part_siblings_ignored(self):
        """Only siblings with part indicators count for grouping."""
        entries = _same_day_entries(
            ("Series - Alpha (Part 1)", _PUB),
            ("Series - Unrelated Episode", _PUB),
        )
        assert reorder_parts("Series - Alpha (Part 1)", _PUB, entries) == "Series - Alpha (Part 1)"


class TestCleanTitle:
    def test_empty_config_no_change(self):
        assert clean_title("Title (3_19_26)", {}) == "Title (3_19_26)"

    def test_none_config_no_change(self):
        assert clean_title("Title (3_19_26)", None) == "Title (3_19_26)"

    def test_both_false_no_change(self):
        assert clean_title("Title (3_19_26)", {"strip_date": False, "reorder_parts": False}) == "Title (3_19_26)"

    def test_strip_date_only(self):
        assert clean_title("Guest (3_19_26)", {"strip_date": True}) == "Guest"

    def test_reorder_parts_with_siblings(self):
        entries = _same_day_entries(
            ("Series - Alpha (Part 1)", _PUB),
            ("Series - Beta (Part 2)", _PUB),
        )
        result = clean_title("Series - Alpha (Part 1)", {"reorder_parts": True}, published=_PUB, all_entries=entries)
        assert result == "Series - Part 1 - Alpha"

    def test_reorder_parts_no_siblings_unchanged(self):
        assert clean_title("Episode (Part 1)", {"reorder_parts": True}) == "Episode (Part 1)"

    def test_both_enabled_with_siblings(self):
        entries = _same_day_entries(
            ("Series - Alpha (Part 1) (3_19_26)", _PUB),
            ("Series - Beta (Part 2) (3_20_26)", _PUB),
        )
        result = clean_title("Series - Alpha (Part 1) (3_19_26)", {"strip_date": True, "reorder_parts": True}, published=_PUB, all_entries=entries)
        assert result == "Series - Part 1 - Alpha"


class TestResolveTitleCleaning:
    def test_no_config_returns_none(self):
        assert resolve_title_cleaning({"settings": {}}) is None

    def test_global_only(self):
        config = {"settings": {"title_cleaning": {"strip_date": True}}}
        assert resolve_title_cleaning(config) == {"strip_date": True}

    def test_feed_only(self):
        config = {"settings": {}}
        feed = {"title_cleaning": {"reorder_parts": True}}
        assert resolve_title_cleaning(config, feed) == {"reorder_parts": True}

    def test_feed_overrides_global(self):
        config = {"settings": {"title_cleaning": {"strip_date": True, "reorder_parts": False}}}
        feed = {"title_cleaning": {"reorder_parts": True}}
        result = resolve_title_cleaning(config, feed)
        assert result == {"strip_date": True, "reorder_parts": True}

    def test_none_feed_config(self):
        config = {"settings": {"title_cleaning": {"strip_date": True}}}
        assert resolve_title_cleaning(config, None) == {"strip_date": True}
