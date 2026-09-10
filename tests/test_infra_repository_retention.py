"""Unit test retensi/pemangkasan Snapshot pada Repository (Req 5.4, 11.4).

Mencakup:

- ``prune_snapshots`` menyisakan tepat ``keep`` Snapshot terbaru per URL dan
  menghapus yang lebih lama (Req 5.4);
- baris ``change_event`` tidak pernah terhapus atau berubah oleh pemangkasan
  (Req 11.4);
- pemangkasan saat jumlah Snapshot <= ``keep`` tidak melakukan apa pun;
- Snapshot terbaru tetap dapat diambil setelah pemangkasan (Req 5.4);
- pemangkasan per-URL tidak menyentuh URL lain, dan pemangkasan global
  memangkas setiap URL secara terpisah.
"""

from datetime import datetime, timedelta

from monitoring.config import SNAPSHOT_RETENTION_PER_URL
from monitoring.domain.models import (
    ChangeEvent,
    ChangeSummary,
    Diff,
    Snapshot,
    WebsiteConfig,
)
from monitoring.infra.repository import Repository

BASE = datetime(2024, 1, 1, 12, 0, 0)


def _make_website(
    website_id: str = "web-1", domain: str = "example.com"
) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name="Example",
        poll_interval_seconds=None,
        created_at=BASE,
    )


def _make_snapshot(
    url: str = "https://example.com/",
    website_id: str = "web-1",
    index: int = 0,
) -> Snapshot:
    """Snapshot ke-``index`` untuk ``url``; index lebih besar = lebih baru."""
    return Snapshot(
        url=url,
        website_id=website_id,
        normalized_text=f"versi {index}",
        content_hash=f"hash-{index}",
        checked_at=BASE + timedelta(hours=index),
    )


def _make_event(event_id: str, website_id: str = "web-1") -> ChangeEvent:
    return ChangeEvent(
        id=event_id,
        website_id=website_id,
        url="https://example.com/",
        detected_at=BASE,
        diff=Diff(text_added=["baris baru"]),
        summary=ChangeSummary(
            text_added=1,
            text_removed=0,
            links_added=0,
            links_removed=0,
            images_changed=0,
        ),
    )


async def _count_snapshots(repo: Repository, url: str) -> int:
    conn = repo._require_conn()
    async with conn.execute(
        "SELECT COUNT(*) FROM snapshot WHERE url = ?", (url,)
    ) as cur:
        (count,) = await cur.fetchone()
    return int(count)


async def _hashes(repo: Repository, url: str):
    conn = repo._require_conn()
    async with conn.execute(
        "SELECT content_hash FROM snapshot WHERE url = ? ORDER BY checked_at ASC",
        (url,),
    ) as cur:
        rows = await cur.fetchall()
    return [row[0] for row in rows]


async def test_prune_keeps_newest_n_snapshots(tmp_path):
    """Menyisakan tepat ``keep`` Snapshot terbaru, sisanya terhapus (Req 5.4)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url = "https://example.com/"
        for i in range(8):
            assert await repo.save_snapshot(_make_snapshot(url=url, index=i)) is True

        deleted = await repo.prune_snapshots(url, keep=3)

        assert deleted == 5
        assert await _count_snapshots(repo, url) == 3
        # Yang tersisa adalah tiga versi terbaru.
        assert await _hashes(repo, url) == ["hash-5", "hash-6", "hash-7"]


async def test_prune_uses_config_default_keep(tmp_path):
    """Tanpa argumen ``keep``, dipakai SNAPSHOT_RETENTION_PER_URL."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url = "https://example.com/"
        total = SNAPSHOT_RETENTION_PER_URL + 4
        for i in range(total):
            await repo.save_snapshot(_make_snapshot(url=url, index=i))

        deleted = await repo.prune_snapshots(url)

        assert deleted == total - SNAPSHOT_RETENTION_PER_URL
        assert await _count_snapshots(repo, url) == SNAPSHOT_RETENTION_PER_URL


