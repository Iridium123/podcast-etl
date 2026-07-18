"""Tests for title_clean.py: strip_date, strip_inline_date, parse_inline_date, reorder_parts, prepend_episode_number, sanitize, clean_title."""
from datetime import datetime

from podcast_etl.title_clean import (
    clean_title,
    parse_inline_date,
    prepend_episode_number,
    reorder_parts,
    sanitize,
    strip_date,
    strip_inline_date,
)


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

    def test_dotted_ymd_in_brackets(self):
        assert strip_date("Guest Name [2026.03.22]") == "Guest Name"

    def test_dotted_numeric_in_parens(self):
        assert strip_date("Guest Name (3.19.26)") == "Guest Name"

    def test_slash_ymd_in_brackets(self):
        assert strip_date("Guest Name [2026/03/22]") == "Guest Name"

    def test_invalid_calendar_triple_kept(self):
        assert strip_date("App Review [1080/60/2]") == "App Review [1080/60/2]"

    def test_three_digit_year_kept(self):
        assert strip_date("Mix [12.13.320]") == "Mix [12.13.320]"

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


class TestStripInlineDate:
    def test_mid_title(self):
        assert strip_inline_date("If Books Could Kill - 2025.10.02 - Sapiens") == "If Books Could Kill - Sapiens"

    def test_leading(self):
        assert strip_inline_date("2025.10.02 - Sapiens") == "Sapiens"

    def test_trailing(self):
        assert strip_inline_date("Sapiens - 2025.10.02") == "Sapiens"

    def test_numeric_variant(self):
        assert strip_inline_date("Guest Name 3_19_26") == "Guest Name"

    def test_month_name_variant(self):
        assert strip_inline_date("Guest Name March 22, 2026") == "Guest Name"

    def test_bracketed_date_left_alone(self):
        # Bracketed dates belong to strip_date; inline must not gut the brackets
        assert strip_inline_date("Guest Name (3_19_26)") == "Guest Name (3_19_26)"

    def test_multiple_dates(self):
        assert strip_inline_date("A 1/2/26 and 3/4/26") == "A and"

    def test_no_date_unchanged(self):
        assert strip_inline_date("Just a Normal Title") == "Just a Normal Title"

    def test_only_date_returns_original(self):
        assert strip_inline_date("2025.10.02") == "2025.10.02"

    def test_empty(self):
        assert strip_inline_date("") == ""

    def test_version_string_kept(self):
        assert strip_inline_date("App Update v2.10.24 Discussion") == "App Update v2.10.24 Discussion"

    def test_invalid_calendar_date_kept(self):
        assert strip_inline_date("Nonsense 13/45/26 here") == "Nonsense 13/45/26 here"

    def test_underscore_separators_tidied(self):
        assert strip_inline_date("Show_2025.10.02_Ep") == "Show Ep"

    def test_trailing_comma_tidied(self):
        assert strip_inline_date("Interview 3.19.26, extended cut") == "Interview extended cut"

    def test_date_inside_larger_parenthetical_stripped(self):
        assert strip_inline_date("Show (Live 2025.10.02) Extended") == "Show (Live) Extended"

    def test_dotted_scene_name(self):
        assert strip_inline_date("Show.Name.2025.10.02.Ep.Title") == "Show.Name Ep.Title"


class TestCleanTitleInlineDate:
    def test_flag_wiring(self):
        assert clean_title("Show - 2025.10.02 - Ep", {"strip_inline_date": True}) == "Show - Ep"

    def test_flag_off(self):
        assert clean_title("Show - 2025.10.02 - Ep", {"strip_inline_date": False}) == "Show - 2025.10.02 - Ep"

    def test_runs_after_strip_date(self):
        assert clean_title(
            "Show [2026-03-22] - 2025.10.02 - Ep",
            {"strip_date": True, "strip_inline_date": True},
        ) == "Show - Ep"


