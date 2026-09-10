"""Unit & integrasi test aksi dashboard: "Cek Sekarang" dan Edit.

Mencakup dua rute baru pada Web Dashboard:

- ``POST /websites/{id}/check`` — pemeriksaan manual segera di luar jadwal
  periodik (Req 8.4). Diuji: keberhasilan (200 + payload JSON ringkasan
  ``CheckResult`` dan ``orchestrator.check_website`` dipanggil sekali dengan
  Monitored_Website yang benar), id tidak dikenal (404), aplikasi tanpa
  orchestrator (503, merosot anggun), pemeriksaan ganda bersamaan untuk website
  yang sama (409), dan kegagalan orchestrator (500 dengan ``ok: false`` tanpa
  membuat aplikasi berhenti).
- ``POST /websites/{id}/edit`` — ubah nama & Polling_Interval khusus
  (Req 1.3, 1.6, 1.9). Diuji: interval valid (``update_website`` dipanggil,
  redirect 303 ke ``/?updated=<domain>``), interval kosong -> ``None`` (memakai
  Polling_Interval global), interval tidak valid (400 dan ``update_website``
  TIDAK dipanggil sehingga Data_Store tidak berubah), id tidak dikenal (404),
  serta nama kosong yang mempertahankan nama lama.

Menggunakan repository/orchestrator palsu berbasis :class:`TestClient`, plus
satu pengujian end-to-end dengan :class:`Repository` nyata pada ``tmp_path``.
Kompatibel Python 3.9.
"""

import asyncio
from datetime import datetime
from typing import Optional

from fastapi.testclient import TestClient

from monitoring.domain.models import WebsiteConfig
from monitoring.infra.repository import Repository
from monitoring.web.app import create_app


def _website(
    website_id: str = "w1",
    domain: str = "example.com",
    name: str = "Example",
    poll_interval_seconds: Optional[int] = None,
) -> WebsiteConfig:
    """Bangun WebsiteConfig contoh untuk pengujian."""
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name,
        poll_interval_seconds=poll_interval_seconds,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


class FakeRepository:
    """Repository palsu: menyimpan website di memori & merekam update."""

    def __init__(self, websites=None):
        self._websites = {w.id: w for w in (websites or [])}
        self.updated = []

    async def list_websites_with_status(self):
        return []

    async def get_website(self, website_id):
        return self._websites.get(website_id)

    async def update_website(self, cfg):
        self.updated.append(cfg)
        self._websites[cfg.id] = cfg

    async def website_stats(self):
        """Ringkasan minimal per website (halaman Websites, Tahap 1)."""
        from monitoring.infra.repository import WebsiteOverview

        return [
            WebsiteOverview(
                website=w,
                last_checked_at=None,
                last_status=None,
                pages_count=0,
                last_change_at=None,
                changes_last_7_days=0,
                daily_counts_7_days=[],
            )
            for w in self._websites.values()
        ]


class FakeCheckResult:
    """Tiruan ``CheckResult`` dengan atribut yang dipakai payload JSON."""

    def __init__(self, website_id: str = "w1", status: str = "success"):
        self.website_id = website_id
        self.status = status
        self.pages_checked = 3
        self.changes_detected = 1
        self.notifications_sent = 1
        self.page_failures = 0
        self.image_failures = 0
        self.snapshot_save_failures = 0
        self.event_save_failures = 0
        self.last_check_updated = True
        self.discovery_failed = False
        self.errors = []


class FakeOrchestrator:
    """Orchestrator palsu yang merekam pemanggilan ``check_website``."""

    def __init__(self, error: Exception = None, result=None):
        self.calls = []
        self._error = error
        self._result = result

    async def check_website(self, website):
        self.calls.append(website)
        if self._error is not None:
            raise self._error
        return self._result or FakeCheckResult(website_id=website.id)


def _client(repository, orchestrator=None) -> TestClient:
    # follow_redirects=False agar respons 303 dapat diperiksa langsung.
    return TestClient(
        create_app(repository, orchestrator=orchestrator), follow_redirects=False
    )


# --- Feature 1: "Cek Sekarang" (Req 8.4) ----------------------------------- #


def test_check_now_runs_check_and_returns_json_summary():
    """POST /check -> 200, check_website dipanggil sekali, payload lengkap (Req 8.4)."""
    website = _website()
    repo = FakeRepository([website])
    orch = FakeOrchestrator()
    resp = _client(repo, orch).post("/websites/w1/check")

    assert resp.status_code == 200
    # Orchestrator dipanggil TEPAT sekali dengan Monitored_Website yang benar.
    assert len(orch.calls) == 1
    assert orch.calls[0].id == "w1"
    assert orch.calls[0].domain == "example.com"

    body = resp.json()
    assert body["ok"] is True
    assert body["website_id"] == "w1"
    assert body["status"] == "success"
    assert body["pages_checked"] == 3
    assert body["changes_detected"] == 1
    assert body["notifications_sent"] == 1
    assert body["page_failures"] == 0


