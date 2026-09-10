"""Property-based tests untuk ``normalize_html`` (task 4.2).

Memvalidasi Correctness Property 9–12 pada design menggunakan Hypothesis
(min. 100 iterasi per properti):

- Property 9: teks di dalam ``<script>``/``<style>`` tidak muncul pada keluaran.
- Property 10: keluaran bebas markup (tanpa tag) dan seluruh entitas didekode.
- Property 11: whitespace dikolaps ke satu spasi tanpa leading/trailing.
- Property 12: normalisasi bersifat deterministik.

Generator HTML kustom di bawah mencakup kasus yang diminta task: teks bercampur
markup, entitas HTML, whitespace berlebih/campuran, elemen ``script``/``style``
bersarang berisi konten, fragmen malformed, dan masukan kosong.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.domain.normalizer import normalize_html

# --------------------------------------------------------------------------- #
# Blok penyusun generator
# --------------------------------------------------------------------------- #

# Kosakata kata terlihat (huruf kecil) yang tidak mengandung sentinel maupun
# karakter markup, sehingga tidak bertabrakan dengan pemeriksaan properti.
_VOCAB = [
    "alpha", "beta", "gamma", "delta", "hello", "world", "content",
    "page", "info", "data", "lorem", "ipsum", "satu", "dua", "tiga",
]

# Tag HTML struktural yang lazim.
_TAGS = [
    "p", "div", "span", "b", "i", "em", "strong", "h1", "h2", "h3",
    "ul", "li", "section", "article",
]

# Whitespace berlebih & campuran (spasi, tab, baris baru).
_WHITESPACE = [" ", "  ", "   ", "\t", "\n", "\r\n", " \t ", "\n\n  ", "\t\t"]

# Entitas yang TIDAK didekode menjadi tanda kurung sudut, sehingga aman untuk
# pemeriksaan "bebas markup" pada Property 10.
_SAFE_ENTITIES = ["&amp;", "&nbsp;", "&eacute;", "&copy;", "&quot;", "&#39;", "&hellip;"]

# Seluruh entitas termasuk yang didekode menjadi ``<``/``>`` (dipakai untuk
# Property 9/11/12 yang tidak memeriksa keberadaan tanda kurung sudut).
_ALL_ENTITIES = _SAFE_ENTITIES + ["&lt;", "&gt;"]

# Fragmen HTML malformed / bukan HTML valid (Req 4.5).
_MALFORMED = [
    "<b>bold</i>",
    "<p>unclosed paragraph",
    "<div><span>nested</div>",
    "<a href=>broken</a>",
    "<img src='x'>",
    "<br>",
    "<<double>>",
    "<p>halo<p>dunia",
]

# Sentinel unik yang hanya diletakkan di dalam script/style (Property 9).
_SCRIPT_SENTINEL = "ZZSCRIPTSENTINELZZ"
_STYLE_SENTINEL = "ZZSTYLESENTINELZZ"

_words = st.sampled_from(_VOCAB)
_whitespace = st.sampled_from(_WHITESPACE)


def _text_fragment(entities: st.SearchStrategy) -> st.SearchStrategy:
    """Rangkaian kata, whitespace, dan entitas yang digabung menjadi satu string."""
    piece = st.one_of(_words, _whitespace, entities)
    return st.lists(piece, max_size=8).map("".join)


def _element(entities: st.SearchStrategy) -> st.SearchStrategy:
    """Elemen HTML well-formed berisi text fragment."""
    return st.builds(
        lambda tag, inner: f"<{tag}>{inner}</{tag}>",
        st.sampled_from(_TAGS),
        _text_fragment(entities),
    )


def _script_element() -> st.SearchStrategy:
    """Elemen ``<script>`` berisi sentinel yang harus dihapus dari keluaran."""
    return st.builds(
        lambda body: f"<script>{body}</script>",
        st.sampled_from(
            [
                f"var s = '{_SCRIPT_SENTINEL}'; if (a < b) doThing();",
                f"console.log('{_SCRIPT_SENTINEL}');",
                f"function f() {{ return '{_SCRIPT_SENTINEL}'; }}",
            ]
        ),
    )


def _style_element() -> st.SearchStrategy:
    """Elemen ``<style>`` berisi sentinel yang harus dihapus dari keluaran."""
    return st.builds(
        lambda body: f"<style>{body}</style>",
        st.sampled_from(
            [
                f".cls {{ content: '{_STYLE_SENTINEL}'; color: red; }}",
                f"/* {_STYLE_SENTINEL} */ body {{ margin: 0; }}",
            ]
        ),
    )


# Generator dokumen umum: campuran teks bermarkup, entitas apa pun, whitespace
# berlebih, script/style bersarang, fragmen malformed, dan (list kosong) masukan
# kosong. Dipakai untuk Property 9, 11, dan 12.
_fragment = st.one_of(
    _text_fragment(st.sampled_from(_ALL_ENTITIES)),
    _element(st.sampled_from(_ALL_ENTITIES)),
    _script_element(),
    _style_element(),
    st.sampled_from(_MALFORMED),
)
html_documents = st.lists(_fragment, max_size=10).map("".join)

# Generator "bebas markup" untuk Property 10: hanya elemen well-formed + teks
# dengan entitas aman + script/style (yang dihapus). Tanpa fragmen malformed dan
# tanpa entitas ``&lt;``/``&gt;`` agar keluaran dijamin tak memuat ``<``/``>``.
_safe_fragment = st.one_of(
    _text_fragment(st.sampled_from(_SAFE_ENTITIES)),
    _element(st.sampled_from(_SAFE_ENTITIES)),
    _script_element(),
    _style_element(),
)
safe_html_documents = st.lists(_safe_fragment, max_size=10).map("".join)


# Dokumen yang dijamin mengandung setidaknya satu script & satu style bersarang
# (Property 9): elemen script/style disisipkan di antara/di dalam elemen lain.
def _document_with_script_style() -> st.SearchStrategy:
    return st.builds(
        lambda before, script, style, after: (
            f"<div>{before}{script}<section>{style}{after}</section></div>"
        ),
        _text_fragment(st.sampled_from(_ALL_ENTITIES)),
        _script_element(),
        _style_element(),
        _element(st.sampled_from(_ALL_ENTITIES)),
    )


# --------------------------------------------------------------------------- #
# Property 9: Script dan style tidak muncul pada teks ternormalisasi
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 9: Script dan style tidak muncul pada teks ternormalisasi
@settings(max_examples=100)
@given(_document_with_script_style())
def test_script_and_style_content_absent(html: str):
    """Validates: Requirements 4.1

    Teks di dalam elemen <script>/<style> (sentinel) tidak boleh muncul pada
    keluaran normalize_html.
    """
    # Prasyarat: sentinel memang hadir pada masukan.
    assert _SCRIPT_SENTINEL in html
    assert _STYLE_SENTINEL in html

    text = normalize_html(html).text

    assert _SCRIPT_SENTINEL not in text
    assert _STYLE_SENTINEL not in text


# --------------------------------------------------------------------------- #
# Property 10: Keluaran normalisasi bebas markup
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 10: Keluaran normalisasi bebas markup
@settings(max_examples=100)
@given(safe_html_documents)
def test_output_free_of_markup(html: str):
    """Validates: Requirements 4.2

    Keluaran tidak mengandung tag HTML (tidak ada '<' atau '>') dan seluruh
    entitas HTML telah didekode (tidak ada sisa token entitas).
    """
    text = normalize_html(html).text

    # Tidak ada tanda kurung sudut -> tidak mungkin ada tag tersisa.
    assert "<" not in text
    assert ">" not in text

    # Seluruh entitas telah didekode: tidak ada token entitas mentah tersisa.
    for entity in _SAFE_ENTITIES:
        assert entity not in text


# --------------------------------------------------------------------------- #
# Property 11: Normalisasi whitespace
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 11: Normalisasi whitespace
@settings(max_examples=100)
@given(html_documents)
def test_whitespace_normalized(html: str):
    """Validates: Requirements 4.3

    Keluaran tidak memiliki whitespace di awal/akhir, tidak ada rangkaian
    whitespace berturut lebih dari satu, dan satu-satunya karakter whitespace
    adalah spasi tunggal.
    """
    text = normalize_html(html).text

    # Tidak ada leading/trailing whitespace.
    assert text == text.strip()
    # Tidak ada dua karakter whitespace berturut-turut.
    assert re.search(r"\s\s", text) is None
    # Tidak ada karakter whitespace selain spasi biasa (mis. tab/newline/NBSP).
    assert re.search(r"[^\S ]", text) is None


# --------------------------------------------------------------------------- #
# Property 12: Determinisme normalisasi
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 12: Determinisme normalisasi
@settings(max_examples=100)
@given(html_documents)
def test_normalization_deterministic(html: str):
    """Validates: Requirements 4.4

    Memanggil normalize_html dua kali atas masukan identik menghasilkan
    keluaran teks yang identik secara karakter demi karakter.
    """
    first = normalize_html(html)
    second = normalize_html(html)

    assert first.text == second.text
    assert first == second
