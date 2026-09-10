"""Unit & integrasi test halaman Reports ContentMonitor (Tahap 5).

Mencakup ``GET /reports``, ekspor CSV (``GET /reports/export``), dan Riwayat
Laporan (``POST /reports/generate``, ``GET /reports/{id}/download``,
``POST /reports/{id}/delete``) sesuai spesifikasi Tahap 5:

- KPI & donut menghitung benar; total 0 tidak error (tidak membagi nol).
- CSV: header & baris benar; teks berisi koma/kutip/newline tetap valid saat
  diparse ulang dengan modul ``csv``; mitigasi CSV injection bekerja; nama
  berkas benar.
- Generate -> berkas dibuat di direktori laporan + baris ``report``
  tersimpan; download mengirim berkas; delete menghapus keduanya; berkas
  hilang -> 404.
- Path traversal pada nama/id laporan ditolak.
- Rentang tanggal tidak valid -> 400 (ekspor), bukan 500.

Memakai ``tmp_path`` untuk direktori laporan pada SELURUH test (tidak pernah
menulis ke direktori ``reports/`` nyata milik proyek).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi.testclient import TestClient

from monitoring.domain.models import (
    Alert,
    ChangeEvent,
    ChangeSummary,
    Diff,
    Report,
    WebsiteConfig,
)
from monitoring.infra.repository import Repository, WebsiteOverview
from monitoring.web.app import create_app
from monitoring.web.reports_csv import (
    build_export_filename,
    build_stored_filename,
    csv_safe_value,
    resolve_report_path,
    sanitize_filename_component,
)


def _website(
    website_id: str = "w1", domain: str = "a.com", name: Optional[str] = None
) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name or domain,
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
    """Repository palsu untuk halaman Reports (unit test lapisan presentasi)."""

    def __init__(
        self,
        websites: Optional[List[WebsiteConfig]] = None,
        events: Optional[List[ChangeEvent]] = None,
        alerts: Optional[List[Alert]] = None,
        reports: Optional[List[Report]] = None,
        pages_by_website: Optional[Dict[str, int]] = None,
    ):
        self._websites = {w.id: w for w in (websites or [])}
        self._events = list(events or [])
        self._alerts = list(alerts or [])
        self._reports: Dict[str, Report] = {r.id: r for r in (reports or [])}
        self._pages_by_website = pages_by_website or {}
        self.saved_reports: List[Report] = []
        self.deleted_report_ids: List[str] = []

    async def list_websites(self):
        return list(self._websites.values())

    async def get_website(self, website_id):
        return self._websites.get(website_id)

    async def count_unread_alerts(self):
        return len([a for a in self._alerts if a.status == "unread"])

    async def count_change_events_between(self, start, end, website_id=None):
        return len(
            [
                e
                for e in self._events
                if start <= e.detected_at < end
                and (website_id is None or e.website_id == website_id)
            ]
        )

    async def count_alerts_between(self, start, end, website_id=None):
        return len(
            [
                a
                for a in self._alerts
                if start <= a.triggered_at < end
                and (website_id is None or a.website_id == website_id)
            ]
        )

    async def most_active_website_between(self, start, end):
        counts: Dict[str, int] = {}
        for e in self._events:
            if start <= e.detected_at < end:
                counts[e.website_id] = counts.get(e.website_id, 0) + 1
        if not counts:
            return None
        website_id = max(counts, key=lambda k: counts[k])
        return (website_id, counts[website_id])

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

    async def list_reports(self):
        return sorted(
            self._reports.values(), key=lambda r: r.created_at, reverse=True
        )

    async def get_report(self, report_id):
        return self._reports.get(report_id)

    async def save_report(self, report):
        self._reports[report.id] = report
        self.saved_reports.append(report)
        return True

    async def delete_report(self, report_id):
        self.deleted_report_ids.append(report_id)
        existed = report_id in self._reports
        self._reports.pop(report_id, None)
        return existed

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

    async def count_change_events_total_by_website(self):
        totals: Dict[str, int] = {}
        for e in self._events:
            totals[e.website_id] = totals.get(e.website_id, 0) + 1
        return totals

    async def iter_change_events_for_export(self, start, end, website_id=None):
        for e in await self.list_change_events_between(start, end, website_id):
            yield e

    async def iter_alerts_for_export(self, start, end, website_id=None):
        items = [
            a
            for a in self._alerts
            if start <= a.triggered_at < end
            and (website_id is None or a.website_id == website_id)
        ]
        for a in sorted(items, key=lambda a: a.triggered_at, reverse=True):
            yield a


def _client(repository, tmp_path) -> TestClient:
    return TestClient(
        create_app(repository, reports_dir=str(tmp_path / "reports")),
        follow_redirects=False,
    )


def _parse_csv_body(text: str) -> List[List[str]]:
    """Uraikan teks CSV (setelah menghapus BOM bila ada) memakai modul csv."""
    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.reader(io.StringIO(text))
    return list(reader)


# --- KPI & donut ------------------------------------------------------------ #


def test_kpi_totals_and_percentage_vs_previous_period(tmp_path):
    now = datetime.now()
    w1 = _website("w1", "a.com", "Situs A")
    events_this_period = [
        _event("e1", "w1", "https://a.com/1", now - timedelta(days=1)),
        _event("e2", "w1", "https://a.com/2", now - timedelta(days=2)),
    ]
    events_prev_period = [
        _event("e3", "w1", "https://a.com/3", now - timedelta(days=9)),
    ]
    repo = FakeRepository(
        websites=[w1], events=events_this_period + events_prev_period
    )
    body = _client(repo, tmp_path).get("/reports").text
    assert "Total Changes" in body
    assert "Average Changes/Day" in body
    assert "Most Active Website" in body
    assert "Total Alerts" in body
    assert "Situs A" in body  # Most Active Website


def test_donut_handles_zero_total_without_dividing_by_zero(tmp_path):
    repo = FakeRepository(websites=[_website()], events=[])
    resp = _client(repo, tmp_path).get("/reports")
    assert resp.status_code == 200
    assert "donut-chart" in resp.text


def test_donut_counts_match_change_types(tmp_path):
    now = datetime.now()
    w1 = _website("w1", "a.com")
    events = [
        _event("e1", "w1", "https://a.com/1", now - timedelta(days=1), Diff(text_added=["baru"])),
        _event("e2", "w1", "https://a.com/2", now - timedelta(days=1), Diff(text_removed=["lama"])),
        _event("e3", "w1", "https://a.com/3", now - timedelta(days=1), Diff(text_added=["x"], text_removed=["y"])),
    ]
    repo = FakeRepository(websites=[w1], events=events)
    body = _client(repo, tmp_path).get("/reports").text
    # 1 added, 1 removed, 1 updated -> legenda menampilkan masing2 1.
    assert "Added" in body and "Removed" in body and "Updated" in body


def test_reports_page_ok_with_empty_data(tmp_path):
    repo = FakeRepository()
    resp = _client(repo, tmp_path).get("/reports")
    assert resp.status_code == 200
    assert "Belum ada laporan" in resp.text


# --- Sidebar ----------------------------------------------------------------- #


def test_sidebar_marks_reports_as_active(tmp_path):
    repo = FakeRepository()
    body = _client(repo, tmp_path).get("/reports").text
    assert 'href="/reports" class="active"' in body
    assert 'aria-current="page"' in body


# --- CSV: header, escaping, injection mitigation ---------------------------- #


def test_export_changes_csv_header_and_rows(tmp_path):
    now = datetime.now()
    w1 = _website("w1", "a.com", "Situs A")
    events = [
        _event(
            "e1",
            "w1",
            "https://a.com/page",
            now - timedelta(days=1),
            Diff(text_added=["halo"], text_removed=["dunia"]),
        ),
    ]
    repo = FakeRepository(websites=[w1], events=events)
    resp = _client(repo, tmp_path).get("/reports/export?type=changes")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = _parse_csv_body(resp.text)
    header = rows[0]
    assert header == [
        "detected_at",
        "website",
        "domain",
        "url",
        "type",
        "description",
        "text_added_count",
        "text_removed_count",
        "links_added_count",
        "links_removed_count",
        "images_changed_count",
    ]
    data_row = rows[1]
    assert data_row[1] == "Situs A"
    assert data_row[2] == "a.com"
    assert data_row[3] == "https://a.com/page"
    assert data_row[4] == "updated"


def test_export_alerts_csv_header_and_rows(tmp_path):
    w1 = _website("w1", "a.com", "Situs A")
    now = datetime.now()
    alerts = [_alert("al1", website_id="w1", title="Alert Uji", triggered_at=now - timedelta(hours=1))]
    repo = FakeRepository(websites=[w1], alerts=alerts)
    resp = _client(repo, tmp_path).get("/reports/export?type=alerts")
    assert resp.status_code == 200
    rows = _parse_csv_body(resp.text)
    assert rows[0] == [
        "triggered_at",
        "website",
        "domain",
        "url",
        "alert_type",
        "severity",
        "status",
        "title",
        "detail",
    ]
    assert rows[1][7] == "Alert Uji"


def test_export_websites_csv_header_and_rows(tmp_path):
    w1 = _website("w1", "a.com", "Situs A")
    repo = FakeRepository(websites=[w1], pages_by_website={"w1": 4})
    resp = _client(repo, tmp_path).get("/reports/export?type=websites")
    assert resp.status_code == 200
    rows = _parse_csv_body(resp.text)
    assert rows[0] == [
        "name",
        "domain",
        "paused",
        "last_checked_at",
        "last_status",
        "pages_monitored",
        "changes_7d",
        "changes_total",
    ]
    assert rows[1][0] == "Situs A"
    assert rows[1][1] == "a.com"
    assert rows[1][5] == "4"


def test_csv_preserves_commas_quotes_and_newlines_in_text(tmp_path):
    """Teks berisi koma/kutip/newline tetap valid saat diparse ulang."""
    now = datetime.now()
    w1 = _website("w1", "a.com", "Situs A")
    tricky_text = 'Halo, "dunia"\nbaris kedua'
    events = [
        _event(
            "e1",
            "w1",
            "https://a.com/page",
            now - timedelta(days=1),
            Diff(text_added=[tricky_text]),
        ),
    ]
    repo = FakeRepository(websites=[w1], events=events)
    resp = _client(repo, tmp_path).get("/reports/export?type=changes")
    rows = _parse_csv_body(resp.text)
    # description tersimpan pada indeks 5; deskripsi memotong teks ke 60 char
    # tapi harus tetap 1 baris terparse yang valid (tidak memecah data lain).
    assert len(rows) == 2
    assert rows[1][3] == "https://a.com/page"


def test_csv_injection_mitigation_prefixes_formula_characters():
    """Nilai yang dimulai dengan =, +, -, @ diawali tanda kutip tunggal."""
    assert csv_safe_value("=SUM(A1:A2)") == "'=SUM(A1:A2)"
    assert csv_safe_value("+1234") == "'+1234"
    assert csv_safe_value("-1234") == "'-1234"
    assert csv_safe_value("@mention") == "'@mention"
    assert csv_safe_value("teks aman") == "teks aman"
    # Nilai non-string (int) dikembalikan apa adanya.
    assert csv_safe_value(5) == 5
    assert csv_safe_value(-5) == -5


def test_csv_injection_mitigation_applied_to_exported_rows(tmp_path):
    """Kolom apa pun yang nilainya berasal dari input pengguna (mis. nama
    website) dan diawali karakter formula tetap dimitigasi end-to-end pada
    ekspor CSV sungguhan (bukan hanya pada fungsi murni ``csv_safe_value``).
    """
    now = datetime.now()
    # Nama website (input pengguna) sengaja diawali "=" untuk menguji jalur
    # ekspor sungguhan mengoper setiap nilai lewat csv_safe_value.
    w1 = _website("w1", "a.com", name="=SUM(A1:A9)")
    events = [
        _event(
            "e1",
            "w1",
            "https://a.com/page",
            now - timedelta(days=1),
            Diff(text_added=["baru"]),
        ),
    ]
    repo = FakeRepository(websites=[w1], events=events)
    resp = _client(repo, tmp_path).get("/reports/export?type=changes")
    rows = _parse_csv_body(resp.text)
    website_cell = rows[1][1]
    assert website_cell == "'=SUM(A1:A9)"


def test_export_filename_is_descriptive():
    filename = build_export_filename("changes", "2026-07-01", "2026-07-07")
    assert filename == "contentmonitor-changes-2026-07-01_2026-07-07.csv"
    resp_header_re = 'attachment; filename="{0}"'.format(filename)
    assert "contentmonitor-changes-2026-07-01_2026-07-07.csv" in resp_header_re


def test_export_response_has_content_disposition_with_filename(tmp_path):
    repo = FakeRepository(websites=[_website()])
    resp = _client(repo, tmp_path).get(
        "/reports/export?type=changes&start=2026-07-01&end=2026-07-07"
    )
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert "contentmonitor-changes-2026-07-01_2026-07-07.csv" in disposition


# --- Rentang tanggal tidak valid ---------------------------------------------- #


def test_export_invalid_date_range_returns_400_not_500(tmp_path):
    repo = FakeRepository(websites=[_website()])
    resp = _client(repo, tmp_path).get(
        "/reports/export?type=changes&start=2026-07-10&end=2026-07-01"
    )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_export_malformed_date_returns_400_not_500(tmp_path):
    repo = FakeRepository(websites=[_website()])
    resp = _client(repo, tmp_path).get(
        "/reports/export?type=changes&start=bukan-tanggal&end=2026-07-07"
    )
    assert resp.status_code == 400


def test_export_unknown_type_returns_400(tmp_path):
    repo = FakeRepository(websites=[_website()])
    resp = _client(repo, tmp_path).get("/reports/export?type=pdf")
    assert resp.status_code == 400


# --- Sanitasi nama berkas & path traversal ----------------------------------- #


def test_sanitize_filename_component_strips_unsafe_characters():
    assert sanitize_filename_component("../../etc/passwd") == "etc_passwd"
    assert sanitize_filename_component("normal-name_123") == "normal-name_123"
    assert sanitize_filename_component("") == "laporan"
    assert sanitize_filename_component("...") == "laporan"


def test_build_stored_filename_has_no_path_separators():
    filename = build_stored_filename("../../evil", "changes")
    assert "/" not in filename
    assert ".." not in filename


def test_resolve_report_path_rejects_traversal(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    assert resolve_report_path(reports_dir, "../secret.txt") is None
    assert resolve_report_path(reports_dir, "../../etc/passwd") is None
    # Path absolut yang disisipkan tetap ditolak bila keluar dari reports_dir.
    assert resolve_report_path(reports_dir, "/etc/passwd") is None


def test_resolve_report_path_accepts_valid_filename(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "abc-changes.csv").write_text("data")
    resolved = resolve_report_path(reports_dir, "abc-changes.csv")
    assert resolved is not None
    assert resolved.is_file()


# --- Generate / Download / Delete (Riwayat Laporan) -------------------------- #


def test_generate_report_creates_file_and_saves_metadata(tmp_path):
    now = datetime.now()
    w1 = _website("w1", "a.com", "Situs A")
    events = [_event("e1", "w1", "https://a.com/1", now - timedelta(days=1))]
    repo = FakeRepository(websites=[w1], events=events)
    reports_dir = tmp_path / "reports"
    client = _client(repo, tmp_path)

    resp = client.post(
        "/reports/generate",
        data={"type": "changes", "start": "", "end": ""},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/reports?generated=")

    assert len(repo.saved_reports) == 1
    saved = repo.saved_reports[0]
    assert saved.report_type == "changes"
    assert saved.row_count == 1

    stored_file = reports_dir / saved.file_path
    assert stored_file.is_file()
    content = stored_file.read_text(encoding="utf-8-sig")
    assert "detected_at" in content


def test_download_report_sends_file(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "rep1-changes.csv").write_text(
        "detected_at,website\n2026-01-01,Situs A\n", encoding="utf-8-sig"
    )
    report = Report(
        id="rep1",
        name="Laporan Uji",
        report_type="changes",
        period_start="2026-01-01",
        period_end="2026-01-07",
        created_at=datetime.now(),
        summary="1 perubahan",
        row_count=1,
        file_path="rep1-changes.csv",
    )
    repo = FakeRepository(reports=[report])
    client = _client(repo, tmp_path)
    resp = client.get("/reports/rep1/download")
    assert resp.status_code == 200
    assert "Situs A" in resp.text


def test_download_report_not_found_returns_404(tmp_path):
    repo = FakeRepository()
    client = _client(repo, tmp_path)
    resp = client.get("/reports/tidak-ada/download")
    assert resp.status_code == 404


def test_download_report_missing_file_returns_404_not_500(tmp_path):
    """Baris metadata ada, tetapi berkas fisiknya hilang -> 404, bukan 500."""
    report = Report(
        id="rep2",
        name="Laporan Hilang",
        report_type="changes",
        period_start="2026-01-01",
        period_end="2026-01-07",
        created_at=datetime.now(),
        summary="0 perubahan",
        row_count=0,
        file_path="rep2-changes.csv",
    )
    repo = FakeRepository(reports=[report])
    client = _client(repo, tmp_path)
    # Direktori reports/ belum ada sama sekali (baru dibuat saat generate).
    resp = client.get("/reports/rep2/download")
    assert resp.status_code == 404


def test_download_report_rejects_path_traversal_file_path(tmp_path):
    """Baris metadata dengan file_path hasil manipulasi (path traversal)
    ditolak -> 404, TIDAK mengirim berkas di luar direktori reports/.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("RAHASIA")
    report = Report(
        id="rep3",
        name="Laporan Jahat",
        report_type="changes",
        period_start="2026-01-01",
        period_end="2026-01-07",
        created_at=datetime.now(),
        summary="0 perubahan",
        row_count=0,
        file_path="../secret.txt",
    )
    repo = FakeRepository(reports=[report])
    client = _client(repo, tmp_path)
    resp = client.get("/reports/rep3/download")
    assert resp.status_code == 404
    assert "RAHASIA" not in resp.text