class TestParseInlineDate:
    def test_year_first_dots(self):
        assert parse_inline_date("Show - 2025.10.02 - Sapiens") == datetime(2025, 10, 2)

    def test_year_first_dashes(self):
        assert parse_inline_date("Show - 2025-10-28 - Eric Adams") == datetime(2025, 10, 28)

    def test_year_first_slashes(self):
        assert parse_inline_date("Show 2025/10/02") == datetime(2025, 10, 2)

    def test_year_first_underscores(self):
        assert parse_inline_date("Show 2025_10_02") == datetime(2025, 10, 2)

    def test_numeric_month_first(self):
        assert parse_inline_date("Guest (03/22/2026)") == datetime(2026, 3, 22)

    def test_numeric_two_digit_year(self):
        assert parse_inline_date("Guest 3.19.26") == datetime(2026, 3, 19)

    def test_two_digit_year_pivot(self):
        assert parse_inline_date("Old Show 3/19/85") == datetime(1985, 3, 19)

    def test_month_name(self):
        assert parse_inline_date("Guest (March 22, 2026)") == datetime(2026, 3, 22)

    def test_month_name_abbreviated(self):
        assert parse_inline_date("Guest Mar 22 2026") == datetime(2026, 3, 22)

    def test_invalid_calendar_date_skipped(self):
        assert parse_inline_date("Nonsense 13/45/26") is None

    def test_invalid_then_valid_returns_valid(self):
        assert parse_inline_date("13/45/26 but 2025.10.02") == datetime(2025, 10, 2)

    def test_no_date(self):
        assert parse_inline_date("Just a Normal Title") is None

    def test_empty(self):
        assert parse_inline_date("") is None

    def test_first_match_wins(self):
        assert parse_inline_date("A 2025.10.02 B 2026.01.01") == datetime(2025, 10, 2)

    def test_digit_run_not_a_date(self):
        # A 4-digit year must not be carved out of a longer digit run
        assert parse_inline_date("id 12025.10.023 x") is None

    def test_version_string_not_a_date(self):
        assert parse_inline_date("Show - Interview v1.2.34") is None

    def test_three_digit_number_not_a_year(self):
        assert parse_inline_date("Mix 12.13.320 kbps") is None

    def test_release_junk_ignored(self):
        assert parse_inline_date(
            "If Books Could Kill - 2025.10.02 - Title [2025_MP3-96 kbps]"
        ) == datetime(2025, 10, 2)


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


class TestPrependEpisodeNumber:
    def test_basic(self):
        assert prepend_episode_number("Rise of the Mongols", 123) == "123 - Rise of the Mongols"

    def test_empty_title(self):
        assert prepend_episode_number("", 42) == "42"

    def test_single_digit(self):
        assert prepend_episode_number("Pilot", 1) == "1 - Pilot"


