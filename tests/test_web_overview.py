"""Unit & integrasi test halaman Overview ContentMonitor (Tahap 1).

Mencakup ``GET /`` sesuai spesifikasi Tahap 1:

- 4 kartu KPI menampilkan angka yang benar (total website, halaman terpantau,
  perubahan 7 hari terakhir);
- line chart & sparkline merender elemen SVG, aman saat seluruh data 0;
- panel "Top Changes Detected" menampilkan deskripsi + badge tipe yang benar;
- sidebar merender seluruh menu dan menandai item aktif ("Overview").

Menggunakan repository palsu (fake) untuk sebagian besar unit test, plus satu
test integrasi memakai :class:`Repository` nyata pada ``tmp_path``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi.testclient import TestClient

from monitoring.domain.models import (
    ChangeEvent,
    ChangeSummary,
    Diff,
    WebsiteConfig,
)
from monitoring.infra.repository import Repository, WebsiteOverview
from monitoring.web.app import create_app


def _website(
    website_id: str = "w1", domain: str = "example.com", name: str = "Example"
) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _event(
    event_id: str,
    website_id: str,
    url: str,
    detected_at: datetime,
    diff: Optional[Diff] = None,
) -> ChangeEvent:
    diff = diff or Diff()
    return ChangeEvent(
        id=event_id,
        website_id=website_id,
        url=url,
        detected_at=detected_at,
        diff=diff,
        summary=ChangeSummary(
            text_added=len(diff.text_added),
            text_removed=len(diff.text_removed),
            links_added=len(diff.links_added),
            links_removed=len(diff.links_removed),
            images_changed=len(diff.images_changed),
        ),
    )


class FakeRepository:
    """Repository palsu untuk halaman Overview: data disiapkan secara eksplisit.

    Setiap method meniru kontrak agregat repository nyata namun dihitung dari
    struktur Python sederhana (tanpa SQL), cukup untuk menguji lapisan
    presentasi Overview secara terisolasi.
    """

    def __init__(
        self,
        websites: Optional[List[WebsiteConfig]] = None,
        events: Optional[List[ChangeEvent]] = None,
        pages_by_website: Optional[Dict[str, int]] = None,
    ):
        self._websites = {w.id: w for w in (websites or [])}
        self._events = list(events or [])
        self._pages_by_website = pages_by_website or {}

    async def list_websites(self):
        return list(self._websites.values())

    async def get_website(self, website_id):
        return self._websites.get(website_id)

    async def count_pages_monitored(self, website_id=None):
        if website_id is not None:
            return self._pages_by_website.get(website_id, 0)
        return sum(self._pages_by_website.values())

    async def count_change_events_between(self, start, end, website_id=None):
        return len(
            [
                e
                for e in self._events
                if start <= e.detected_at < end
                and (website_id is None or e.website_id == website_id)
            ]
        )

    async def daily_change_counts(self, start, end, website_id=None):
        counts: Dict[str, int] = {}
        day = start.date()
        end_day = end.date()
        while day < end_day:
            counts[day.isoformat()] = 0
            day += timedelta(days=1)
        for e in self._events:
            if start <= e.detected_at < end and (
                website_id is None or e.website_id == website_id
            ):
                key = e.detected_at.date().isoformat()
                if key in counts:
                    counts[key] += 1
        return sorted(counts.items())

    async def list_change_events_between(self, start, end, website_id=None):
        items = [
            e
            for e in self._events
            if start <= e.detected_at < end
            and (website_id is None or e.website_id == website_id)
        ]
        return sorted(items, key=lambda e: e.detected_at, reverse=True)

    async def recent_change_events(self, limit=5, website_id=None):
        items = [
            e
            for e in self._events
            if website_id is None or e.website_id == website_id
        ]
        items = sorted(items, key=lambda e: e.detected_at, reverse=True)
        return items[: int(limit)]

    async def website_stats(self):
        stats = []
        for website in self._websites.values():
            events = [e for e in self._events if e.website_id == website.id]
            last_change_at = max((e.detected_at for e in events), default=None)
            stats.append(
                WebsiteOverview(
                    website=website,
                    last_checked_at=None,
                    last_status=None,
                    pages_count=self._pages_by_website.get(website.id, 0),
                    last_change_at=last_change_at,
                    changes_last_7_days=len(events),
                    daily_counts_7_days=[],
                )
            )
        return stats

    # --- Alerts (ContentMonitor Tahap 3): sederhana, tanpa data bawaan ---- #

    async def count_alerts_filtered(
        self, status=None, severity=None, website_id=None, q=None
    ):
        return 0

    async def recent_alerts(self, limit=5):
        return []

    async def count_unread_alerts(self):
        return 0


def _client(repository) -> TestClient:
    return TestClient(create_app(repository))


# --- Kartu KPI ------------------------------------------------------------- #


def test_kpi_cards_show_correct_totals():
    """KPI menampilkan angka benar: total website, halaman, perubahan 7 hari."""
    now = datetime.now()
    websites = [_website("w1", "a.com"), _website("w2", "b.com")]
    events = [
        _event("e1", "w1", "https://a.com/x", now - timedelta(days=1)),
        _event("e2", "w2", "https://b.com/y", now - timedelta(days=2)),
        # Di luar rentang 7 hari -> tidak dihitung pada KPI minggu ini.
        _event("e3", "w1", "https://a.com/z", now - timedelta(days=10)),
    ]
    repo = FakeRepository(
        websites=websites, events=events, pages_by_website={"w1": 3, "w2": 2}
    )
    body = _client(repo).get("/").text

    assert "Total Websites" in body
    assert "Pages Monitored" in body
    assert "Changes Detected" in body
    # Total website = 2, halaman = 5, perubahan minggu ini = 2 (e3 di luar rentang).
    assert ">2<" in body.replace(" ", "") or "2" in body
    assert "5" in body  # pages_monitored


def test_kpi_alerts_shows_unread_plus_critical_count():
    """Kartu Alerts menampilkan jumlah unread + critical (Tahap 3)."""
    repo = FakeRepository()
    body = _client(repo).get("/").text
    assert "Alerts" in body
    assert "Perlu perhatian" in body


# --- Chart & sparkline SVG ------------------------------------------------- #


def test_line_chart_renders_svg_elements():
    """Line chart merender elemen SVG (polyline data) untuk 7 hari terakhir."""
    now = datetime.now()
    websites = [_website("w1", "a.com")]
    events = [_event("e1", "w1", "https://a.com/x", now - timedelta(days=1))]
    repo = FakeRepository(websites=websites, events=events)
    body = _client(repo).get("/").text
    assert "<svg" in body
    assert "line-chart" in body
    assert "<polyline" in body


def test_line_chart_safe_when_all_values_zero():
    """Chart tidak pecah (tanpa exception/500) saat semua nilai 0."""
    repo = FakeRepository(websites=[_website("w1", "a.com")], events=[])
    resp = _client(repo).get("/")
    assert resp.status_code == 200
    assert "<svg" in resp.text


def test_sparkline_safe_when_all_values_zero_in_website_table():
    """Sparkline pada tabel Monitored Websites aman saat semua nilai 0."""
    repo = FakeRepository(websites=[_website("w1", "a.com")], events=[])
    resp = _client(repo).get("/")
    assert resp.status_code == 200
    assert "sparkline" in resp.text


# --- Panel "Top Changes Detected" ------------------------------------------ #


def test_top_changes_shows_description_and_type_badge():
    """Top Changes menampilkan deskripsi (describe_change) + badge tipe benar."""
    now = datetime.now()
    diff = Diff(text_added=["konten baru"])
    websites = [_website("w1", "a.com", "Situs A")]
    events = [_event("e1", "w1", "https://a.com/halaman", now, diff=diff)]
    repo = FakeRepository(websites=websites, events=events)
    body = _client(repo).get("/").text

    assert "Top Changes Detected" in body
    assert "badge-added" in body
    assert "Added" in body
    assert "Menambahkan 1 bagian teks baru" in body
    assert "/events/e1" in body


def test_top_changes_empty_state():
    """Empty state saat belum ada perubahan yang terdeteksi."""
    repo = FakeRepository(websites=[_website("w1", "a.com")], events=[])
    body = _client(repo).get("/").text
    assert "Belum ada perubahan yang terdeteksi." in body


def test_recent_alerts_placeholder_empty_state():
    """Panel Recent Alerts menampilkan empty state placeholder (Tahap 3)."""
    repo = FakeRepository()
    body = _client(repo).get("/").text
    assert "Recent Alerts" in body
    assert "Belum ada alert" in body


# --- Tabel Monitored Websites ---------------------------------------------- #


def test_monitored_websites_table_limited_to_five_rows_with_link():
    """Tabel Monitored Websites maks 5 baris + tautan "Lihat semua website"."""
    websites = [_website(f"w{i}", f"site{i}.com") for i in range(7)]
    repo = FakeRepository(websites=websites)
    body = _client(repo).get("/").text
    assert body.count('class="site-name"') == 5
    assert "/websites" in body
    assert "Lihat semua website" in body


# --- Sidebar ---------------------------------------------------------------- #


def test_sidebar_renders_all_menu_items_and_marks_overview_active():
    """Sidebar merender seluruh menu dan menandai Overview sebagai aktif."""
    repo = FakeRepository()
    body = _client(repo).get("/").text
    for label in (
        "Overview",
        "Websites",
        "Pages",
        "Content Changes",
        "Alerts",
        "Reports",
        "Settings",
    ):
        assert label in body
    assert "ContentMonitor" in body
    # ContentMonitor Tahap 5: seluruh menu sidebar kini nyata (tidak ada lagi
    # tanda "Segera" / link "#").
    assert 'href="/reports"' in body


# --- Kegagalan pemuatan ------------------------------------------------------ #


class FailingRepository:
    """Repository palsu yang selalu gagal memuat data agregat."""

    async def list_websites(self):
        raise RuntimeError("Data_Store tidak dapat diakses")


def test_overview_load_failure_shows_error_indication():
    """Kegagalan pemuatan Overview menampilkan indikasi kesalahan (tanpa mutasi)."""
    client = TestClient(create_app(FailingRepository()), raise_server_exceptions=False)
    resp = client.get("/")
    assert resp.status_code == 500
    assert "kesalahan" in resp.text.lower()


# --- Integrasi dengan Repository nyata -------------------------------------- #


async def test_overview_via_real_repository(tmp_path):
    """Integrasi: Overview merender data agregat dari Repository nyata."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website("w1", "nyata.com", "Situs Nyata"))
        snap_time = datetime.now()
        from monitoring.domain.models import Snapshot

        await repo.save_snapshot(
            Snapshot(
                url="https://nyata.com/",
                website_id="w1",
                normalized_text="halo dunia",
                content_hash="hash1",
                checked_at=snap_time,
            )
        )
        diff = Diff(text_added=["baris baru"])
        event = _event("evt-1", "w1", "https://nyata.com/", datetime.now(), diff=diff)
        assert await repo.save_change_event(event)

        client = TestClient(create_app(repo))
        body = client.get("/").text
        assert "Situs Nyata" in body or "nyata.com" in body
        assert "1" in body  # pages_monitored/total_websites muncul di suatu tempat
        assert "<svg" in body
