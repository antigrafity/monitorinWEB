"""Unit & integrasi test rute manajemen konfigurasi Web Dashboard (task 14.3).

Mencakup perilaku ``POST /websites`` dan penghapusan (``DELETE /websites/{id}``
serta alias ramah-form ``POST /websites/{id}/delete``) sesuai Req 1.1, 1.2,
1.4, 1.7, 1.8:

- POST domain valid -> ``add_website`` dipanggil dengan config yang benar,
  keberhasilan berupa redirect 303 ke ``/?added=<domain>`` (Req 1.1);
- POST domain tidak valid -> ``add_website`` TIDAK dipanggil, indikasi kesalahan
  ditampilkan (400) (Req 1.4);
- POST interval khusus tidak valid -> ``add_website`` TIDAK dipanggil, indikasi
  kesalahan ditampilkan (Req 1.6/1.9);
- POST domain duplikat -> ``DuplicateWebsiteError`` dipermukaan sebagai indikasi
  kesalahan (Req 1.7);
- POST melebihi kapasitas -> ``CapacityExceededError`` dipermukaan (Req 1.8);
- DELETE /websites/{id} dan POST /websites/{id}/delete -> ``remove_website``
  dipanggil, keberhasilan dikembalikan (Req 1.2).

Menggunakan repository in-memory palsu yang merekam pemanggilan add/remove,
serta satu pengujian end-to-end memakai :class:`Repository` pada DB sementara.
"""

from datetime import datetime

from fastapi.testclient import TestClient

from monitoring.domain.models import WebsiteConfig
from monitoring.infra.repository import (
    CapacityExceededError,
    DuplicateWebsiteError,
    Repository,
)
from monitoring.web.app import create_app


class RecordingRepository:
    """Repository palsu yang merekam pemanggilan add/remove.

    Dapat dikonfigurasi untuk mengangkat pengecualian tertentu pada
    ``add_website`` agar jalur penolakan duplikat/kapasitas dapat diuji.
    """

    def __init__(self, add_error: Exception = None):
        self.added = []
        self.removed = []
        self._add_error = add_error

    async def list_websites_with_status(self):
        return []

    async def add_website(self, cfg):
        if self._add_error is not None:
            raise self._add_error
        self.added.append(cfg)

    async def remove_website(self, website_id):
        self.removed.append(website_id)


def _client(repository) -> TestClient:
    # follow_redirects=False agar respons redirect 303 dapat diperiksa langsung.
    return TestClient(create_app(repository), follow_redirects=False)


