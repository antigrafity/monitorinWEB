"""Uji integrasi menyeluruh (end-to-end dengan mock jaringan) — task 16.

Menjalankan SATU siklus penuh Monitoring_System memakai komponen NYATA yang
dirangkai apa adanya, dengan satu-satunya titik tiruan berada di lapisan
jaringan (``httpx.MockTransport``) dan pada penemuan halaman
(``discover_pages`` di-stub agar test mengendalikan persis halaman mana yang
diproses). Dengan demikian pipeline lengkap teruji end-to-end:

    discover_pages (stub) -> Fetcher(MockTransport) -> normalize -> hash ->
    Snapshot -> detect_changes -> Repository (SQLite nyata) -> Notifier (fake).

Komponen nyata yang dipakai:

- :class:`monitoring.infra.repository.Repository` pada file SQLite sementara
  (``tmp_path``).
- :class:`monitoring.app.orchestrator.CheckOrchestrator` apa adanya.
- :class:`monitoring.infra.fetcher.Fetcher` dengan ``httpx.MockTransport`` yang
  menyajikan HTML/gambar terkanal berdasarkan path URL. Isi disimpan dalam
  ``dict`` yang bisa diubah (mutable) sehingga test dapat mengganti konten
  antar siklus.
- Web Dashboard nyata via ``TestClient(create_app(repository))``.

Skenario (Req 6.5, 7.4, 12.2, 12.3):

1. **Baseline** — siklus pertama tidak membuat Change_Event, Notifier tidak
   dipanggil, Snapshot tersimpan, ``last_status='success'`` (Req 12.3, 6.7).
2. **Perubahan teks/link/section** — siklus kedua mengubah HTML sebuah halaman
   (teks + link + heading/section) → tepat satu Change_Event untuk halaman itu
   dan Notifier dipanggil sekali (Req 6.5, 12.2); event terpersist & Diff
   mencerminkan perubahan.
3. **Perubahan gambar URL-sama-isi-beda** — HTML tak berubah tetapi ``<img>``
   pada URL yang sama kini mengembalikan bytes berbeda → Change_Event karena
   ``images_changed`` (Req 7.4), Notifier dipanggil.
4. **Halaman gagal fetch tidak menghentikan siklus** — satu URL mengembalikan
   status non-2xx; halaman lain tetap diproses (Req 3.3 mendukung 12.3).
5. **Dashboard hanya menampilkan halaman yang berubah** — setelah siklus
   perubahan, ``GET /`` dan ``GET /websites/{id}`` menampilkan riwayat hanya
   untuk halaman yang berubah (Req 12.3).

Determinisme & kecepatan: tanpa jaringan nyata dan tanpa ``sleep``. Sebuah
clock monoton di-inject sebagai ``now`` agar Snapshot berturut memiliki
``checked_at`` menaik (PRIMARY KEY snapshot = ``url`` + ``checked_at``).

Kompatibilitas: target runtime Python 3.9. ``asyncio.Semaphore``/``Fetcher``
dikonstruksi di dalam event loop yang berjalan (di dalam coroutine test).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
from fastapi.testclient import TestClient

from monitoring.app.orchestrator import CheckOrchestrator
from monitoring.domain.models import WebsiteConfig
from monitoring.infra.discovery import DiscoveryResult
from monitoring.infra.fetcher import Fetcher, make_semaphore
from monitoring.infra.repository import Repository
from monitoring.web.app import create_app


# --- Konstanta URL halaman & gambar yang disajikan mock ------------------ #

DOMAIN = "example.com"
HOME = "https://example.com/"
ABOUT = "https://example.com/about"
PRODUCTS = "https://example.com/products"
BROKEN = "https://example.com/broken"
LOGO_PATH = "/logo.png"
LOGO_URL = "https://example.com/logo.png"

WEBSITE_ID = "web-e2e-1"


# --- Konten terkanal (mutable) & handler MockTransport ------------------- #


def _baseline_pages() -> Dict[str, Optional[str]]:
    """Kembalikan pemetaan path -> HTML baseline (dapat diubah antar siklus).

    HOME memuat sebuah ``<img>`` (untuk skenario perubahan gambar) dan tautan
    internal ke ABOUT & PRODUCTS.
    """
    return {
        "/": (
            "<html><body>"
            "<h1>Beranda</h1>"
            "<p>Selamat datang di situs kami</p>"
            '<img src="/logo.png">'
            '<a href="/about">Tentang</a>'
            '<a href="/products">Produk</a>'
            "</body></html>"
        ),
        "/about": (
            "<html><body>"
            "<h1>Tentang Kami</h1>"
            "<p>Kami adalah perusahaan A</p>"
            '<a href="/">Beranda</a>'
            "</body></html>"
        ),
        "/products": (
            "<html><body>"
            "<h1>Produk</h1>"
            "<p>Daftar produk kami</p>"
            "</body></html>"
        ),
    }


class MockSite:
    """Situs tiruan yang menyajikan HTML & gambar terkanal via ``MockTransport``.

    Isi disimpan pada ``pages`` (path -> HTML atau ``None``) dan ``images``
    (path -> bytes). Test dapat mengubah keduanya antar siklus untuk
    mensimulasikan perubahan konten. Path ``/broken`` selalu mengembalikan
    status 500 untuk mensimulasikan kegagalan fetch (Req 3.3, 3.4).
    """

    def __init__(self) -> None:
        self.pages: Dict[str, Optional[str]] = _baseline_pages()
        self.images: Dict[str, bytes] = {LOGO_PATH: b"logo-versi-1"}
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path

        # Gambar (Req 7.1): sajikan bytes terkanal.
        if path in self.images:
            return httpx.Response(
                200,
                content=self.images[path],
                headers={"content-type": "image/png"},
            )

        # Halaman yang sengaja gagal (Req 3.3/3.4): status non-2xx.
        if path == "/broken":
            return httpx.Response(500, text="kesalahan server")

        body = self.pages.get(path)
        if body is None:
            return httpx.Response(404, text="tidak ditemukan")
        return httpx.Response(200, html=body)


class RecordingNotifier:
    """Notifier tiruan yang merekam setiap pemanggilan ``notify`` (Req 12.2)."""

    def __init__(self, return_value: bool = True) -> None:
        self._return_value = return_value
        self.calls: List = []

    async def notify(self, event, website) -> bool:
        self.calls.append((event, website))
        return self._return_value


class MonotonicClock:
    """Penyedia waktu monoton agar ``checked_at`` berturut selalu menaik.

    Diperlukan karena PRIMARY KEY tabel ``snapshot`` adalah ``(url,
    checked_at)``; tanpa waktu menaik, Snapshot siklus kedua untuk URL yang
    sama akan bertabrakan dengan baseline.
    """

    def __init__(self, start: datetime) -> None:
        self._t = start

    def __call__(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


def _website() -> WebsiteConfig:
    return WebsiteConfig(
        id=WEBSITE_ID,
        domain=DOMAIN,
        name="Situs Example",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _stub_discovery(monkeypatch, urls: List[str]) -> None:
    """Stub ``discover_pages`` agar mengembalikan ``urls`` tetap (tanpa jaringan)."""

    async def fake_discover(website, fetcher):
        return DiscoveryResult(urls=list(urls), failed=False, failure_reason=None)

    monkeypatch.setattr(
        "monitoring.app.orchestrator.discover_pages", fake_discover
    )


async def _last_check_row(repo: Repository, website_id: str):
    conn = repo._require_conn()
    async with conn.execute(
        "SELECT last_checked_at, last_status FROM website_config WHERE id = ?",
        (website_id,),
    ) as cur:
        return await cur.fetchone()


# --- Skenario 1: Baseline ------------------------------------------------- #


async def test_baseline_cycle_creates_no_events(tmp_path, monkeypatch):
    """Siklus baseline: tidak ada event/notif, Snapshot tersimpan, status success.

    Validates: Requirements 12.3, 6.7
    """
    site = MockSite()
    notifier = RecordingNotifier()
    urls = [HOME, ABOUT, PRODUCTS]
    _stub_discovery(monkeypatch, urls)

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        fetcher = Fetcher(make_semaphore(10), transport=site.transport)
        orch = CheckOrchestrator(
            repo, fetcher, notifier, now=MonotonicClock(datetime(2024, 1, 1))
        )

        result = await orch.check_website(_website())

        # Seluruh halaman diproses, tidak ada perubahan pada baseline (Req 6.7).
        assert result.pages_checked == 3
        assert result.changes_detected == 0
        assert result.notifications_sent == 0
        assert notifier.calls == []
        assert result.status == "success"
        assert result.last_check_updated is True

        # Tidak ada Change_Event yang dibuat untuk siklus baseline (Req 12.3).
        assert await repo.list_change_events(WEBSITE_ID) == []

        # Snapshot tersimpan untuk setiap halaman.
        for url in urls:
            assert await repo.get_latest_snapshot(url) is not None

        # Status pemeriksaan terakhir = success.
        row = await _last_check_row(repo, WEBSITE_ID)
        assert row[1] == "success"
        assert row[0] is not None


# --- Skenario 2: Perubahan teks/link/section ------------------------------ #


async def test_text_link_section_change_creates_single_event_and_notifies(
    tmp_path, monkeypatch
):
    """Perubahan teks + link + heading pada satu halaman → tepat satu event + notif.

    Validates: Requirements 6.5, 12.2
    """
    site = MockSite()
    notifier = RecordingNotifier()
    urls = [HOME, ABOUT, PRODUCTS]
    _stub_discovery(monkeypatch, urls)

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        fetcher = Fetcher(make_semaphore(10), transport=site.transport)
        orch = CheckOrchestrator(
            repo, fetcher, notifier, now=MonotonicClock(datetime(2024, 1, 1))
        )

        # Siklus 1: baseline (tanpa event).
        await orch.check_website(_website())
        assert notifier.calls == []
        assert await repo.list_change_events(WEBSITE_ID) == []

        # Siklus 2: ubah HALAMAN ABOUT — teks berubah, heading/section berubah,
        # dan sebuah link baru (/kontak) ditambahkan.
        site.pages["/about"] = (
            "<html><body>"
            "<h1>Tentang Kami Terbaru</h1>"
            "<p>Kami adalah perusahaan A yang telah berkembang</p>"
            '<a href="/">Beranda</a>'
            '<a href="/kontak">Kontak</a>'
            "</body></html>"
        )
        result = await orch.check_website(_website())

        # Tepat satu halaman berubah, satu notifikasi terkirim (Req 6.5, 12.2).
        assert result.changes_detected == 1
        assert result.notifications_sent == 1
        assert len(notifier.calls) == 1

        # Event terpersist hanya untuk halaman ABOUT (Req 12.2/12.3).
        events = await repo.list_change_events(WEBSITE_ID)
        assert len(events) == 1
        event = events[0]
        assert event.url == ABOUT

        # Diff mencerminkan perubahan teks, link, dan section.
        diff = event.diff
        assert diff.text_added, "harus ada baris teks yang ditambahkan"
        assert diff.text_removed, "harus ada baris teks yang dihapus"
        assert "https://example.com/kontak" in diff.links_added
        # Heading berubah → section lama dihapus & section baru ditambahkan.
        assert diff.sections_added or diff.sections_removed

        # Notifikasi merujuk event/halaman yang benar.
        notified_event, notified_website = notifier.calls[0]
        assert notified_event.url == ABOUT
        assert notified_website.id == WEBSITE_ID


# --- Skenario 3: Perubahan gambar URL-sama-isi-beda ----------------------- #


async def test_image_same_url_different_content_creates_event(
    tmp_path, monkeypatch
):
    """HTML tak berubah tetapi isi gambar pada URL sama berubah → event (Req 7.4).

    Validates: Requirements 7.4
    """
    site = MockSite()
    notifier = RecordingNotifier()
    urls = [HOME, ABOUT, PRODUCTS]
    _stub_discovery(monkeypatch, urls)

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        fetcher = Fetcher(make_semaphore(10), transport=site.transport)
        orch = CheckOrchestrator(
            repo, fetcher, notifier, now=MonotonicClock(datetime(2024, 1, 1))
        )

        # Siklus 1: baseline dengan gambar versi 1.
        await orch.check_website(_website())
        assert notifier.calls == []

        # Siklus 2: HTML SAMA PERSIS, tetapi isi gambar (URL sama) berubah.
        site.images[LOGO_PATH] = b"logo-versi-2-berbeda-total"
        result = await orch.check_website(_website())

        # Hanya HOME (satu-satunya halaman bergambar) yang berubah (Req 7.4).
        assert result.changes_detected == 1
        assert result.notifications_sent == 1
        assert len(notifier.calls) == 1

        events = await repo.list_change_events(WEBSITE_ID)
        assert len(events) == 1
        event = events[0]
        assert event.url == HOME
        # Perubahan terjadi pada gambar URL-sama-isi-beda (Req 7.4).
        assert LOGO_URL in event.diff.images_changed
        # Teks tidak berubah pada skenario ini.
        assert event.diff.text_added == []
        assert event.diff.text_removed == []


# --- Skenario 4: Halaman gagal fetch tidak menghentikan siklus ------------ #


async def test_page_fetch_failure_does_not_stop_cycle(tmp_path, monkeypatch):
    """Satu URL gagal fetch (500); halaman lain tetap diproses (Req 3.3).

    Validates: Requirements 12.3
    """
    site = MockSite()
    notifier = RecordingNotifier()
    # Sertakan BROKEN (selalu 500) di tengah daftar untuk memastikan halaman
    # setelahnya tetap diproses.
    urls = [HOME, BROKEN, ABOUT, PRODUCTS]
    _stub_discovery(monkeypatch, urls)

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        fetcher = Fetcher(make_semaphore(10), transport=site.transport)
        orch = CheckOrchestrator(
            repo, fetcher, notifier, now=MonotonicClock(datetime(2024, 1, 1))
        )

        result = await orch.check_website(_website())

        # Tiga halaman baik diproses, satu gagal — siklus tidak berhenti.
        assert result.pages_checked == 3
        assert result.page_failures == 1
        assert result.status == "success"

        # Halaman baik memiliki Snapshot; halaman gagal tidak.
        for url in (HOME, ABOUT, PRODUCTS):
            assert await repo.get_latest_snapshot(url) is not None
        assert await repo.get_latest_snapshot(BROKEN) is None


# --- Skenario 5: Dashboard hanya menampilkan halaman yang berubah --------- #


async def test_dashboard_reflects_only_changed_pages(tmp_path, monkeypatch):
    """Dashboard menampilkan riwayat hanya untuk halaman yang berubah (Req 12.3).

    Validates: Requirements 12.3
    """
    site = MockSite()
    notifier = RecordingNotifier()
    urls = [HOME, ABOUT, PRODUCTS]
    _stub_discovery(monkeypatch, urls)

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        fetcher = Fetcher(make_semaphore(10), transport=site.transport)
        orch = CheckOrchestrator(
            repo, fetcher, notifier, now=MonotonicClock(datetime(2024, 1, 1))
        )

        # Baseline lalu ubah hanya halaman ABOUT.
        await orch.check_website(_website())
        site.pages["/about"] = (
            "<html><body>"
            "<h1>Tentang Kami Terbaru</h1>"
            "<p>Konten tentang telah diperbarui</p>"
            '<a href="/">Beranda</a>'
            "</body></html>"
        )
        await orch.check_website(_website())

        # Dashboard nyata melalui TestClient.
        client = TestClient(create_app(repo))

        # GET / : daftar website termuat dan status success tampil (Req 10.1/12.3).
        index = client.get("/")
        assert index.status_code == 200
        assert "Situs Example" in index.text

        # GET /websites/{id} : riwayat memuat HANYA halaman yang berubah (ABOUT).
        history = client.get(f"/websites/{WEBSITE_ID}")
        assert history.status_code == 200
        body = history.text
        assert ABOUT in body
        # Halaman yang TIDAK berubah tidak muncul di riwayat (Req 12.3).
        assert PRODUCTS not in body

        # Konsistensi dengan Data_Store: hanya satu event, untuk ABOUT.
        events = await repo.list_change_events(WEBSITE_ID)
        assert [e.url for e in events] == [ABOUT]
