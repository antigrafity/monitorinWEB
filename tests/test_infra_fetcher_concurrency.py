"""Uji batas konkurensi Fetcher (task 9.3, integrasi).

Memverifikasi bahwa jumlah permintaan HTTP bersamaan yang dilakukan Fetcher
tidak pernah melebihi batas ``asyncio.Semaphore`` global yang di-inject
(Req 3.1), meskipun banyak pemanggilan ``fetch_page`` dijalankan secara paralel
melalui SATU instance Fetcher yang dibagikan.

Pendekatan:

- Bangun satu Fetcher dengan semaphore kecil (``make_semaphore(3)``) yang
  dibagikan lintas seluruh task.
- Injeksikan transport async kustom (:class:`_ConcurrencyTrackingTransport`)
  yang, pada setiap permintaan, menaikkan penghitung in-flight, ``await
  asyncio.sleep(...)`` singkat untuk MEMAKSA tumpang-tindih antar permintaan,
  mencatat konkurensi maksimum yang teramati, lalu menurunkan penghitung saat
  keluar.
- Luncurkan banyak (20) task ``fetch_page`` bersamaan via ``asyncio.gather``.
- Tegaskan bahwa konkurensi maksimum yang teramati TIDAK pernah melebihi batas
  semaphore (3) dan seluruh permintaan selesai dengan sukses.

Transport dibuat async-aware (subkelas ``httpx.AsyncBaseTransport``) karena
``httpx.MockTransport`` bawaan menjalankan handler secara sinkron sehingga tidak
andal untuk memaksa tumpang-tindih. Dengan ``handle_async_request`` yang
menyisipkan ``asyncio.sleep``, kontrol dikembalikan ke event loop sehingga
konkurensi nyata terbentuk dan dapat diukur.

Kompatibilitas: Python 3.9 (``from __future__ import annotations``,
``typing.Optional``). Semaphore/Fetcher dikonstruksi di dalam event loop yang
berjalan (di dalam coroutine test) karena ``asyncio.Semaphore`` pada 3.9
mengambil loop saat konstruksi.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from monitoring.infra.fetcher import Fetcher, make_semaphore

pytestmark = pytest.mark.integration

URL = "https://example.com/page"


class _ConcurrencyTrackingTransport(httpx.AsyncBaseTransport):
    """Transport async tiruan yang mengukur konkurensi permintaan.

    Setiap permintaan menaikkan penghitung in-flight, menunggu sejenak untuk
    memaksa tumpang-tindih dengan permintaan lain, mencatat nilai in-flight
    maksimum yang pernah teramati, lalu menurunkan penghitung sebelum
    mengembalikan respons 200.
    """

    def __init__(self, delay_seconds: float = 0.02) -> None:
        self._delay_seconds = delay_seconds
        self.in_flight = 0
        self.max_in_flight = 0
        self.total_requests = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Masuk: naikkan in-flight dan catat maksimum yang teramati.
        self.in_flight += 1
        self.total_requests += 1
        if self.in_flight > self.max_in_flight:
            self.max_in_flight = self.in_flight
        try:
            # Paksa tumpang-tindih: kembalikan kontrol ke event loop sehingga
            # task lain yang telah melewati semaphore dapat ikut masuk.
            await asyncio.sleep(self._delay_seconds)
        finally:
            # Keluar: turunkan in-flight.
            self.in_flight -= 1
        return httpx.Response(200, text="<html>ok</html>")


@pytest.mark.integration
async def test_concurrent_fetch_page_never_exceeds_semaphore_limit():
    """Validates: Requirements 3.1

    Dengan batas semaphore 3 dan 20 permintaan bersamaan, konkurensi maksimum
    yang teramati tidak pernah melebihi 3, dan seluruh permintaan berhasil.
    """
    limit = 3
    num_tasks = 20

    transport = _ConcurrencyTrackingTransport(delay_seconds=0.02)
    fetcher = Fetcher(make_semaphore(limit), transport=transport)

    results = await asyncio.gather(
        *(fetcher.fetch_page(URL) for _ in range(num_tasks))
    )

    # Batas konkurensi dihormati (Req 3.1).
    assert transport.max_in_flight <= limit
    # Seluruh permintaan benar-benar dijalankan.
    assert transport.total_requests == num_tasks
    # Semua permintaan selesai dengan sukses.
    assert len(results) == num_tasks
    assert all(r.ok for r in results)
    assert all(r.status_code == 200 for r in results)
    # Setelah selesai, tidak ada permintaan yang tersisa in-flight.
    assert transport.in_flight == 0


@pytest.mark.integration
async def test_concurrency_actually_reaches_the_limit():
    """Validates: Requirements 3.1

    Sanity check: dengan permintaan jauh lebih banyak daripada batas dan delay
    yang memaksa tumpang-tindih, konkurensi maksimum yang teramati benar-benar
    mencapai batas semaphore (bukan sekadar <= batas karena tidak ada paralel).
    """
    limit = 3
    num_tasks = 20

    transport = _ConcurrencyTrackingTransport(delay_seconds=0.02)
    fetcher = Fetcher(make_semaphore(limit), transport=transport)

    await asyncio.gather(*(fetcher.fetch_page(URL) for _ in range(num_tasks)))

    # Konkurensi yang teramati mencapai batas penuh: membuktikan paralelisme
    # nyata terjadi dan batas semaphore-lah yang menjadi pembatas.
    assert transport.max_in_flight == limit
