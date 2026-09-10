"""Unit & property test untuk klasifikasi tipe perubahan (ContentMonitor Tahap 1).

Menguji ``classify_change`` (added/removed/updated) dan ``describe_change``
(deskripsi 1 baris Bahasa Indonesia) di ``monitoring.domain.classify``.

Kompatibel Python 3.9.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.domain.classify import (
    CHANGE_TYPE_ADDED,
    CHANGE_TYPE_REMOVED,
    CHANGE_TYPE_UPDATED,
    classify_change,
    describe_change,
)
from monitoring.domain.models import Diff


# --- Unit test: kasus contoh spesifik ------------------------------------- #


def test_only_additions_classified_as_added():
    diff = Diff(text_added=["baris baru"], links_added=["https://x.com/a"])
    assert classify_change(diff) == CHANGE_TYPE_ADDED


def test_only_removals_classified_as_removed():
    diff = Diff(text_removed=["baris lama"], sections_removed=["Bagian Lama"])
    assert classify_change(diff) == CHANGE_TYPE_REMOVED


def test_mixed_additions_and_removals_classified_as_updated():
    diff = Diff(text_added=["baru"], text_removed=["lama"])
    assert classify_change(diff) == CHANGE_TYPE_UPDATED


def test_images_changed_always_updated_even_if_only_additions_otherwise():
    diff = Diff(text_added=["baru"], images_changed=["https://x.com/g.png"])
    assert classify_change(diff) == CHANGE_TYPE_UPDATED


def test_empty_diff_falls_back_to_updated():
    assert classify_change(Diff()) == CHANGE_TYPE_UPDATED


def test_describe_single_text_pair_shows_from_to():
    diff = Diff(text_removed=["harga lama 100"], text_added=["harga baru 150"])
    desc = describe_change(diff)
    assert "harga lama 100" in desc
    assert "harga baru 150" in desc
    assert "diubah dari" in desc.lower()


def test_describe_only_additions():
    diff = Diff(text_added=["satu", "dua", "tiga"])
    desc = describe_change(diff)
    assert "Menambahkan 3 bagian teks baru" in desc


def test_describe_only_removals():
    diff = Diff(text_removed=["satu", "dua"])
    desc = describe_change(diff)
    assert "Menghapus 2 bagian teks" in desc


def test_describe_images_changed():
    diff = Diff(images_changed=["https://x.com/a.png", "https://x.com/b.png"])
    desc = describe_change(diff)
    assert "2 gambar diperbarui" in desc


def test_describe_links_added_and_removed():
    diff = Diff(links_added=["https://x.com/1"], links_removed=["https://x.com/2"])
    desc = describe_change(diff)
    assert "link ditambahkan" in desc
    assert "link dihapus" in desc


def test_describe_truncates_long_text_with_ellipsis():
    long_old = "a" * 100
    long_new = "b" * 100
    diff = Diff(text_removed=[long_old], text_added=[long_new])
    desc = describe_change(diff)
    assert "…" in desc
    # Tidak lebih dari 60 karakter mentah sebelum "…" untuk tiap potongan.
    assert "a" * 61 not in desc


def test_describe_combines_at_most_two_clauses():
    diff = Diff(
        text_added=["baru"],
        images_changed=["https://x.com/g.png"],
        links_added=["https://x.com/l1"],
    )
    desc = describe_change(diff)
    assert desc.count("·") <= 1  # maksimal 2 klausa -> maksimal 1 pemisah


def test_describe_empty_diff_has_no_details_message():
    assert describe_change(Diff()) == "Tidak ada rincian perubahan."


# --- Property-based test (Hypothesis, >= 100 iterasi) --------------------- #

_text_lines = st.lists(st.text(min_size=1, max_size=40), max_size=5)
_urls = st.lists(
    st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=15,
    ).map(lambda s: "https://example.com/" + s),
    max_size=5,
)


@st.composite
def _diffs(draw):
    return Diff(
        text_added=draw(_text_lines),
        text_removed=draw(_text_lines),
        links_added=draw(_urls),
        links_removed=draw(_urls),
        sections_added=draw(_text_lines),
        sections_removed=draw(_text_lines),
        images_added=draw(_urls),
        images_removed=draw(_urls),
        images_changed=draw(_urls),
    )


# Feature: contentmonitor, Property: klasifikasi tipe perubahan
@settings(max_examples=100)
@given(_diffs())
def test_property_classify_change_always_one_of_three_values(diff):
    """*For any* Diff, hasil classify_change selalu salah satu dari 3 nilai."""
    result = classify_change(diff)
    assert result in (CHANGE_TYPE_ADDED, CHANGE_TYPE_REMOVED, CHANGE_TYPE_UPDATED)


# Feature: contentmonitor, Property: klasifikasi tipe perubahan
@settings(max_examples=100)
@given(_diffs())
def test_property_only_additions_always_added(diff):
    """*For any* Diff yang hanya berisi penambahan, hasilnya selalu "added"."""
    only_additions = Diff(
        text_added=diff.text_added,
        links_added=diff.links_added,
        sections_added=diff.sections_added,
        images_added=diff.images_added,
    )
    # Pastikan benar-benar ada setidaknya satu penambahan agar tidak jatuh ke
    # kasus Diff kosong (fallback "updated").
    if (
        only_additions.text_added
        or only_additions.links_added
        or only_additions.sections_added
        or only_additions.images_added
    ):
        assert classify_change(only_additions) == CHANGE_TYPE_ADDED


# Feature: contentmonitor, Property: klasifikasi tipe perubahan
@settings(max_examples=100)
@given(_diffs())
def test_property_only_removals_always_removed(diff):
    """*For any* Diff yang hanya berisi penghapusan, hasilnya selalu "removed"."""
    only_removals = Diff(
        text_removed=diff.text_removed,
        links_removed=diff.links_removed,
        sections_removed=diff.sections_removed,
        images_removed=diff.images_removed,
    )
    if (
        only_removals.text_removed
        or only_removals.links_removed
        or only_removals.sections_removed
        or only_removals.images_removed
    ):
        assert classify_change(only_removals) == CHANGE_TYPE_REMOVED


# Feature: contentmonitor, Property: klasifikasi tipe perubahan
@settings(max_examples=100)
@given(_diffs())
def test_property_describe_change_never_raises_and_returns_str(diff):
    """describe_change selalu mengembalikan string tanpa exception untuk Diff apa pun."""
    desc = describe_change(diff)
    assert isinstance(desc, str)
    assert len(desc) > 0
