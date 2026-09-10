"""Property-based tests untuk Change_Detector (task 6.2).

Menguji Correctness Property 15, 16, 17, 18, 19, 21, dan 22 pada design memakai
Hypothesis (minimal 100 iterasi per properti). Berisi generator untuk pasangan
``Snapshot`` (teks, link, image_hashes) dan daftar blok section.

Semua assertion diselaraskan dengan implementasi ``detect_changes`` pada
``monitoring.domain.detector``:

- Gerbang "tidak berubah" memakai ``content_hash`` sama DAN seluruh
  ``image_hashes`` sama. Pada properti yang menguji perhitungan Diff (15, 16,
  17, 19, 21, 22) generator selalu memaksa ``content_hash`` berbeda agar
  gerbang tidak memotong dan Diff benar-benar dihitung.
- Diff teks memakai ``difflib`` (berbasis baris), sehingga properti diformulasi
  sebagai relasi subset yang berlaku untuk keluaran ``difflib`` mana pun.
"""

from __future__ import annotations

from datetime import datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from monitoring.domain.detector import detect_changes
from monitoring.domain.models import Diff, Snapshot

CHECKED_AT = datetime(2024, 1, 1, 12, 0, 0)
LATER = datetime(2024, 1, 2, 12, 0, 0)

URL = "https://example.com/page"
WEBSITE_ID = "web-1"

# --------------------------------------------------------------------------- #
# Strategi generator
# --------------------------------------------------------------------------- #
# Baris teks dari alfabet kecil agar difflib menghasilkan pencocokan yang
# menarik (banyak baris tumpang tindih) dan bebas dari batas baris Unicode.
_LINE = st.text(alphabet="abcdefg 12345", max_size=10)
_TEXT = st.lists(_LINE, max_size=6).map(lambda xs: "\n".join(xs))

# Kumpulan kecil URL/hasil agar himpunan link/gambar sering tumpang tindih.
_URL_POOL = [
    "https://example.com/a",
    "https://example.com/b",
    "https://example.com/c",
    "https://example.com/d",
]
_LINKS = st.lists(st.sampled_from(_URL_POOL), max_size=4)

_IMG_POOL = [
    "https://example.com/1.png",
    "https://example.com/2.png",
    "https://example.com/3.png",
]
_HASH_POOL = ["ha", "hb", "hc"]
_IMAGES = st.dictionaries(
    st.sampled_from(_IMG_POOL), st.sampled_from(_HASH_POOL), max_size=3
)

_SEC_POOL = [
    "Judul A\nisi a",
    "Judul B\nisi b",
    "Judul C\nisi c",
    "Judul D\nisi d",
]
_SECTIONS = st.lists(st.sampled_from(_SEC_POOL), max_size=4)

_HASH = st.text(alphabet="0123456789abcdef", min_size=1, max_size=8)


def _result_diff(result) -> Diff:
    """Kembalikan Diff hasil deteksi; ``Diff()`` kosong bila ``None``.

    Cabang defensif ``detect_changes`` mengembalikan ``diff=None`` hanya ketika
    seluruh dimensi Diff kosong, sehingga memperlakukannya sebagai Diff kosong
    menjaga relasi himpunan tetap valid.
    """
    return result.diff if result.diff is not None else Diff()


@st.composite
def _changed_pairs(draw):
    """Pasangan (previous, current) dengan ``content_hash`` dijamin berbeda.

    Berbagi ``url``/``website_id`` sehingga Change_Event dapat diverifikasi.
    Field lain (teks, link, gambar) acak dan independen.
    """
    base_hash = draw(_HASH)
    prev = Snapshot(
        url=URL,
        website_id=WEBSITE_ID,
        normalized_text=draw(_TEXT),
        content_hash=base_hash,
        checked_at=CHECKED_AT,
        links=draw(_LINKS),
        image_hashes=draw(_IMAGES),
    )
    cur = Snapshot(
        url=URL,
        website_id=WEBSITE_ID,
        normalized_text=draw(_TEXT),
        content_hash=base_hash + "!",  # dijamin berbeda -> gerbang tidak memotong
        checked_at=LATER,
        links=draw(_LINKS),
        image_hashes=draw(_IMAGES),
    )
    return prev, cur


