"""Unit test operasi Alert pada Repository (ContentMonitor Tahap 3).

Mencakup:

- ``save_alert`` + ``list_alerts_filtered`` (filter status/severity/website/q,
  pagination, urutan terbaru dulu).
- ``count_alerts_filtered``, ``count_alerts_by_status``,
  ``count_alerts_by_severity``.
- ``mark_alert_status`` (unread -> read -> resolved).
- ``count_unread_alerts`` dan ``recent_alerts``.
- Kegagalan simpan (constraint dilanggar) mengembalikan ``False`` tanpa
  mengangkat exception (Req desain umum ketahanan Repository).
- Migrasi idempoten: tabel ``alert`` tersedia setelah ``connect()`` berulang.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from monitoring.domain.models import Alert, WebsiteConfig
from monitoring.infra.repository import Repository


def _website(website_id: str = "w1", domain: str = "a.com") -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=domain,
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _alert(
    alert_id: str,
    website_id: str = "w1",
    url=None,
    alert_type: str = "page_unreachable",
    severity: str = "critical",
    title: str = "Halaman tidak dapat diakses",
    detail=None,
    triggered_at: datetime = datetime(2024, 1, 1, 12, 0, 0),
    status: str = "unread",
) -> Alert:
    return Alert(
        id=alert_id,
        website_id=website_id,
        url=url,
        alert_type=alert_type,
        severity=severity,
        title=title,
        detail=detail,
        triggered_at=triggered_at,
        status=status,
    )


async def test_save_and_list_alerts_ordered_newest_first(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        t0 = datetime(2024, 1, 1, 12, 0, 0)
        a1 = _alert("a1", triggered_at=t0)
        a2 = _alert("a2", triggered_at=t0 + timedelta(minutes=5))
        assert await repo.save_alert(a1) is True
        assert await repo.save_alert(a2) is True

        rows = await repo.list_alerts_filtered()
        assert [r.id for r in rows] == ["a2", "a1"]


async def test_list_alerts_filtered_by_status(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        await repo.save_alert(_alert("a1", status="unread"))
        await repo.save_alert(_alert("a2", status="read"))
        await repo.save_alert(_alert("a3", status="resolved"))

        unread = await repo.list_alerts_filtered(status="unread")
        assert [a.id for a in unread] == ["a1"]

        resolved = await repo.list_alerts_filtered(status="resolved")
        assert [a.id for a in resolved] == ["a3"]


async def test_list_alerts_filtered_by_severity(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        await repo.save_alert(_alert("a1", severity="critical"))
        await repo.save_alert(_alert("a2", severity="warning"))
        await repo.save_alert(_alert("a3", severity="info"))

        critical = await repo.list_alerts_filtered(severity="critical")
        assert [a.id for a in critical] == ["a1"]


async def test_list_alerts_filtered_by_website(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website("w1", "a.com"))
        await repo.add_website(_website("w2", "b.com"))
        await repo.save_alert(_alert("a1", website_id="w1"))
        await repo.save_alert(_alert("a2", website_id="w2"))

        w1_alerts = await repo.list_alerts_filtered(website_id="w1")
        assert [a.id for a in w1_alerts] == ["a1"]


async def test_list_alerts_filtered_by_q_matches_title_detail_and_url(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        await repo.save_alert(
            _alert("a1", title="Judul halaman berubah", url="https://a.com/promo")
        )
        await repo.save_alert(
            _alert("a2", title="Lainnya", detail="mengandung kata kunci unik")
        )
        await repo.save_alert(_alert("a3", title="Tidak relevan"))

        by_title = await repo.list_alerts_filtered(q="judul")
        assert [a.id for a in by_title] == ["a1"]

        by_url = await repo.list_alerts_filtered(q="promo")
        assert [a.id for a in by_url] == ["a1"]

        by_detail = await repo.list_alerts_filtered(q="kunci unik")
        assert [a.id for a in by_detail] == ["a2"]


async def test_list_alerts_filtered_pagination(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        base = datetime(2024, 1, 1, 12, 0, 0)
        for i in range(15):
            await repo.save_alert(
                _alert(f"a{i}", triggered_at=base + timedelta(minutes=i))
            )

        page1 = await repo.list_alerts_filtered(limit=10, offset=0)
        assert len(page1) == 10
        # Terbaru dulu: a14 adalah yang paling baru.
        assert page1[0].id == "a14"

        page2 = await repo.list_alerts_filtered(limit=10, offset=10)
        assert len(page2) == 5


async def test_count_alerts_filtered_matches_list_filters(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        await repo.save_alert(_alert("a1", severity="critical", status="unread"))
        await repo.save_alert(_alert("a2", severity="warning", status="read"))

        assert await repo.count_alerts_filtered() == 2
        assert await repo.count_alerts_filtered(severity="critical") == 1
        assert await repo.count_alerts_filtered(status="read") == 1


async def test_count_alerts_by_status_and_severity(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        await repo.save_alert(_alert("a1", severity="critical", status="unread"))
        await repo.save_alert(_alert("a2", severity="critical", status="read"))
        await repo.save_alert(_alert("a3", severity="warning", status="unread"))

        by_status = await repo.count_alerts_by_status()
        assert by_status["unread"] == 2
        assert by_status["read"] == 1

        by_severity = await repo.count_alerts_by_severity()
        assert by_severity["critical"] == 2
        assert by_severity["warning"] == 1


async def test_mark_alert_status_updates_row(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        await repo.save_alert(_alert("a1", status="unread"))

        await repo.mark_alert_status("a1", "read")
        rows = await repo.list_alerts_filtered(status="read")
        assert [a.id for a in rows] == ["a1"]

        await repo.mark_alert_status("a1", "resolved")
        rows = await repo.list_alerts_filtered(status="resolved")
        assert [a.id for a in rows] == ["a1"]


async def test_mark_alert_status_nonexistent_id_is_noop(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        # Tidak boleh mengangkat exception meski id tidak ada.
        await repo.mark_alert_status("tidak-ada", "read")


async def test_count_unread_alerts(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        await repo.save_alert(_alert("a1", status="unread"))
        await repo.save_alert(_alert("a2", status="unread"))
        await repo.save_alert(_alert("a3", status="read"))

        assert await repo.count_unread_alerts() == 2


async def test_recent_alerts_limit_and_order(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        base = datetime(2024, 1, 1, 12, 0, 0)
        for i in range(7):
            await repo.save_alert(
                _alert(f"a{i}", triggered_at=base + timedelta(minutes=i))
            )

        recent = await repo.recent_alerts(limit=3)
        assert [a.id for a in recent] == ["a6", "a5", "a4"]


async def test_save_alert_failure_returns_false_without_raising(tmp_path):
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_website())
        alert = _alert("a1")
        assert await repo.save_alert(alert) is True
        # Menyimpan Alert dengan id duplikat melanggar PRIMARY KEY -> False,
        # bukan exception, konsisten dengan save_snapshot/save_change_event.
        assert await repo.save_alert(alert) is False


async def test_alert_table_created_idempotently_on_reconnect(tmp_path):
    """Migrasi/skema Alert idempoten: aman dipanggil berulang (basis data lama)."""
    db_path = str(tmp_path / "monitoring.db")
    async with Repository(db_path) as repo:
        await repo.add_website(_website())
        await repo.save_alert(_alert("a1"))

    # Sambungkan ulang (mensimulasikan restart) -> skema tetap valid, data ada.
    async with Repository(db_path) as repo:
        rows = await repo.list_alerts_filtered()
        assert [a.id for a in rows] == ["a1"]
