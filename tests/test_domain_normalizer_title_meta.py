"""Unit test untuk extract_title & extract_meta_description (ContentMonitor
Tahap 3, bagian C).

Memverifikasi:

- Whitespace dikolaps & di-trim.
- ``None`` bila tag/atribut tidak ada atau isinya kosong setelah normalisasi.
- Tidak pernah mengangkat exception pada HTML malformed atau kosong.
- Pencarian meta description tidak peka huruf besar/kecil pada atribut name.
"""

from __future__ import annotations

from monitoring.domain.normalizer import extract_meta_description, extract_title


# --------------------------------------------------------------------------- #
# extract_title
# --------------------------------------------------------------------------- #
def test_extract_title_basic():
    html = "<html><head><title>Judul Halaman</title></head><body></body></html>"
    assert extract_title(html) == "Judul Halaman"


def test_extract_title_collapses_whitespace():
    html = "<title>  Judul   dengan\n banyak   spasi  </title>"
    assert extract_title(html) == "Judul dengan banyak spasi"


def test_extract_title_none_when_no_tag():
    html = "<html><head></head><body><p>tanpa judul</p></body></html>"
    assert extract_title(html) is None


def test_extract_title_none_when_empty():
    html = "<title></title>"
    assert extract_title(html) is None


def test_extract_title_none_when_only_whitespace():
    html = "<title>   \n\t  </title>"
    assert extract_title(html) is None


def test_extract_title_empty_input_returns_none():
    assert extract_title("") is None
    assert extract_title(None) is None  # type: ignore[arg-type]


def test_extract_title_malformed_html_does_not_raise():
    html = "<title>Judul tak lengkap<html><body><p>isi"
    # Tidak boleh raise; hasil boleh apa saja selama tidak exception.
    result = extract_title(html)
    assert result is None or isinstance(result, str)


def test_extract_title_deeply_malformed_html_does_not_raise():
    html = "<<<>>>title<><<>Judul rusak</title]"
    result = extract_title(html)
    assert result is None or isinstance(result, str)


# --------------------------------------------------------------------------- #
# extract_meta_description
# --------------------------------------------------------------------------- #
def test_extract_meta_description_basic():
    html = '<meta name="description" content="Ini deskripsi halaman.">'
    assert extract_meta_description(html) == "Ini deskripsi halaman."


def test_extract_meta_description_collapses_whitespace():
    html = '<meta name="description" content="  Deskripsi\n  dengan   spasi  ">'
    assert extract_meta_description(html) == "Deskripsi dengan spasi"


def test_extract_meta_description_case_insensitive_name():
    html = '<meta name="Description" content="Konten Description">'
    assert extract_meta_description(html) == "Konten Description"

    html2 = '<meta name="DESCRIPTION" content="Konten UPPER">'
    assert extract_meta_description(html2) == "Konten UPPER"


def test_extract_meta_description_none_when_no_tag():
    html = "<html><head></head><body></body></html>"
    assert extract_meta_description(html) is None


def test_extract_meta_description_none_when_wrong_name():
    html = '<meta name="keywords" content="kata kunci">'
    assert extract_meta_description(html) is None


def test_extract_meta_description_none_when_no_content_attribute():
    html = '<meta name="description">'
    assert extract_meta_description(html) is None


def test_extract_meta_description_none_when_empty_content():
    html = '<meta name="description" content="">'
    assert extract_meta_description(html) is None


def test_extract_meta_description_none_when_only_whitespace_content():
    html = '<meta name="description" content="   ">'
    assert extract_meta_description(html) is None


def test_extract_meta_description_empty_input_returns_none():
    assert extract_meta_description("") is None
    assert extract_meta_description(None) is None  # type: ignore[arg-type]


def test_extract_meta_description_malformed_html_does_not_raise():
    html = '<meta name="description" content="tak lengkap<html><body>'
    result = extract_meta_description(html)
    assert result is None or isinstance(result, str)


def test_extract_meta_description_picks_first_matching_tag():
    html = (
        '<meta name="description" content="Pertama">'
        '<meta name="description" content="Kedua">'
    )
    assert extract_meta_description(html) == "Pertama"