class TestSanitize:
    # --- Invalid character replacement ---

    def test_colon_replaced_and_collapsed(self):
        assert sanitize("Title: Subtitle") == "Title - Subtitle"

    def test_backslash_replaced(self):
        assert sanitize("Path\\Name") == "Path_Name"

    def test_forward_slash_replaced(self):
        assert sanitize("Either/Or") == "Either_Or"

    def test_asterisk_replaced(self):
        assert sanitize("Best*Episode") == "Best_Episode"

    def test_question_mark_trailing(self):
        assert sanitize("What?") == "What"

    def test_pipe_replaced(self):
        assert sanitize("Option A | Option B") == "Option A - Option B"

    def test_angle_brackets_replaced(self):
        assert sanitize("foo<bar>baz") == "foo_bar_baz"

    def test_double_quotes_become_single(self):
        assert sanitize('He said "hello"') == "He said 'hello'"

    def test_control_char_replaced(self):
        assert sanitize("Line\x00One") == "Line_One"

    # --- Separator collapsing (requires 2+ separator chars) ---

    def test_double_dash_collapsed(self):
        assert sanitize("Foo - - Bar") == "Foo - Bar"

    def test_triple_dash_collapsed(self):
        assert sanitize("Foo - - - Bar") == "Foo - Bar"

    def test_single_underscore_preserved(self):
        assert sanitize("Foo_Bar") == "Foo_Bar"

    def test_mixed_separators_collapsed(self):
        assert sanitize("Foo _-_ Bar") == "Foo - Bar"

    def test_existing_separator_preserved(self):
        assert sanitize("Show - Episode") == "Show - Episode"

    def test_single_space_preserved(self):
        assert sanitize("Hello World") == "Hello World"

    def test_hyphenated_compound_word_preserved(self):
        assert sanitize("Spider-Man") == "Spider-Man"

    def test_en_dash_pair_collapsed(self):
        assert sanitize("Foo \u2013\u2013 Bar") == "Foo - Bar"

    def test_em_dash_with_spaces_collapsed(self):
        assert sanitize("Foo \u2014 Bar") == "Foo - Bar"

    # --- Edge cases ---

    def test_empty_string(self):
        assert sanitize("") == ""

    def test_no_changes_needed(self):
        assert sanitize("Normal Title") == "Normal Title"

    def test_leading_invalid_char_cleaned(self):
        assert sanitize(":Title") == "Title"

    def test_trailing_invalid_char_cleaned(self):
        assert sanitize("Title:") == "Title"

    def test_all_invalid_returns_original(self):
        assert sanitize(":::") == ":::"

    # --- Real-world regression: double-dash from reorder_parts ---

    def test_double_dash_from_reorder(self):
        assert sanitize("The Ku Klux Klan - - Part 3 - Birth of a Nation") == (
            "The Ku Klux Klan - Part 3 - Birth of a Nation"
        )


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

    def test_sanitize_only(self):
        assert clean_title("Title: Subtitle", {"sanitize": True}) == "Title - Subtitle"

    def test_sanitize_false_no_change(self):
        assert clean_title("Title: Subtitle", {"sanitize": False}) == "Title: Subtitle"

    def test_sanitize_runs_after_reorder(self):
        """Sanitize cleans up double-dashes left by reorder_parts."""
        entries = _same_day_entries(
            ("The Ku Klux Klan - The Rise of Evil (Part 1)", _PUB),
            ("The Ku Klux Klan - Birth of a Nation (Part 3)", _PUB),
        )
        result = clean_title(
            "The Ku Klux Klan - Birth of a Nation (Part 3)",
            {"reorder_parts": True, "sanitize": True},
            published=_PUB,
            all_entries=entries,
        )
        assert " - - " not in result

    def test_prepend_episode_number_only(self):
        result = clean_title("My Episode", {"prepend_episode_number": True}, episode_number=7)
        assert result == "7 - My Episode"

    def test_prepend_episode_number_no_number_unchanged(self):
        result = clean_title("My Episode", {"prepend_episode_number": True}, episode_number=None)
        assert result == "My Episode"

    def test_prepend_episode_number_false_unchanged(self):
        result = clean_title("My Episode", {"prepend_episode_number": False}, episode_number=7)
        assert result == "My Episode"

    def test_prepend_episode_number_after_reorder(self):
        """Episode number comes before the reordered part indicator."""
        entries = _same_day_entries(
            ("Series - Alpha (Part 1)", _PUB),
            ("Series - Beta (Part 2)", _PUB),
        )
        result = clean_title(
            "Series - Alpha (Part 1)",
            {"reorder_parts": True, "prepend_episode_number": True},
            published=_PUB,
            all_entries=entries,
            episode_number=123,
        )
        assert result == "123 - Series - Part 1 - Alpha"

    def test_full_chain_with_episode_number(self):
        """strip_date -> reorder_parts -> prepend_episode_number -> sanitize."""
        entries = _same_day_entries(
            ("Series - Alpha (Part 1) (3_19_26)", _PUB),
            ("Series - Beta (Part 2) (3_20_26)", _PUB),
        )
        result = clean_title(
            "Series - Alpha (Part 1) (3_19_26)",
            {"strip_date": True, "reorder_parts": True, "prepend_episode_number": True, "sanitize": True},
            published=_PUB,
            all_entries=entries,
            episode_number=42,
        )
        assert result == "42 - Series - Part 1 - Alpha"
        assert " - - " not in result


