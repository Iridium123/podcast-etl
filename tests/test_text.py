"""Tests for podcast_etl.text — description cleaning and blacklist."""

import pytest

from podcast_etl.text import apply_blacklist, clean_description, contains_blacklisted


class TestCleanDescription:
    def test_none_returns_none(self):
        assert clean_description(None) is None

    def test_empty_returns_none(self):
        assert clean_description("") is None
        assert clean_description("   ") is None

    def test_plain_text_preserved(self):
        text = "Andy and Tarik chat about the value of losing."
        assert clean_description(text) == text

    def test_plain_text_multiline(self):
        text = "Line one.\n\nLine two."
        assert clean_description(text) == text

    def test_html_entity_encoded(self):
        """Patreon-style: &lt;html&gt;&lt;p&gt;content&lt;/p&gt;&lt;/html&gt;"""
        raw = "&lt;html&gt;&lt;p&gt;Hello world&lt;/p&gt;&lt;p&gt;Second paragraph&lt;/p&gt;&lt;/html&gt;"
        result = clean_description(raw)
        assert "Hello world" in result
        assert "Second paragraph" in result
        assert "<" not in result

    def test_cdata_html(self):
        """Buzzsprout-style: CDATA wrapping HTML."""
        raw = "<![CDATA[<p>Join KP and JJ this week.</p><p>Music by Def Lev.</p>]]>"
        result = clean_description(raw)
        assert "Join KP and JJ this week." in result
        assert "Music by Def Lev." in result
        assert "<p>" not in result

    def test_html_with_links_stripped(self):
        raw = '<p>Check out <a href="https://example.com">our site</a> for more.</p>'
        result = clean_description(raw)
        assert result == "Check out our site for more."

    def test_br_tags_become_newlines(self):
        raw = "Line one<br/>Line two<br>Line three<BR />Line four"
        result = clean_description(raw)
        assert "Line one\nLine two\nLine three\nLine four" == result

    def test_paragraph_breaks_preserved(self):
        raw = "<p>First paragraph.</p><p>Second paragraph.</p>"
        result = clean_description(raw)
        lines = [l for l in result.split("\n") if l.strip()]
        assert lines == ["First paragraph.", "Second paragraph."]

    def test_double_encoded_entities(self):
        """Handle &amp;lt; style double-encoding."""
        raw = "&amp;lt;p&amp;gt;Hello&amp;lt;/p&amp;gt;"
        result = clean_description(raw)
        assert result == "Hello"

    def test_excessive_newlines_collapsed(self):
        raw = "One\n\n\n\n\nTwo"
        result = clean_description(raw)
        assert result == "One\n\nTwo"

    def test_whitespace_normalized_per_line(self):
        raw = "  lots   of    spaces  "
        result = clean_description(raw)
        assert result == "lots of spaces"

    def test_mixed_content(self):
        """Real-world mixed: HTML entities + tags + plain text."""
        raw = "&lt;p&gt;Hello &amp; welcome&lt;/p&gt;"
        result = clean_description(raw)
        assert result == "Hello & welcome"

    def test_div_and_blockquote(self):
        raw = "<div>Inside div</div><blockquote>Quoted text</blockquote>"
        result = clean_description(raw)
        assert "Inside div" in result
        assert "Quoted text" in result

    def test_heading_tags(self):
        raw = "<h1>Title</h1><p>Body text</p>"
        result = clean_description(raw)
        assert "Title" in result
        assert "Body text" in result
        assert "<h1>" not in result

    def test_list_items(self):
        raw = "<ul><li>Item one</li><li>Item two</li></ul>"
        result = clean_description(raw)
        assert "Item one" in result
        assert "Item two" in result


class TestContainsBlacklisted:
    def test_no_match(self):
        assert not contains_blacklisted("Hello world", ["secret"])

    def test_match(self):
        assert contains_blacklisted("Hello Ben Smith", ["Ben Smith"])

    def test_case_insensitive(self):
        assert contains_blacklisted("hello BEN", ["ben"])

    def test_empty_blacklist(self):
        assert not contains_blacklisted("Hello", [])

    def test_none_text(self):
        assert not contains_blacklisted(None, ["something"])


class TestApplyBlacklist:
    def test_clean_text_passes(self):
        assert apply_blacklist("Hello world", ["secret"]) == "Hello world"

    def test_blacklisted_returns_none(self):
        assert apply_blacklist("Hello Ben Smith", ["Ben Smith"]) is None

    def test_none_input(self):
        assert apply_blacklist(None, ["something"]) is None

    def test_empty_blacklist(self):
        assert apply_blacklist("anything", []) == "anything"

    def test_multiple_blacklist_entries(self):
        assert apply_blacklist("contains bad word", ["good", "bad"]) is None
        assert apply_blacklist("contains good word", ["bad", "evil"]) == "contains good word"
