"""Scheduler — pemicu pemeriksaan periodik per Monitored_Website (task 13).

Mengimplementasikan bagian *Scheduler* pada design:

- ``effective_interval(website, global_interval_seconds)`` — fungsi murni yang
  memilih Polling_Interval efektif: gunakan ``poll_interval_seconds`` khusus
  website bila ada dan valid (10..86400 detik), selain itu gunakan interval
  global (Req 1.6, 8.2, 8.3). Ini adalah target Property 2.
- ``run()`` — loop asyncio yang pada setiap iterasi membaca ulang konfigurasi
  (daftar website + interval global) sehingga perubahan interval, penambahan,
  maupun penghapusan website berlaku pada siklus berikutnya tanpa menghentikan
  pemeriksaan yang sedang berjalan (Req 1.2, 1.3, 8.4). Untuk setiap website
  yang jatuh tempo (dengan toleransi <= 5 detik, Req 8.1) pemeriksaan
  diluncurkan sebagai ``asyncio.Task`` — TIDAK di-``await`` inline — agar
  penjadwalan siklus berikutnya tidak menunggu, dan task yang sedang berjalan
  tidak terinterupsi oleh perubahan konfigurasi (Req 8.4).

Desain diuji tanpa waktu nyata: ``now`` (callable -> float detik) dan ``sleep``
(async callable) di-inject sehingga pengujian dapat memakai clock tiruan dan
tanpa jeda nyata. ``stop()`` (berbasis :class:`asyncio.Event`) dan opsi
``max_iterations`` mengakhiri loop secara deterministik.

Kompatibel Python 3.9 melalui ``from __future__ import annotations`` dan
``typing``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Dict, List, Optional, Protocol

from monitoring.config import (
    DEFAULT_GLOBAL_INTERVAL_SECONDS,
    validate_poll_interval_seconds,
)
from monitoring.domain.models import WebsiteConfig

# Kunci app_config untuk override Polling_Interval global (dalam detik).
GLOBAL_INTERVAL_KEY = "global_poll_interval_seconds"

# Toleransi penjadwalan maksimum (detik) sesuai Req 8.1. Website dianggap
# jatuh tempo bila waktu berlalu sejak pemeriksaan terakhir mencapai
# ``interval - tolerance`` sehingga pemicuan tidak pernah terlambat lebih dari
# toleransi ini relatif terhadap granularitas tick loop.
SCHEDULE_TOLERANCE_SECONDS = 5.0

# Granularitas tick loop bawaan (detik). Dijaga <= toleransi (Req 8.1) agar
# pemicuan tidak terlambat lebih dari toleransi.
DEFAULT_TICK_SECONDS = 1.0


class _OrchestratorLike(Protocol):
    """Antarmuka minimal orchestrator yang dibutuhkan Scheduler."""

    async def check_website(self, website: WebsiteConfig): ...


class _RepositoryLike(Protocol):
    """Antarmuka minimal repository yang dibutuhkan Scheduler."""

    async def list_websites(self) -> List[WebsiteConfig]: ...

    async def get_app_config(self, key: str) -> Optional[str]: ...


class Scheduler:
    """Menjadwalkan pemeriksaan periodik untuk setiap Monitored_Website.

    Dependensi di-inject agar mudah diuji secara deterministik:

    - ``repository``: sumber konfigurasi (``list_websites`` + ``get_app_config``).
    - ``orchestrator``: objek dengan ``async check_website(website)``.
    - ``now``: callable -> float (detik). Bawaan :func:`time.monotonic`.
    - ``sleep``: async callable(seconds). Bawaan :func:`asyncio.sleep`.

    State pemeriksaan terakhir per ``website.id`` disimpan in-memory pada
    ``_last_run``. Task pemeriksaan yang sedang berjalan dilacak pada
    ``_tasks`` agar tidak diluncurkan ganda untuk website yang sama dan agar
    tidak terinterupsi oleh perubahan konfigurasi (Req 8.4).
    """

    def __init__(
        self,
        repository: _RepositoryLike,
        orchestrator: _OrchestratorLike,
        *,
        now: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        tolerance_seconds: float = SCHEDULE_TOLERANCE_SECONDS,
        default_global_interval_seconds: int = DEFAULT_GLOBAL_INTERVAL_SECONDS,
        global_interval_key: str = GLOBAL_INTERVAL_KEY,
        max_iterations: Optional[int] = None,
    ) -> None:
        self._repository = repository
        self._orchestrator = orchestrator
        self._now = now if now is not None else time.monotonic
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._tick_seconds = float(tick_seconds)
        self._tolerance = float(tolerance_seconds)
        self._default_global_interval = int(default_global_interval_seconds)
        self._global_interval_key = global_interval_key
        self._max_iterations = max_iterations

        # Dibuat lazily di dalam konteks async (Python 3.9 mengikat Event ke
        # event loop saat konstruksi; menundanya membuat Scheduler dapat
        # dibangun di luar event loop, mis. saat uji fungsi murni).
        self._stop_event: Optional[asyncio.Event] = None
        # Waktu (detik, dari ``now``) pemeriksaan terakhir diluncurkan per id.
        self._last_run: Dict[str, float] = {}
        # Task pemeriksaan yang diluncurkan per id (untuk mencegah luncuran
        # ganda & untuk membiarkannya selesai tanpa interupsi — Req 8.4).
        self._tasks: Dict[str, "asyncio.Future"] = {}

    # --- Pemilihan interval efektif (fungsi murni; Property 2) ----------- #

    def effective_interval(
        self, website: WebsiteConfig, global_interval_seconds: int
    ) -> int:
        """Kembalikan Polling_Interval efektif untuk ``website`` (Req 1.6, 8.2, 8.3).

        Gunakan ``poll_interval_seconds`` khusus website bila disetel (bukan
        ``None``) dan valid dalam rentang 10..86400 detik; selain itu gunakan
        ``global_interval_seconds``.
        """
        custom = website.poll_interval_seconds
        if custom is not None and validate_poll_interval_seconds(custom).ok:
            return custom
        return global_interval_seconds

    # --- Loop penjadwalan ------------------------------------------------ #

    def _get_stop_event(self) -> asyncio.Event:
        """Kembalikan ``stop_event``, membuatnya lazily pada pemanggilan pertama."""
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        return self._stop_event

    async def run(self) -> None:
        """Jalankan loop penjadwalan sampai ``stop()`` atau ``max_iterations``.

        Pada setiap iterasi konfigurasi dibaca ulang (Req 1.2, 1.3, 8.4). Loop
        tidak menunggu pemeriksaan selesai: setiap pemeriksaan diluncurkan
        sebagai task terpisah (lihat :meth:`run_iteration`).
        """
        stop_event = self._get_stop_event()
        stop_event.clear()
        iterations = 0
        while not stop_event.is_set():
            await self.run_iteration()
            iterations += 1
            if (
                self._max_iterations is not None
                and iterations >= self._max_iterations
            ):
                break
            if stop_event.is_set():
                break
            await self._sleep(self._tick_seconds)
        # Biarkan pemeriksaan yang sudah diluncurkan selesai (tidak diinterupsi,
        # Req 8.4) sebelum loop benar-benar berhenti.
        await self.drain()

    async def run_iteration(self) -> List[str]:
        """Lakukan satu iterasi penjadwalan.

        1. Baca ulang konfigurasi: daftar website + interval global efektif
           (Req 1.2, 1.3, 8.4).
        2. Bersihkan state untuk website yang telah dihapus (Req 1.2). Task yang
           masih berjalan untuk website dihapus TIDAK dibatalkan (Req 8.4).
        3. Untuk setiap website yang jatuh tempo (toleransi <= 5s, Req 8.1),
           luncurkan ``check_website`` sebagai task tanpa menunggunya selesai.

        Returns:
            Daftar ``website.id`` yang pemeriksaannya diluncurkan pada iterasi
            ini (berguna untuk pengujian).
        """
        websites = await self._repository.list_websites()
        global_interval = await self._read_global_interval()
        retention_str = await self._repository.get_app_config(
            "change_event_retention_days"
        )
        try:
            retention_days = int(retention_str) if retention_str is not None else 90
        except ValueError:
            retention_days = 90
        prune_fn = getattr(self._repository, "prune_change_events", None)
        if prune_fn is not None:
            await prune_fn(retention_days)
        current_time = float(self._now())
        current_ids = {w.id for w in websites}

        # Bereskan referensi state untuk website yang telah dihapus (Req 1.2).
        for gone in set(self._last_run) - current_ids:
            self._last_run.pop(gone, None)
        for gone in set(self._tasks) - current_ids:
            task = self._tasks[gone]
            # Jangan interupsi task yang masih berjalan (Req 8.4); lepas
            # referensinya hanya bila sudah selesai.
            if task.done():
                self._tasks.pop(gone, None)

        launched: List[str] = []
        for website in websites:
            if website.paused:
                # ContentMonitor Tahap 1: website dijeda dilewati, tidak
                # dijadwalkan (tidak dihapus dari state; bila kelak
                # dilanjutkan, ia mengikuti aturan jatuh-tempo normal).
                continue
            interval = self.effective_interval(website, global_interval)
            if not self._is_due(website.id, interval, current_time):
                continue
            # Jangan luncurkan bila pemeriksaan sebelumnya untuk website ini
            # masih berjalan (Req 8.4).
            existing = self._tasks.get(website.id)
            if existing is not None and not existing.done():
                continue
            self._tasks[website.id] = self._launch(website)
            self._last_run[website.id] = current_time
            launched.append(website.id)

        return launched

    def _launch(self, website: WebsiteConfig) -> "asyncio.Future":
        """Luncurkan ``check_website`` sebagai task tanpa menunggunya (Req 8.4)."""
        return asyncio.ensure_future(self._orchestrator.check_website(website))

    def _is_due(
        self, website_id: str, interval: int, current_time: float
    ) -> bool:
        """True bila ``website_id`` jatuh tempo untuk diperiksa (Req 8.1).

        Website yang belum pernah diperiksa langsung jatuh tempo. Selain itu,
        jatuh tempo bila waktu berlalu sejak pemeriksaan terakhir mencapai
        ``interval - tolerance`` sehingga pemicuan berada dalam toleransi
        penjadwalan (Req 8.1).
        """
        last = self._last_run.get(website_id)
        if last is None:
            return True
        return (current_time - last) >= (interval - self._tolerance)

    async def _read_global_interval(self) -> int:
        """Baca Polling_Interval global efektif (detik) (Req 8.2, 8.3).

        Ambil override dari ``app_config`` bila ada dan berupa bilangan bulat
        yang dapat diurai; selain itu pakai ``DEFAULT_GLOBAL_INTERVAL_SECONDS``
        (6 jam) sebagai bawaan (Req 8.3).
        """
        raw = await self._repository.get_app_config(self._global_interval_key)
        if raw is None:
            return self._default_global_interval
        try:
            return int(raw)
        except (TypeError, ValueError):
            return self._default_global_interval

    # --- Kontrol siklus hidup ------------------------------------------- #

    def stop(self) -> None:
        """Minta loop berhenti pada kesempatan berikutnya."""
        self._get_stop_event().set()

    async def drain(self) -> None:
        """Tunggu seluruh task pemeriksaan yang sudah diluncurkan selesai.

        Task tidak dibatalkan (Req 8.4) — dibiarkan berjalan hingga tuntas.
        """
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    @property
    def last_run(self) -> Dict[str, float]:
        """Salinan pemetaan ``website.id -> waktu pemeriksaan terakhir``."""
        return dict(self._last_run)
