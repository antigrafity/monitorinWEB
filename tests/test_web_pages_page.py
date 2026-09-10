"""Unit & integrasi test halaman Pages ContentMonitor (Tahap 2).

Mencakup ``GET /pages`` beserta jeda/lanjutkan per halaman dan pencatatan
kegagalan (status "Broken") sesuai spesifikasi Tahap 2:

- tab All/Active/Paused/Broken menghitung & memfilter dengan benar;
- search (`q`) + filter website + pagination bekerja;
- halaman ditemukan otomatis: TIDAK ada tombol "+ Add Page"/interval per
  halaman (Interval yang ditampilkan adalah interval EFEKTIF website induk);
- jeda halaman (POST /pages/pause) membuat CheckOrchestrator melewatinya;
- halaman gagal fetch tercatat sebagai "Broken" (page_state.last_error), dan
  halaman yang kembali berhasil membersihkan status Broken tersebut.

Menggunakan repository palsu (fake) untuk unit test tampilan, dan
:class:`~monitoring.infra.repository.Repository` nyata pada ``tmp_path``
untuk integrasi (page_state, CheckOrchestrator).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi.testclient import TestClient

from monitoring.app.orchestrator import CheckOrchestrator
from monitoring.domain.models import Snapshot, WebsiteConfig
from monitoring.infra.discovery import DiscoveryResult
from monitoring.infra.fetcher import FetchResult, ImageFetchResult
from monitoring.infra.repository import PageOverview, Repository
from monitoring.web.app import PAGES_PAGE_SIZE, create_app


def _website(website_id: str, domain: str, name: Optional[str] = None, paused: bool = False) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name or domain,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        paused=paused,
    )


def _page(
    url: str,
    website: WebsiteConfig,
    last_checked_at: Optional[datetime] = None,
    last_change_at: Optional[datetime] = None,
    page_paused: bool = False,
    last_error: Optional[str] = None,
) -> PageOverview:
    return PageOverview(
        url=url,
        website=website,
        last_checked_at=last_checked_at,
        last_change_at=last_change_at,
        page_paused=page_paused,
        last_error=last_error,
        last_error_at=datetime.now() if last_error else None,
    )


class FakeRepository:
    """Repository palsu untuk halaman Pages: daftar disiapkan secara eksplisit."""

    def __init__(
        self,
        websites: Optional[List[WebsiteConfig]] = None,
        pages: Optional[List[PageOverview]] = None,
    ):
        self._websites = {w.id: w for w in (websites or [])}
        self._pages = list(pages or [])
        self.pause_calls: List[tuple] = []

    async def list_websites(self):
        return list(self._websites.values())

    async def list_pages(self, website_id=None):
        if website_id is None:
            return list(self._pages)
        return [p for p in self._pages if p.website.id == website_id]

    async def set_page_paused(self, url, paused):
        self.pause_calls.append((url, paused))
        for i, p in enumerate(self._pages):
            if p.url == url:
                self._pages[i] = _page(
                    p.url,
                    p.website,
                    p.last_checked_at,
                    p.last_change_at,
                    page_paused=paused,
                    last_error=p.last_error,
                )


class FailingRepository:
    """Repository palsu yang selalu gagal memuat data."""

    async def list_websites(self):
        raise RuntimeError("Data_Store tidak dapat diakses")


def _client(repository) -> TestClient:
    return TestClient(create_app(repository), follow_redirects=False)


# --- Tab status All/Active/Paused/Broken --------------------------------- #


def test_tabs_count_and_filter_correctly():
    """Tab All/Active/Paused/Broken menghitung & memfilter dengan benar."""
    w1 = _website("w1", "a.com")
    pages = [
        _page("https://a.com/active", w1, last_checked_at=datetime.now()),
        _page("https://a.com/paused", w1, page_paused=True),
        _page("https://a.com/broken", w1, last_error="Gagal mengambil halaman"),
    ]
    repo = FakeRepository([w1], pages)

    body_all = _client(repo).get("/pages?status=all").text
    assert "a.com/active" in body_all
    assert "a.com/paused" in body_all
    assert "a.com/broken" in body_all

    body_active = _client(repo).get("/pages?status=active").text
    assert "a.com/active" in body_active
    assert "a.com/paused" not in body_active
    assert "a.com/broken" not in body_active

    body_paused = _client(repo).get("/pages?status=paused").text
    assert "a.com/paused" in body_paused
    assert "a.com/active" not in body_paused
    assert "a.com/broken" not in body_paused

    body_broken = _client(repo).get("/pages?status=broken").text
    assert "a.com/broken" in body_broken
    assert "a.com/active" not in body_broken
    assert "a.com/paused" not in body_broken


def test_tab_counts_shown_in_labels():
    """Jumlah tiap tab ditampilkan pada label tab."""
    w1 = _website("w1", "a.com")
    pages = [
        _page("https://a.com/1", w1, last_checked_at=datetime.now()),
        _page("https://a.com/2", w1, last_checked_at=datetime.now()),
        _page("https://a.com/3", w1, page_paused=True),
        _page("https://a.com/4", w1, last_error="gagal"),
    ]
    repo = FakeRepository([w1], pages)
    body = _client(repo).get("/pages").text
    assert '<span class="tab-count">4</span>' in body  # All
    assert '<span class="tab-count">2</span>' in body  # Active
    assert '<span class="tab-count">1</span>' in body  # Paused/Broken (masing2 1)


def test_website_paused_makes_page_paused_status():
    """Halaman dianggap "Paused" bila website induknya dijeda (bukan page itu sendiri)."""
    w1 = _website("w1", "a.com", paused=True)
    pages = [_page("https://a.com/1", w1, last_checked_at=datetime.now())]
    repo = FakeRepository([w1], pages)
    body = _client(repo).get("/pages?status=paused").text
    assert "a.com/1" in body


def test_broken_takes_priority_over_paused_in_label():
    """Status "Broken" diprioritaskan di atas "Paused" pada label (Tahap 2)."""
    w1 = _website("w1", "a.com")
    pages = [
        _page("https://a.com/1", w1, page_paused=True, last_error="gagal fetch")
    ]
    repo = FakeRepository([w1], pages)
    body = _client(repo).get("/pages?status=broken").text
    assert "a.com/1" in body
    body_paused = _client(repo).get("/pages?status=paused").text
    assert "a.com/1" not in body_paused


# --- Search & filter website ------------------------------------------------ #


def test_search_filters_by_url():
    """Search (`q`) mencocokkan path/URL halaman."""
    w1 = _website("w1", "a.com")
    pages = [
        _page("https://a.com/promo", w1),
        _page("https://a.com/contact", w1),
    ]
    repo = FakeRepository([w1], pages)
    body = _client(repo).get("/pages?q=promo").text
    assert "a.com/promo" in body
    assert "a.com/contact" not in body


def test_website_filter_restricts_to_one_website():
    """Filter `website` membatasi hasil ke satu Monitored_Website."""
    w1 = _website("w1", "a.com")
    w2 = _website("w2", "b.com")
    pages = [_page("https://a.com/1", w1), _page("https://b.com/1", w2)]
    repo = FakeRepository([w1, w2], pages)
    body = _client(repo).get("/pages?website=w1").text
    assert "a.com/1" in body
    assert "b.com/1" not in body


# --- Pagination -------------------------------------------------------------- #


def test_pagination_shows_range_text():
    """Pagination membatasi baris & menampilkan "Showing X to Y of Z pages"."""
    w1 = _website("w1", "a.com")
    pages = [_page(f"https://a.com/{i}", w1) for i in range(15)]
    repo = FakeRepository([w1], pages)
    body = _client(repo).get("/pages?page=1").text
    assert "Showing 1 to {0} of 15 pages".format(PAGES_PAGE_SIZE) in body

    body2 = _client(repo).get("/pages?page=2").text
    assert "Showing 11 to 15 of 15 pages" in body2


def test_pagination_out_of_range_does_not_error():
    """Halaman di luar rentang dijepit, TIDAK error."""
    w1 = _website("w1", "a.com")
    pages = [_page(f"https://a.com/{i}", w1) for i in range(3)]
    repo = FakeRepository([w1], pages)
    resp = _client(repo).get("/pages?page=999")
    assert resp.status_code == 200
    assert "Showing 1 to 3 of 3 pages" in resp.text


# --- Interval efektif website (bukan per-halaman) --------------------------- #


def test_no_add_page_button_and_shows_effective_website_interval():
    """TIDAK ada tombol "+ Add Page"; interval yang tampil adalah interval WEBSITE."""
    w1 = _website("w1", "a.com")
    pages = [_page("https://a.com/1", w1)]
    repo = FakeRepository([w1], pages)
    body = _client(repo).get("/pages").text
    assert "+ Add Page" not in body
    assert "Global (6 jam)" in body


# --- Empty state -------------------------------------------------------- #


def test_empty_state_when_no_pages_at_all():
    """Belum ada halaman sama sekali -> empty state "belum ada halaman"."""
    repo = FakeRepository([_website("w1", "a.com")], [])
    body = _client(repo).get("/pages").text
    assert "Belum ada halaman yang terpantau" in body


def test_empty_state_no_results_for_filter():
    """Ada halaman, tapi tidak ada yang cocok filter -> pesan berbeda."""
    w1 = _website("w1", "a.com")
    pages = [_page("https://a.com/1", w1)]
    repo = FakeRepository([w1], pages)
    body = _client(repo).get("/pages?q=tidakketemu").text
    assert "Tidak ada halaman untuk filter ini" in body
    assert "Belum ada halaman yang terpantau" not in body


# --- Jeda / Lanjutkan (unit, fake repo) -------------------------------------- #


def test_pause_page_calls_repository_and_redirects():
    """POST /pages/pause -> set_page_paused(url, True), redirect 303."""
    w1 = _website("w1", "a.com")
    pages = [_page("https://a.com/1", w1)]
    repo = FakeRepository([w1], pages)
    resp = _client(repo).post(
        "/pages/pause", data={"url": "https://a.com/1", "next": "/pages"}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/pages"
    assert repo.pause_calls == [("https://a.com/1", True)]


def test_resume_page_calls_repository_and_redirects():
    """POST /pages/resume -> set_page_paused(url, False), redirect 303."""
    w1 = _website("w1", "a.com")
    pages = [_page("https://a.com/1", w1, page_paused=True)]
    repo = FakeRepository([w1], pages)
    resp = _client(repo).post(
        "/pages/resume", data={"url": "https://a.com/1", "next": "/pages"}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/pages"
    assert repo.pause_calls == [("https://a.com/1", False)]


def test_pause_redirect_preserves_active_filter_via_next():
    """Redirect setelah jeda mempertahankan filter aktif lewat `next`."""
    w1 = _website("w1", "a.com")
    pages = [_page("https://a.com/1", w1)]
    repo = FakeRepository([w1], pages)
    next_url = "/pages?status=active&website=w1&q=foo&page=1"
    resp = _client(repo).post(
        "/pages/pause", data={"url": "https://a.com/1", "next": next_url}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == next_url


def test_pause_redirect_rejects_external_url():
    """``next`` eksternal ("//evil.com") ditolak, pakai default /pages."""
    w1 = _website("w1", "a.com")
    repo = FakeRepository([w1], [_page("https://a.com/1", w1)])
    resp = _client(repo).post(
        "/pages/pause", data={"url": "https://a.com/1", "next": "//evil.com"}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/pages"


# --- Sidebar --------------------------------------------------------------- #


def test_sidebar_marks_pages_as_active():
    """Sidebar menandai menu "Pages" sebagai aktif pada halaman ini."""
    repo = FakeRepository([], [])
    body = _client(repo).get("/pages").text
    assert 'href="/pages" class="active"' in body
    assert 'aria-current="page"' in body


# --- Kegagalan pemuatan ------------------------------------------------------ #


def test_load_failure_shows_error_indication():
    """Kegagalan pemuatan menampilkan indikasi kesalahan tanpa mutasi data."""
    client = TestClient(
        create_app(FailingRepository()), raise_server_exceptions=False
    )
    resp = client.get("/pages")
    assert resp.status_code == 500
    assert "kesalahan" in resp.text.lower()


# --- Integrasi: Repository nyata + jeda per halaman -------------------------- #


def _cfg(website_id: str, domain: str) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=domain,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


async def test_pages_page_via_real_repository(tmp_path):
    """Integrasi ringan: halaman Pages merender data dari Repository nyata."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_cfg("w1", "nyata.com"))
        await repo.save_snapshot(
            Snapshot(
                url="https://nyata.com/",
                website_id="w1",
                normalized_text="halo",
                content_hash="h1",
                checked_at=datetime.now(),
            )
        )
        client = TestClient(create_app(repo))
        body = client.get("/pages").text
        assert "nyata.com" in body


