"""Unit test pemuatan data saat restart pada Repository (task 8.4).

Mencakup:

- restart: setelah Repository ditutup lalu dibuka ulang pada file yang sama,
  ``list_websites``, ``load_latest_snapshots``, dan ``load_state`` memuat data
  yang dipersistenkan dengan Snapshot terbaru per URL (Req 11.3);
- ketahanan terhadap data rusak: baris Snapshot/Website_Config dengan JSON
  atau timestamp tidak valid dilewati, data valid tetap dimuat, dan indikasi
  kesalahan pemuatan dipermukaan tanpa mengangkat pengecualian (Req 11.6).
"""

from datetime import datetime, timedelta

from monitoring.domain.models import Snapshot, WebsiteConfig
from monitoring.infra.repository import LoadState, Repository


def _make_website(
    website_id: str = "web-1", domain: str = "example.com"
) -> WebsiteConfig:
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


# --- Restart: data termuat kembali dengan Snapshot terbaru per URL (Req 11.3) ---


async def test_load_latest_snapshots_after_restart(tmp_path):
    """Setelah restart, load_latest_snapshots mengembalikan Snapshot terbaru per URL."""
    db_file = str(tmp_path / "monitoring.db")
    base = datetime(2024, 1, 1, 12, 0, 0)

    async with Repository(db_file) as repo:
        await repo.add_website(_make_website())
        # Dua URL berbeda; salah satunya punya banyak Snapshot (beda checked_at).
        await repo.save_snapshot(
            _make_snapshot(url="https://example.com/", content_hash="a-old", checked_at=base)
        )
        await repo.save_snapshot(
            _make_snapshot(
                url="https://example.com/",
                content_hash="a-new",
                checked_at=base + timedelta(hours=2),
                links=["https://example.com/x"],
                image_hashes={"https://example.com/i.png": "imghash-1"},
            )
        )
        await repo.save_snapshot(
            _make_snapshot(url="https://example.com/about", content_hash="b", checked_at=base)
        )

    # Buka ulang Repository baru pada file yang sama (simulasi restart).
    async with Repository(db_file) as repo:
        snapshots, load_errors = await repo.load_latest_snapshots()

        assert load_errors == []
        assert set(snapshots.keys()) == {
            "https://example.com/",
            "https://example.com/about",
        }
        # Snapshot terbaru per URL (checked_at terbesar).
        assert snapshots["https://example.com/"].content_hash == "a-new"
        assert snapshots["https://example.com/"].links == ["https://example.com/x"]
        assert snapshots["https://example.com/"].image_hashes == {
            "https://example.com/i.png": "imghash-1"
        }
        assert snapshots["https://example.com/about"].content_hash == "b"


async def test_load_state_after_restart(tmp_path):
    """load_state memuat Website_Config + Snapshot terbaru tanpa error (Req 11.3)."""
    db_file = str(tmp_path / "monitoring.db")

    async with Repository(db_file) as repo:
        await repo.add_website(_make_website("web-1", "a.com"))
        await repo.add_website(_make_website("web-2", "b.com"))
        await repo.save_snapshot(_make_snapshot(url="https://a.com/", website_id="web-1"))

    async with Repository(db_file) as repo:
        state = await repo.load_state()

        assert isinstance(state, LoadState)
        assert state.has_errors is False
        assert {w.id for w in state.websites} == {"web-1", "web-2"}
        assert set(state.snapshots.keys()) == {"https://a.com/"}


