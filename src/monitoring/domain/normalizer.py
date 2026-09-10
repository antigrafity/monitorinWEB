"""Normalizer domain — fungsi murni untuk membersihkan HTML (task 4.1).

Mengubah HTML mentah menjadi teks bersih sesuai Requirement 4:

- Hapus elemen ``<script>`` & ``<style>`` beserta isinya (Req 4.1).
- Hapus seluruh tag HTML dan dekode entitas HTML (Req 4.2).
- Kolaps rangkaian whitespace berturut menjadi satu spasi, lalu trim (Req 4.3).
- Deterministik: masukan identik menghasilkan keluaran identik (Req 4.4).
- HTML malformed -> tetap ekstrak teks + ``parse_incomplete=True`` (Req 4.5).
- Masukan kosong (0 karakter) -> keluaran ``""`` tanpa error (Req 4.6).

Modul ini murni (tanpa I/O) sehingga menjadi target property-based testing.
Kompatibel Python 3.9 melalui ``from __future__ import annotations``.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Comment, Doctype, NavigableString, ProcessingInstruction
from lxml import etree

# Nama tag heading yang menandai awal sebuah section (Req 6.4).
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Kolaps setiap rangkaian whitespace (spasi, tab, baris baru, termasuk
# whitespace Unicode seperti NBSP hasil dekode ``&nbsp;``) menjadi satu spasi.
_WHITESPACE_RE = re.compile(r"\s+")

# Elemen yang seluruh isinya harus dibuang dari keluaran teks (Req 4.1).
_STRIP_ELEMENTS = ("script", "style")


@dataclass(frozen=True)
class NormalizeResult:
    """Hasil normalisasi HTML.

    Attributes:
        text: Teks ternormalisasi yang bebas markup dan whitespace berlebih.
        parse_incomplete: ``True`` bila HTML tidak dapat diurai secara sempurna
            (malformed) namun teks tetap diekstrak semampunya (Req 4.5).
    """

    text: str
    parse_incomplete: bool


def _collapse_whitespace(text: str) -> str:
    """Ganti setiap rangkaian whitespace menjadi satu spasi lalu trim (Req 4.3)."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _detect_parse_incomplete(html: str) -> bool:
    """Deteksi apakah HTML malformed / tidak dapat diurai sempurna (Req 4.5).

    Menggunakan parser HTML lxml dalam mode recovery: bila parser mencatat
    galat apa pun (mis. tag tidak tertutup, karakter tidak valid) selama
    proses recovery, HTML dianggap tidak sempurna. Deterministik untuk masukan
    yang sama.
    """
    parser = etree.HTMLParser(recover=True)
    try:
        etree.fromstring(html, parser)
    except Exception:
        # Kegagalan penguraian total tetap dianggap tidak sempurna, bukan error.
        return True
    return len(parser.error_log) > 0


def normalize_html(html: str) -> NormalizeResult:
    """Normalisasi HTML mentah menjadi teks bersih (Req 4.1–4.6).

    Args:
        html: Konten HTML mentah.

    Returns:
        ``NormalizeResult`` berisi teks ternormalisasi dan tanda apakah proses
        penguraian tidak sempurna.
    """
    # Req 4.6: masukan kosong -> keluaran kosong tanpa error.
    if not html:
        return NormalizeResult(text="", parse_incomplete=False)

    parse_incomplete = _detect_parse_incomplete(html)

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        # Fallback defensif: bila BeautifulSoup gagal total, buang tag secara
        # kasar dan dekode entitas agar teks tetap dihasilkan (Req 4.5).
        stripped = re.sub(r"<[^>]*>", " ", html)
        text = _collapse_whitespace(_html.unescape(stripped))
        return NormalizeResult(text=text, parse_incomplete=True)

    # Req 4.1: hapus script & style beserta isinya sepenuhnya.
    for element in soup(_STRIP_ELEMENTS):
        element.decompose()

    # Req 4.2: get_text menghapus seluruh tag dan mendekode entitas HTML.
    raw_text = soup.get_text(separator=" ")

    # Req 4.3: kolaps whitespace + trim.
    text = _collapse_whitespace(raw_text)
    return NormalizeResult(text=text, parse_incomplete=parse_incomplete)


# Elemen block-level: setiap elemen ini memulai blok teks tersendiri sehingga
# diff teks dapat menunjuk paragraf/heading/item daftar yang berubah, bukan
# seluruh halaman. Elemen inline (``span``, ``a``, ``b``, ``em``, ...) TIDAK
# ada di sini; teksnya menyatu dengan blok induknya.
_BLOCK_ELEMENTS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "caption",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hgroup",
        "hr",
        "html",
        "legend",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
    }
)

# Tipe NavigableString yang bukan teks terlihat (komentar, doctype, dsb.).
_NON_TEXT_STRINGS = (Comment, Doctype, ProcessingInstruction)


