"""Unit test operasi CRUD Website_Config pada Repository (task 8.2).

Mencakup:

- tambah + list + count (Req 1.1, 1.5);
- tolak entri duplikat domain (Req 1.7);
- tolak penambahan yang melebihi kapasitas maksimum (Req 1.8);
- hapus Monitored_Website (Req 1.2);
- ubah pengaturan Monitored_Website (Req 1.3).

Batas kapasitas dibuat kecil melalui konstruktor ``Repository(max_websites=...)``
agar pengujian batas kapasitas cepat tanpa menyisipkan 100 baris.
"""

from datetime import datetime, timedelta

import pytest

from monitoring.domain.models import WebsiteConfig
from monitoring.infra.repository import (
    CapacityExceededError,
    DuplicateWebsiteError,
    Repository,
)


def _make_config(
    website_id: str,
    domain: str,
    name: str = "Situs",
    poll_interval_seconds=None,
    created_at=None,
) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=domain,
        name=name,
        poll_interval_seconds=poll_interval_seconds,
        created_at=created_at or datetime(2024, 1, 1, 12, 0, 0),
    )


async def test_add_list_and_count(tmp_path):
    """Tambah beberapa website lalu list & count merefleksikan penyimpanan."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        assert await repo.count_websites() == 0

        base = datetime(2024, 1, 1, 12, 0, 0)
        await repo.add_website(
            _make_config("id-1", "example.com", "Example", 60, base)
        )
        await repo.add_website(
            _make_config(
                "id-2", "contoh.co.id", "Contoh", None, base + timedelta(minutes=1)
            )
        )

        assert await repo.count_websites() == 2

        websites = await repo.list_websites()
        assert [w.id for w in websites] == ["id-1", "id-2"]

        first = websites[0]
        assert first.domain == "example.com"
        assert first.name == "Example"
        assert first.poll_interval_seconds == 60
        assert first.created_at == base

        # poll_interval_seconds None dipertahankan sebagai None.
        assert websites[1].poll_interval_seconds is None


async def test_add_duplicate_domain_rejected(tmp_path):
    """Menambahkan domain yang sudah terdaftar ditolak (Req 1.7)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_config("id-1", "example.com"))

        with pytest.raises(DuplicateWebsiteError) as exc_info:
            await repo.add_website(_make_config("id-2", "example.com"))

        assert exc_info.value.domain == "example.com"
        # Tidak ada baris tambahan yang tersimpan.
        assert await repo.count_websites() == 1


async def test_add_beyond_capacity_rejected(tmp_path):
    """Penambahan yang melebihi kapasitas maksimum ditolak (Req 1.8)."""
    # Batas kecil agar cepat.
    async with Repository(str(tmp_path / "monitoring.db"), max_websites=2) as repo:
        await repo.add_website(_make_config("id-1", "a.com"))
        await repo.add_website(_make_config("id-2", "b.com"))

        with pytest.raises(CapacityExceededError) as exc_info:
            await repo.add_website(_make_config("id-3", "c.com"))

        assert exc_info.value.max_websites == 2
        # Kapasitas tidak terlampaui; baris ketiga tidak tersimpan.
        assert await repo.count_websites() == 2


async def test_remove_website(tmp_path):
    """Menghapus Monitored_Website menghilangkannya dari Data_Store (Req 1.2)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_config("id-1", "a.com"))
        await repo.add_website(_make_config("id-2", "b.com"))

        await repo.remove_website("id-1")

        remaining = await repo.list_websites()
        assert [w.id for w in remaining] == ["id-2"]
        assert await repo.count_websites() == 1


async def test_remove_nonexistent_is_noop(tmp_path):
    """Menghapus id yang tidak ada tidak menimbulkan error (idempoten)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(_make_config("id-1", "a.com"))
        await repo.remove_website("tidak-ada")
        assert await repo.count_websites() == 1


async def test_update_website(tmp_path):
    """Mengubah name & poll_interval_seconds diterapkan; domain tak berubah (Req 1.3)."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(
            _make_config("id-1", "example.com", "Nama Lama", 60)
        )

        await repo.update_website(
            _make_config("id-1", "example.com", "Nama Baru", 3600)
        )

        websites = await repo.list_websites()
        assert len(websites) == 1
        updated = websites[0]
        assert updated.name == "Nama Baru"
        assert updated.poll_interval_seconds == 3600
        assert updated.domain == "example.com"


async def test_update_poll_interval_to_none(tmp_path):
    """Mengubah poll_interval_seconds menjadi None (pakai global) diterapkan."""
    async with Repository(str(tmp_path / "monitoring.db")) as repo:
        await repo.add_website(
            _make_config("id-1", "example.com", "Situs", 120)
        )

        await repo.update_website(
            _make_config("id-1", "example.com", "Situs", None)
        )

        websites = await repo.list_websites()
        assert websites[0].poll_interval_seconds is None


async def test_add_after_capacity_freed(tmp_path):
    """Setelah menghapus, penambahan baru diterima kembali (Req 1.2, 1.8)."""
    async with Repository(str(tmp_path / "monitoring.db"), max_websites=1) as repo:
        await repo.add_website(_make_config("id-1", "a.com"))

        with pytest.raises(CapacityExceededError):
            await repo.add_website(_make_config("id-2", "b.com"))

        await repo.remove_website("id-1")
        # Sekarang ada ruang lagi.
        await repo.add_website(_make_config("id-2", "b.com"))
        assert await repo.count_websites() == 1
