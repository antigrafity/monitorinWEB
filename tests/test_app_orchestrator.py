"""Unit test CheckOrchestrator (task 12).

Memverifikasi kebijakan pelaporan hanya-saat-berubah dan isolasi kegagalan
pada tingkat orkestrasi:

- lapor hanya saat berubah: baseline pertama tidak membuat Change_Event/notif;
  pemeriksaan kedua dengan konten berbeda membuat Change_Event + memicu notif
  (Req 12.1, 12.2, 12.3, 9.5).
- tidak ada notifikasi saat konten identik + last_checked tetap diperbarui
  (Req 12.1).
- kegagalan pembuatan Change_Event: notif tidak dipicu, baseline dipertahankan,
  last_checked tidak diperbarui ke success (Req 12.4).
- kegagalan fetch satu halaman tidak menghentikan pemrosesan halaman lain
  (Req 3.3).

Menggunakan Fetcher & Notifier tiruan (fake) yang mengembalikan hasil terkanal
serta Repository nyata di ``tmp_path``. Waktu di-inject via ``now`` agar
Snapshot berturut memiliki ``checked_at`` berbeda (PRIMARY KEY snapshot).
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from monitoring.app.orchestrator import CheckOrchestrator
from monitoring.domain.models import WebsiteConfig
from monitoring.infra.discovery import DiscoveryResult
from monitoring.infra.fetcher import FetchResult, ImageFetchResult
from monitoring.infra.repository import Repository


# --- Test doubles ------------------------------------------------------- #


class FakeFetcher:
    """Fetcher tiruan: mengembalikan HTML/gambar terkanal per URL.

    ``pages`` memetakan URL -> HTML (atau ``None`` untuk mensimulasikan
    kegagalan fetch). ``images`` memetakan URL gambar -> bytes.
    """

    def __init__(
        self,
        pages: Dict[str, Optional[str]],
        images: Optional[Dict[str, bytes]] = None,
    ) -> None:
        self._pages = pages
        self._images = images or {}
        self.fetched_pages: List[str] = []

    async def fetch_page(self, url: str) -> FetchResult:
        self.fetched_pages.append(url)
        html = self._pages.get(url)
        if html is None:
            return FetchResult(
                url=url,
                ok=False,
                status_code=500,
                html=None,
                failure_reason="disimulasikan gagal",
            )
        return FetchResult(
            url=url, ok=True, status_code=200, html=html, failure_reason=None
        )

    async def fetch_image(self, url: str) -> ImageFetchResult:
        content = self._images.get(url)
        if content is None:
            return ImageFetchResult(
                url=url, ok=False, content=None, failure_reason="tak ada gambar"
            )
        return ImageFetchResult(
            url=url, ok=True, content=content, failure_reason=None
        )


class FakeNotifier:
    """Notifier tiruan yang merekam pemanggilan ``notify``."""

    def __init__(self, return_value: bool = True) -> None:
        self._return_value = return_value
        self.calls: List = []

    async def notify(self, event, website) -> bool:
        self.calls.append((event, website))
        return self._return_value


def _website() -> WebsiteConfig:
    return WebsiteConfig(
        id="web-1",
        domain="example.com",
        name="Example",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


class _Clock:
    """Penyedia waktu monoton untuk memastikan checked_at berturut berbeda."""

    def __init__(self, start: datetime) -> None:
        self._t = start

    def __call__(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


async def _last_check(repo: Repository, website_id: str):
    conn = repo._require_conn()
    async with conn.execute(
        "SELECT last_checked_at, last_status FROM website_config WHERE id = ?",
        (website_id,),
    ) as cur:
        return await cur.fetchone()


# --- Tests -------------------------------------------------------------- #


async def test_baseline_then_change_reports_and_notifies(tmp_path, monkeypatch):
    """Baseline tanpa event/notif; perubahan -> event tersimpan + notif (Req 12.1, 12.2)."""
    url = "https://example.com/"
    pages = {url: "<html><body><h1>Judul</h1><p>versi satu</p></body></html>"}
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())

        # discover_pages di-stub agar mengembalikan satu URL tanpa jaringan.
        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        clock = _Clock(datetime(2024, 1, 1, 12, 0, 0))
        orch = CheckOrchestrator(repo, fetcher, notifier, now=clock)

        # Pemeriksaan pertama = baseline: tidak ada event/notif (Req 6.7).
        r1 = await orch.check_website(_website())
        assert r1.pages_checked == 1
        assert r1.changes_detected == 0
        assert notifier.calls == []
        assert r1.status == "success"
        assert r1.last_check_updated is True
        assert await repo.list_change_events("web-1") == []

        # Ubah konten halaman lalu periksa lagi -> perubahan terdeteksi.
        pages[url] = "<html><body><h1>Judul</h1><p>versi DUA berbeda</p></body></html>"
        r2 = await orch.check_website(_website())
        assert r2.changes_detected == 1
        assert r2.notifications_sent == 1
        assert len(notifier.calls) == 1
        events = await repo.list_change_events("web-1")
        assert len(events) == 1
        assert events[0].url == url


async def test_unchanged_no_notification_but_updates_last_check(tmp_path, monkeypatch):
    """Konten identik -> tidak ada event/notif, last_checked tetap diperbarui (Req 12.1)."""
    url = "https://example.com/"
    html = "<html><body><h1>Judul</h1><p>tetap sama</p></body></html>"
    pages = {url: html}
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        clock = _Clock(datetime(2024, 1, 1, 12, 0, 0))
        orch = CheckOrchestrator(repo, fetcher, notifier, now=clock)

        await orch.check_website(_website())  # baseline
        r2 = await orch.check_website(_website())  # identik

        assert r2.changes_detected == 0
        assert notifier.calls == []
        assert r2.status == "success"
        assert r2.last_check_updated is True

        row = await _last_check(repo, "web-1")
        assert row[1] == "success"
        assert row[0] is not None


async def test_change_event_save_failure_no_notify_no_success(tmp_path, monkeypatch):
    """save_change_event gagal -> tanpa notif, baseline dipertahankan, tanpa success (Req 12.4)."""
    url = "https://example.com/"
    pages = {url: "<html><body><h1>Judul</h1><p>versi satu</p></body></html>"}
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        clock = _Clock(datetime(2024, 1, 1, 12, 0, 0))
        orch = CheckOrchestrator(repo, fetcher, notifier, now=clock)

        await orch.check_website(_website())  # baseline (success)

        # Paksa kegagalan penyimpanan Change_Event.
        async def fail_save_event(evt):
            return False

        monkeypatch.setattr(repo, "save_change_event", fail_save_event)

        pages[url] = "<html><body><h1>Judul</h1><p>berubah total</p></body></html>"
        r2 = await orch.check_website(_website())

        # Notifikasi TIDAK dipicu untuk event yang gagal disimpan (Req 12.4).
        assert notifier.calls == []
        assert r2.event_save_failures == 1
        assert r2.notifications_sent == 0
        # last_checked TIDAK diperbarui ke success pada siklus ini.
        assert r2.status == "incomplete"
        assert r2.last_check_updated is False
        row = await _last_check(repo, "web-1")
        # Masih menyimpan status success dari baseline (tidak diperbarui lagi).
        assert row[1] == "success"


async def test_page_fetch_failure_does_not_stop_cycle(tmp_path, monkeypatch):
    """Satu URL gagal fetch, URL lain tetap diproses (Req 3.3)."""
    good = "https://example.com/ok"
    bad = "https://example.com/broken"
    pages = {
        good: "<html><body><h1>OK</h1><p>halaman baik</p></body></html>",
        bad: None,  # None -> fetch gagal
    }
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())

        async def fake_discover(website, f):
            return DiscoveryResult(
                urls=[bad, good], failed=False, failure_reason=None
            )

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        clock = _Clock(datetime(2024, 1, 1, 12, 0, 0))
        orch = CheckOrchestrator(repo, fetcher, notifier, now=clock)

        r = await orch.check_website(_website())

        # Halaman baik tetap diproses meski satu URL gagal.
        assert r.pages_checked == 1
        assert r.page_failures == 1
        assert good in fetcher.fetched_pages
        # Snapshot untuk halaman baik tersimpan sebagai baseline.
        assert await repo.get_latest_snapshot(good) is not None
        # Halaman gagal tidak menyimpan Snapshot.
        assert await repo.get_latest_snapshot(bad) is None
        # Siklus tidak dianggap gagal total.
        assert r.status == "success"


async def test_discovery_failure_marks_failure(tmp_path, monkeypatch):
    """Discovery gagal (homepage tak dapat diakses) -> last_status failure (Req 2.7)."""
    fetcher = FakeFetcher({})
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())

        async def fake_discover(website, f):
            return DiscoveryResult(
                urls=[], failed=True, failure_reason="homepage tak dapat diakses"
            )

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        orch = CheckOrchestrator(repo, fetcher, notifier)
        r = await orch.check_website(_website())

        assert r.discovery_failed is True
        assert r.status == "failure"
        assert r.last_check_updated is True
        row = await _last_check(repo, "web-1")
        assert row[1] == "failure"


async def test_image_content_change_detected(tmp_path, monkeypatch):
    """URL gambar sama tetapi isi berbeda -> perubahan terdeteksi (Req 7.4)."""
    url = "https://example.com/"
    img = "https://example.com/logo.png"
    html = f'<html><body><h1>Judul</h1><img src="{img}"><p>teks</p></body></html>'
    pages = {url: html}
    fetcher = FakeFetcher(pages, images={img: b"gambar-versi-1"})
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr(
            "monitoring.app.orchestrator.discover_pages", fake_discover
        )

        clock = _Clock(datetime(2024, 1, 1, 12, 0, 0))
        orch = CheckOrchestrator(repo, fetcher, notifier, now=clock)

        await orch.check_website(_website())  # baseline dengan gambar v1
        assert notifier.calls == []

        # HTML sama, tapi isi gambar berubah -> images_changed (Req 7.4).
        fetcher._images[img] = b"gambar-versi-2-berbeda"
        r2 = await orch.check_website(_website())

        assert r2.changes_detected == 1
        assert r2.notifications_sent == 1