async def test_load_latest_snapshots_empty(tmp_path):
    """Tanpa Snapshot tersimpan, load_latest_snapshots mengembalikan dict kosong."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        snapshots, load_errors = await repo.load_latest_snapshots()
        assert snapshots == {}
        assert load_errors == []


# --- Data rusak: dilewati + indikasi kesalahan, tanpa crash (Req 11.6) ---


async def test_load_latest_snapshots_skips_corrupt_row(tmp_path):
    """Snapshot dengan links_json tidak valid dilewati; valid tetap dimuat (Req 11.6)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        # Snapshot valid.
        await repo.save_snapshot(
            _make_snapshot(url="https://example.com/good", content_hash="ok")
        )

        # Sisipkan baris Snapshot rusak langsung via SQL: links_json bukan JSON valid.
        conn = repo._require_conn()
        await conn.execute(
            "INSERT INTO snapshot "
            "(url, website_id, normalized_text, content_hash, "
            "links_json, image_hashes_json, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/bad",
                "web-1",
                "teks",
                "hash-bad",
                "{ini-bukan-json-valid",  # links_json rusak
                "{}",
                datetime(2024, 1, 2, 12, 0, 0).isoformat(),
            ),
        )
        await conn.commit()

        # Tidak boleh mengangkat pengecualian.
        snapshots, load_errors = await repo.load_latest_snapshots()

        # Snapshot valid tetap dimuat.
        assert "https://example.com/good" in snapshots
        assert snapshots["https://example.com/good"].content_hash == "ok"
        # Baris rusak dilewati.
        assert "https://example.com/bad" not in snapshots
        # Indikasi kesalahan pemuatan dipermukaan untuk baris rusak.
        assert len(load_errors) == 1
        assert "https://example.com/bad" in load_errors[0]


async def test_load_latest_snapshots_skips_corrupt_image_hashes(tmp_path):
    """Snapshot dengan image_hashes_json tidak valid dilewati (Req 11.6)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        await repo.save_snapshot(
            _make_snapshot(url="https://example.com/good", content_hash="ok")
        )

        conn = repo._require_conn()
        await conn.execute(
            "INSERT INTO snapshot "
            "(url, website_id, normalized_text, content_hash, "
            "links_json, image_hashes_json, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/bad",
                "web-1",
                "teks",
                "hash-bad",
                "[]",
                "%%rusak%%",  # image_hashes_json rusak
                datetime(2024, 1, 2, 12, 0, 0).isoformat(),
            ),
        )
        await conn.commit()

        snapshots, load_errors = await repo.load_latest_snapshots()

        assert "https://example.com/good" in snapshots
        assert "https://example.com/bad" not in snapshots
        assert len(load_errors) == 1


async def test_load_state_surfaces_load_errors_without_crashing(tmp_path):
    """load_state melanjutkan operasi & mempermukaan error saat data rusak (Req 11.6)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        await repo.save_snapshot(
            _make_snapshot(url="https://example.com/good", content_hash="ok")
        )

        conn = repo._require_conn()
        await conn.execute(
            "INSERT INTO snapshot "
            "(url, website_id, normalized_text, content_hash, "
            "links_json, image_hashes_json, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/bad",
                "web-1",
                "teks",
                "hash-bad",
                "bukan-json",
                "{}",
                datetime(2024, 1, 2, 12, 0, 0).isoformat(),
            ),
        )
        await conn.commit()

        state = await repo.load_state()

        # Website tetap termuat, Snapshot valid termuat, error dipermukaan.
        assert [w.id for w in state.websites] == ["web-1"]
        assert "https://example.com/good" in state.snapshots
        assert state.has_errors is True
        assert len(state.load_errors) == 1


async def test_load_state_skips_corrupt_website_row(tmp_path):
    """Website_Config dengan created_at tidak valid dilewati; valid tetap dimuat (Req 11.6)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website("web-1", "a.com"))

        # Sisipkan baris website rusak langsung via SQL: created_at bukan ISO valid.
        conn = repo._require_conn()
        await conn.execute(
            "INSERT INTO website_config "
            "(id, domain, name, poll_interval_seconds, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("web-bad", "b.com", "Bad", None, "bukan-timestamp"),
        )
        await conn.commit()

        state = await repo.load_state()

        assert [w.id for w in state.websites] == ["web-1"]
        assert state.has_errors is True
        assert any("web-bad" in msg for msg in state.load_errors)
