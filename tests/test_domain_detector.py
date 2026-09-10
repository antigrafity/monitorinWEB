"""Unit test untuk Change_Detector (task 6.1).

Menguji perilaku ``detect_changes`` pada contoh spesifik dan edge-case:
baseline, kondisi tidak-berubah, diff teks/link/section/gambar, deteksi gambar
URL-sama-isi-beda, dan pembuatan tepat satu Change_Event.

Property-based test menyeluruh berada pada task 6.2.
"""

from __future__ import annotations

from datetime import datetime

from monitoring.domain.detector import detect_changes
from monitoring.domain.models import Snapshot

CHECKED_AT = datetime(2024, 1, 1, 12, 0, 0)
LATER = datetime(2024, 1, 2, 12, 0, 0)


def _snap(
    *,
    text: str = "konten halaman",
    content_hash: str = "hash-a",
    links=None,
    image_hashes=None,
    url: str = "https://example.com/",
    website_id: str = "web-1",
    checked_at: datetime = CHECKED_AT,
    title=None,
    meta_description=None,
) -> Snapshot:
    return Snapshot(
        url=url,
        website_id=website_id,
        normalized_text=text,
        content_hash=content_hash,
        checked_at=checked_at,
        links=list(links) if links is not None else [],
        image_hashes=dict(image_hashes) if image_hashes is not None else {},
        title=title,
        meta_description=meta_description,
    )


def test_baseline_when_no_previous_snapshot():
    # Req 6.7: tanpa previous -> baseline, tidak ada Change_Event.
    result = detect_changes(None, _snap())
    assert result.changed is False
    assert result.diff is None
    assert result.change_event is None


