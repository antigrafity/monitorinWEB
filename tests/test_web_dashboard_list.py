"""Unit test rute daftar & status Web Dashboard (task 14.1).

Mencakup perilaku ``GET /`` sesuai Req 10.1, 10.2, 10.3, 10.6, 10.8:

- daftar menampilkan domain & nama tiap Monitored_Website (Req 10.1);
- website yang belum pernah diperiksa menampilkan indikasi "belum pernah"
  (Req 10.2);
- status sukses vs gagal ditampilkan dengan indikator visual berbeda
  (Req 10.3);
- empty state saat tidak ada website (Req 10.6);
- kegagalan pemuatan data -> indikasi kesalahan tanpa mengubah data (Req 10.8).

Menggunakan repository in-memory palsu agar rute dapat diuji tanpa DB nyata,
serta satu pengujian integrasi ringan memakai :class:`Repository` pada DB
sementara untuk memastikan ``list_websites_with_status`` bekerja end-to-end.
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from monitoring.domain.models import WebsiteConfig
from monitoring.infra.repository import (
    Repository,
    WebsiteOverview,
    WebsiteStatusView,
)
from monitoring.web.app import create_app


def _view(
    website_id: str,
    domain: str,
    name: str,
    last_checked_at=None,
    last_status=None,
) -> WebsiteStatusView:
    return WebsiteStatusView(
        website=WebsiteConfig(
            id=website_id,
            domain=domain,
            name=name,
            poll_interval_seconds=None,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
        ),
        last_checked_at=last_checked_at,
        last_status=last_status,
    )


class FakeRepository:
    """Repository palsu yang mengembalikan daftar view yang telah ditentukan."""

    def __init__(self, views):
        self._views = views

    async def list_websites_with_status(self):
        return list(self._views)

    async def website_stats(self):
        """Turunkan WebsiteOverview dari daftar view (halaman Websites, Tahap 1)."""
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


class FailingRepository:
    """Repository palsu yang selalu gagal memuat (Req 10.8)."""

    async def list_websites_with_status(self):
        raise RuntimeError("Data_Store tidak dapat diakses")


def _client(repository) -> TestClient:
    return TestClient(create_app(repository))


def test_list_shows_websites_with_domain_and_name():
    """Daftar menampilkan domain & nama tiap website (Req 10.1)."""
    repo = FakeRepository(
        [
            _view("id-1", "example.com", "Example Situs",
                  datetime(2024, 1, 2, 10, 0, 0), "success"),
            _view("id-2", "contoh.co.id", "Contoh Perusahaan",
                  datetime(2024, 1, 2, 11, 0, 0), "failure"),
        ]
    )
    resp = _client(repo).get("/websites")
    assert resp.status_code == 200
    body = resp.text
    assert "example.com" in body
    assert "Example Situs" in body
    assert "contoh.co.id" in body
    assert "Contoh Perusahaan" in body


def test_never_checked_website_shows_indication():
    """Website yang belum pernah diperiksa menampilkan indikasi (Req 10.2)."""
    repo = FakeRepository(
        [_view("id-1", "baru.com", "Situs Baru", None, None)]
    )
    resp = _client(repo).get("/websites")
    assert resp.status_code == 200
    assert "Belum pernah diperiksa" in resp.text


def test_success_and_failure_render_distinct_indicators():
    """Status sukses & gagal memakai indikator visual yang berbeda (Req 10.3)."""
    repo = FakeRepository(
        [
            _view("id-1", "sukses.com", "Sukses",
                  datetime(2024, 1, 2, 10, 0, 0), "success"),
            _view("id-2", "gagal.com", "Gagal",
                  datetime(2024, 1, 2, 11, 0, 0), "failure"),
        ]
    )
    body = _client(repo).get("/websites").text
    # Indikator berbeda untuk sukses vs gagal (kelas CSS + label berbeda).
    assert 'class="status status-success"' in body
    assert 'class="status status-failure"' in body
    # Label teks berbeda memastikan indikator visual dapat dibedakan.
    assert "Berhasil" in body
    assert "Gagal" in body


def test_empty_state_when_no_websites():
    """Empty state saat tidak ada Monitored_Website (Req 10.6)."""
    resp = _client(FakeRepository([])).get("/websites")
    assert resp.status_code == 200
    assert "Belum ada website yang dipantau" in resp.text


def test_load_failure_shows_error_indication():
    """Kegagalan pemuatan data menampilkan indikasi kesalahan (Req 10.8)."""
    client = TestClient(create_app(FailingRepository()), raise_server_exceptions=False)
    resp = client.get("/websites")
    assert resp.status_code == 500
    assert "kesalahan" in resp.text.lower()
    # Tidak menampilkan empty-state maupun daftar; hanya indikasi error.
    assert "Belum ada website yang dipantau" not in resp.text


async def test_list_websites_with_status_via_real_repository(tmp_path):
    """Integrasi ringan: rute memakai Repository nyata pada DB sementara.

    Memverifikasi ``list_websites_with_status`` membaca last_checked_at/
    last_status dari tabel website_config dan dashboard merendernya (Req 10.1–10.3).
    """
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        base = datetime(2024, 1, 1, 12, 0, 0)
        await repo.add_website(
            WebsiteConfig("id-1", "diperiksa.com", "Diperiksa", None, base)
        )
        await repo.add_website(
            WebsiteConfig(
                "id-2", "belum.com", "Belum", None, base + timedelta(minutes=1)
            )
        )
        # id-1 sudah diperiksa dengan status success; id-2 belum pernah.
        await repo.update_last_check(
            "id-1", datetime(2024, 1, 2, 9, 30, 0), "success"
        )

        views = await repo.list_websites_with_status()
        assert [v.website.id for v in views] == ["id-1", "id-2"]
        assert views[0].is_success and not views[0].never_checked
        assert views[1].never_checked

        client = TestClient(create_app(repo))
        body = client.get("/websites").text
        assert "diperiksa.com" in body
        assert "belum.com" in body
        assert "status-success" in body
        assert "Belum pernah diperiksa" in body