@st.composite
def _unchanged_pairs(draw):
    """Pasangan yang memenuhi kondisi tidak-berubah (Property 18).

    Salah satu dari: ``previous is None`` (baseline) ATAU ``content_hash`` sama
    dan seluruh ``image_hashes`` identik.
    """
    cur = Snapshot(
        url=URL,
        website_id=WEBSITE_ID,
        normalized_text=draw(_TEXT),
        content_hash=draw(_HASH),
        checked_at=LATER,
        links=draw(_LINKS),
        image_hashes=draw(_IMAGES),
    )
    if draw(st.booleans()):
        return None, cur
    # previous dengan content_hash & image_hashes identik; teks/link boleh beda
    # karena gerbang tidak bergantung padanya.
    prev = Snapshot(
        url=URL,
        website_id=WEBSITE_ID,
        normalized_text=draw(_TEXT),
        content_hash=cur.content_hash,
        checked_at=CHECKED_AT,
        links=draw(_LINKS),
        image_hashes=dict(cur.image_hashes),
    )
    return prev, cur


@st.composite
def _guaranteed_change_pairs(draw):
    """Pasangan yang dijamin mengandung perubahan (Property 19).

    ``current`` memuat link unik yang tidak ada pada ``previous`` sehingga
    ``links_added`` tidak kosong -> pasti terdeteksi berubah.
    """
    base_hash = draw(_HASH)
    prev_links = draw(_LINKS)  # dari pool yang tidak memuat sentinel
    sentinel = "https://example.com/unique-added"
    cur_links = draw(_LINKS) + [sentinel]
    prev = Snapshot(
        url=URL,
        website_id=WEBSITE_ID,
        normalized_text=draw(_TEXT),
        content_hash=base_hash,
        checked_at=CHECKED_AT,
        links=prev_links,
        image_hashes=draw(_IMAGES),
    )
    cur = Snapshot(
        url=URL,
        website_id=WEBSITE_ID,
        normalized_text=draw(_TEXT),
        content_hash=base_hash + "!",
        checked_at=LATER,
        links=cur_links,
        image_hashes=draw(_IMAGES),
    )
    return prev, cur


_SLOW = [HealthCheck.too_slow, HealthCheck.data_too_large]


# --------------------------------------------------------------------------- #
# Property 15: Diff teks added/removed benar
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 15: Diff teks added/removed benar
@settings(max_examples=150, suppress_health_check=_SLOW)
@given(_changed_pairs())
def test_text_added_removed_correct(pair):
    """Validates: Requirements 6.2

    ``text_added`` hanya memuat baris dari teks baru, ``text_removed`` hanya
    memuat baris dari teks lama; dan baris yang benar-benar baru/hilang (selisih
    himpunan baris) pasti tercakup pada added/removed.
    """
    previous, current = pair
    result = detect_changes(previous, current)
    diff = _result_diff(result)

    old_lines = previous.normalized_text.splitlines()
    new_lines = current.normalized_text.splitlines()

    # added berasal dari teks baru, removed berasal dari teks lama.
    assert set(diff.text_added) <= set(new_lines)
    assert set(diff.text_removed) <= set(old_lines)

    # Baris yang ada di baru tapi tidak di lama pasti ada pada added; sebaliknya
    # untuk removed.
    assert (set(new_lines) - set(old_lines)) <= set(diff.text_added)
    assert (set(old_lines) - set(new_lines)) <= set(diff.text_removed)


# --------------------------------------------------------------------------- #
# Property 16: Deteksi perubahan link berbasis himpunan URL
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 16: Deteksi perubahan link berbasis himpunan URL
@settings(max_examples=150, suppress_health_check=_SLOW)
@given(_changed_pairs())
def test_links_added_removed_are_set_difference(pair):
    """Validates: Requirements 6.3

    ``links_added`` sama dengan himpunan link baru \\ lama, dan ``links_removed``
    sama dengan lama \\ baru.
    """
    previous, current = pair
    result = detect_changes(previous, current)
    diff = _result_diff(result)

    assert set(diff.links_added) == set(current.links) - set(previous.links)
    assert set(diff.links_removed) == set(previous.links) - set(current.links)


