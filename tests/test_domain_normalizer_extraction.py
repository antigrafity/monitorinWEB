"""Unit test contoh untuk ekstraksi link, gambar, dan section (task 4.3).

Memverifikasi perilaku konkret ``extract_links``, ``extract_image_urls``, dan
``split_sections`` sesuai Requirement 6.3, 6.4, dan 7.3: resolusi URL
relatif->absolut, banyak link/gambar, deduplikasi berurutan, pemisahan section
berdasarkan heading, dan kasus kosong.
"""

from __future__ import annotations

from monitoring.domain.normalizer import (
    Section,
    extract_image_urls,
    extract_links,
    split_sections,
)

BASE = "https://example.com/blog/"


# --------------------------------------------------------------------------- #
# extract_links (Req 6.3)
# --------------------------------------------------------------------------- #


def test_extract_links_empty_html_returns_empty():
    assert extract_links("", BASE) == []


def test_extract_links_resolves_relative_to_absolute():
    """Req 6.3: href relatif diselesaikan menjadi URL absolut via base_url."""
    html = '<a href="post-1">One</a>'
    assert extract_links(html, BASE) == ["https://example.com/blog/post-1"]


def test_extract_links_resolves_root_relative_and_absolute():
    html = (
        '<a href="/about">About</a>'
        '<a href="https://other.com/page">External</a>'
    )
    assert extract_links(html, BASE) == [
        "https://example.com/about",
        "https://other.com/page",
    ]


def test_extract_links_multiple_in_document_order():
    html = (
        '<a href="/a">A</a>'
        '<a href="/b">B</a>'
        '<a href="/c">C</a>'
    )
    assert extract_links(html, BASE) == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_extract_links_deduplicates_preserving_order():
    """URL absolut yang sama muncul tepat sekali, urutan pertama dipertahankan."""
    html = (
        '<a href="/a">A</a>'
        '<a href="/b">B</a>'
        '<a href="/a">A lagi</a>'
    )
    assert extract_links(html, BASE) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_extract_links_ignores_anchors_without_href():
    html = '<a>tanpa href</a><a href="">kosong</a><a href="/ok">ok</a>'
    assert extract_links(html, BASE) == ["https://example.com/ok"]


def test_extract_links_is_deterministic():
    html = '<a href="/a">A</a><a href="/b">B</a>'
    assert extract_links(html, BASE) == extract_links(html, BASE)


# --------------------------------------------------------------------------- #
# extract_image_urls (Req 7.3)
# --------------------------------------------------------------------------- #


def test_extract_image_urls_empty_html_returns_empty():
    assert extract_image_urls("", BASE) == []


def test_extract_image_urls_resolves_relative_to_absolute():
    html = '<img src="img/logo.png">'
    assert extract_image_urls(html, BASE) == [
        "https://example.com/blog/img/logo.png"
    ]


def test_extract_image_urls_multiple_in_document_order():
    html = (
        '<img src="/1.png">'
        '<img src="/2.png">'
        '<img src="https://cdn.example.com/3.png">'
    )
    assert extract_image_urls(html, BASE) == [
        "https://example.com/1.png",
        "https://example.com/2.png",
        "https://cdn.example.com/3.png",
    ]


def test_extract_image_urls_deduplicates_preserving_order():
    html = '<img src="/x.png"><img src="/y.png"><img src="/x.png">'
    assert extract_image_urls(html, BASE) == [
        "https://example.com/x.png",
        "https://example.com/y.png",
    ]


def test_extract_image_urls_ignores_img_without_src():
    html = '<img alt="no src"><img src="">'
    assert extract_image_urls(html, BASE) == []


# --------------------------------------------------------------------------- #
# split_sections (Req 6.4)
# --------------------------------------------------------------------------- #


def test_split_sections_empty_html_returns_empty():
    assert split_sections("") == []


def test_split_sections_no_heading_returns_empty():
    """Konten tanpa heading tidak menghasilkan section."""
    assert split_sections("<p>hanya paragraf tanpa heading</p>") == []


def test_split_sections_single_heading_with_content():
    html = "<h1>Judul</h1><p>Isi   paragraf</p>"
    assert split_sections(html) == [Section(heading="Judul", content="Isi paragraf")]


def test_split_sections_splits_by_headings():
    """Req 6.4: tiap heading memulai section baru sampai heading berikutnya."""
    html = (
        "<h1>Bagian Satu</h1><p>Teks satu</p>"
        "<h2>Bagian Dua</h2><p>Teks dua</p>"
    )
    assert split_sections(html) == [
        Section(heading="Bagian Satu", content="Teks satu"),
        Section(heading="Bagian Dua", content="Teks dua"),
    ]


def test_split_sections_heading_without_content():
    html = "<h1>Kosong</h1><h2>Berisi</h2><p>ada</p>"
    assert split_sections(html) == [
        Section(heading="Kosong", content=""),
        Section(heading="Berisi", content="ada"),
    ]


def test_split_sections_all_heading_levels():
    html = "".join(f"<h{i}>H{i}</h{i}>" for i in range(1, 7))
    result = split_sections(html)
    assert [s.heading for s in result] == ["H1", "H2", "H3", "H4", "H5", "H6"]


def test_split_sections_ignores_content_before_first_heading():
    html = "<p>prelude</p><h1>Judul</h1><p>isi</p>"
    assert split_sections(html) == [Section(heading="Judul", content="isi")]


def test_split_sections_excludes_script_and_style_content():
    html = (
        "<h1>Judul</h1>"
        "<script>var x=1;</script>"
        "<style>.a{}</style>"
        "<p>terlihat</p>"
    )
    assert split_sections(html) == [Section(heading="Judul", content="terlihat")]


def test_split_sections_as_block_useful_for_set_diff():
    """Section.as_block menghasilkan teks yang bisa dibandingkan sebagai himpunan."""
    html = "<h1>A</h1><p>x</p><h2>B</h2><p>y</p>"
    blocks = {s.as_block() for s in split_sections(html)}
    assert blocks == {"A\nx", "B\ny"}


def test_split_sections_is_deterministic():
    html = "<h1>A</h1><p>x</p><h2>B</h2><p>y</p>"
    assert split_sections(html) == split_sections(html)
