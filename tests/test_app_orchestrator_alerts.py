"""Integrasi CheckOrchestrator <-> Alert (ContentMonitor Tahap 3, bagian E).

Menguji:

- Halaman gagal diakses -> Alert 'critical' tersimpan; ``alerts_created`` > 0.
- Penghapusan konten signifikan -> Alert 'warning' tersimpan.
- Judul/meta berubah -> Alert 'info' tersimpan.
- Sitemap tidak dapat diakses -> Alert 'warning' level-website.
- SSL akan kadaluarsa (via ``ssl_checker`` disuntikkan) -> Alert tersimpan,
  DAN notifikasi ringkas 'critical' dikirim (satu pesan per siklus, bukan
  spam).
- ``ssl_checker`` TIDAK disuntikkan -> cek SSL dilewati sepenuhnya (tidak ada
  Alert SSL, tidak menyentuh jaringan).
- Kegagalan menyimpan Alert TIDAK menghentikan siklus.
- ``CheckResult.alerts_created`` mengagregasi jumlah Alert lintas halaman.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from monitoring.app.orchestrator import CheckOrchestrator
from monitoring.domain.models import WebsiteConfig
from monitoring.infra.discovery import DiscoveryResult
from monitoring.infra.fetcher import FetchResult, ImageFetchResult
from monitoring.infra.repository import Repository


class FakeFetcher:
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
                status_code=404,
                html=None,
                failure_reason="disimulasikan gagal (404)",
            )
        return FetchResult(url=url, ok=True, status_code=200, html=html, failure_reason=None)

    async def fetch_image(self, url: str) -> ImageFetchResult:
        content = self._images.get(url)
        if content is None:
            return ImageFetchResult(url=url, ok=False, content=None, failure_reason="n/a")
        return ImageFetchResult(url=url, ok=True, content=content, failure_reason=None)


class FakeNotifier:
    def __init__(self, return_value: bool = True) -> None:
        self._return_value = return_value
        self.calls: List = []
        self.text_calls: List[str] = []

    async def notify(self, event, website) -> bool:
        self.calls.append((event, website))
        return self._return_value

    async def notify_text(self, text: str) -> bool:
        self.text_calls.append(text)
        return self._return_value


def _website(website_id: str = "web-1", domain: str = "example.com") -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name="Example",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


class _Clock:
    def __init__(self, start: datetime) -> None:
        self._t = start

    def __call__(self) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


# --------------------------------------------------------------------------- #
# 1. Halaman gagal diakses -> Alert 'critical'
# --------------------------------------------------------------------------- #
async def test_page_unreachable_creates_critical_alert_and_sends_summary(
    tmp_path, monkeypatch
):
    url = "https://example.com/"
    fetcher = FakeFetcher({url: None})  # None -> selalu gagal
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _website()
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)

        orch = CheckOrchestrator(repo, fetcher, notifier, now=_Clock(datetime(2024, 1, 1)))
        result = await orch.check_website(website)

        assert result.alerts_created >= 1
        alerts = await repo.list_alerts_filtered(severity="critical")
        assert len(alerts) == 1
        assert alerts[0].alert_type == "page_unreachable"
        assert "404" in alerts[0].title

        # Ringkasan critical -> SATU pesan notify_text, bukan notify per-alert.
        assert len(notifier.text_calls) == 1
        assert "1 alert kritis" in notifier.text_calls[0]


# --------------------------------------------------------------------------- #
# 2. Penghapusan konten signifikan -> Alert 'warning'
# --------------------------------------------------------------------------- #
async def test_significant_removal_creates_warning_alert(tmp_path, monkeypatch):
    url = "https://example.com/"
    big_html = "<html><body>" + "".join(f"<p>blok {i}</p>" for i in range(10)) + "</body></html>"
    small_html = "<html><body><p>blok 0</p></body></html>"  # hapus 9 dari 10 blok
    pages = {url: big_html}
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _website()
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)

        clock = _Clock(datetime(2024, 1, 1, 12, 0, 0))
        orch = CheckOrchestrator(repo, fetcher, notifier, now=clock)

        await orch.check_website(website)  # baseline
        pages[url] = small_html
        result = await orch.check_website(website)

        assert result.changes_detected == 1
        alerts = await repo.list_alerts_filtered(severity="warning")
        assert any(a.alert_type == "significant_removal" for a in alerts)


# --------------------------------------------------------------------------- #
# 3 & 4. Judul/meta berubah -> Alert 'info'
# --------------------------------------------------------------------------- #
async def test_title_and_meta_changed_creates_info_alerts(tmp_path, monkeypatch):
    url = "https://example.com/"
    html_v1 = (
        '<html><head><title>Judul Lama</title>'
        '<meta name="description" content="Meta lama"></head>'
        "<body><p>konten</p></body></html>"
    )
    html_v2 = (
        '<html><head><title>Judul Baru</title>'
        '<meta name="description" content="Meta baru"></head>'
        "<body><p>konten</p></body></html>"
    )
    pages = {url: html_v1}
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _website()
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)

        clock = _Clock(datetime(2024, 1, 1, 12, 0, 0))
        orch = CheckOrchestrator(repo, fetcher, notifier, now=clock)

        await orch.check_website(website)  # baseline
        pages[url] = html_v2
        result = await orch.check_website(website)

        assert result.changes_detected == 1
        info_alerts = await repo.list_alerts_filtered(severity="info")
        types = {a.alert_type for a in info_alerts}
        assert "title_changed" in types
        assert "meta_changed" in types


# --------------------------------------------------------------------------- #
# 7. Sitemap tidak dapat diakses -> Alert 'warning' level-website
# --------------------------------------------------------------------------- #
async def test_sitemap_unreachable_creates_warning_alert(tmp_path, monkeypatch):
    url = "https://example.com/"
    fetcher = FakeFetcher({url: "<html><body><p>halo</p></body></html>"})
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _website()
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(
                urls=[url],
                failed=False,
                failure_reason=None,
                sitemap_unreachable=True,
            )

        monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)

        orch = CheckOrchestrator(repo, fetcher, notifier, now=_Clock(datetime(2024, 1, 1)))
        result = await orch.check_website(website)

        assert result.alerts_created >= 1
        alerts = await repo.list_alerts_filtered(severity="warning")
        assert any(a.alert_type == "sitemap_unreachable" for a in alerts)
        assert any(a.url is None for a in alerts)


# --------------------------------------------------------------------------- #
# 8. SSL akan kadaluarsa (ssl_checker disuntikkan) -> Alert + notifikasi
# --------------------------------------------------------------------------- #
async def test_ssl_checker_injected_creates_alert_and_notifies(tmp_path, monkeypatch):
    url = "https://example.com/"
    fetcher = FakeFetcher({url: "<html><body><p>halo</p></body></html>"})
    notifier = FakeNotifier()

    async def fake_ssl_checker(domain: str):
        return datetime(2024, 1, 3, 12, 0, 0)  # 2 hari dari "now" -> critical

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _website()
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)

        clock = _Clock(datetime(2024, 1, 1, 12, 0, 0))
        orch = CheckOrchestrator(
            repo, fetcher, notifier, now=clock, ssl_checker=fake_ssl_checker
        )
        result = await orch.check_website(website)

        assert result.alerts_created >= 1
        alerts = await repo.list_alerts_filtered(severity="critical")
        assert any(a.alert_type == "ssl_expiring" for a in alerts)
        assert len(notifier.text_calls) == 1


async def test_ssl_checker_not_injected_skips_ssl_check_entirely(tmp_path, monkeypatch):
    """Tanpa ssl_checker (bawaan None) -> tidak ada Alert SSL sama sekali."""
    url = "https://example.com/"
    fetcher = FakeFetcher({url: "<html><body><p>halo</p></body></html>"})
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _website()
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)

        orch = CheckOrchestrator(repo, fetcher, notifier, now=_Clock(datetime(2024, 1, 1)))
        await orch.check_website(website)

        alerts = await repo.list_alerts_filtered()
        assert all(a.alert_type != "ssl_expiring" for a in alerts)


# --------------------------------------------------------------------------- #
# Kegagalan menyimpan Alert tidak menghentikan siklus
# --------------------------------------------------------------------------- #
async def test_alert_save_failure_does_not_stop_cycle(tmp_path, monkeypatch):
    url = "https://example.com/"
    fetcher = FakeFetcher({url: None})  # gagal diakses -> mencoba membuat Alert
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _website()
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[url], failed=False, failure_reason=None)

        monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)

        async def fail_save_alert(alert):
            return False

        monkeypatch.setattr(repo, "save_alert", fail_save_alert)

        orch = CheckOrchestrator(repo, fetcher, notifier, now=_Clock(datetime(2024, 1, 1)))
        # Tidak boleh mengangkat exception meski save_alert selalu gagal.
        result = await orch.check_website(website)
        assert result.alerts_created == 0
        assert result.status == "success"


# --------------------------------------------------------------------------- #
# CheckResult.alerts_created mengagregasi lintas halaman
# --------------------------------------------------------------------------- #
async def test_alerts_created_aggregates_across_pages(tmp_path, monkeypatch):
    good = "https://example.com/ok"
    bad = "https://example.com/broken"
    pages = {
        good: "<html><body><p>halaman baik</p></body></html>",
        bad: None,
    }
    fetcher = FakeFetcher(pages)
    notifier = FakeNotifier()

    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        website = _website()
        await repo.add_website(website)

        async def fake_discover(website, f):
            return DiscoveryResult(urls=[good, bad], failed=False, failure_reason=None)

        monkeypatch.setattr("monitoring.app.orchestrator.discover_pages", fake_discover)

        orch = CheckOrchestrator(repo, fetcher, notifier, now=_Clock(datetime(2024, 1, 1)))
        result = await orch.check_website(website)

        assert result.alerts_created == 1  # hanya "bad" yang gagal & memicu alert