# --------------------------------------------------------------------------- #
# Property 17: Deteksi perubahan section berbasis heading
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 17: Deteksi perubahan section berbasis heading
@settings(max_examples=150, suppress_health_check=_SLOW)
@given(_changed_pairs(), _SECTIONS, _SECTIONS)
def test_sections_added_removed_are_set_difference(pair, prev_sections, cur_sections):
    """Validates: Requirements 6.4

    ``sections_added``/``sections_removed`` dihitung sebagai selisih himpunan
    blok section yang diberikan.
    """
    previous, current = pair
    result = detect_changes(
        previous,
        current,
        previous_sections=prev_sections,
        current_sections=cur_sections,
    )
    diff = _result_diff(result)

    assert set(diff.sections_added) == set(cur_sections) - set(prev_sections)
    assert set(diff.sections_removed) == set(prev_sections) - set(cur_sections)


# --------------------------------------------------------------------------- #
# Property 18: Kondisi tidak-berubah tidak menghasilkan Change_Event
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 18: Kondisi tidak-berubah tidak menghasilkan Change_Event
@settings(max_examples=150, suppress_health_check=_SLOW)
@given(_unchanged_pairs())
def test_unchanged_produces_no_change_event(pair):
    """Validates: Requirements 6.1, 6.6, 6.7, 12.1

    Tanpa previous, atau content_hash sama & seluruh image_hash sama ->
    ``changed = False`` dan tidak ada Change_Event (tidak memicu notifikasi).
    """
    previous, current = pair
    result = detect_changes(previous, current)
    assert result.changed is False
    assert result.change_event is None
    assert result.diff is None


# --------------------------------------------------------------------------- #
# Property 19: Perubahan menghasilkan tepat satu Change_Event
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 19: Perubahan menghasilkan tepat satu Change_Event
@settings(max_examples=150, suppress_health_check=_SLOW)
@given(_guaranteed_change_pairs(), st.datetimes(), st.text(min_size=1, max_size=12))
def test_change_produces_exactly_one_event(pair, detected_at, event_id):
    """Validates: Requirements 6.5, 12.2

    Bila ada perbedaan apa pun, tepat satu Change_Event dibuat yang mencatat
    URL halaman, waktu deteksi, dan Diff.
    """
    previous, current = pair
    result = detect_changes(
        previous, current, detected_at=detected_at, event_id=event_id
    )
    assert result.changed is True
    assert result.diff is not None
    event = result.change_event
    assert event is not None
    assert event.url == current.url
    assert event.detected_at == detected_at
    assert event.diff is result.diff


# --------------------------------------------------------------------------- #
# Property 21: Deteksi gambar added/removed berbasis URL
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 21: Deteksi gambar added/removed berbasis URL
@settings(max_examples=150, suppress_health_check=_SLOW)
@given(_changed_pairs())
def test_images_added_removed_by_url(pair):
    """Validates: Requirements 7.3

    ``images_added`` = URL hanya pada Snapshot baru; ``images_removed`` = URL
    hanya pada Snapshot lama.
    """
    previous, current = pair
    result = detect_changes(previous, current)
    diff = _result_diff(result)

    prev_urls = set(previous.image_hashes)
    cur_urls = set(current.image_hashes)
    assert set(diff.images_added) == cur_urls - prev_urls
    assert set(diff.images_removed) == prev_urls - cur_urls


# --------------------------------------------------------------------------- #
# Property 22: Deteksi gambar dengan URL sama tetapi isi berbeda
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 22: Deteksi gambar dengan URL sama tetapi isi berbeda
@settings(max_examples=150, suppress_health_check=_SLOW)
@given(_changed_pairs())
def test_images_changed_iff_hash_differs(pair):
    """Validates: Requirements 7.4

    Untuk URL gambar yang ada pada kedua Snapshot, URL masuk ``images_changed``
    jika dan hanya jika Image_Hash-nya berbeda.
    """
    previous, current = pair
    result = detect_changes(previous, current)
    diff = _result_diff(result)

    changed = set(diff.images_changed)
    common = set(previous.image_hashes) & set(current.image_hashes)
    for url in common:
        differs = previous.image_hashes[url] != current.image_hashes[url]
        assert (url in changed) == differs
    # images_changed hanya boleh berisi URL yang ada di kedua Snapshot.
    assert changed <= common