def test_post_valid_domain_calls_add_website_and_redirects():
    """POST domain valid -> add_website dipanggil, redirect 303 (Req 1.1)."""
    repo = RecordingRepository()
    resp = _client(repo).post(
        "/websites",
        data={"domain": "example.com", "name": "Example Situs"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/websites?added=example.com"
    assert len(repo.added) == 1
    cfg = repo.added[0]
    assert cfg.domain == "example.com"
    assert cfg.name == "Example Situs"
    assert cfg.poll_interval_seconds is None
    assert cfg.id  # id di-generate (uuid4)
    assert isinstance(cfg.created_at, datetime)


def test_post_valid_domain_without_name_uses_domain_as_name():
    """Bila nama kosong, domain dipakai sebagai nama tampilan."""
    repo = RecordingRepository()
    resp = _client(repo).post(
        "/websites",
        data={"domain": "contoh.co.id", "name": ""},
    )
    assert resp.status_code == 303
    assert len(repo.added) == 1
    assert repo.added[0].name == "contoh.co.id"


def test_post_valid_domain_with_custom_interval():
    """Interval khusus valid diteruskan ke WebsiteConfig (Req 1.6)."""
    repo = RecordingRepository()
    resp = _client(repo).post(
        "/websites",
        data={
            "domain": "interval.com",
            "name": "Interval",
            "poll_interval_seconds": "60",
        },
    )
    assert resp.status_code == 303
    assert len(repo.added) == 1
    assert repo.added[0].poll_interval_seconds == 60


def test_post_invalid_domain_does_not_call_add_website():
    """POST domain tidak valid -> add_website TIDAK dipanggil (Req 1.4)."""
    repo = RecordingRepository()
    resp = _client(repo).post(
        "/websites",
        data={"domain": "tidak valid!", "name": "Invalid"},
    )
    assert resp.status_code == 400
    assert repo.added == []
    # Indikasi kesalahan format ditampilkan.
    assert "tidak valid" in resp.text.lower()


def test_post_invalid_interval_does_not_call_add_website():
    """Interval khusus tidak valid -> add_website TIDAK dipanggil (Req 1.6/1.9)."""
    repo = RecordingRepository()
    resp = _client(repo).post(
        "/websites",
        data={
            "domain": "valid.com",
            "name": "Valid",
            "poll_interval_seconds": "5",  # < POLL_MIN_SECONDS (10)
        },
    )
    assert resp.status_code == 400
    assert repo.added == []
    assert "tidak valid" in resp.text.lower()


def test_post_non_integer_interval_does_not_call_add_website():
    """Interval khusus non-integer -> add_website TIDAK dipanggil (Req 1.9)."""
    repo = RecordingRepository()
    resp = _client(repo).post(
        "/websites",
        data={
            "domain": "valid.com",
            "name": "Valid",
            "poll_interval_seconds": "abc",
        },
    )
    assert resp.status_code == 400
    assert repo.added == []


def test_post_duplicate_domain_surfaces_error():
    """POST domain duplikat -> DuplicateWebsiteError dipermukaan (Req 1.7)."""
    repo = RecordingRepository(add_error=DuplicateWebsiteError("dup.com"))
    resp = _client(repo).post(
        "/websites",
        data={"domain": "dup.com", "name": "Duplikat"},
    )
    assert resp.status_code == 400
    assert "sudah terdaftar" in resp.text.lower()


def test_post_capacity_exceeded_surfaces_error():
    """POST melebihi kapasitas -> CapacityExceededError dipermukaan (Req 1.8)."""
    repo = RecordingRepository(add_error=CapacityExceededError(100))
    resp = _client(repo).post(
        "/websites",
        data={"domain": "penuh.com", "name": "Penuh"},
    )
    assert resp.status_code == 400
    assert "kapasitas maksimum" in resp.text.lower()


def test_delete_method_calls_remove_website():
    """DELETE /websites/{id} -> remove_website dipanggil, sukses (Req 1.2)."""
    repo = RecordingRepository()
    resp = _client(repo).delete("/websites/id-123")
    assert resp.status_code == 200
    assert repo.removed == ["id-123"]
    body = resp.json()
    assert body["deleted"] is True
    assert body["website_id"] == "id-123"


def test_post_delete_alias_calls_remove_website_and_redirects():
    """POST /websites/{id}/delete -> remove_website dipanggil, redirect (Req 1.2)."""
    repo = RecordingRepository()
    resp = _client(repo).post("/websites/id-456/delete")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert repo.removed == ["id-456"]


# --- Integrasi end-to-end dengan Repository nyata pada DB sementara -------- #


async def test_add_and_delete_end_to_end_via_real_repository(tmp_path):
    """E2E: tambah lalu hapus Monitored_Website memakai Repository nyata (Req 1.1, 1.2).

    Memverifikasi POST menyimpan website ke Data_Store dan penghapusan
    menghilangkannya, seluruhnya melewati rute FastAPI + repository sungguhan.
    """
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        client = TestClient(create_app(repo), follow_redirects=False)

        # Tambah website (Req 1.1).
        resp = client.post(
            "/websites",
            data={"domain": "e2e.com", "name": "End To End"},
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/websites?added=e2e.com"

        websites = await repo.list_websites()
        assert len(websites) == 1
        added = websites[0]
        assert added.domain == "e2e.com"
        assert added.name == "End To End"

        # Hapus website lewat alias form (Req 1.2).
        resp = client.post(f"/websites/{added.id}/delete")
        assert resp.status_code == 303
        assert await repo.count_websites() == 0


async def test_duplicate_domain_end_to_end_via_real_repository(tmp_path):
    """E2E: domain duplikat ditolak dengan indikasi kesalahan (Req 1.7)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(
            WebsiteConfig(
                id="existing",
                domain="dup.com",
                name="Existing",
                poll_interval_seconds=None,
                created_at=datetime(2024, 1, 1, 12, 0, 0),
            )
        )
        client = TestClient(create_app(repo), follow_redirects=False)
        resp = client.post(
            "/websites",
            data={"domain": "dup.com", "name": "Duplikat"},
        )
        assert resp.status_code == 400
        assert "sudah terdaftar" in resp.text.lower()
        # Tidak ada penambahan baru.
        assert await repo.count_websites() == 1
