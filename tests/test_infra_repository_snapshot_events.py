"""Unit test operasi Snapshot & Change_Event pada Repository (task 8.3).

Mencakup:

- ``save_snapshot`` + ``get_latest_snapshot`` (Req 5.2, 5.3, 5.4);
- kegagalan simpan Snapshot mempertahankan Snapshot sebelumnya + indikator
  gagal (Req 5.4, 5.5);
- ``save_change_event`` + ``list_change_events`` urut ``detected_at`` DESC
  (Req 10.4, 11.2);
- kegagalan simpan Change_Event mengembalikan indikator gagal (Req 11.5);
- ``update_last_check`` menyetel last_checked_at & last_status (Req 10.2, 10.3).

Foreign key aktif, sehingga setiap Snapshot/Change_Event memerlukan baris
``website_config`` induk terlebih dahulu.
"""

from datetime import datetime, timedelta

import pytest

from monitoring.domain.models import (
    ChangeEvent,
    ChangeSummary,
    Diff,
    Snapshot,
    WebsiteConfig,
)
from monitoring.infra.repository import Repository


def _make_website(website_id: str = "web-1", domain: str = "example.com") -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name="Example",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def _make_snapshot(
    url: str = "https://example.com/",
    website_id: str = "web-1",
    text: str = "halo dunia",
    content_hash: str = "hash-1",
    checked_at: datetime = None,
    links=None,
    image_hashes=None,
) -> Snapshot:
    return Snapshot(
        url=url,
        website_id=website_id,
        normalized_text=text,
        content_hash=content_hash,
        checked_at=checked_at or datetime(2024, 1, 1, 12, 0, 0),
        links=links if links is not None else [],
        image_hashes=image_hashes if image_hashes is not None else {},
    )


def _make_event(
    event_id: str,
    website_id: str = "web-1",
    url: str = "https://example.com/",
    detected_at: datetime = None,
) -> ChangeEvent:
    return ChangeEvent(
        id=event_id,
        website_id=website_id,
        url=url,
        detected_at=detected_at or datetime(2024, 1, 1, 12, 0, 0),
        diff=Diff(text_added=["baris baru"]),
        summary=ChangeSummary(
            text_added=1,
            text_removed=0,
            links_added=0,
            links_removed=0,
            images_changed=0,
        ),
    )


async def test_save_and_get_latest_snapshot(tmp_path):
    """Snapshot tersimpan lalu diambil kembali secara utuh (Req 5.2, 5.3)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())

        snap = _make_snapshot(
            links=["https://example.com/a", "https://example.com/b"],
            image_hashes={"https://example.com/i.png": "imghash-1"},
        )
        assert await repo.save_snapshot(snap) is True

        loaded = await repo.get_latest_snapshot(snap.url)
        assert loaded is not None
        assert loaded.url == snap.url
        assert loaded.website_id == snap.website_id
        assert loaded.normalized_text == snap.normalized_text
        assert loaded.content_hash == snap.content_hash
        assert loaded.checked_at == snap.checked_at
        assert loaded.links == snap.links
        assert loaded.image_hashes == snap.image_hashes


async def test_get_latest_snapshot_missing_returns_none(tmp_path):
    """URL tanpa Snapshot mengembalikan None."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        assert await repo.get_latest_snapshot("https://example.com/none") is None


async def test_get_latest_snapshot_returns_most_recent(tmp_path):
    """get_latest_snapshot mengembalikan Snapshot dengan checked_at terbaru (Req 5.4)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        base = datetime(2024, 1, 1, 12, 0, 0)

        old = _make_snapshot(content_hash="old", checked_at=base)
        new = _make_snapshot(
            content_hash="new", checked_at=base + timedelta(hours=1)
        )
        assert await repo.save_snapshot(old) is True
        assert await repo.save_snapshot(new) is True

        loaded = await repo.get_latest_snapshot(old.url)
        assert loaded.content_hash == "new"


async def test_save_snapshot_keeps_history(tmp_path):
    """Menyimpan Snapshot baru tidak menghapus Snapshot lama (Req 5.4)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        base = datetime(2024, 1, 1, 12, 0, 0)

        await repo.save_snapshot(_make_snapshot(content_hash="v1", checked_at=base))
        await repo.save_snapshot(
            _make_snapshot(content_hash="v2", checked_at=base + timedelta(hours=1))
        )

        conn = repo._require_conn()
        async with conn.execute(
            "SELECT COUNT(*) FROM snapshot WHERE url = ?", ("https://example.com/",)
        ) as cur:
            (count,) = await cur.fetchone()
        assert count == 2


