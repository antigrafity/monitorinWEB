"""Uji integrasi Scheduler dengan clock tiruan (task 13).

Memverifikasi (Req 1.2, 1.3, 8.1, 8.4):

- Sebuah website diperiksa ketika jatuh tempo berdasarkan interval efektifnya
  (dengan toleransi), tidak diperiksa sebelum jatuh tempo.
- Perubahan interval, penambahan, dan penghapusan website antar-iterasi berlaku
  pada siklus berikutnya.
- Pemeriksaan diluncurkan sebagai task tanpa menunggu, dan task yang sedang
  berjalan tidak terinterupsi oleh perubahan konfigurasi.
- ``run()`` berhenti secara deterministik via ``stop()``/``max_iterations``
  tanpa jeda nyata.

Semua uji deterministik: memakai clock tiruan yang dimajukan manual, orchestrator
tiruan yang merekam pemanggilan ``check_website``, dan tanpa ``asyncio.sleep``
nyata.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional

import pytest

from monitoring.domain.models import WebsiteConfig
from monitoring.infra.scheduler import Scheduler


# --- Test doubles ------------------------------------------------------- #


class FakeClock:
    """Clock monoton tiruan berbasis float detik, dimajukan manual."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


class FakeOrchestrator:
    """Merekam pemanggilan ``check_website`` beserta waktu (dari clock)."""

    def __init__(self, clock: Optional[FakeClock] = None) -> None:
        self._clock = clock
        self.calls: List[str] = []
        self.call_times: List[float] = []

    async def check_website(self, website: WebsiteConfig):
        self.calls.append(website.id)
        if self._clock is not None:
            self.call_times.append(self._clock.t)
        return None


class SlowOrchestrator:
    """Orchestrator yang menahan penyelesaian check_website via Event.

    Dipakai untuk memverifikasi bahwa task yang sedang berjalan tidak
    terinterupsi oleh perubahan konfigurasi antar-iterasi (Req 8.4).
    """

    def __init__(self) -> None:
        self.started: List[str] = []
        self.finished: List[str] = []
        self._gate = asyncio.Event()

    def release(self) -> None:
        self._gate.set()

    async def check_website(self, website: WebsiteConfig):
        self.started.append(website.id)
        await self._gate.wait()
        self.finished.append(website.id)
        return None


class FakeRepository:
    """Repository tiruan: daftar website & interval global dapat dimutasi."""

    def __init__(
        self,
        websites: List[WebsiteConfig],
        global_interval: Optional[str] = None,
    ) -> None:
        self.websites = list(websites)
        self.global_interval = global_interval
        self.config: Dict[str, str] = {}
        if global_interval is not None:
            self.config["global_poll_interval_seconds"] = global_interval
        self.prune_calls: List[int] = []

    async def list_websites(self) -> List[WebsiteConfig]:
        return list(self.websites)

    async def get_app_config(self, key: str) -> Optional[str]:
        if key == "global_poll_interval_seconds" and self.global_interval is not None:
            return self.global_interval
        return self.config.get(key)

    async def prune_change_events(self, older_than_days: int = 90) -> int:
        self.prune_calls.append(older_than_days)
        return 0


def _website(
    website_id: str, poll: Optional[int] = None, paused: bool = False
) -> WebsiteConfig:
    return WebsiteConfig(
        id=website_id,
        domain=f"{website_id}.example.com",
        name=website_id,
        poll_interval_seconds=poll,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        paused=paused,
    )


# --- Tests -------------------------------------------------------------- #


async def test_website_checked_when_due_by_effective_interval():
    """Website diperiksa saat pertama kali & setelah interval efektif berlalu."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=100)])
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    # Iterasi pertama: belum pernah diperiksa -> jatuh tempo langsung.
    launched = await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert launched == ["a"]
    assert orch.calls == ["a"]

    # Belum jatuh tempo (50 < 100 - 5) -> tidak diluncurkan lagi.
    clock.advance(50)
    launched = await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert launched == []
    assert orch.calls == ["a"]

    # Dalam toleransi (96 >= 100 - 5) -> jatuh tempo lagi.
    clock.advance(46)  # total 96
    launched = await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert launched == ["a"]
    assert orch.calls == ["a", "a"]


async def test_custom_interval_overrides_global():
    """Interval khusus website dipakai alih-alih interval global (Req 8.2)."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    # Global besar (3600s) namun website punya interval khusus kecil (20s).
    repo = FakeRepository([_website("a", poll=20)], global_interval="3600")
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    await scheduler.run_iteration()  # baseline
    await asyncio.sleep(0)
    assert orch.calls == ["a"]

    clock.advance(20)  # >= 20 - 5 -> jatuh tempo memakai interval khusus
    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a", "a"]


async def test_default_global_interval_when_unset():
    """Tanpa override & tanpa interval khusus -> pakai default 6 jam (Req 8.3)."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=None)], global_interval=None)
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    await scheduler.run_iteration()  # baseline
    await asyncio.sleep(0)
    assert orch.calls == ["a"]

    # 20000s belum cukup untuk default 21600s (6 jam).
    clock.advance(20000)
    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a"]

    # 21595s >= 21600 - 5 -> jatuh tempo.
    clock.advance(1595)  # total 21595
    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a", "a"]


async def test_added_website_scheduled_next_cycle():
    """Menambah website antar-iterasi berlaku pada siklus berikutnya (Req 1.3)."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=100)])
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a"]

    # Tambah website b -> muncul pada iterasi berikutnya.
    repo.websites.append(_website("b", poll=100))
    launched = await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert launched == ["b"]
    assert orch.calls == ["a", "b"]