async def test_prune_is_noop_when_fewer_than_keep(tmp_path):
    """Jumlah Snapshot <= keep -> tidak ada yang dihapus."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url = "https://example.com/"
        for i in range(3):
            await repo.save_snapshot(_make_snapshot(url=url, index=i))

        assert await repo.prune_snapshots(url, keep=5) == 0
        assert await _count_snapshots(repo, url) == 3

        # Tepat sama dengan keep juga no-op.
        assert await repo.prune_snapshots(url, keep=3) == 0
        assert await _count_snapshots(repo, url) == 3


async def test_prune_empty_table_is_noop(tmp_path):
    """Tanpa Snapshot sama sekali, pemangkasan aman dan mengembalikan 0."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        assert await repo.prune_snapshots() == 0
        assert await repo.prune_snapshots("https://example.com/none") == 0


async def test_latest_snapshot_still_retrievable_after_prune(tmp_path):
    """Snapshot terbaru tetap terbaca setelah pemangkasan (Req 5.4)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url = "https://example.com/"
        for i in range(6):
            await repo.save_snapshot(_make_snapshot(url=url, index=i))

        await repo.prune_snapshots(url, keep=2)

        latest = await repo.get_latest_snapshot(url)
        assert latest is not None
        assert latest.content_hash == "hash-5"
        assert latest.checked_at == BASE + timedelta(hours=5)


async def test_prune_never_deletes_newest_even_with_keep_zero(tmp_path):
    """``keep`` di bawah 1 dinaikkan menjadi 1: baseline tak pernah hilang (Req 5.4)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url = "https://example.com/"
        for i in range(4):
            await repo.save_snapshot(_make_snapshot(url=url, index=i))

        deleted = await repo.prune_snapshots(url, keep=0)

        assert deleted == 3
        assert await _count_snapshots(repo, url) == 1
        latest = await repo.get_latest_snapshot(url)
        assert latest is not None
        assert latest.content_hash == "hash-3"


async def test_prune_does_not_touch_change_events(tmp_path):
    """Pemangkasan tidak menghapus/mengubah baris change_event (Req 11.4)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url = "https://example.com/"
        for i in range(7):
            await repo.save_snapshot(_make_snapshot(url=url, index=i))
        for i in range(3):
            assert await repo.save_change_event(_make_event(f"e{i}")) is True

        before = await repo.list_change_events("web-1")
        assert len(before) == 3

        await repo.prune_snapshots(url, keep=2)

        after = await repo.list_change_events("web-1")
        assert [e.id for e in after] == [e.id for e in before]
        assert [e.diff for e in after] == [e.diff for e in before]
        assert [e.summary for e in after] == [e.summary for e in before]


async def test_prune_per_url_leaves_other_urls_untouched(tmp_path):
    """Pemangkasan sebuah URL tidak menyentuh Snapshot URL lain."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url_a = "https://example.com/a"
        url_b = "https://example.com/b"
        for i in range(5):
            await repo.save_snapshot(_make_snapshot(url=url_a, index=i))
            await repo.save_snapshot(_make_snapshot(url=url_b, index=i))

        deleted = await repo.prune_snapshots(url_a, keep=2)

        assert deleted == 3
        assert await _count_snapshots(repo, url_a) == 2
        assert await _count_snapshots(repo, url_b) == 5


async def test_prune_all_urls_prunes_each_url_separately(tmp_path):
    """Tanpa argumen ``url``, setiap URL dipangkas hingga ``keep`` masing-masing."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url_a = "https://example.com/a"
        url_b = "https://example.com/b"
        for i in range(4):
            await repo.save_snapshot(_make_snapshot(url=url_a, index=i))
        for i in range(6):
            await repo.save_snapshot(_make_snapshot(url=url_b, index=i))

        deleted = await repo.prune_snapshots(keep=2)

        assert deleted == (4 - 2) + (6 - 2)
        assert await _count_snapshots(repo, url_a) == 2
        assert await _count_snapshots(repo, url_b) == 2
        assert (await repo.get_latest_snapshot(url_a)).content_hash == "hash-3"
        assert (await repo.get_latest_snapshot(url_b)).content_hash == "hash-5"


async def test_prune_is_idempotent(tmp_path):
    """Pemangkasan berulang dengan keep sama tidak menghapus lagi."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_website())
        url = "https://example.com/"
        for i in range(6):
            await repo.save_snapshot(_make_snapshot(url=url, index=i))

        assert await repo.prune_snapshots(url, keep=3) == 3
        assert await repo.prune_snapshots(url, keep=3) == 0
        assert await _count_snapshots(repo, url) == 3
