"""Unit test lapisan presentasi (tampilan) Web Dashboard.

Menguji hasil render template setelah dashboard didesain ulang, tanpa
menyentuh logika bisnis:

- kartu ringkasan menampilkan hitungan yang benar: total website dipantau,
  berhasil, gagal, dan belum pernah diperiksa (Req 10.1, 10.2, 10.3);
- kontrol per baris ("Cek Sekarang" dan "Edit") dirender untuk setiap
  Monitored_Website dengan URL aksi yang tepat (Req 8.4, 1.3);
- Polling_Interval ditampilkan sebagai "Global" bila website memakai
  Polling_Interval global (``poll_interval_seconds is None``) (Req 1.6, 8.3);
- halaman detail perubahan menampilkan perbandingan sisi-ke-sisi dengan region
  "Sebelum" (baris teks dihapus) dan "Sesudah" (baris teks ditambahkan)
  (Req 10.5);
- helper pemformatan waktu relatif & durasi bekerja sesuai harapan;
- stylesheet statis dilayani dari ``/static/style.css`` (aset lokal, offline).

Kompatibel Python 3.9.
"""

import re
from datetime import datetime, timedelta
from typing import Optional

from fastapi.testclient import TestClient

from monitoring.domain.models import (
    ChangeEvent,
    ChangeSummary,
    Diff,
    WebsiteConfig,
)
from monitoring.infra.repository import WebsiteOverview, WebsiteStatusView
from monitoring.web.app import (
    create_app,
    format_duration_id,
    format_interval_id,
    format_relative_id,
)


def _view(
    website_id: str,
    domain: str,
    name: str,
    last_checked_at: Optional[datetime] = None,
    last_status: Optional[str] = None,
    poll_interval_seconds: Optional[int] = None,
) -> WebsiteStatusView:
    return WebsiteStatusView(
        website=WebsiteConfig(
            id=website_id,
            domain=domain,
            name=name,
            poll_interval_seconds=poll_interval_seconds,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
        ),
        last_checked_at=last_checked_at,
        last_status=last_status,
    )


class FakeRepository:
    """Repository palsu yang mengembalikan daftar view yang telah ditentukan."""

    def __init__(self, views=None, events=None, websites=None):
        self._views = views or []
        self._events = {e.id: e for e in (events or [])}
        self._websites = {w.id: w for w in (websites or [])}

    async def list_websites_with_status(self):
        return list(self._views)

    async def get_website(self, website_id):
        return self._websites.get(website_id)

    async def list_change_events(self, website_id):
        items = [e for e in self._events.values() if e.website_id == website_id]
        return sorted(items, key=lambda e: e.detected_at, reverse=True)

    async def get_change_event(self, event_id):
        return self._events.get(event_id)

    async def website_stats(self):
        """Turunkan WebsiteOverview dari daftar view (halaman Websites)."""
        return [
            WebsiteOverview(
                website=v.website,
                last_checked_at=v.last_checked_at,
                last_status=v.last_status,
                pages_count=0,
                last_change_at=None,
                changes_last_7_days=0,
                daily_counts_7_days=[],
            )
            for v in self._views
        ]


def _client(repository) -> TestClient:
    return TestClient(create_app(repository))


def _summary(body: str, key: str) -> int:
    """Ambil angka pada kartu ringkasan bertanda ``data-summary="key"``."""
    match = re.search(
        r'data-summary="' + key + r'">\s*(\d+)\s*<', body
    )
    assert match is not None, "kartu ringkasan '{0}' tidak dirender".format(key)
    return int(match.group(1))


def _cells(body: str, side: str):
    """Kumpulkan teks tiap sel perbandingan pada sisi tertentu.

    Sel kosong (penyeimbang) tidak memiliki ``span.diff-text`` sehingga
    otomatis terlewat.
    """
    texts = []
    for matched_side, content in re.findall(
        r'data-side="(before|after)"(.*?)</div>', body, re.S
    ):
        if matched_side != side:
            continue
        inner = re.search(r'<span class="diff-text">(.*?)</span>', content, re.S)
        if inner is not None:
            texts.append(inner.group(1))
    return texts


# --- Kartu ringkasan ------------------------------------------------------ #


