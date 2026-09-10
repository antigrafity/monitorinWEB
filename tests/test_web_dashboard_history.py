"""Unit test rute riwayat & detail Diff Web Dashboard (task 14.2).

Mencakup perilaku ``GET /websites/{id}`` dan ``GET /events/{id}`` sesuai
Req 10.4, 10.5, 10.7:

- riwayat Change_Event ditampilkan terbaru→terlama (Req 10.4);
- empty state saat website belum memiliki Change_Event (Req 10.7);
- detail Change_Event menampilkan seluruh isi Diff (Req 10.5);
- Change_Event tidak ditemukan ditangani (404).

Menggunakan repository in-memory palsu agar rute dapat diuji tanpa DB nyata,
serta satu pengujian integrasi ringan memakai :class:`Repository` pada DB
sementara untuk memastikan alur end-to-end bekerja.
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from monitoring.domain.models import (
    ChangeEvent,
    ChangeSummary,
    Diff,
    WebsiteConfig,
)
from monitoring.infra.repository import Repository
from monitoring.web.app import create_app


def _website(website_id="id-1", domain="example.com", name="Example Situs"):
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _event(event_id, website_id, url, detected_at, diff=None):
    diff = diff or Diff()
    summary = ChangeSummary(
        text_added=len(diff.text_added),
        text_removed=len(diff.text_removed),
        links_added=len(diff.links_added),
        links_removed=len(diff.links_removed),
        images_changed=len(diff.images_changed),
    )
    return ChangeEvent(
        id=event_id,
        website_id=website_id,
        url=url,
        detected_at=detected_at,
        diff=diff,
        summary=summary,
    )


class FakeRepository:
    """Repository palsu dengan website & event yang telah ditentukan."""

    def __init__(self, websites=None, events=None):
        self._websites = {w.id: w for w in (websites or [])}
        self._events = {e.id: e for e in (events or [])}

    async def get_website(self, website_id):
        return self._websites.get(website_id)

    async def list_change_events(self, website_id):
        # Kembalikan event milik website terurut menurun (terbaru dulu),
        # meniru perilaku Repository nyata (Req 10.4).
        items = [e for e in self._events.values() if e.website_id == website_id]
        return sorted(items, key=lambda e: e.detected_at, reverse=True)

    async def get_change_event(self, event_id):
        return self._events.get(event_id)


class FailingRepository:
    """Repository palsu yang selalu gagal memuat (Req 10.8)."""

    async def get_website(self, website_id):
        raise RuntimeError("Data_Store tidak dapat diakses")

    async def list_change_events(self, website_id):
        raise RuntimeError("Data_Store tidak dapat diakses")

    async def get_change_event(self, event_id):
        raise RuntimeError("Data_Store tidak dapat diakses")


def _client(repository, raise_server_exceptions=True) -> TestClient:
    return TestClient(
        create_app(repository), raise_server_exceptions=raise_server_exceptions
    )


# --- GET /websites/{id} --------------------------------------------------- #


def test_history_shows_events_newest_first():
    """Riwayat Change_Event ditampilkan terbaru→terlama (Req 10.4)."""
    website = _website()
    older = _event(
        "evt-old", "id-1", "https://example.com/lama",
        datetime(2024, 1, 2, 9, 0, 0),
    )
    newer = _event(
        "evt-new", "id-1", "https://example.com/baru",
        datetime(2024, 1, 3, 9, 0, 0),
    )
    # Sisipkan dengan urutan acak untuk memastikan pengurutan dari repo.
    repo = FakeRepository(websites=[website], events=[older, newer])
    resp = _client(repo).get("/websites/id-1")
    assert resp.status_code == 200
    body = resp.text
    # Nama & domain website muncul pada header.
    assert "Example Situs" in body
    assert "example.com" in body
    # Kedua URL event muncul, dan yang terbaru muncul sebelum yang terlama.
    idx_new = body.find("https://example.com/baru")
    idx_old = body.find("https://example.com/lama")
    assert idx_new != -1 and idx_old != -1
    assert idx_new < idx_old


def test_history_empty_state_when_no_events():
    """Empty state saat website belum memiliki Change_Event (Req 10.7)."""
    repo = FakeRepository(websites=[_website()], events=[])
    resp = _client(repo).get("/websites/id-1")
    assert resp.status_code == 200
    assert "belum ada perubahan tercatat" in resp.text.lower()


def test_history_website_not_found_returns_404():
    """Website tidak ditemukan ditangani (404)."""
    repo = FakeRepository(websites=[], events=[])
    resp = _client(repo).get("/websites/tidak-ada")
    assert resp.status_code == 404
    assert "tidak ditemukan" in resp.text.lower()


def test_history_load_failure_shows_error():
    """Kegagalan pemuatan menampilkan indikasi kesalahan (Req 10.8)."""
    resp = _client(FailingRepository(), raise_server_exceptions=False).get(
        "/websites/id-1"
    )
    assert resp.status_code == 500
    assert "kesalahan" in resp.text.lower()


# --- GET /events/{id} ----------------------------------------------------- #


def test_event_detail_shows_diff_contents():
    """Detail Change_Event menampilkan seluruh isi Diff (Req 10.5)."""
    diff = Diff(
        text_added=["baris teks baru"],
        text_removed=["baris teks lama"],
        links_added=["https://example.com/link-baru"],
        links_removed=["https://example.com/link-lama"],
        sections_added=["Section Baru"],
        sections_removed=["Section Lama"],
        images_added=["https://example.com/gambar-baru.png"],
        images_removed=["https://example.com/gambar-lama.png"],
        images_changed=["https://example.com/gambar-berubah.png"],
    )
    event = _event(
        "evt-1", "id-1", "https://example.com/halaman",
        datetime(2024, 1, 3, 9, 0, 0), diff=diff,
    )
    repo = FakeRepository(websites=[_website()], events=[event])
    resp = _client(repo).get("/events/evt-1")
    assert resp.status_code == 200
    body = resp.text
    # URL halaman & waktu deteksi.
    assert "https://example.com/halaman" in body
    # Seluruh isi Diff terrender.
    assert "baris teks baru" in body
    assert "baris teks lama" in body
    assert "https://example.com/link-baru" in body
    assert "https://example.com/link-lama" in body
    assert "Section Baru" in body
    assert "Section Lama" in body
    assert "https://example.com/gambar-baru.png" in body
    assert "https://example.com/gambar-lama.png" in body
    assert "https://example.com/gambar-berubah.png" in body


def test_event_detail_not_found_returns_404():
    """Change_Event tidak ditemukan ditangani (404)."""
    repo = FakeRepository(websites=[_website()], events=[])
    resp = _client(repo).get("/events/tidak-ada")
    assert resp.status_code == 404
    assert "tidak ditemukan" in resp.text.lower()


def test_event_detail_load_failure_shows_error():
    """Kegagalan pemuatan detail menampilkan indikasi kesalahan (Req 10.8)."""
    resp = _client(FailingRepository(), raise_server_exceptions=False).get(
        "/events/evt-1"
    )
    assert resp.status_code == 500
    assert "kesalahan" in resp.text.lower()


# --- Integrasi ringan dengan Repository nyata ----------------------------- #


async def test_history_and_detail_via_real_repository(tmp_path):
    """Integrasi ringan: rute memakai Repository nyata pada DB sementara.

    Memverifikasi riwayat terurut menurun (Req 10.4) dan detail Diff terbaca
    dari change_event (Req 10.5), termasuk empty state (Req 10.7).
    """
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        base = datetime(2024, 1, 1, 12, 0, 0)
        await repo.add_website(_website("id-1", "example.com", "Example Situs"))
        await repo.add_website(_website("id-2", "kosong.com", "Kosong"))

        diff = Diff(
            text_added=["teks baru"],
            links_added=["https://example.com/l1"],
            images_changed=["https://example.com/g1.png"],
        )
        e_old = _event(
            "evt-old", "id-1", "https://example.com/lama",
            base + timedelta(hours=1),
        )
        e_new = _event(
            "evt-new", "id-1", "https://example.com/baru",
            base + timedelta(hours=2), diff=diff,
        )
        assert await repo.save_change_event(e_old)
        assert await repo.save_change_event(e_new)

        client = TestClient(create_app(repo))

        # Riwayat id-1: terbaru dulu.
        body = client.get("/websites/id-1").text
        idx_new = body.find("https://example.com/baru")
        idx_old = body.find("https://example.com/lama")
        assert idx_new != -1 and idx_old != -1
        assert idx_new < idx_old

        # Empty state untuk website tanpa event.
        assert "belum ada perubahan tercatat" in client.get("/websites/id-2").text.lower()

        # Detail Diff terbaca.
        detail = client.get("/events/evt-new").text
        assert "teks baru" in detail
        assert "https://example.com/l1" in detail
        assert "https://example.com/g1.png" in detail

        # Event tidak ada -> 404.
        assert client.get("/events/tidak-ada").status_code == 404