def test_check_now_unknown_website_returns_404():
    """POST /check pada id tidak dikenal -> 404, orchestrator tidak dipanggil."""
    repo = FakeRepository([_website()])
    orch = FakeOrchestrator()
    resp = _client(repo, orch).post("/websites/tidak-ada/check")

    assert resp.status_code == 404
    assert orch.calls == []
    body = resp.json()
    assert body["ok"] is False
    assert "tidak ditemukan" in body["error"].lower()


def test_check_now_without_orchestrator_returns_503():
    """Tanpa orchestrator -> 503 dengan pesan JSON jelas (merosot anggun)."""
    repo = FakeRepository([_website()])
    resp = _client(repo).post("/websites/w1/check")

    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False
    assert "orchestrator" in body["error"].lower()


def test_check_now_backward_compatible_factory_signature():
    """create_app(repository) tanpa argumen kedua tetap didukung."""
    repo = FakeRepository([_website()])
    client = TestClient(create_app(repo), follow_redirects=False)
    assert client.get("/websites").status_code == 200


def test_check_now_duplicate_concurrent_check_returns_409():
    """Pemeriksaan ganda untuk website yang sama -> 409 (penanda in-flight).

    Penanda in-flight di-*seed* langsung pada ``app.state`` karena TestClient
    bersifat sinkron sehingga sulit menjalankan dua request benar-benar
    bersamaan; ini menguji guard-nya secara langsung.
    """
    repo = FakeRepository([_website()])
    orch = FakeOrchestrator()
    app = create_app(repo, orchestrator=orch)
    app.state.checks_in_flight.add("w1")
    client = TestClient(app, follow_redirects=False)

    resp = client.post("/websites/w1/check")
    assert resp.status_code == 409
    assert orch.calls == []
    body = resp.json()
    assert body["ok"] is False
    assert "sedang berjalan" in body["error"].lower()


def test_check_now_concurrent_requests_second_gets_409_via_event():
    """Dua pemeriksaan benar-benar bersamaan -> yang kedua 409 (Req 8.4).

    Menjalankan dua request lewat ``httpx.AsyncClient`` pada ASGI app: request
    pertama ditahan pada ``asyncio.Event`` di dalam orchestrator, sehingga
    request kedua tiba saat penanda in-flight masih aktif.
    """
    import httpx

    repo = FakeRepository([_website()])

    class GatedOrchestrator:
        """Orchestrator yang menahan pemeriksaan hingga ``gate`` dibuka."""

        def __init__(self):
            self.calls = []
            # Event dibuat DI DALAM event loop (penting pada Python 3.9).
            self.started = asyncio.Event()
            self.gate = asyncio.Event()

        async def check_website(self, website):
            self.calls.append(website)
            self.started.set()
            await self.gate.wait()
            return FakeCheckResult(website_id=website.id)

    state = {}

    async def scenario():
        orch = GatedOrchestrator()
        app = create_app(repo, orchestrator=orch)
        state["app"] = app
        state["orch"] = orch
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = asyncio.ensure_future(client.post("/websites/w1/check"))
            await asyncio.wait_for(orch.started.wait(), timeout=5)
            second = await client.post("/websites/w1/check")
            orch.gate.set()
            first_resp = await asyncio.wait_for(first, timeout=5)
            return first_resp, second

    first_resp, second_resp = asyncio.run(scenario())
    orch = state["orch"]
    app = state["app"]

    assert first_resp.status_code == 200
    assert second_resp.status_code == 409
    # Hanya satu pemeriksaan yang benar-benar dijalankan.
    assert len(orch.calls) == 1
    # Penanda in-flight dibersihkan setelah selesai (blok finally).
    assert "w1" not in app.state.checks_in_flight


def test_check_now_orchestrator_error_returns_500_and_clears_in_flight():
    """Orchestrator mengangkat pengecualian -> 500 ok:false, app tetap hidup."""
    repo = FakeRepository([_website()])
    orch = FakeOrchestrator(error=RuntimeError("fetch meledak"))
    app = create_app(repo, orchestrator=orch)
    client = TestClient(app, follow_redirects=False)

    resp = client.post("/websites/w1/check")
    assert resp.status_code == 500
    body = resp.json()
    assert body["ok"] is False
    assert "fetch meledak" in body["error"]
    # Penanda in-flight tetap dibersihkan (finally) sehingga bisa dicoba lagi.
    assert "w1" not in app.state.checks_in_flight
    assert client.post("/websites/w1/check").status_code == 500


# --- Feature 2: Edit nama & interval (Req 1.3, 1.6, 1.9) ------------------- #


