"""Unit & integrasi test halaman Websites ContentMonitor (Tahap 1).

Mencakup ``GET /websites`` dan aksi jeda/lanjutkan sesuai spesifikasi Tahap 1:

- search (`q`) memfilter server-side berdasarkan nama/domain;
- pagination server-side (`page`, 10 baris/halaman) membatasi baris & teks
  "Showing X to Y of Z websites"; halaman di luar rentang tidak error;
- sparkline SVG aman saat semua data 0;
- Jeda/lanjutkan mengubah status website (dan scheduler melewati website yang
  dijeda — diuji lewat repository nyata + Scheduler);
- sidebar menandai "Websites" sebagai item aktif.

Menggunakan repository palsu (fake) untuk sebagian besar unit test, plus test
integrasi dengan :class:`Repository` nyata pada ``tmp_path``.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi.testclient import TestClient

from monitoring.domain.models import WebsiteConfig
from monitoring.infra.repository import Repository, WebsiteOverview
from monitoring.infra.scheduler import Scheduler
from monitoring.web.app import WEBSITES_PAGE_SIZE, create_app


def _website(
    website_id: str,
    domain: str,
    name: Optional[str] = None,
    paused: bool = False,
) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name or domain,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        paused=paused,
    )


class FakeRepository:
    """Repository palsu untuk halaman Websites: website disimpan di memori."""

    def __init__(self, websites: Optional[List[WebsiteConfig]] = None):
        self._websites = {w.id: w for w in (websites or [])}
        self.paused_calls: List[tuple] = []

    async def website_stats(self):
        return [
            WebsiteOverview(
                website=w,
                last_checked_at=None,
                last_status=None,
                pages_count=0,
                last_change_at=None,
                changes_last_7_days=0,
                daily_counts_7_days=[("2024-01-0{0}".format(i + 1), 0) for i in range(7)],
            )
            for w in self._websites.values()
        ]

    async def set_website_paused(self, website_id, paused):
        self.paused_calls.append((website_id, paused))
        if website_id in self._websites:
            existing = self._websites[website_id]
            self._websites[website_id] = WebsiteConfig(
                id=existing.id,
                domain=existing.domain,
                name=existing.name,
                poll_interval_seconds=existing.poll_interval_seconds,
                created_at=existing.created_at,
                paused=paused,
            )


class FailingRepository:
    """Repository palsu yang selalu gagal memuat ringkasan website."""

    async def website_stats(self):
        raise RuntimeError("Data_Store tidak dapat diakses")


def _client(repository) -> TestClient:
    return TestClient(create_app(repository), follow_redirects=False)


# --- Search ---------------------------------------------------------------- #


def test_search_filters_by_name_or_domain():
    """Search (`q`) memfilter berdasarkan nama ATAU domain, case-insensitive."""
    repo = FakeRepository(
        [
            _website("w1", "alpha.com", "Alpha Situs"),
            _website("w2", "beta.com", "Beta Corp"),
            _website("w3", "gamma.com", "Gamma Inc"),
        ]
    )
    body = _client(repo).get("/websites?q=beta").text
    assert "beta.com" in body
    assert "alpha.com" not in body
    assert "gamma.com" not in body


def test_search_matches_domain_case_insensitive():
    """Pencarian tidak peka huruf besar/kecil."""
    repo = FakeRepository([_website("w1", "ExampleSite.com", "Example")])
    body = _client(repo).get("/websites?q=examplesite").text
    assert "ExampleSite.com" in body


def test_search_no_results_shows_distinct_empty_state():
    """Hasil pencarian kosong menampilkan empty state berbeda dari "belum ada"."""
    repo = FakeRepository([_website("w1", "alpha.com")])
    body = _client(repo).get("/websites?q=tidakketemu").text
    assert "Tidak ada hasil untuk pencarian" in body
    assert "Belum ada website yang dipantau" not in body


def test_empty_state_when_no_websites_at_all():
    """Tanpa website sama sekali -> empty state "belum ada" (bukan hasil pencarian)."""
    repo = FakeRepository([])
    body = _client(repo).get("/websites").text
    assert "Belum ada website yang dipantau" in body


# --- Pagination -------------------------------------------------------------- #


def test_pagination_limits_rows_and_shows_range_text():
    """Pagination membatasi baris (10/halaman) & menampilkan "Showing X to Y of Z"."""
    websites = [_website(f"w{i}", f"site{i}.com") for i in range(15)]
    repo = FakeRepository(websites)
    body = _client(repo).get("/websites?page=1").text
    assert body.count('class="site-name"') == WEBSITES_PAGE_SIZE
    assert "Showing 1 to 10 of 15 websites" in body

    body_page2 = _client(repo).get("/websites?page=2").text
    assert body_page2.count('class="site-name"') == 5
    assert "Showing 11 to 15 of 15 websites" in body_page2


def test_pagination_out_of_range_page_does_not_error():
    """Halaman di luar rentang dijepit ke halaman valid, TIDAK error."""
    websites = [_website(f"w{i}", f"site{i}.com") for i in range(3)]
    repo = FakeRepository(websites)
    resp = _client(repo).get("/websites?page=999")
    assert resp.status_code == 200
    assert "Showing 1 to 3 of 3 websites" in resp.text

    resp_negative = _client(repo).get("/websites?page=-5")
    assert resp_negative.status_code == 200


def test_pagination_combined_with_search():
    """Pagination & search dapat dikombinasikan secara konsisten."""
    websites = [_website(f"w{i}", f"match{i}.com") for i in range(12)]
    websites.append(_website("other", "different.com"))
    repo = FakeRepository(websites)
    body = _client(repo).get("/websites?q=match&page=2").text
    assert "different.com" not in body
    assert "Showing 11 to 12 of 12 websites" in body


# --- Sparkline SVG ------------------------------------------------------------ #


def test_sparkline_renders_safely_with_all_zero_data():
    """Sparkline SVG merender dengan aman saat seluruh data 7 hari bernilai 0."""
    repo = FakeRepository([_website("w1", "alpha.com")])
    resp = _client(repo).get("/websites")
    assert resp.status_code == 200
    assert "sparkline" in resp.text


# --- Jeda / Lanjutkan --------------------------------------------------------- #


def test_pause_action_calls_repository_and_redirects():
    """POST /websites/{id}/pause -> set_website_paused(True), redirect 303."""
    repo = FakeRepository([_website("w1", "alpha.com")])
    resp = _client(repo).post("/websites/w1/pause", data={"next": "/websites"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/websites"
    assert repo.paused_calls == [("w1", True)]


def test_resume_action_calls_repository_and_redirects():
    """POST /websites/{id}/resume -> set_website_paused(False), redirect 303."""
    repo = FakeRepository([_website("w1", "alpha.com", paused=True)])
    resp = _client(repo).post("/websites/w1/resume", data={"next": "/websites"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/websites"
    assert repo.paused_calls == [("w1", False)]


def test_pause_redirect_defaults_to_websites_without_next():
    """Tanpa ``next``, redirect default ke /websites."""
    repo = FakeRepository([_website("w1", "alpha.com")])
    resp = _client(repo).post("/websites/w1/pause")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/websites"


def test_pause_redirect_rejects_external_url_in_next():
    """``next`` yang bukan path lokal (mis. "//evil.com") ditolak, pakai default."""
    repo = FakeRepository([_website("w1", "alpha.com")])
    resp = _client(repo).post("/websites/w1/pause", data={"next": "//evil.com"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/websites"


def test_paused_website_shows_paused_status_badge():
    """Website dijeda menampilkan badge status "Paused" pada tabel."""
    repo = FakeRepository([_website("w1", "alpha.com", paused=True)])
    body = _client(repo).get("/websites").text
    assert "Paused" in body
    assert "status-paused" in body


# --- Sidebar ------------------------------------------------------------------ #


def test_sidebar_marks_websites_as_active():
    """Sidebar menandai menu "Websites" sebagai aktif pada halaman ini."""
    repo = FakeRepository([])
    body = _client(repo).get("/websites").text
    assert 'href="/websites" class="active"' in body or "active" in body
    assert "aria-current=\"page\"" in body


# --- Kegagalan pemuatan -------------------------------------------------------- #


def test_load_failure_shows_error_indication():
    """Kegagalan pemuatan menampilkan indikasi kesalahan tanpa mutasi data."""
    client = TestClient(create_app(FailingRepository()), raise_server_exceptions=False)
    resp = client.get("/websites")
    assert resp.status_code == 500
    assert "kesalahan" in resp.text.lower()


# --- Integrasi: pause + scheduler melewati website yang dijeda --------------- #


async def test_pause_via_real_repository_and_scheduler_skips_it(tmp_path):
    """Integrasi: jeda via rute web, lalu Scheduler benar-benar melewatinya."""

    class FakeOrchestrator:
        def __init__(self):
            self.calls: List[str] = []

        async def check_website(self, website):
            self.calls.append(website.id)
            return None

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website("w1", "aktif.com", "Aktif"))
        await repo.add_website(_website("w2", "dijeda.com", "Dijeda"))

        client = TestClient(create_app(repo), follow_redirects=False)
        resp = client.post("/websites/w2/pause")
        assert resp.status_code == 303

        stored = await repo.get_website("w2")
        assert stored.paused is True

        orch = FakeOrchestrator()
        scheduler = Scheduler(repo, orch)
        launched = await scheduler.run_iteration()

        assert "w1" in launched
        assert "w2" not in launched
        assert "w2" not in orch.calls

        # Lanjutkan kembali -> website tersedia untuk dijadwalkan lagi.
        resp = client.post("/websites/w2/resume")
        assert resp.status_code == 303
        stored = await repo.get_website("w2")
        assert stored.paused is False


async def test_websites_page_via_real_repository(tmp_path):
    """Integrasi ringan: halaman Websites merender data dari Repository nyata."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website("w1", "nyata.com", "Situs Nyata"))
        client = TestClient(create_app(repo))
        body = client.get("/websites").text
        assert "nyata.com" in body
        assert "Situs Nyata" in body
