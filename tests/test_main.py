"""Smoke test entry point aplikasi (task 15).

Menguji wiring aplikasi TANPA benar-benar mengikat port jaringan atau
menjalankan loop scheduler tak-hingga. Fokus:

- ``build_components`` menghasilkan aplikasi dashboard yang berfungsi dan
  repository yang terhubung; dashboard memuat daftar (Req 10.1).
- Persistensi lintas restart pada tingkat wiring: tulis website, tutup, buka
  ulang, ``load_state`` mengembalikannya (Req 11.1, 11.3).
- ``NullNotifier`` mengembalikan ``False`` tanpa mengangkat pengecualian saat
  kredensial Telegram tidak tersedia.

Server uvicorn maupun ``scheduler.run()`` TIDAK dijalankan di sini agar tidak
ada test yang menggantung.
"""

from datetime import datetime

from fastapi.testclient import TestClient

from monitoring.domain.models import WebsiteConfig
from monitoring.infra.notifier import Notifier
from monitoring.infra.repository import Repository
from monitoring.main import (
    NullNotifier,
    _build_notifier,
    build_components,
)


async def test_build_components_dashboard_loads_list(tmp_path):
    """build_components menghasilkan dashboard yang memuat daftar (Req 10.1).

    Menyisipkan sebuah website pada DB sementara, merangkai komponen, lalu
    memverifikasi ``GET /`` merespons dan menampilkan domain. Membersihkan
    repository di akhir; server tidak dijalankan.
    """
    db_path = str(tmp_path / "monitoring.db")

    # Siapkan data awal via repository terpisah untuk mensimulasikan state yang
    # sudah tersimpan sebelum aplikasi start.
    async with Repository(db_path) as seed:
        await seed.add_website(
            WebsiteConfig(
                id="id-1",
                domain="contoh.com",
                name="Contoh",
                poll_interval_seconds=None,
                created_at=datetime(2024, 1, 1, 12, 0, 0),
            )
        )

    components = await build_components(db_path, notifier=NullNotifier())
    try:
        # Dashboard memuat daftar (Req 10.1).
        client = TestClient(components.app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "contoh.com" in resp.text

        # Komponen ter-wiring dengan benar.
        assert components.repository is not None
        assert components.scheduler is not None
        assert components.orchestrator is not None
        # Tidak ada kesalahan pemuatan pada data bersih.
        assert components.load_error_count == 0
    finally:
        await components.repository.close()


async def test_persistence_across_restart(tmp_path):
    """Website tetap ada setelah repository dibuka ulang (Req 11.1, 11.3).

    Verifikasi persistensi lintas restart pada tingkat wiring: tulis lalu tutup,
    lalu ``build_components`` (yang menghubungkan repo & memanggil load_state)
    memuat kembali website tersebut.
    """
    db_path = str(tmp_path / "monitoring.db")

    async with Repository(db_path) as repo:
        await repo.add_website(
            WebsiteConfig(
                id="id-persist",
                domain="persist.com",
                name="Persist",
                poll_interval_seconds=None,
                created_at=datetime(2024, 1, 1, 12, 0, 0),
            )
        )

    # Buka ulang lewat build_components; load_state harus mengembalikan website.
    components = await build_components(db_path, notifier=NullNotifier())
    try:
        state = await components.repository.load_state()
        domains = [w.domain for w in state.websites]
        assert "persist.com" in domains
        assert not state.has_errors
    finally:
        await components.repository.close()


async def test_null_notifier_returns_false_without_raising():
    """NullNotifier tidak pernah mengirim dan tidak mengangkat pengecualian."""

    class _Evt:
        id = "evt-1"
        url = "https://contoh.com/"

    result = await NullNotifier().notify(_Evt(), object())
    assert result is False


def test_build_notifier_without_credentials_returns_null(monkeypatch):
    """Tanpa kredensial Telegram, _build_notifier mengembalikan NullNotifier."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert isinstance(_build_notifier(), NullNotifier)


def test_build_notifier_with_credentials_returns_notifier(monkeypatch):
    """Dengan kredensial lengkap, _build_notifier mengembalikan Notifier nyata."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    assert isinstance(_build_notifier(), Notifier)