def _own_text_parts(element) -> List[str]:
    """Kumpulkan teks milik ``element`` sendiri, tanpa sub-blok di dalamnya.

    Teks dari elemen inline (mis. ``<b>``, ``<a>``) ikut terkumpul karena
    secara visual menyatu dengan blok induk, sedangkan subtree elemen
    block-level dilewati agar tidak terhitung dua kali (blok tersebut akan
    dikeluarkan sebagai bloknya sendiri).
    """
    parts: List[str] = []
    for child in element.children:
        name = getattr(child, "name", None)
        if name is None:
            if isinstance(child, _NON_TEXT_STRINGS):
                continue
            if isinstance(child, NavigableString):
                parts.append(str(child))
            continue
        if name in _BLOCK_ELEMENTS:
            continue
        parts.extend(_own_text_parts(child))
    return parts


def extract_text_blocks(html: str) -> List[str]:
    """Ekstrak teks per blok (paragraf/heading/item) dari HTML (Req 4.1–4.6, 6.2).

    Berbeda dengan :func:`normalize_html` yang mengembalikan satu baris teks
    panjang, fungsi ini mengembalikan satu entri per elemen block-level
    (``p``, ``h1``–``h6``, ``li``, ``td``, ``th``, ``blockquote``,
    ``figcaption``, ``dd``, ``dt``, dan elemen block-level lain yang memiliki
    teks langsung). Dengan demikian diff berbasis baris pada Change_Detector
    dapat menunjuk blok mana yang berubah, bukan menyatakan seluruh teks
    halaman tergantikan.

    Perilaku:

    - Isi ``<script>``/``<style>`` dibuang lebih dulu (Req 4.1).
    - Whitespace di dalam setiap blok dikolaps menjadi satu spasi dan di-trim
      (Req 4.3); blok yang kosong setelah normalisasi dibuang.
    - Urutan dokumen dipertahankan dan duplikat TIDAK dihapus (dua paragraf
      identik bermakna dua blok).
    - Deterministik: masukan identik -> keluaran identik (Req 4.4).
    - Masukan kosong/``None`` -> ``[]`` (Req 4.6).
    - HTML malformed tidak pernah mengangkat exception; teks tetap diekstrak
      semampunya (Req 4.5).

    Args:
        html: Konten HTML mentah.

    Returns:
        Daftar blok teks ternormalisasi dalam urutan dokumen.
    """
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        # Fallback defensif: buang tag secara kasar sehingga teks tetap ada
        # meski parser gagal total (Req 4.5).
        stripped = re.sub(r"<[^>]*>", " ", html)
        text = _collapse_whitespace(_html.unescape(stripped))
        return [text] if text else []

    try:
        # Req 4.1: hapus script & style beserta isinya sepenuhnya.
        for element in soup(_STRIP_ELEMENTS):
            element.decompose()

        blocks: List[str] = []
        for element in soup.find_all(True):
            if element.name not in _BLOCK_ELEMENTS:
                continue
            text = _collapse_whitespace(" ".join(_own_text_parts(element)))
            if text:
                blocks.append(text)
        return blocks
    except Exception:
        # Req 4.5: HTML aneh tidak boleh menghentikan pemrosesan.
        stripped = re.sub(r"<[^>]*>", " ", html)
        text = _collapse_whitespace(_html.unescape(stripped))
        return [text] if text else []


@dataclass(frozen=True)
class Section:
    """Sebuah section konten: blok teks di bawah sebuah heading (Req 6.4).

    Attributes:
        heading: Teks heading (``h1``–``h6``) ternormalisasi.
        content: Teks konten ternormalisasi yang mengikuti heading sampai
            heading berikutnya. Kosong bila tidak ada konten di antara heading.
    """

    heading: str
    content: str

    def as_block(self) -> str:
        """Representasi teks tunggal untuk perbandingan berbasis himpunan.

        Menggabungkan heading dan konten sehingga dua section dianggap sama
        jika, dan hanya jika, heading maupun kontennya sama. Cocok dipakai
        sebagai elemen ``set`` saat mendeteksi section ditambah/dihapus.
        """
        if self.content:
            return f"{self.heading}\n{self.content}"
        return self.heading


def _parse(html: str) -> BeautifulSoup:
    """Bangun objek BeautifulSoup dengan parser lxml (dipakai bersama)."""
    return BeautifulSoup(html or "", "lxml")


def _dedup_preserving_order(items: List[str]) -> List[str]:
    """Hapus duplikat sambil mempertahankan urutan kemunculan pertama."""
    seen = set()
    result: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def extract_links(html: str, base_url: str) -> List[str]:
    """Ekstrak URL absolut dari seluruh ``<a href>`` (Req 6.3).

    Mengumpulkan nilai atribut ``href`` dari setiap tag ``<a>``, lalu
    menyelesaikannya menjadi URL absolut terhadap ``base_url`` memakai
    ``urllib.parse.urljoin``. Urutan mengikuti urutan dokumen dan duplikat
    dihilangkan sambil mempertahankan kemunculan pertama, sehingga keluaran
    deterministik untuk masukan yang sama.

    Args:
        html: Konten HTML mentah.
        base_url: URL dasar untuk menyelesaikan URL relatif.

    Returns:
        Daftar URL absolut unik dalam urutan dokumen.
    """
    if not html:
        return []
    soup = _parse(html)
    urls: List[str] = []
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if href is None:
            continue
        href = href.strip()
        if not href:
            continue
        urls.append(urljoin(base_url, href))
    return _dedup_preserving_order(urls)


