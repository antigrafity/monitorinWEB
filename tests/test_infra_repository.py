"""Smoke test persistensi Repository / Data_Store SQLite (task 8.1).

Memverifikasi fondasi Data_Store:

- inisialisasi skema membuat seluruh tabel & index yang diharapkan;
- mode WAL aktif;
- data yang ditulis tetap ada setelah koneksi ditutup lalu Repository baru
  dibuka ulang pada file yang sama (Req 11.1);
- penulisan diserialkan melalui satu writer + ``asyncio.Lock``.
"""

import asyncio

import pytest

from monitoring.infra.repository import Repository


async def test_schema_and_wal_initialized(tmp_path):
    """connect() membuat seluruh tabel/index dan mengaktifkan WAL."""
    db_file = tmp_path / "monitoring.db"
    repo = Repository(str(db_file))
    await repo.connect()
    try:
        conn = repo._require_conn()

        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] async for row in cur}
        assert {"website_config", "snapshot", "change_event", "app_config"} <= tables

        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ) as cur:
            indexes = {row[0] async for row in cur}
        assert {"idx_snapshot_latest", "idx_event_website"} <= indexes

        async with conn.execute("PRAGMA journal_mode") as cur:
            mode = (await cur.fetchone())[0]
        assert mode.lower() == "wal"
    finally:
        await repo.close()


async def test_data_persists_across_reopen(tmp_path):
    """Tulis lalu buka ulang koneksi: data tetap ada (Req 11.1)."""
    db_file = tmp_path / "monitoring.db"

    repo1 = Repository(str(db_file))
    await repo1.connect()
    await repo1.set_app_config("global_poll_interval_seconds", "1800")
    await repo1.close()

    # Koneksi/Repository baru pada file yang sama.
    repo2 = Repository(str(db_file))
    await repo2.connect()
    try:
        value = await repo2.get_app_config("global_poll_interval_seconds")
        assert value == "1800"
    finally:
        await repo2.close()


async def test_context_manager_lifecycle(tmp_path):
    """Repository mendukung async context manager dan tetap persisten."""
    db_file = tmp_path / "monitoring.db"

    async with Repository(str(db_file)) as repo:
        await repo.set_app_config("k", "v")

    async with Repository(str(db_file)) as repo:
        assert await repo.get_app_config("k") == "v"


async def test_get_app_config_missing_returns_none(tmp_path):
    """Kunci yang tidak ada mengembalikan None."""
    db_file = tmp_path / "monitoring.db"
    async with Repository(str(db_file)) as repo:
        assert await repo.get_app_config("tidak-ada") is None


async def test_concurrent_writes_are_serialized(tmp_path):
    """Penulisan bersamaan diserialkan tanpa error dan seluruhnya tersimpan."""
    db_file = tmp_path / "monitoring.db"
    async with Repository(str(db_file)) as repo:
        await asyncio.gather(
            *(repo.set_app_config(f"key-{i}", str(i)) for i in range(25))
        )
        for i in range(25):
            assert await repo.get_app_config(f"key-{i}") == str(i)


async def test_get_before_connect_raises(tmp_path):
    """Operasi sebelum connect() memunculkan error yang jelas."""
    repo = Repository(str(tmp_path / "monitoring.db"))
    with pytest.raises(RuntimeError):
        await repo.get_app_config("k")