async def test_pause_page_via_web_route_persists_and_shown_in_paused_tab(tmp_path):
    """Integrasi: jeda via rute web tersimpan & muncul di tab Paused."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_cfg("w1", "nyata.com"))
        url = "https://nyata.com/"
        await repo.save_snapshot(
            Snapshot(
                url=url,
                website_id="w1",
                normalized_text="halo",
                content_hash="h1",
                checked_at=datetime.now(),
            )
        )
        client = TestClient(create_app(repo), follow_redirects=False)
        resp = client.post("/pages/pause", data={"url": url})
        assert resp.status_code == 303

        states = await repo.get_page_states()
        assert len(states) == 1
        assert states[0].url == url
        assert states[0].paused is True

        body = client.get("/pages?status=paused").text
        assert "nyata.com" in body


# --- Integrasi: CheckOrchestrator melewati halaman yang dijeda ------------- #


class FakeFetcher:
    """Fetcher tiruan minimal untuk uji orkestrator (Tahap 2)."""

    def __init__(self, pages: Dict[str, Optional[str]]):
        self._pages = pages
        self.fetched_pages: List[str] = []

    async def fetch_page(self, url: str) -> FetchResult:
        self.fetched_pages.append(url)
        html = self._pages.get(url)
        if html is None:
            return FetchResult(
                url=url, ok=False, status_code=500, html=None,
                failure_reason="disimulasikan gagal",
            )
        return FetchResult(url=url, ok=True, status_code=200, html=html, failure_reason=None)

    async def fetch_image(self, url: str) -> ImageFetchResult:
        return ImageFetchResult(url=url, ok=False, content=None, failure_reason="n/a")


class FakeNotifier:
    async def notify(self, event, website) -> bool:
        return True


async def test_orchestrator_skips_paused_page(tmp_path, monkeypatch):
    """Halaman yang dijeda TIDAK di-fetch oleh CheckOrchestrator (Tahap 2)."""
    active_url = "https://example.com/active"
    paused_url = "https://example.com/paused"
    pages = {
        active_url: "<html><body><h1>Aktif</h1><p>konten</p></body></html>",
        paused_url: "<html><body><h1>Dijeda</h1><p>konten</p></body></html>",
    }
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _cfg("w1", "example.com")
        await repo.add_website(website)
        await repo.set_page_paused(paused_url, True)
        # website_id untuk baris page_state di atas akan kosong ("") karena
        # belum ada Snapshot; set ulang lewat baris langsung agar terkait w1.
        conn = repo._require_conn()
        await conn.execute(
            "UPDATE page_state SET website_id = ? WHERE url = ?",
            ("w1", paused_url),
        )
        await conn.commit()

        async def fake_discover(website, f):
            return DiscoveryResult(
                urls=[active_url, paused_url], failed=False, failure_reason=None
            )

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        orch = CheckOrchestrator(repo, fetcher, notifier)
        result = await orch.check_website(website)

        assert active_url in fetcher.fetched_pages
        assert paused_url not in fetcher.fetched_pages
        assert result.pages_checked == 1


# --- Integrasi: kegagalan fetch -> "Broken"; sukses -> bersihkan ----------- #


async def test_failed_page_fetch_marks_broken_status(tmp_path, monkeypatch):
    """Halaman gagal fetch -> page_state.last_error terisi -> status "Broken"."""
    url = "https://example.com/"
    fetcher = FakeFetcher({url: None})  # None -> selalu gagal
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _cfg("w1", "example.com")
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        orch = CheckOrchestrator(repo, fetcher, notifier)
        await orch.check_website(website)

        states = await repo.get_page_states(website_id="w1")
        assert len(states) == 1
        assert states[0].url == url
        assert states[0].last_error is not None


async def test_successful_page_fetch_clears_broken_status(tmp_path, monkeypatch):
    """Halaman yang kembali berhasil diambil membersihkan status "Broken"."""
    url = "https://example.com/"
    html_ok = "<html><body><h1>OK</h1><p>konten</p></body></html>"
    pages = {url: None}  # gagal dulu
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _cfg("w1", "example.com")
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        orch = CheckOrchestrator(repo, fetcher, notifier)
        await orch.check_website(website)  # gagal -> Broken

        states = await repo.get_page_states(website_id="w1")
        assert states[0].last_error is not None

        # Sekarang halaman berhasil diambil.
        pages[url] = html_ok
        await orch.check_website(website)

        states_after = await repo.get_page_states(website_id="w1")
        assert states_after[0].last_error is None

        # Terlihat sebagai "Active" (bukan "Broken") pada halaman Pages.
        from monitoring.web.app import create_app as _create_app
        from fastapi.testclient import TestClient as _TestClient

        client = _TestClient(_create_app(repo))
        body = client.get("/pages?status=broken").text
        assert "Tidak ada halaman untuk filter ini" in body