def extract_image_urls(html: str, base_url: str) -> List[str]:
    """Ekstrak URL absolut dari seluruh ``<img src>`` (Req 7.3).

    Mengumpulkan nilai atribut ``src`` dari setiap tag ``<img>``, lalu
    menyelesaikannya menjadi URL absolut terhadap ``base_url``. Urutan
    mengikuti urutan dokumen dan duplikat dihilangkan sambil mempertahankan
    kemunculan pertama (deterministik).

    Args:
        html: Konten HTML mentah.
        base_url: URL dasar untuk menyelesaikan URL relatif.

    Returns:
        Daftar URL absolut gambar unik dalam urutan dokumen.
    """
    if not html:
        return []
    soup = _parse(html)
    urls: List[str] = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src is None:
            continue
        src = src.strip()
        if not src:
            continue
        urls.append(urljoin(base_url, src))
    return _dedup_preserving_order(urls)


def extract_title(html: str) -> Optional[str]:
    """Ekstrak teks ``<title>`` dari HTML (ContentMonitor Tahap 3).

    Whitespace di dalam judul dikolaps menjadi satu spasi dan di-trim (selaras
    Req 4.3). Mengembalikan ``None`` bila tidak ada tag ``<title>`` atau bila
    isinya kosong setelah normalisasi. Tidak pernah mengangkat exception pada
    HTML malformed (Req 4.5) — kegagalan penguraian apa pun cukup menghasilkan
    ``None``.

    Args:
        html: Konten HTML mentah.

    Returns:
        Teks judul ternormalisasi, atau ``None``.
    """
    if not html:
        return None
    try:
        soup = _parse(html)
        tag = soup.find("title")
        if tag is None:
            return None
        text = _collapse_whitespace(tag.get_text(separator=" "))
        return text or None
    except Exception:
        return None


def extract_meta_description(html: str) -> Optional[str]:
    """Ekstrak isi ``<meta name="description" content="...">`` (Tahap 3).

    Pencarian tidak peka huruf besar/kecil pada atribut ``name`` (mis.
    ``Description``/``DESCRIPTION`` tetap dikenali). Whitespace pada isi
    dikolaps menjadi satu spasi dan di-trim (Req 4.3). Mengembalikan ``None``
    bila tag tidak ada, atribut ``content`` tidak ada, atau isinya kosong
    setelah normalisasi. Tidak pernah mengangkat exception pada HTML malformed
    (Req 4.5).

    Args:
        html: Konten HTML mentah.

    Returns:
        Isi meta description ternormalisasi, atau ``None``.
    """
    if not html:
        return None
    try:
        soup = _parse(html)
        for tag in soup.find_all("meta"):
            name = tag.get("name")
            if name is None or name.strip().lower() != "description":
                continue
            content = tag.get("content")
            if content is None:
                continue
            text = _collapse_whitespace(content)
            return text or None
        return None
    except Exception:
        return None


def split_sections(html: str) -> List[Section]:
    """Pisahkan konten menjadi section berdasarkan heading ``h1``–``h6`` (Req 6.4).

    Sebuah section adalah blok teks di bawah sebuah heading: teks heading itu
    sendiri beserta seluruh teks konten yang mengikutinya sampai heading
    berikutnya. Teks heading maupun konten dinormalisasi (kolaps whitespace +
    trim) agar perbandingan section stabil dan bebas whitespace berlebih.

    Konten sebelum heading pertama diabaikan (bukan bagian dari section mana
    pun). Keluaran deterministik dalam urutan dokumen dan berguna untuk
    mendeteksi section yang ditambahkan/dihapus sebagai selisih himpunan.

    Args:
        html: Konten HTML mentah.

    Returns:
        Daftar ``Section`` dalam urutan kemunculan heading pada dokumen.
    """
    if not html:
        return []
    soup = _parse(html)

    # Buang script & style agar teks konten section tidak tercemar (Req 4.1).
    for element in soup(_STRIP_ELEMENTS):
        element.decompose()

    headings = soup.find_all(_HEADING_TAGS)
    sections: List[Section] = []
    for heading in headings:
        heading_text = _collapse_whitespace(heading.get_text(separator=" "))
        # Kumpulkan teks seluruh saudara setelah heading sampai heading berikut.
        content_parts: List[str] = []
        for sibling in heading.next_siblings:
            name = getattr(sibling, "name", None)
            if name in _HEADING_TAGS:
                break
            text = (
                sibling.get_text(separator=" ")
                if name is not None
                else str(sibling)
            )
            if text:
                content_parts.append(text)
        content_text = _collapse_whitespace(" ".join(content_parts))
        sections.append(Section(heading=heading_text, content=content_text))
    return sections
