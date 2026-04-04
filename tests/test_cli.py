"""Tests for cli.py helper functions: parse_date_range."""
from datetime import date

import click
import pytest

from podcast_etl.cli import parse_date_range


# ---------------------------------------------------------------------------
# parse_date_range
# ---------------------------------------------------------------------------

def test_parse_date_range_single_date():
    assert parse_date_range("2026-03-01") == (date(2026, 3, 1), date(2026, 3, 1))


def test_parse_date_range_closed():
    assert parse_date_range("2026-03-01..2026-03-05") == (date(2026, 3, 1), date(2026, 3, 5))


def test_parse_date_range_open_end():
    assert parse_date_range("2026-03-01..") == (date(2026, 3, 1), None)


def test_parse_date_range_open_start():
    assert parse_date_range("..2026-03-05") == (None, date(2026, 3, 5))


def test_parse_date_range_start_after_end_raises():
    with pytest.raises(click.BadParameter):
        parse_date_range("2026-03-05..2026-03-01")


def test_parse_date_range_both_empty_raises():
    with pytest.raises(click.BadParameter):
        parse_date_range("..")


def test_parse_date_range_invalid_format_raises():
    with pytest.raises(ValueError):
        parse_date_range("not-a-date")
