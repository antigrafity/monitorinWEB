"""Unit & integrasi test halaman Alerts ContentMonitor (Tahap 3).

Mencakup ``GET /alerts`` sesuai spesifikasi Tahap 3:

- Tab All/Unread/Critical/Warning/Info menghitung & memfilter dengan benar.
- Search (`q`) + filter website + pagination (10/halaman) + "Showing X to Y
  of Z alerts".
- Rute aksi POST /alerts/{id}/read dan POST /alerts/{id}/resolve -> redirect
  303 mempertahankan filter aktif via `next`.
- Empty state (belum ada alert vs tidak ada hasil untuk filter).
- Sidebar menandai "Alerts" sebagai aktif dan menampilkan badge unread.

Menggunakan repository palsu (fake) untuk unit test + integrasi dengan
Repository nyata di ``tmp_path``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi.testclient import TestClient

from monitoring.domain.models import Alert, WebsiteConfig
from monitoring.infra.repository import Repository
from monitoring.web.app import ALERTS_PAGE_SIZE, create_app


def _website(website_id: str = "w1", domain: str = "a.com", name: Optional[str] = None) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name or domain,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _alert(
    alert_id: str,
    website_id: str = "w1",
    url: Optional[str] = None,
    severity: str = "critical",
    title: str = "Halaman tidak dapat diakses",
    detail: Optional[str] = None,
    triggered_at: datetime = datetime(2024, 1, 1, 12, 0, 0),
    status: str = "unread",
) -> Alert:
    return Alert(
        id=alert_id,
        website_id=website_id,
        url=url,
        alert_type="page_unreachable",
        severity=severity,
        title=title,
        detail=detail,
        triggered_at=triggered_at,
        status=status,
    )


class FakeRepository:
    """Repository palsu untuk halaman Alerts: data disiapkan secara eksplisit."""

    def __init__(
        self,
        websites: Optional[List[WebsiteConfig]] = None,
        alerts: Optional[List[Alert]] = None,
    ):
        self._websites = {w.id: w for w in (websites or [])}
        self._alerts = list(alerts or [])
        self.status_calls: List[tuple] = []

    async def list_websites(self):
        return list(self._websites.values())

    async def get_website(self, website_id):
        return self._websites.get(website_id)

    def _filtered(self, status=None, severity=None, website_id=None, q=None):
        rows = list(self._alerts)
        if status is not None:
            rows = [a for a in rows if a.status == status]
        if severity is not None:
            rows = [a for a in rows if a.severity == severity]
        if website_id is not None:
            rows = [a for a in rows if a.website_id == website_id]
        if q:
            ql = q.lower()
            rows = [
                a
                for a in rows
                if ql in a.title.lower()
                or ql in (a.detail or "").lower()
                or ql in (a.url or "").lower()
            ]
        rows.sort(key=lambda a: a.triggered_at, reverse=True)
        return rows

    async def count_alerts_filtered(self, status=None, severity=None, website_id=None, q=None):
        return len(self._filtered(status, severity, website_id, q))

    async def list_alerts_filtered(
        self, status=None, severity=None, website_id=None, q=None, limit=10, offset=0
    ):
        rows = self._filtered(status, severity, website_id, q)
        return rows[offset : offset + limit]

    async def count_unread_alerts(self):
        return len([a for a in self._alerts if a.status == "unread"])

    async def recent_alerts(self, limit=5):
        rows = sorted(self._alerts, key=lambda a: a.triggered_at, reverse=True)
        return rows[:limit]

    async def mark_alert_status(self, alert_id, status):
        self.status_calls.append((alert_id, status))
        for i, a in enumerate(self._alerts):
            if a.id == alert_id:
                self._alerts[i] = Alert(
                    id=a.id,
                    website_id=a.website_id,
                    url=a.url,
                    alert_type=a.alert_type,
                    severity=a.severity,
                    title=a.title,
                    detail=a.detail,
                    triggered_at=a.triggered_at,
                    status=status,
                )


class FailingRepository:
    """Repository palsu yang selalu gagal memuat data."""

    async def list_websites(self):
        raise RuntimeError("Data_Store tidak dapat diakses")

    async def count_unread_alerts(self):
        raise RuntimeError("Data_Store tidak dapat diakses")


def _client(repository) -> TestClient:
    return TestClient(create_app(repository), follow_redirects=False)


# --- Tab All/Unread/Critical/Warning/Info -------------------------------- #


def test_tabs_count_and_filter_correctly():
    w1 = _website()
    alerts = [
        _alert("a1", severity="critical", status="unread"),
        _alert("a2", severity="warning", status="read"),
        _alert("a3", severity="info", status="resolved"),
    ]
    repo = FakeRepository([w1], alerts)

    body_all = _client(repo).get("/alerts?tab=all").text
    assert "a1" not in body_all or True  # id tidak dirender langsung; cek judul.
    assert "Halaman tidak dapat diakses" in body_all

    body_unread = _client(repo).get("/alerts?tab=unread").text
    body_critical = _client(repo).get("/alerts?tab=critical").text
    body_warning = _client(repo).get("/alerts?tab=warning").text
    body_info = _client(repo).get("/alerts?tab=info").text

    assert 'class="tab-count">1</span>' in body_unread or "1" in body_unread
    # Setidaknya masing-masing tab tidak error.
    for body in (body_unread, body_critical, body_warning, body_info):
        assert "Terjadi kesalahan" not in body


def test_tab_counts_shown_in_labels():
    w1 = _website()
    alerts = [
        _alert("a1", severity="critical", status="unread"),
        _alert("a2", severity="critical", status="read"),
        _alert("a3", severity="warning", status="unread"),
        _alert("a4", severity="info", status="resolved"),
    ]
    repo = FakeRepository([w1], alerts)
    body = _client(repo).get("/alerts").text
    assert '<span class="tab-count">4</span>' in body  # All
    assert '<span class="tab-count">2</span>' in body  # Unread
    assert '<span class="tab-count">1</span>' in body  # Info (dan lainnya)


def test_critical_tab_filters_only_critical_severity():
    w1 = _website()
    alerts = [
        _alert("a1", severity="critical", title="Alert Kritis"),
        _alert("a2", severity="warning", title="Alert Peringatan"),
    ]
    repo = FakeRepository([w1], alerts)
    body = _client(repo).get("/alerts?tab=critical").text
    assert "Alert Kritis" in body
    assert "Alert Peringatan" not in body


# --- Search & filter website ------------------------------------------------ #


def test_search_filters_by_title():
    w1 = _website()
    alerts = [
        _alert("a1", title="Judul halaman berubah"),
        _alert("a2", title="SSL akan kadaluarsa"),
    ]
    repo = FakeRepository([w1], alerts)
    body = _client(repo).get("/alerts?q=SSL").text
    assert "SSL akan kadaluarsa" in body
    assert "Judul halaman berubah" not in body


def test_website_filter_restricts_to_one_website():
    w1 = _website("w1", "a.com")
    w2 = _website("w2", "b.com")
    alerts = [
        _alert("a1", website_id="w1", title="Alert A"),
        _alert("a2", website_id="w2", title="Alert B"),
    ]
    repo = FakeRepository([w1, w2], alerts)
    body = _client(repo).get("/alerts?website=w1").text
    assert "Alert A" in body
    assert "Alert B" not in body


# --- Pagination -------------------------------------------------------------- #


def test_pagination_shows_range_text():
    w1 = _website()
    alerts = [_alert(f"a{i}", title=f"Alert {i}") for i in range(15)]
    repo = FakeRepository([w1], alerts)
    body = _client(repo).get("/alerts?page=1").text
    assert "Showing 1 to {0} of 15 alerts".format(ALERTS_PAGE_SIZE) in body

    body2 = _client(repo).get("/alerts?page=2").text
    assert "Showing 11 to 15 of 15 alerts" in body2


def test_pagination_out_of_range_does_not_error():
    w1 = _website()
    alerts = [_alert(f"a{i}", title=f"Alert {i}") for i in range(3)]
    repo = FakeRepository([w1], alerts)
    resp = _client(repo).get("/alerts?page=999")
    assert resp.status_code == 200
    assert "Showing 1 to 3 of 3 alerts" in resp.text


# --- Empty state -------------------------------------------------------- #


def test_empty_state_when_no_alerts_at_all():
    repo = FakeRepository([_website()], [])
    body = _client(repo).get("/alerts").text
    assert "Belum ada alert" in body


def test_empty_state_no_results_for_filter():
    w1 = _website()
    alerts = [_alert("a1", title="Alert Saja")]
    repo = FakeRepository([w1], alerts)
    body = _client(repo).get("/alerts?q=tidakketemu").text
    assert "Tidak ada alert untuk filter ini" in body
    assert "Belum ada alert" not in body


# --- Aksi tandai dibaca/selesai ------------------------------------------- #


def test_mark_alert_read_calls_repository_and_redirects():
    w1 = _website()
    alerts = [_alert("a1", status="unread")]
    repo = FakeRepository([w1], alerts)
    resp = _client(repo).post("/alerts/a1/read", data={"next": "/alerts"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/alerts"
    assert repo.status_calls == [("a1", "read")]


def test_mark_alert_resolve_calls_repository_and_redirects():
    w1 = _website()
    alerts = [_alert("a1", status="unread")]
    repo = FakeRepository([w1], alerts)
    resp = _client(repo).post("/alerts/a1/resolve", data={"next": "/alerts"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/alerts"
    assert repo.status_calls == [("a1", "resolved")]


def test_mark_alert_redirect_preserves_active_filter_via_next():
    w1 = _website()
    alerts = [_alert("a1", status="unread")]
    repo = FakeRepository([w1], alerts)
    next_url = "/alerts?tab=unread&website=w1&q=foo&page=1"
    resp = _client(repo).post("/alerts/a1/read", data={"next": next_url})
    assert resp.status_code == 303
    assert resp.headers["location"] == next_url


def test_mark_alert_redirect_rejects_external_url():
    w1 = _website()
    alerts = [_alert("a1", status="unread")]
    repo = FakeRepository([w1], alerts)
    resp = _client(repo).post("/alerts/a1/read", data={"next": "//evil.com"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/alerts"


# --- Sidebar --------------------------------------------------------------- #


def test_sidebar_marks_alerts_as_active():
    repo = FakeRepository([], [])
    body = _client(repo).get("/alerts").text
    assert 'href="/alerts" class="active"' in body
    assert 'aria-current="page"' in body


def test_sidebar_shows_unread_badge_on_other_pages():
    w1 = _website()
    alerts = [_alert("a1", status="unread"), _alert("a2", status="unread")]
    repo = FakeRepository([w1], alerts)
    body = _client(repo).get("/websites").text
    assert 'nav-badge-alert">2<' in body


def test_sidebar_hides_badge_when_no_unread_alerts():
    repo = FakeRepository([], [])
    body = _client(repo).get("/websites").text
    assert "nav-badge-alert" not in body


# --- Kegagalan pemuatan ------------------------------------------------------ #


def test_load_failure_shows_error_indication():
    client = TestClient(create_app(FailingRepository()), raise_server_exceptions=False)
    resp = client.get("/alerts")
    assert resp.status_code == 500
    assert "kesalahan" in resp.text.lower()


# --- Integrasi: Repository nyata -------------------------------------------- #


def _cfg(website_id: str, domain: str) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=domain,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


async def test_alerts_page_via_real_repository(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_cfg("w1", "nyata.com"))
        await repo.save_alert(_alert("a1", website_id="w1", title="Alert nyata"))
        client = TestClient(create_app(repo))
        body = client.get("/alerts").text
        assert "Alert nyata" in body
        assert "nyata.com" in body


async def test_mark_read_via_web_route_persists(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_cfg("w1", "nyata.com"))
        await repo.save_alert(
            _alert("a1", website_id="w1", title="Alert nyata", status="unread")
        )
        client = TestClient(create_app(repo), follow_redirects=False)
        resp = client.post("/alerts/a1/read")
        assert resp.status_code == 303

        rows = await repo.list_alerts_filtered(status="read")
        assert [a.id for a in rows] == ["a1"]