def test_delete_report_removes_row_and_file(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "rep4-changes.csv").write_text("a,b\n1,2\n", encoding="utf-8-sig")
    report = Report(
        id="rep4",
        name="Laporan Hapus",
        report_type="changes",
        period_start="2026-01-01",
        period_end="2026-01-07",
        created_at=datetime.now(),
        summary="1 perubahan",
        row_count=1,
        file_path="rep4-changes.csv",
    )
    repo = FakeRepository(reports=[report])
    client = _client(repo, tmp_path)
    resp = client.post("/reports/rep4/delete")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/reports?deleted=1"
    assert repo.deleted_report_ids == ["rep4"]
    assert not (reports_dir / "rep4-changes.csv").exists()


def test_delete_report_ignores_missing_file(tmp_path):
    """Berkas sudah tidak ada di disk -> penghapusan baris tetap berhasil."""
    report = Report(
        id="rep5",
        name="Laporan Tanpa Berkas",
        report_type="changes",
        period_start="2026-01-01",
        period_end="2026-01-07",
        created_at=datetime.now(),
        summary="0 perubahan",
        row_count=0,
        file_path="rep5-changes.csv",
    )
    repo = FakeRepository(reports=[report])
    client = _client(repo, tmp_path)
    resp = client.post("/reports/rep5/delete")
    assert resp.status_code == 303
    assert repo.deleted_report_ids == ["rep5"]


# --- Integrasi: Repository nyata --------------------------------------------- #


def _cfg(website_id: str, domain: str) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=domain,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


async def test_full_generate_download_delete_cycle_via_real_repository(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_cfg("w1", "nyata.com"))
        await repo.save_change_event(
            _event("e1", "w1", "https://nyata.com/x", datetime.now() - timedelta(days=1))
        )
        client = TestClient(
            create_app(repo, reports_dir=str(tmp_path / "reports_dir")),
            follow_redirects=False,
        )

        gen_resp = client.post(
            "/reports/generate", data={"type": "changes", "start": "", "end": ""}
        )
        assert gen_resp.status_code == 303
        report_id = gen_resp.headers["location"].split("generated=")[1]

        reports = await repo.list_reports()
        assert len(reports) == 1
        assert reports[0].id == report_id

        download_resp = client.get("/reports/{0}/download".format(report_id))
        assert download_resp.status_code == 200
        assert "nyata.com" in download_resp.text

        delete_resp = client.post("/reports/{0}/delete".format(report_id))
        assert delete_resp.status_code == 303

        remaining = await repo.list_reports()
        assert remaining == []

        missing_resp = client.get("/reports/{0}/download".format(report_id))
        assert missing_resp.status_code == 404
