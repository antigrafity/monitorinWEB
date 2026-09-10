"""Entry point aplikasi Monitoring_System (task 15).

Modul ini merangkai seluruh komponen menjadi satu aplikasi yang dapat
dijalankan dan menjalankan DUA "sisi" secara bersamaan dalam SATU event loop
asyncio (sesuai bagian *Architecture* pada design):

1. **Sisi Monitoring** — :class:`monitoring.infra.scheduler.Scheduler` memicu
   pemeriksaan periodik per Monitored_Website melalui
   :class:`monitoring.app.orchestrator.CheckOrchestrator`.
2. **Sisi Dashboard** — aplikasi FastAPI dari
   :func:`monitoring.web.app.create_app` disajikan oleh ``uvicorn`` sebagai
   sebuah task pada event loop yang sama.

Alur ``run_app``:

1. Buat & hubungkan :class:`Repository` (SQLite persisten, Req 11.1) sehingga
   skema terinisialisasi. Path DB diambil dari env ``MONITORING_DB_PATH``
   dengan nilai bawaan yang wajar.
2. Muat konfigurasi/state persisten via ``repository.load_state()`` (Req 11.3);
   bila ada kesalahan pemuatan (``has_errors``) dicatat ke log namun operasi
   tetap dilanjutkan (Req 11.6 sudah ditangani di repository).
3. Bangun ``Fetcher`` (``make_semaphore`` dibuat DI DALAM event loop agar aman
   pada Python 3.9), ``Notifier`` (dari env; anggun bila token/chat tidak
   diset), ``CheckOrchestrator``, dan ``Scheduler``.
4. Buat aplikasi dashboard FastAPI via
   ``create_app(repository, orchestrator=orchestrator)`` (orchestrator di-inject
   agar tombol "Cek Sekarang" dapat memicu pemeriksaan segera).
5. Jalankan loop scheduler dan server uvicorn secara konkuren; pastikan
   *graceful shutdown*: hentikan scheduler (``stop()`` + ``drain()``), minta
   uvicorn keluar, lalu tutup repository.

CATATAN KEAMANAN: Dashboard TIDAK memiliki autentikasi/otorisasi apa pun. Siapa
pun yang dapat menjangkau host:port dapat melihat serta mengubah daftar
Monitored_Website. JANGAN mengekspos dashboard ke jaringan publik tanpa
proteksi tambahan (mis. reverse proxy dengan autentikasi, firewall, atau
VPN). Implementasi autentikasi sengaja di luar ruang lingkup task ini.

Kompatibel Python 3.9 melalui ``from __future__ import annotations`` dan
``typing``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import uvicorn

from monitoring.app.orchestrator import CheckOrchestrator
from monitoring.config import DEFAULT_FETCH_CONCURRENCY, DEFAULT_FETCH_TIMEOUT_SECONDS
from monitoring.infra.fetcher import Fetcher, make_semaphore
from monitoring.infra.notifier import Notifier
from monitoring.infra.repository import Repository
from monitoring.infra.scheduler import Scheduler
from monitoring.infra.ssl_check import check_ssl_expiry
from monitoring.web.app import create_app

logger = logging.getLogger(__name__)

# --- Nama variabel lingkungan yang dibaca aplikasi -------------------------- #
ENV_DB_PATH = "MONITORING_DB_PATH"
ENV_HOST = "MONITORING_HOST"
ENV_PORT = "MONITORING_PORT"
ENV_TELEGRAM_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_TELEGRAM_CHAT = "TELEGRAM_CHAT_ID"

# --- Nilai bawaan ----------------------------------------------------------- #
DEFAULT_DB_PATH = "monitoring.db"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class NullNotifier:
    """Notifier no-op yang dipakai bila kredensial Telegram tidak tersedia.

    Menjaga antarmuka ``async notify(event, website) -> bool`` yang sama dengan
    :class:`monitoring.infra.notifier.Notifier`, tetapi tidak pernah mengirim
    apa pun. Setiap pemanggilan mencatat peringatan (sekali per event) dan
    mengembalikan ``False`` sehingga siklus pemeriksaan tetap berjalan tanpa
    mengangkat pengecualian (selaras dengan Req 9.4).

    Dengan demikian sisi monitoring tetap dapat berjalan penuh (discovery,
    fetch, deteksi, persistensi) meski notifikasi dinonaktifkan; hanya
    pengiriman Telegram yang dilewati.
    """

    async def notify(self, event: Any, website: Any) -> bool:  # noqa: D401
        logger.warning(
            "Notifikasi Telegram dinonaktifkan (kredensial tidak diset); "
            "melewati notifikasi untuk Change_Event %s (URL %s).",
            getattr(event, "id", "?"),
            getattr(event, "url", "?"),
        )
        return False


@dataclass
class AppComponents:
    """Kumpulan komponen aplikasi yang sudah dirangkai dan siap dijalankan.

    Difaktorkan sebagai struktur terpisah agar dapat diuji tanpa benar-benar
    mengikat port jaringan atau menjalankan loop scheduler tak-hingga.
    """

    repository: Repository
    fetcher: Fetcher
    notifier: Any
    orchestrator: CheckOrchestrator
    scheduler: Scheduler
    app: Any
    load_error_count: int = 0


def _build_notifier() -> Any:
    """Bangun Notifier dari variabel lingkungan; anggun bila tidak diset.

    Bila ``TELEGRAM_BOT_TOKEN`` dan ``TELEGRAM_CHAT_ID`` keduanya tersedia,
    kembalikan :class:`Notifier` yang siap mengirim. Bila salah satu tidak
    diset, kembalikan :class:`NullNotifier` (no-op) dan catat peringatan bahwa
    notifikasi dinonaktifkan — aplikasi tetap dapat start dan sisi monitoring
    tetap berjalan.
    """
    token = os.environ.get(ENV_TELEGRAM_TOKEN)
    chat_id = os.environ.get(ENV_TELEGRAM_CHAT)
    if token and chat_id:
        logger.info("Notifier Telegram aktif (kredensial terbaca dari lingkungan).")
        return Notifier(bot_token=token, chat_id=chat_id)
    logger.warning(
        "Kredensial Telegram tidak lengkap (%s / %s belum diset). Notifikasi "
        "dinonaktifkan; monitoring tetap berjalan tetapi perubahan tidak akan "
        "dikirim ke Telegram.",
        ENV_TELEGRAM_TOKEN,
        ENV_TELEGRAM_CHAT,
    )
    return NullNotifier()


async def build_components(
    db_path: Optional[str] = None,
    *,
    concurrency: int = DEFAULT_FETCH_CONCURRENCY,
    timeout_seconds: int = DEFAULT_FETCH_TIMEOUT_SECONDS,
    notifier: Optional[Any] = None,
) -> AppComponents:
    """Rangkai seluruh komponen aplikasi dan kembalikan :class:`AppComponents`.

    Fungsi ini melakukan semua wiring TANPA menjalankan server maupun loop
    scheduler, sehingga aman dipakai pada pengujian.

    Langkah:

    1. Buat & hubungkan :class:`Repository` pada ``db_path`` (skema
       terinisialisasi; persisten lintas restart — Req 11.1).
    2. Muat state persisten via ``load_state()`` (Req 11.3); kesalahan pemuatan
       dicatat ke log namun tidak menghentikan wiring (Req 11.6).
    3. Buat ``asyncio.Semaphore`` global (``make_semaphore``) DI DALAM event
       loop (penting untuk Python 3.9) lalu bangun ``Fetcher``.
    4. Bangun ``Notifier`` dari lingkungan (atau pakai ``notifier`` yang
       di-inject untuk pengujian), ``CheckOrchestrator``, dan ``Scheduler``.
    5. Buat aplikasi dashboard FastAPI via ``create_app(repository,
       orchestrator=orchestrator)``.

    Pemanggil bertanggung jawab menutup ``repository`` (dan menghentikan
    ``scheduler`` bila dijalankan). :func:`run_app` melakukan keduanya.
    """
    resolved_db_path = db_path or os.environ.get(ENV_DB_PATH) or DEFAULT_DB_PATH

    repository = Repository(resolved_db_path)
    await repository.connect()

    # Muat state persisten saat start (Req 11.3). Kegagalan pemuatan tidak
    # menghentikan operasi; hanya dicatat (Req 11.6).
    load_state = await repository.load_state()
    if load_state.has_errors:
        logger.warning(
            "Pemuatan state saat start menemukan %d kesalahan; operasi tetap "
            "dilanjutkan (Req 11.6). Detail: %s",
            len(load_state.load_errors),
            "; ".join(load_state.load_errors),
        )
    logger.info(
        "State termuat: %d Monitored_Website, %d Snapshot terbaru.",
        len(load_state.websites),
        len(load_state.snapshots),
    )

    # Semaphore harus dibuat di dalam event loop (Python 3.9).
    semaphore = make_semaphore(concurrency)
    fetcher = Fetcher(semaphore, timeout_seconds=timeout_seconds)

    resolved_notifier = notifier if notifier is not None else _build_notifier()

    # ``ssl_checker`` diinjeksikan di sini (bukan bawaan CheckOrchestrator)
    # agar cek SSL nyata (ContentMonitor Tahap 3, sumber Alert #8) HANYA
    # terjadi pada wiring produksi; test unit/integrasi yang membangun
    # CheckOrchestrator tanpa argumen ini tidak pernah menyentuh jaringan.
    orchestrator = CheckOrchestrator(
        repository, fetcher, resolved_notifier, ssl_checker=check_ssl_expiry
    )
    scheduler = Scheduler(repository, orchestrator)

    # Orchestrator di-inject agar rute "Cek Sekarang"
    # (POST /websites/{id}/check) dapat memicu pemeriksaan segera (Req 8.4).
    app = create_app(repository, orchestrator=orchestrator)

    return AppComponents(
        repository=repository,
        fetcher=fetcher,
        notifier=resolved_notifier,
        orchestrator=orchestrator,
        scheduler=scheduler,
        app=app,
        load_error_count=len(load_state.load_errors),
    )


def _resolve_host_port() -> "tuple[str, int]":
    """Ambil host & port dari lingkungan dengan bawaan 127.0.0.1:8000."""
    host = os.environ.get(ENV_HOST) or DEFAULT_HOST
    raw_port = os.environ.get(ENV_PORT)
    port = DEFAULT_PORT
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError:
            logger.warning(
                "Nilai %s tidak valid (%r); memakai port bawaan %d.",
                ENV_PORT,
                raw_port,
                DEFAULT_PORT,
            )
    return host, port


async def run_app(
    db_path: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Jalankan sisi monitoring dan dashboard bersama dalam satu event loop.

    Menjalankan ``scheduler.run()`` dan ``uvicorn.Server.serve()`` sebagai dua
    task konkuren. Ketika salah satu selesai (mis. server menerima sinyal
    shutdown / Ctrl+C), dilakukan *graceful shutdown*: scheduler dihentikan dan
    di-``drain`` (membiarkan pemeriksaan yang sedang berjalan selesai — Req
    8.4), server diminta keluar, task yang tersisa dibatalkan, lalu repository
    ditutup.
    """
    resolved_host = host if host is not None else _resolve_host_port()[0]
    resolved_port = port if port is not None else _resolve_host_port()[1]

    components = await build_components(db_path)
    repository = components.repository
    scheduler = components.scheduler

    # PERINGATAN KEAMANAN: dashboard tanpa autentikasi (lihat docstring modul).
    logger.warning(
        "Dashboard berjalan TANPA autentikasi pada http://%s:%d — jangan "
        "diekspos ke jaringan publik tanpa proteksi tambahan.",
        resolved_host,
        resolved_port,
    )

    config = uvicorn.Config(
        components.app,
        host=resolved_host,
        port=resolved_port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    scheduler_task = asyncio.ensure_future(scheduler.run())
    server_task = asyncio.ensure_future(server.serve())

    try:
        # Tunggu hingga salah satu sisi berhenti (mis. sinyal shutdown pada
        # server, atau scheduler dihentikan).
        await asyncio.wait(
            {scheduler_task, server_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        # --- Graceful shutdown ------------------------------------------- #
        # 1. Hentikan scheduler & biarkan pemeriksaan berjalan selesai.
        scheduler.stop()
        try:
            await scheduler.drain()
        except Exception:  # noqa: BLE001 - shutdown harus tetap lanjut
            logger.exception("Kegagalan saat drain scheduler ketika shutdown.")
        if not scheduler_task.done():
            scheduler_task.cancel()

        # 2. Minta uvicorn keluar dengan anggun.
        server.should_exit = True
        if not server_task.done():
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("Kegagalan saat menutup server uvicorn.")

        # Pastikan scheduler_task benar-benar selesai/terbatalkan.
        if not scheduler_task.done():
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

        # 3. Tutup repository (Data_Store).
        await repository.close()
        logger.info("Aplikasi berhenti dengan bersih.")


def main() -> None:
    """Titik masuk sinkron: konfigurasikan logging lalu jalankan ``run_app``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:  # pragma: no cover - jalur interaktif
        logger.info("Diminta berhenti (KeyboardInterrupt).")


if __name__ == "__main__":  # pragma: no cover
    main()