async def test_save_snapshot_failure_preserves_previous(tmp_path):
    """Kegagalan simpan mempertahankan Snapshot sebelumnya + indikator gagal (Req 5.5).

    Kegagalan disimulasikan dengan menyisipkan Snapshot yang melanggar PRIMARY
    KEY (url, checked_at) yang sama dengan Snapshot yang sudah ada.
    """
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        checked_at = datetime(2024, 1, 1, 12, 0, 0)

        original = _make_snapshot(content_hash="original", checked_at=checked_at)
        assert await repo.save_snapshot(original) is True

        # url & checked_at sama -> pelanggaran PRIMARY KEY -> gagal.
        conflicting = _make_snapshot(content_hash="baru", checked_at=checked_at)
        assert await repo.save_snapshot(conflicting) is False

        # Snapshot sebelumnya tetap utuh, tidak berubah.
        loaded = await repo.get_latest_snapshot(original.url)
        assert loaded.content_hash == "original"


async def test_save_snapshot_failure_via_error_preserves_previous(tmp_path, monkeypatch):
    """Kegagalan I/O saat simpan mengembalikan False & mempertahankan data (Req 5.5)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        base = datetime(2024, 1, 1, 12, 0, 0)

        original = _make_snapshot(content_hash="original", checked_at=base)
        assert await repo.save_snapshot(original) is True

        conn = repo._require_conn()
        real_execute = conn.execute

        async def boom(sql, params=()):
            if sql.strip().upper().startswith("INSERT INTO SNAPSHOT"):
                raise RuntimeError("kegagalan tulis disimulasikan")
            return await real_execute(sql, params)

        monkeypatch.setattr(conn, "execute", boom)

        new = _make_snapshot(
            content_hash="tidak-tersimpan", checked_at=base + timedelta(hours=1)
        )
        assert await repo.save_snapshot(new) is False

        monkeypatch.undo()

        # Snapshot sebelumnya tetap yang terbaru.
        loaded = await repo.get_latest_snapshot(original.url)
        assert loaded.content_hash == "original"


async def test_save_and_list_change_events_desc(tmp_path):
    """Change_Event tersimpan lalu terurut detected_at DESC (Req 10.4, 11.2)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        base = datetime(2024, 1, 1, 12, 0, 0)

        e1 = _make_event("e1", detected_at=base)
        e2 = _make_event("e2", detected_at=base + timedelta(hours=2))
        e3 = _make_event("e3", detected_at=base + timedelta(hours=1))
        # Disimpan tidak berurutan.
        assert await repo.save_change_event(e1) is True
        assert await repo.save_change_event(e2) is True
        assert await repo.save_change_event(e3) is True

        events = await repo.list_change_events("web-1")
        assert [e.id for e in events] == ["e2", "e3", "e1"]
        # Diff & summary ter-deserialisasi dengan benar.
        assert events[0].diff.text_added == ["baris baru"]
        assert events[0].summary.text_added == 1


async def test_list_change_events_only_for_website(tmp_path):
    """list_change_events hanya mengembalikan event milik website terkait."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website("web-1", "a.com"))
        await repo.add_website(_make_website("web-2", "b.com"))

        await repo.save_change_event(_make_event("e1", website_id="web-1"))
        await repo.save_change_event(_make_event("e2", website_id="web-2"))

        events = await repo.list_change_events("web-1")
        assert [e.id for e in events] == ["e1"]


async def test_list_change_events_empty(tmp_path):
    """Website tanpa Change_Event mengembalikan daftar kosong (Req 10.7)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        assert await repo.list_change_events("web-1") == []


async def test_save_change_event_failure_returns_false(tmp_path):
    """Menyimpan Change_Event dengan id duplikat gagal & tak mengubah data (Req 11.5)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())

        assert await repo.save_change_event(_make_event("e1")) is True
        # id sama -> pelanggaran PRIMARY KEY.
        assert await repo.save_change_event(_make_event("e1")) is False

        events = await repo.list_change_events("web-1")
        assert len(events) == 1


async def test_update_last_check(tmp_path):
    """update_last_check menyetel last_checked_at & last_status (Req 10.2, 10.3)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        ts = datetime(2024, 3, 1, 8, 30, 0)

        await repo.update_last_check("web-1", ts, "success")

        conn = repo._require_conn()
        async with conn.execute(
            "SELECT last_checked_at, last_status FROM website_config WHERE id = ?",
            ("web-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row[0] == ts.isoformat()
        assert row[1] == "success"


async def test_change_events_preserved_across_reopen(tmp_path):
    """Change_Event tetap ada setelah Repository dibuka ulang (Req 11.1, 11.2)."""
    db_file = str(tmp_path / "monitoring.db")
    async with Repository(db_file) as repo:
        await repo.add_website(_make_website())
        await repo.save_change_event(_make_event("e1"))

    async with Repository(db_file) as repo:
        events = await repo.list_change_events("web-1")
        assert [e.id for e in events] == ["e1"]
