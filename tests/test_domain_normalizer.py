"""Unit test dasar untuk ``normalize_html`` (task 4.1).

Memverifikasi perilaku konkret sesuai Requirement 4.1–4.6. Property-based test
menyeluruh (Property 9–12) adalah task terpisah (4.2); di sini hanya contoh
spesifik & edge-case untuk memastikan implementasi berperilaku benar.
"""

from __future__ import annotations

from monitoring.domain.normalizer import NormalizeResult, normalize_html


def test_empty_input_returns_empty_without_error():
    """Req 4.6: input kosong -> teks kosong, tanpa error, bukan malformed."""
    result = normalize_html("")
    assert isinstance(result, NormalizeResult)
    assert result.text == ""
    assert result.parse_incomplete is False


def test_script_and_style_content_removed():
    """Req 4.1: teks di dalam <script>/<style> tidak muncul pada keluaran."""
    html = (
        "<html><body>"
        "<p>Visible</p>"
        "<script>var secret = 'do-not-show';</script>"
        "<style>.x{color:red;/*hidden*/}</style>"
        "</body></html>"
    )
    result = normalize_html(html)
    assert result.text == "Visible"
    assert "secret" not in result.text
    assert "color" not in result.text


def test_tags_removed_and_entities_decoded():
    """Req 4.2: tag dihapus dan entitas HTML didekode."""
    html = "<p>Fish &amp; Chips &lt;tag&gt; caf&eacute;</p>"
    result = normalize_html(html)
    assert result.text == "Fish & Chips <tag> café"
    assert "<p>" not in result.text
    assert "&amp;" not in result.text


def test_nbsp_entity_collapsed_to_single_space():
    """Req 4.2 + 4.3: &nbsp; didekode lalu ikut dikolaps sebagai whitespace."""
    result = normalize_html("<p>a&nbsp;&nbsp;&nbsp;b</p>")
    assert result.text == "a b"


def test_consecutive_whitespace_collapsed_and_trimmed():
    """Req 4.3: rangkaian whitespace jadi satu spasi; awal/akhir di-trim."""
    html = "<div>   Hello \t\n\n   World   </div>"
    result = normalize_html(html)
    assert result.text == "Hello World"
    assert not result.text.startswith(" ")
    assert not result.text.endswith(" ")
    assert "  " not in result.text


def test_deterministic_identical_input_identical_output():
    """Req 4.4: dua pemanggilan atas input identik -> keluaran identik."""
    html = "<html><body><h1>Title</h1><p>Body &amp; more</p></body></html>"
    first = normalize_html(html)
    second = normalize_html(html)
    assert first == second
    assert first.text == second.text


def test_malformed_html_still_extracts_text_and_flags_incomplete():
    """Req 4.5: HTML malformed tetap menghasilkan teks + parse_incomplete=True."""
    # End tag yang tidak sesuai memicu galat recovery parser.
    html = "<b>bold text</i>"
    result = normalize_html(html)
    assert result.parse_incomplete is True
    assert "bold text" in result.text


def test_well_formed_html_not_flagged_incomplete():
    """Req 4.5 (kontras): HTML valid tidak ditandai parse_incomplete."""
    html = "<html><body><p>clean</p></body></html>"
    result = normalize_html(html)
    assert result.parse_incomplete is False
    assert result.text == "clean"


def test_plain_text_without_tags():
    """Input teks tanpa markup tetap dinormalisasi dengan benar."""
    result = normalize_html("just   plain    text")
    assert result.text == "just plain text"
    assert result.parse_incomplete is False
