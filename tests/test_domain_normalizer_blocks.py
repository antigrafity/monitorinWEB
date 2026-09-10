"""Unit test ``extract_text_blocks`` — presisi diff teks (Req 4.1–4.6, 6.2).

Memverifikasi bahwa teks halaman dipecah menjadi satu entri per blok
(heading, paragraf, item daftar, sel tabel, blockquote, dan blok lain yang
memiliki teks langsung) sehingga diff berbasis baris pada Change_Detector dapat
menunjuk blok yang berubah, bukan seluruh halaman.

Cakupan: pemisahan blok, tidak ada duplikasi pada sarang (nesting), pembuangan
``<script>``/``<style>``, kolaps whitespace per blok, masukan kosong/malformed,
dan determinisme. Termasuk pula penegasan bahwa kontrak ``normalize_html``
(keluaran satu baris) tidak berubah.
"""

from __future__ import annotations

import pytest

from monitoring.domain.normalizer import extract_text_blocks, normalize_html


# --------------------------------------------------------------------------- #
# Pemisahan blok (Req 6.2)
# --------------------------------------------------------------------------- #


def test_paragraphs_become_separate_blocks():
    assert extract_text_blocks("<p>a</p><p>b</p>") == ["a", "b"]


def test_headings_paragraphs_list_items_and_cells_are_blocks():
    html = (
        "<h1>Judul</h1>"
        "<p>Paragraf</p>"
        "<ul><li>Item 1</li><li>Item 2</li></ul>"
        "<table><tr><td>Sel 1</td><td>Sel 2</td></tr></table>"
    )
    assert extract_text_blocks(html) == [
        "Judul",
        "Paragraf",
        "Item 1",
        "Item 2",
        "Sel 1",
        "Sel 2",
    ]


def test_blockquote_and_figcaption_are_blocks():
    html = "<blockquote>Kutipan</blockquote><figcaption>Keterangan</figcaption>"
    assert extract_text_blocks(html) == ["Kutipan", "Keterangan"]


def test_blocks_follow_document_order():
    html = "<p>satu</p><h2>dua</h2><li>tiga</li>"
    assert extract_text_blocks(html) == ["satu", "dua", "tiga"]


def test_duplicate_text_is_not_deduplicated():
    """Dua paragraf identik bermakna dua blok (bukan dihapus sebagai duplikat)."""
    assert extract_text_blocks("<p>dup</p><p>dup</p>") == ["dup", "dup"]


# --------------------------------------------------------------------------- #
# Sarang (nesting): tidak ada teks yang terhitung dua kali
# --------------------------------------------------------------------------- #


def test_wrapper_div_does_not_duplicate_paragraph_text():
    """``<div>`` yang membungkus ``<p>`` tidak memancarkan teks paragraf dua kali."""
    assert extract_text_blocks("<div><p>Halo dunia</p></div>") == ["Halo dunia"]


def test_deeply_nested_wrappers_emit_each_text_once():
    html = (
        "<section><article><div><p>Alpha</p>"
        "<ul><li>Beta</li></ul></div></article></section>"
    )
    assert extract_text_blocks(html) == ["Alpha", "Beta"]


def test_inline_markup_is_merged_into_parent_block():
    """Elemen inline (``b``, ``a``, ``span``) menyatu dengan blok induknya."""
    html = '<p>Halo <b>dunia</b> dan <a href="/x">tautan</a></p>'
    assert extract_text_blocks(html) == ["Halo dunia dan tautan"]


def test_block_with_direct_text_and_child_block_emits_both_once():
    """Teks langsung sebuah blok terpisah dari blok anaknya, tanpa duplikasi."""
    blocks = extract_text_blocks("<div>teks langsung<p>anak</p>ekor</div>")
    assert blocks == ["teks langsung ekor", "anak"]
    # "anak" hanya muncul sekali di seluruh keluaran.
    assert sum(1 for b in blocks if "anak" in b) == 1


# --------------------------------------------------------------------------- #
# script/style, whitespace, blok kosong (Req 4.1, 4.3)
# --------------------------------------------------------------------------- #


def test_script_and_style_content_removed():
    html = "<p>a</p><script>jahat()</script><style>p{color:red}</style>"
    assert extract_text_blocks(html) == ["a"]


def test_whitespace_collapsed_within_each_block():
    html = "<p>  banyak\n\tspasi   di sini  </p>"
    assert extract_text_blocks(html) == ["banyak spasi di sini"]


def test_each_block_is_single_line():
    """Setiap blok tidak boleh memuat newline agar diff baris tetap presisi."""
    html = "<p>baris\nsatu</p><p>baris\nkedua</p>"
    blocks = extract_text_blocks(html)
    assert blocks == ["baris satu", "baris kedua"]
    assert all("\n" not in block for block in blocks)


def test_empty_blocks_are_dropped():
    html = "<p></p><p>   </p><div><br></div><p>isi</p>"
    assert extract_text_blocks(html) == ["isi"]


def test_html_entities_decoded():
    assert extract_text_blocks("<p>a &amp; b</p>") == ["a & b"]


# --------------------------------------------------------------------------- #
# Masukan kosong & malformed (Req 4.5, 4.6)
# --------------------------------------------------------------------------- #


def test_empty_input_returns_empty_list():
    assert extract_text_blocks("") == []


@pytest.mark.parametrize(
    "html",
    [
        "<p>tak ditutup <div>lain",
        "<<<>>>",
        "<p>a</p></div></p>",
        "teks tanpa tag",
        "<html><body><p>ok",
        "&nbsp;&amp;",
    ],
)
def test_malformed_input_does_not_raise(html):
    """Req 4.5: HTML malformed tetap menghasilkan daftar blok tanpa exception."""
    blocks = extract_text_blocks(html)
    assert isinstance(blocks, list)
    assert all(isinstance(block, str) for block in blocks)
    assert all(block == block.strip() and block for block in blocks)


def test_plain_text_without_tags_is_one_block():
    assert extract_text_blocks("teks tanpa tag") == ["teks tanpa tag"]


# --------------------------------------------------------------------------- #
# Determinisme (Req 4.4)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "html",
    [
        "<h1>J</h1><p>a</p><ul><li>b</li></ul>",
        "<div><p>x</p><div><p>y</p></div></div>",
        "<p>tak ditutup <div>lain",
        "",
    ],
)
def test_deterministic_for_identical_input(html):
    assert extract_text_blocks(html) == extract_text_blocks(html)


# --------------------------------------------------------------------------- #
# Kontrak normalize_html tidak berubah (Property 9–12 pada design)
# --------------------------------------------------------------------------- #


def test_normalize_html_still_returns_single_line():
    html = "<h1>Judul</h1><p>Paragraf satu</p><p>Paragraf dua</p>"
    result = normalize_html(html)
    assert "\n" not in result.text
    assert result.text == "Judul Paragraf satu Paragraf dua"
    # extract_text_blocks memecah teks yang sama menjadi beberapa blok.
    assert extract_text_blocks(html) == [
        "Judul",
        "Paragraf satu",
        "Paragraf dua",
    ]