def test_unchanged_when_hash_and_images_equal():
    # Req 6.6, 12.1: content_hash sama & image_hash sama -> tidak berubah.
    prev = _snap(content_hash="h", image_hashes={"https://example.com/a.png": "i1"})
    cur = _snap(
        content_hash="h",
        image_hashes={"https://example.com/a.png": "i1"},
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    assert result.changed is False
    assert result.diff is None
    assert result.change_event is None


def test_text_change_produces_added_and_removed_lines():
    # Req 6.2: content_hash beda -> diff baris teks added/removed.
    prev = _snap(text="baris lama", content_hash="h1")
    cur = _snap(text="baris baru", content_hash="h2", checked_at=LATER)
    result = detect_changes(prev, cur)
    assert result.changed is True
    assert result.diff is not None
    assert "baris baru" in result.diff.text_added
    assert "baris lama" in result.diff.text_removed


def test_link_change_detected_by_url_set_difference():
    # Req 6.3: link added/removed berdasarkan himpunan URL.
    prev = _snap(
        content_hash="h1",
        links=["https://example.com/a", "https://example.com/b"],
    )
    cur = _snap(
        content_hash="h2",
        links=["https://example.com/b", "https://example.com/c"],
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    assert result.diff.links_added == ["https://example.com/c"]
    assert result.diff.links_removed == ["https://example.com/a"]


def test_section_change_detected_from_provided_blocks():
    # Req 6.4: section added/removed sebagai selisih himpunan blok.
    prev = _snap(content_hash="h1")
    cur = _snap(content_hash="h2", checked_at=LATER)
    result = detect_changes(
        prev,
        cur,
        previous_sections=["Judul A\nisi a", "Judul B\nisi b"],
        current_sections=["Judul B\nisi b", "Judul C\nisi c"],
    )
    assert result.diff.sections_added == ["Judul C\nisi c"]
    assert result.diff.sections_removed == ["Judul A\nisi a"]


def test_image_added_and_removed_by_url():
    # Req 7.3: gambar added/removed berdasarkan keberadaan URL.
    prev = _snap(
        content_hash="h1",
        image_hashes={"https://example.com/a.png": "i1"},
    )
    cur = _snap(
        content_hash="h2",
        image_hashes={"https://example.com/b.png": "i2"},
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    assert result.diff.images_added == ["https://example.com/b.png"]
    assert result.diff.images_removed == ["https://example.com/a.png"]


def test_image_same_url_different_hash_is_changed():
    # Req 7.4: URL gambar sama tetapi image_hash beda -> changed.
    # content_hash tetap sama; hanya isi gambar yang berubah.
    prev = _snap(content_hash="h", image_hashes={"https://example.com/a.png": "i1"})
    cur = _snap(
        content_hash="h",
        image_hashes={"https://example.com/a.png": "i2"},
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    assert result.changed is True
    assert result.diff.images_changed == ["https://example.com/a.png"]
    assert result.diff.images_added == []
    assert result.diff.images_removed == []


def test_change_creates_exactly_one_event_with_url_time_and_diff():
    # Req 6.5, 12.2: perubahan -> tepat satu Change_Event dengan URL, waktu, Diff.
    prev = _snap(text="lama", content_hash="h1", url="https://example.com/page")
    cur = _snap(
        text="baru",
        content_hash="h2",
        url="https://example.com/page",
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    event = result.change_event
    assert event is not None
    assert event.url == "https://example.com/page"
    assert event.detected_at == LATER  # default = current.checked_at
    assert event.diff is result.diff
    assert event.id  # id tidak kosong


def test_detected_at_and_event_id_can_be_provided():
    prev = _snap(text="lama", content_hash="h1")
    cur = _snap(text="baru", content_hash="h2", checked_at=LATER)
    custom_time = datetime(2030, 5, 5, 5, 5, 5)
    result = detect_changes(
        prev, cur, detected_at=custom_time, event_id="fixed-id"
    )
    assert result.change_event.detected_at == custom_time
    assert result.change_event.id == "fixed-id"


def test_summary_counts_match_diff_lengths():
    # Ringkasan konsisten dengan panjang daftar diff.
    prev = _snap(
        text="a",
        content_hash="h1",
        links=["https://example.com/x"],
        image_hashes={"https://example.com/i.png": "i1"},
    )
    cur = _snap(
        text="b",
        content_hash="h2",
        links=["https://example.com/y"],
        image_hashes={"https://example.com/i.png": "i2"},
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    summary = result.change_event.summary
    diff = result.diff
    assert summary.text_added == len(diff.text_added)
    assert summary.text_removed == len(diff.text_removed)
    assert summary.links_added == len(diff.links_added)
    assert summary.links_removed == len(diff.links_removed)
    assert summary.images_changed == len(diff.images_changed)


# --------------------------------------------------------------------------- #
# ContentMonitor Tahap 3: perubahan title/meta_description SAJA harus
# terdeteksi meski content_hash & image_hashes identik (title/meta TIDAK ikut
# ke dalam normalized_text/content_hash).
# --------------------------------------------------------------------------- #
def test_only_title_changed_is_detected_despite_identical_hash_and_images():
    """Perubahan HANYA pada title -> tetap 'changed', diff.title_changed terisi."""
    prev = _snap(
        text="konten sama",
        content_hash="h-sama",
        image_hashes={"https://example.com/a.png": "i1"},
        title="Judul Lama",
    )
    cur = _snap(
        text="konten sama",
        content_hash="h-sama",
        image_hashes={"https://example.com/a.png": "i1"},
        title="Judul Baru",
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    assert result.changed is True
    assert result.diff is not None
    assert result.diff.title_changed == ("Judul Lama", "Judul Baru")
    assert result.diff.meta_changed is None
    assert result.change_event is not None


def test_only_meta_description_changed_is_detected():
    """Perubahan HANYA pada meta description -> tetap 'changed'."""
    prev = _snap(
        text="konten sama",
        content_hash="h-sama",
        meta_description="Meta lama",
    )
    cur = _snap(
        text="konten sama",
        content_hash="h-sama",
        meta_description="Meta baru",
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    assert result.changed is True
    assert result.diff.meta_changed == ("Meta lama", "Meta baru")
    assert result.diff.title_changed is None


def test_unchanged_title_and_meta_do_not_affect_unchanged_gate():
    """Title/meta identik & hash identik -> tetap 'tidak berubah' (Req 6.1, 6.6)."""
    prev = _snap(
        text="konten sama",
        content_hash="h-sama",
        title="Judul Sama",
        meta_description="Meta Sama",
    )
    cur = _snap(
        text="konten sama",
        content_hash="h-sama",
        title="Judul Sama",
        meta_description="Meta Sama",
        checked_at=LATER,
    )
    result = detect_changes(prev, cur)
    assert result.changed is False
    assert result.diff is None
    assert result.change_event is None


def test_title_and_content_both_change_reports_both_in_diff():
    """Judul & konten teks berubah bersamaan -> Diff memuat keduanya."""
    prev = _snap(text="teks lama", content_hash="h1", title="Judul Lama")
    cur = _snap(
        text="teks baru", content_hash="h2", title="Judul Baru", checked_at=LATER
    )
    result = detect_changes(prev, cur)
    assert result.changed is True
    assert result.diff.title_changed == ("Judul Lama", "Judul Baru")
    assert "teks baru" in result.diff.text_added