def test_summary_cards_show_correct_counts():
    """Kartu ringkasan menghitung total/berhasil/gagal/belum diperiksa."""
    now = datetime.now()
    repo = FakeRepository(
        [
            _view("id-1", "a.com", "A", now - timedelta(minutes=5), "success"),
            _view("id-2", "b.com", "B", now - timedelta(hours=2), "success"),
            _view("id-3", "c.com", "C", now - timedelta(days=1), "failure"),
            _view("id-4", "d.com", "D", None, None),
        ]
    )
    body = _client(repo).get("/websites").text

    assert _summary(body, "total") == 4
    assert _summary(body, "success") == 2
    assert _summary(body, "failure") == 1
    assert _summary(body, "never") == 1


def test_summary_cards_all_zero_when_no_websites():
    """Tanpa website, seluruh kartu ringkasan bernilai 0 (Req 10.6)."""
    body = _client(FakeRepository([])).get("/websites").text
    assert _summary(body, "total") == 0
    assert _summary(body, "success") == 0
    assert _summary(body, "failure") == 0
    assert _summary(body, "never") == 0
    assert "Belum ada website yang dipantau" in body


# --- Kontrol per baris ---------------------------------------------------- #


def test_row_controls_render_with_correct_action_urls():
    """Tombol "Cek Sekarang" & form Edit dirender per website (Req 8.4, 1.3)."""
    repo = FakeRepository(
        [
            _view("id-1", "a.com", "A", None, None),
            _view("id-2", "b.com", "B", datetime.now(), "success"),
        ]
    )
    body = _client(repo).get("/websites").text

    for website_id in ("id-1", "id-2"):
        # a) Cek Sekarang -> POST /websites/{id}/check lewat fetch().
        assert 'data-check-url="/websites/{0}/check"'.format(website_id) in body
        # b) Edit -> form inline ke /websites/{id}/edit.
        assert 'action="/websites/{0}/edit"'.format(website_id) in body
        # c) Riwayat.
        assert 'href="/websites/{0}"'.format(website_id) in body
        # d) Hapus (dengan konfirmasi JS).
        assert 'action="/websites/{0}/delete"'.format(website_id) in body

    assert "Cek Sekarang" in body
    assert body.count("Cek Sekarang") >= 2
    # Konfirmasi penghapusan tetap ada.
    assert "data-confirm=" in body
    # Field edit prefilled dengan nama saat ini.
    assert 'name="name"' in body
    assert 'name="poll_interval_seconds"' in body


def test_edit_form_prefills_current_values():
    """Form edit terisi nama & interval saat ini; kosong bila memakai global."""
    repo = FakeRepository(
        [
            _view("id-1", "a.com", "Situs A", None, None, poll_interval_seconds=600),
            _view("id-2", "b.com", "Situs B", None, None),
        ]
    )
    body = _client(repo).get("/websites").text
    assert 'value="Situs A"' in body
    assert 'value="600"' in body
    # Website tanpa interval khusus -> field interval dibiarkan kosong.
    assert 'value=""' in body


# --- Polling_Interval efektif -------------------------------------------- #


def test_interval_column_shows_global_when_none():
    """poll_interval_seconds None -> ditampilkan sebagai "Global" (Req 1.6/8.3)."""
    repo = FakeRepository(
        [_view("id-1", "global.com", "Global", None, None, None)]
    )
    body = _client(repo).get("/websites").text
    assert "Global (6 jam)" in body


def test_interval_column_shows_custom_interval_human_friendly():
    """Interval khusus ditampilkan dalam bentuk mudah dibaca."""
    repo = FakeRepository(
        [
            _view("id-1", "a.com", "A", None, None, 600),
            _view("id-2", "b.com", "B", None, None, 90),
        ]
    )
    body = _client(repo).get("/websites").text
    assert "10 menit" in body
    assert "1 menit 30 detik" in body


def test_format_helpers():
    """Helper durasi & waktu relatif memformat sesuai harapan."""
    assert format_duration_id(21600) == "6 jam"
    assert format_duration_id(600) == "10 menit"
    assert format_duration_id(90) == "1 menit 30 detik"
    assert format_interval_id(None) == "Global (6 jam)"
    assert format_interval_id(120) == "2 menit"

    now = datetime.now()
    assert format_relative_id(now - timedelta(minutes=5)) == "5 menit lalu"
    assert format_relative_id(now - timedelta(hours=3)) == "3 jam lalu"
    assert format_relative_id(now - timedelta(days=2)) == "2 hari lalu"
    assert format_relative_id(None) == ""