def test_edit_valid_interval_updates_and_redirects():
    """Edit interval valid -> update_website dipanggil, redirect 303 (Req 1.3)."""
    repo = FakeRepository([_website(poll_interval_seconds=None)])
    resp = _client(repo).post(
        "/websites/w1/edit",
        data={"name": "Nama Baru", "poll_interval_seconds": "120"},
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/websites?updated=example.com"
    assert len(repo.updated) == 1
    cfg = repo.updated[0]
    assert cfg.id == "w1"
    assert cfg.domain == "example.com"  # domain tidak berubah
    assert cfg.name == "Nama Baru"
    assert cfg.poll_interval_seconds == 120


def test_edit_blank_interval_stores_none_global():
    """Interval kosong -> disimpan None (memakai Polling_Interval global) (Req 1.6)."""
    repo = FakeRepository([_website(poll_interval_seconds=300)])
    resp = _client(repo).post(
        "/websites/w1/edit",
        data={"name": "Tetap", "poll_interval_seconds": ""},
    )

    assert resp.status_code == 303
    assert len(repo.updated) == 1
    assert repo.updated[0].poll_interval_seconds is None


def test_edit_invalid_interval_returns_400_and_does_not_update():
    """Interval tidak valid -> 400 dan update_website TIDAK dipanggil (Req 1.9)."""
    repo = FakeRepository([_website()])
    resp = _client(repo).post(
        "/websites/w1/edit",
        data={"name": "Nama", "poll_interval_seconds": "5"},
    )

    assert resp.status_code == 400
    assert repo.updated == []
    assert "tidak valid" in resp.text.lower()


def test_edit_non_integer_interval_returns_400_and_does_not_update():
    """Interval non-integer -> 400 dan update_website TIDAK dipanggil (Req 1.9)."""
    repo = FakeRepository([_website()])
    resp = _client(repo).post(
        "/websites/w1/edit",
        data={"name": "Nama", "poll_interval_seconds": "abc"},
    )

    assert resp.status_code == 400
    assert repo.updated == []


def test_edit_unknown_website_returns_404():
    """Edit id tidak dikenal -> 404, tidak ada perubahan Data_Store."""
    repo = FakeRepository([_website()])
    resp = _client(repo).post(
        "/websites/tidak-ada/edit",
        data={"name": "Nama", "poll_interval_seconds": "60"},
    )

    assert resp.status_code == 404
    assert repo.updated == []
    assert "tidak ditemukan" in resp.json()["error"].lower()


def test_edit_blank_name_keeps_existing_name():
    """Nama kosong -> nama lama dipertahankan (tidak dikosongkan) (Req 1.3)."""
    repo = FakeRepository([_website(name="Nama Lama")])
    resp = _client(repo).post(
        "/websites/w1/edit",
        data={"name": "", "poll_interval_seconds": "60"},
    )

    assert resp.status_code == 303
    assert len(repo.updated) == 1
    assert repo.updated[0].name == "Nama Lama"
    assert repo.updated[0].poll_interval_seconds == 60


def test_index_shows_updated_confirmation():
    """GET /websites?updated=<domain> menampilkan konfirmasi perubahan (Req 1.3)."""
    repo = FakeRepository([])
    resp = _client(repo).get("/websites?updated=example.com")
    assert resp.status_code == 200
    assert "example.com" in resp.text


# --- Integrasi end-to-end dengan Repository nyata pada DB sementara -------- #


async def test_edit_and_check_end_to_end_via_real_repository(tmp_path):
    """E2E: edit pengaturan lalu "Cek Sekarang" dengan Repository nyata.

    Memverifikasi ``POST /edit`` benar-benar mengubah nama & Polling_Interval di
    Data_Store (Req 1.3, 1.6) dan ``POST /check`` memanggil orchestrator dengan
    Monitored_Website hasil pembacaan Data_Store (Req 8.4).
    """
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(
            _website(website_id="e2e", domain="e2e.com", name="Nama Lama")
        )
        orch = FakeOrchestrator()
        client = TestClient(
            create_app(repo, orchestrator=orch), follow_redirects=False
        )

        # Edit nama + interval khusus (Req 1.3, 1.6).
        resp = client.post(
            "/websites/e2e/edit",
            data={"name": "Nama Baru", "poll_interval_seconds": "600"},
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/websites?updated=e2e.com"

        stored = await repo.get_website("e2e")
        assert stored.name == "Nama Baru"
        assert stored.poll_interval_seconds == 600

        # Interval kosong -> kembali memakai Polling_Interval global (None).
        resp = client.post(
            "/websites/e2e/edit",
            data={"name": "", "poll_interval_seconds": ""},
        )
        assert resp.status_code == 303
        stored = await repo.get_website("e2e")
        assert stored.poll_interval_seconds is None
        assert stored.name == "Nama Baru"  # nama kosong -> dipertahankan

        # "Cek Sekarang" memakai konfigurasi dari Data_Store (Req 8.4).
        resp = client.post("/websites/e2e/check")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert len(orch.calls) == 1
        assert orch.calls[0].domain == "e2e.com"