async def test_removed_website_not_scheduled():
    """Menghapus website menghentikan penjadwalannya pada siklus berikutnya (Req 1.2)."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=20), _website("b", poll=20)])
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    await scheduler.run_iteration()  # baseline a & b
    await asyncio.sleep(0)
    assert sorted(orch.calls) == ["a", "b"]

    # Hapus b sebelum jatuh tempo berikutnya.
    repo.websites = [_website("a", poll=20)]
    clock.advance(20)
    launched = await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert launched == ["a"]
    assert "b" not in launched
    # b tidak lagi memiliki state penjadwalan tersimpan.
    assert "b" not in scheduler.last_run


async def test_interval_change_applies_next_cycle():
    """Perubahan interval berlaku pada penjadwalan siklus berikutnya (Req 8.4)."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=1000)])
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    await scheduler.run_iteration()  # baseline pada t=0
    await asyncio.sleep(0)
    assert orch.calls == ["a"]

    # Dengan interval lama (1000), t=50 belum jatuh tempo.
    clock.advance(50)
    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a"]

    # Ubah interval menjadi 20 -> pada t=50, 50 >= 20 - 5, jatuh tempo.
    repo.websites = [_website("a", poll=20)]
    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a", "a"]


async def test_running_check_not_interrupted_by_config_change():
    """Task berjalan tidak terinterupsi oleh perubahan konfigurasi (Req 8.4)."""
    clock = FakeClock()
    orch = SlowOrchestrator()
    repo = FakeRepository([_website("a", poll=20)])
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    # Luncurkan pemeriksaan a (tertahan pada gate, belum selesai).
    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.started == ["a"]
    assert orch.finished == []

    # Hapus a dari konfigurasi saat pemeriksaannya masih berjalan.
    repo.websites = []
    clock.advance(100)
    launched = await scheduler.run_iteration()
    await asyncio.sleep(0)
    # Tidak ada peluncuran baru, dan task lama TIDAK dibatalkan.
    assert launched == []
    assert orch.finished == []

    # Lepaskan gate -> task lama menyelesaikan pemeriksaannya tanpa interupsi.
    orch.release()
    await scheduler.drain()
    assert orch.finished == ["a"]


async def test_no_duplicate_launch_while_running():
    """Tidak meluncurkan pemeriksaan ganda untuk website yang masih berjalan (Req 8.4)."""
    clock = FakeClock()
    orch = SlowOrchestrator()
    repo = FakeRepository([_website("a", poll=1)])
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.started == ["a"]

    # Meski jatuh tempo lagi, task sebelumnya masih berjalan -> tidak ganda.
    clock.advance(100)
    launched = await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert launched == []
    assert orch.started == ["a"]

    orch.release()
    await scheduler.drain()


async def test_run_loop_stops_via_max_iterations():
    """``run()`` berhenti setelah max_iterations tanpa jeda nyata."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=10)])

    sleep_calls: List[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.advance(seconds)

    scheduler = Scheduler(
        repo,
        orch,
        now=clock,
        sleep=fake_sleep,
        tick_seconds=1.0,
        tolerance_seconds=5.0,
        max_iterations=3,
    )

    await scheduler.run()

    # Tiga iterasi berjalan; hanya iterasi pertama meluncurkan (baseline),
    # karena tick 1s << interval 10s.
    assert orch.calls == ["a"]
    # sleep dipanggil antar iterasi (max_iterations-1 kali).
    assert len(sleep_calls) == 2


async def test_paused_website_is_not_scheduled():
    """Website dengan paused=True dilewati; tidak dijadwalkan (ContentMonitor Tahap 1)."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository(
        [_website("a", poll=10, paused=True), _website("b", poll=10)]
    )
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    launched = await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert launched == ["b"]
    assert orch.calls == ["b"]
    assert "a" not in scheduler.last_run


async def test_run_loop_stops_via_stop_event():
    """``run()`` berhenti ketika ``stop()`` dipanggil dari dalam sleep."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=10)])

    iteration_count = {"n": 0}

    async def fake_sleep(seconds: float) -> None:
        iteration_count["n"] += 1
        clock.advance(seconds)
        if iteration_count["n"] >= 2:
            scheduler.stop()

    scheduler = Scheduler(
        repo, orch, now=clock, sleep=fake_sleep, tick_seconds=1.0
    )

    await asyncio.wait_for(scheduler.run(), timeout=1.0)
    # Loop berhenti setelah stop() dipanggil; baseline terjadwal sekali.
    assert orch.calls == ["a"]


async def test_global_interval_change_applies_next_cycle():
    """Perubahan interval global di app_config langsung dipakai pada siklus berikutnya."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=None)], global_interval="1000")
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a"]

    clock.advance(50)
    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a"]

    # Ubah interval global menjadi 20 detik -> pada t=50, 50 >= 20 - 5, jatuh tempo.
    repo.global_interval = "20"
    await scheduler.run_iteration()
    await asyncio.sleep(0)
    assert orch.calls == ["a", "a"]


async def test_scheduler_prunes_change_events_each_iteration():
    """Scheduler memanggil prune_change_events setiap iterasi dengan retensi dari app_config."""
    clock = FakeClock()
    orch = FakeOrchestrator(clock)
    repo = FakeRepository([_website("a", poll=10)])
    scheduler = Scheduler(repo, orch, now=clock, tolerance_seconds=5.0)

    await scheduler.run_iteration()
    assert repo.prune_calls == [90]  # Bawaan 90 hari

    repo.config["change_event_retention_days"] = "30"
    await scheduler.run_iteration()
    assert repo.prune_calls == [90, 30]

