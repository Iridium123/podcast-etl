"""Tests for title_clean.py: strip_date, reorder_parts, clean_title."""
from podcast_etl.title_clean import strip_date


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