def test_last_checked_shows_absolute_and_relative_time():
    """Pemeriksaan terakhir ditampilkan absolut + relatif (Req 10.1)."""
    checked = datetime(2026, 7, 30, 14, 3, 0)
    repo = FakeRepository([_view("id-1", "a.com", "A", checked, "success")])
    body = _client(repo).get("/websites").text
    assert "30 Jul 2026 14:03" in body
    assert "lalu)" in body


# --- Perbandingan sisi-ke-sisi pada detail perubahan --------------------- #


def _event(event_id="evt-1", website_id="id-1", diff=None):
    diff = diff or Diff()
    return ChangeEvent(
        id=event_id,
        website_id=website_id,
        url="https://example.com/halaman",
        detected_at=datetime(2024, 1, 3, 9, 0, 0),
        diff=diff,
        summary=ChangeSummary(
            text_added=len(diff.text_added),
            text_removed=len(diff.text_removed),
            links_added=len(diff.links_added),
            links_removed=len(diff.links_removed),
            images_changed=len(diff.images_changed),
        ),
    )


def test_event_detail_renders_side_by_side_before_after():
    """Perbandingan "Sebelum" (dihapus) vs "Sesudah" (ditambah) (Req 10.5)."""
    diff = Diff(
        text_removed=["harga lama 100", "stok lama 5"],
        text_added=["harga baru 150", "stok baru 9"],
    )
    repo = FakeRepository(events=[_event(diff=diff)])
    body = _client(repo).get("/events/evt-1").text

    # Region "Sebelum" dan "Sesudah" hadir.
    assert "Sebelum" in body
    assert "Sesudah" in body

    before = _cells(body, "before")
    after = _cells(body, "after")
    assert before == ["harga lama 100", "stok lama 5"]
    assert after == ["harga baru 150", "stok baru 9"]
    # Pewarnaan hapus/tambah dibedakan.
    assert "diff-removed" in body
    assert "diff-added" in body


def test_event_detail_pairs_rows_with_empty_placeholder():
    """Sisi yang lebih pendek diberi sel kosong sebagai penyeimbang (Req 10.5)."""
    diff = Diff(text_removed=["hanya satu baris lama"], text_added=["baru 1", "baru 2"])
    repo = FakeRepository(events=[_event(diff=diff)])
    body = _client(repo).get("/events/evt-1").text

    assert _cells(body, "before") == ["hanya satu baris lama"]
    assert _cells(body, "after") == ["baru 1", "baru 2"]
    # Satu sel penyeimbang pada sisi "Sebelum".
    assert body.count('class="diff-line diff-empty" data-side="before"') == 1


def test_event_detail_image_thumbnails_rendered_lazily():
    """Gambar dirender sebagai pratinjau thumbnail dengan lazy loading."""
    diff = Diff(
        images_added=["https://example.com/baru.png"],
        images_changed=["https://example.com/berubah.png"],
    )
    repo = FakeRepository(events=[_event(diff=diff)])
    body = _client(repo).get("/events/evt-1").text

    assert 'src="https://example.com/baru.png"' in body
    assert 'src="https://example.com/berubah.png"' in body
    assert 'loading="lazy"' in body
    # Fallback anggun bila gambar gagal dimuat.
    assert "thumb-fallback" in body


def test_event_detail_hides_empty_change_groups():
    """Kelompok perubahan tanpa isi tidak dirender."""
    diff = Diff(text_added=["hanya teks"])
    repo = FakeRepository(events=[_event(diff=diff)])
    body = _client(repo).get("/events/evt-1").text

    assert "Perubahan Link" not in body
    assert "Perubahan Section" not in body
    assert "Perubahan Gambar" not in body


# --- Aset statis --------------------------------------------------------- #


def test_static_stylesheet_is_served_locally():
    """Stylesheet dilayani dari /static (tanpa CDN, tetap jalan offline)."""
    client = _client(FakeRepository([]))
    body = client.get("/").text
    assert '/static/style.css' in body

    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "prefers-color-scheme: dark" in resp.text
