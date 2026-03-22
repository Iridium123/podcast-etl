"""Tests for title_clean.py: strip_date, reorder_parts, clean_title."""
from podcast_etl.title_clean import reorder_parts, strip_date


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


class TestReorderParts:
    # Basic reordering
    def test_part_parens(self):
        assert reorder_parts("The Great Episode (Part 1)") == "Part 1 - The Great Episode"

    def test_part_brackets(self):
        assert reorder_parts("The Great Episode [Part 2]") == "Part 2 - The Great Episode"

    def test_part_braces(self):
        assert reorder_parts("The Great Episode {Part 3}") == "Part 3 - The Great Episode"

    # Pt. and Pt variants
    def test_pt_dot(self):
        assert reorder_parts("The Great Episode (Pt. 1)") == "Pt. 1 - The Great Episode"

    def test_pt_no_dot(self):
        assert reorder_parts("The Great Episode (Pt 2)") == "Pt 2 - The Great Episode"

    # Case insensitivity (preserves original case)
    def test_lowercase_part(self):
        assert reorder_parts("The Great Episode (part 1)") == "part 1 - The Great Episode"

    def test_uppercase_part(self):
        assert reorder_parts("The Great Episode (PART 1)") == "PART 1 - The Great Episode"

    # Multi-digit
    def test_multi_digit_part(self):
        assert reorder_parts("The Great Episode (Part 12)") == "Part 12 - The Great Episode"

    # Trailing separator cleanup
    def test_trailing_dash_before_part(self):
        assert reorder_parts("The Great Episode - (Part 1)") == "Part 1 - The Great Episode"

    # No match cases
    def test_bare_part_not_reordered(self):
        assert reorder_parts("The Great Episode Part 1") == "The Great Episode Part 1"

    def test_no_part_unchanged(self):
        assert reorder_parts("Just a Normal Title") == "Just a Normal Title"

    def test_empty_string(self):
        assert reorder_parts("") == ""

    # Only first match moves
    def test_multiple_parts_only_first_moves(self):
        assert reorder_parts("Episode (Part 1) (Part 2)") == "Part 1 - Episode (Part 2)"

    # Part at start — already at front, but brackets should be removed
    def test_part_at_start_parens(self):
        assert reorder_parts("(Part 1) The Great Episode") == "Part 1 - The Great Episode"
