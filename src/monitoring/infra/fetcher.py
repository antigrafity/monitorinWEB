"""Fetcher — pengambilan konten HTTP secara asynchronous (Req 3.1–3.5).

Modul ini mengimplementasikan bagian pengambilan halaman HTML dari lapisan
infrastruktur (task 9.1). Fetcher menggunakan ``httpx.AsyncClient`` (HTTP
biasa, TANPA headless browser sesuai Req 3.2) dan membatasi jumlah permintaan
bersamaan lintas seluruh Monitored_Website melalui sebuah ``asyncio.Semaphore``
global yang di-inject dari luar (Req 3.1).

Perilaku pemetaan hasil (Req 3.3, 3.4):

- Respons dengan kode status di rentang keberhasilan 2xx -> ``ok=True`` beserta
  body HTML.
- Respons dengan kode status di luar 2xx -> ``ok=False`` dan kode status dicatat
  (Req 3.4).
- Kegagalan koneksi atau timeout -> ``ok=False`` beserta ``failure_reason``,
  tanpa mengangkat pengecualian sehingga pemanggil dapat melanjutkan pemeriksaan
  halaman lain (Req 3.3).

Timeout per-request dapat dikonfigurasi (bawaan 30 detik, rentang 1..300)
sesuai Req 3.5, dan divalidasi pada konstruktor.

Kompatibilitas: target runtime Python 3.9 (memakai ``from __future__ import
annotations`` dan ``typing.Optional`` alih-alih sintaks union PEP 604).

Pengunduhan gambar (``fetch_image``, task 9.2) juga diimplementasikan di sini:
GET di bawah semaphore global dengan timeout per-gambar (bawaan 30 detik),
batas ukuran maksimum (bawaan 10 MB), dan hingga 3 percobaan (Req 7.1). Bila
seluruh percobaan gagal atau isi melebihi batas ukuran, URL gambar ditandai
gagal (``ok=False``) TANPA mengangkat pengecualian sehingga pemrosesan gambar
lain tetap berlanjut (Req 7.5).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx

from monitoring.config import (
    DEFAULT_FETCH_CONCURRENCY,
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    FETCH_CONCURRENCY_MAX,
    FETCH_CONCURRENCY_MIN,
    FETCH_TIMEOUT_MAX_SECONDS,
    FETCH_TIMEOUT_MIN_SECONDS,
    IMAGE_MAX_BYTES,
    IMAGE_MAX_RETRIES,
    IMAGE_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class FetchResult:
    """Hasil pengambilan sebuah halaman HTML (Req 3.3, 3.4).

    - ``url``: URL yang diambil.
    - ``ok``: ``True`` jika dan hanya jika kode status berada di rentang
      keberhasilan 2xx (Req 3.4).
    - ``status_code``: kode status HTTP yang diterima, atau ``None`` bila
      permintaan gagal sebelum menerima respons (mis. error koneksi/timeout).
    - ``html``: body HTML bila berhasil, selain itu ``None``.
    - ``failure_reason``: penjelasan singkat penyebab kegagalan, atau ``None``
      bila berhasil (Req 3.3).
    """

    url: str
    ok: bool
    status_code: Optional[int]
    html: Optional[str]
    failure_reason: Optional[str]


@dataclass(frozen=True)
class ImageFetchResult:
    """Hasil pengunduhan sebuah gambar (Req 7.1, 7.5).

    - ``url``: URL gambar yang diunduh.
    - ``ok``: ``True`` jika dan hanya jika gambar berhasil diunduh dengan status
      2xx dan ukuran tidak melebihi batas maksimum.
    - ``content``: isi biner gambar bila berhasil, selain itu ``None``. Isi yang
      melebihi batas ukuran TIDAK dipertahankan (``None``).
    - ``failure_reason``: penjelasan singkat penyebab kegagalan setelah seluruh
      percobaan habis atau bila isi melebihi batas ukuran, atau ``None`` bila
      berhasil (Req 7.5).
    """

    url: str
    ok: bool
    content: Optional[bytes]
    failure_reason: Optional[str]


class ConcurrencyLimitError(ValueError):
    """Batas konkurensi di luar rentang yang diperbolehkan (Req 3.1)."""


class TimeoutConfigError(ValueError):
    """Nilai timeout per-request di luar rentang yang diperbolehkan (Req 3.5)."""


def _is_valid_integer(value: object) -> bool:
    """True hanya bila ``value`` bilangan bulat sejati (bukan ``bool``/``float``).

    Konsisten dengan gaya validasi pada :mod:`monitoring.config`.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def make_semaphore(limit: int = DEFAULT_FETCH_CONCURRENCY) -> asyncio.Semaphore:
    """Buat ``asyncio.Semaphore`` global untuk membatasi konkurensi Fetcher.

    ``limit`` adalah batas jumlah permintaan bersamaan lintas seluruh
    Monitored_Website (bawaan ``DEFAULT_FETCH_CONCURRENCY`` = 10). Nilai harus
    berupa bilangan bulat dalam rentang ``FETCH_CONCURRENCY_MIN``..
    ``FETCH_CONCURRENCY_MAX`` (1..100) sesuai Req 3.1; di luar rentang atau
    bukan bilangan bulat sejati akan menolak dengan
    :class:`ConcurrencyLimitError`.
    """
    if not _is_valid_integer(limit):
        raise ConcurrencyLimitError(
            "Batas konkurensi harus berupa bilangan bulat dalam rentang "
            f"{FETCH_CONCURRENCY_MIN}\u2013{FETCH_CONCURRENCY_MAX}."
        )
    if limit < FETCH_CONCURRENCY_MIN or limit > FETCH_CONCURRENCY_MAX:
        raise ConcurrencyLimitError(
            "Batas konkurensi di luar rentang yang diperbolehkan "
            f"({FETCH_CONCURRENCY_MIN}\u2013{FETCH_CONCURRENCY_MAX}): {limit!r}."
        )
    return asyncio.Semaphore(limit)


def is_success_status(status_code: int) -> bool:
    """True bila ``status_code`` berada dalam rentang keberhasilan 2xx (Req 3.4)."""
    return 200 <= status_code < 300


class Fetcher:
    """Pengambil halaman HTML asynchronous dengan batas konkurensi global.

    ``semaphore`` di-inject dari luar sehingga batas konkurensi berlaku lintas
    seluruh Monitored_Website (Req 3.1). ``timeout_seconds`` menetapkan batas
    waktu per HTTP request (bawaan 30, rentang 1..300; Req 3.5) dan divalidasi
    pada konstruktor.

    Untuk keperluan pengujian, sebuah ``client`` (``httpx.AsyncClient``) atau
    ``transport`` (``httpx.BaseTransport``, mis. ``httpx.MockTransport``) dapat
    di-inject sehingga status HTTP arbitrer dapat disimulasikan tanpa jaringan
    nyata. Bila ``client`` di-inject, Fetcher tidak menutupnya (kepemilikan ada
    di pemanggil); bila tidak, Fetcher membuat dan menutup ``AsyncClient``
    sekali pakai per permintaan.
    """

    def __init__(
        self,
        semaphore: asyncio.Semaphore,
        timeout_seconds: int = DEFAULT_FETCH_TIMEOUT_SECONDS,
        client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
        image_timeout_seconds: int = IMAGE_TIMEOUT_SECONDS,
        image_max_bytes: int = IMAGE_MAX_BYTES,
        image_max_retries: int = IMAGE_MAX_RETRIES,
    ) -> None:
        if not _is_valid_integer(timeout_seconds):
            raise TimeoutConfigError(
                "Timeout harus berupa bilangan bulat detik dalam rentang "
                f"{FETCH_TIMEOUT_MIN_SECONDS}\u2013{FETCH_TIMEOUT_MAX_SECONDS}."
            )
        if (
            timeout_seconds < FETCH_TIMEOUT_MIN_SECONDS
            or timeout_seconds > FETCH_TIMEOUT_MAX_SECONDS
        ):
            raise TimeoutConfigError(
                "Timeout di luar rentang yang diperbolehkan "
                f"({FETCH_TIMEOUT_MIN_SECONDS}\u2013{FETCH_TIMEOUT_MAX_SECONDS} "
                f"detik): {timeout_seconds!r}."
            )

        self._semaphore = semaphore
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._transport = transport
        # Parameter unduhan gambar (Req 7.1); dapat di-inject untuk pengujian
        # (mis. batas ukuran kecil agar uji cepat).
        self._image_timeout_seconds = image_timeout_seconds
        self._image_max_bytes = image_max_bytes
        self._image_max_retries = image_max_retries

    @property
    def timeout_seconds(self) -> int:
        """Batas waktu per HTTP request dalam detik (Req 3.5)."""
        return self._timeout_seconds

    @property
    def image_max_bytes(self) -> int:
        """Batas ukuran maksimum unduhan gambar dalam byte (Req 7.1)."""
        return self._image_max_bytes

    @property
    def image_max_retries(self) -> int:
        """Jumlah percobaan maksimum (total tries) unduhan gambar (Req 7.1)."""
        return self._image_max_retries

    async def fetch_page(self, url: str) -> FetchResult:
        """Ambil sebuah halaman HTML via HTTP GET (Req 3.2, 3.3, 3.4).

        Menggunakan ``httpx.AsyncClient`` (tanpa headless browser). Permintaan
        dijalankan di bawah semaphore global sehingga jumlah permintaan
        bersamaan tidak melebihi batas konkurensi (Req 3.1).

        Mengembalikan :class:`FetchResult`:

        - ``ok=True`` bila status 2xx, menyertakan body HTML (Req 3.4);
        - ``ok=False`` bila status di luar 2xx, mencatat kode status (Req 3.4);
        - ``ok=False`` bila error koneksi/timeout, mencatat ``failure_reason``
          tanpa mengangkat pengecualian (Req 3.3).
        """
        async with self._semaphore:
            if self._client is not None:
                return await self._request(self._client, url)

            # Tanpa client yang di-inject: buat AsyncClient sekali pakai dengan
            # timeout terkonfigurasi (Req 3.5) dan tutup setelah selesai.
            client = httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(self._timeout_seconds),
            )
            try:
                return await self._request(client, url)
            finally:
                await client.aclose()

    async def fetch_image(self, url: str) -> ImageFetchResult:
        """Unduh sebuah gambar via HTTP GET (Req 7.1, 7.5).

        Permintaan dijalankan di bawah semaphore global (Req 3.1) dengan batas
        waktu per-gambar (bawaan 30 detik). Diulang hingga
        ``image_max_retries`` percobaan (total tries, bawaan 3) bila terjadi
        error/timeout atau status di luar 2xx. Bila seluruh percobaan gagal,
        URL gambar ditandai gagal (``ok=False``) TANPA mengangkat pengecualian
        sehingga pemrosesan gambar lain tetap berlanjut (Req 7.5).

        Batas ukuran maksimum (bawaan 10 MB) diperiksa via header
        ``Content-Length`` bila ada dan via panjang isi yang benar-benar
        terunduh; bila melebihi batas, hasil ditandai gagal (``ok=False``,
        alasan "terlalu besar") dan isi yang berlebih TIDAK dipertahankan.
        Kelebihan ukuran bersifat definitif sehingga tidak diulang.
        """
        async with self._semaphore:
            if self._client is not None:
                return await self._fetch_image_with_retries(self._client, url)

            client = httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(self._image_timeout_seconds),
            )
            try:
                return await self._fetch_image_with_retries(client, url)
            finally:
                await client.aclose()

    async def _fetch_image_with_retries(
        self, client: httpx.AsyncClient, url: str
    ) -> ImageFetchResult:
        """Jalankan unduhan gambar dengan pengulangan (Req 7.1, 7.5).

        Melakukan hingga ``image_max_retries`` percobaan. Kelebihan ukuran
        bersifat definitif (tidak diulang); error/timeout dan status non-2xx
        dianggap percobaan gagal yang dapat diulang. Mengembalikan
        :class:`ImageFetchResult` tanpa mengangkat pengecualian.
        """
        last_reason = "tidak ada percobaan yang dijalankan"
        attempts = max(1, self._image_max_retries)
        for _ in range(attempts):
            try:
                response = await client.get(
                    url,
                    timeout=httpx.Timeout(self._image_timeout_seconds),
                    follow_redirects=True,
                )
            except httpx.TimeoutException as exc:
                last_reason = (
                    f"timeout setelah {self._image_timeout_seconds}s: {exc!r}"
                )
                continue
            except httpx.HTTPError as exc:
                last_reason = f"kegagalan permintaan: {exc!r}"
                continue

            status = response.status_code
            if not is_success_status(status):
                last_reason = (
                    f"kode status {status} di luar rentang keberhasilan 2xx"
                )
                continue

            # Pemeriksaan awal via Content-Length bila tersedia (Req 7.1).
            declared = response.headers.get("content-length")
            if declared is not None:
                try:
                    declared_len = int(declared)
                except ValueError:
                    declared_len = None
                if declared_len is not None and declared_len > self._image_max_bytes:
                    # Kelebihan ukuran bersifat definitif; jangan simpan isi.
                    return ImageFetchResult(
                        url=url,
                        ok=False,
                        content=None,
                        failure_reason=(
                            f"gambar terlalu besar: Content-Length {declared_len} "
                            f"byte melebihi batas {self._image_max_bytes} byte"
                        ),
                    )

            content = response.content
            if len(content) > self._image_max_bytes:
                # Kelebihan ukuran bersifat definitif; jangan simpan isi.
                return ImageFetchResult(
                    url=url,
                    ok=False,
                    content=None,
                    failure_reason=(
                        f"gambar terlalu besar: {len(content)} byte melebihi "
                        f"batas {self._image_max_bytes} byte"
                    ),
                )

            return ImageFetchResult(
                url=url, ok=True, content=content, failure_reason=None
            )

        # Seluruh percobaan habis tanpa keberhasilan (Req 7.5).
        return ImageFetchResult(
            url=url,
            ok=False,
            content=None,
            failure_reason=(
                f"gagal setelah {attempts} percobaan; alasan terakhir: {last_reason}"
            ),
        )

    async def _request(self, client: httpx.AsyncClient, url: str) -> FetchResult:
        """Lakukan GET dan petakan respons/kegagalan menjadi FetchResult."""
        try:
            response = await client.get(
                url,
                timeout=httpx.Timeout(self._timeout_seconds),
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            # Timeout permintaan (Req 3.3, 3.5).
            return FetchResult(
                url=url,
                ok=False,
                status_code=None,
                html=None,
                failure_reason=f"timeout setelah {self._timeout_seconds}s: {exc!r}",
            )
        except httpx.HTTPError as exc:
            # Kegagalan koneksi/permintaan lain (Req 3.3).
            return FetchResult(
                url=url,
                ok=False,
                status_code=None,
                html=None,
                failure_reason=f"kegagalan permintaan: {exc!r}",
            )

        status = response.status_code
        if is_success_status(status):
            return FetchResult(
                url=url,
                ok=True,
                status_code=status,
                html=response.text,
                failure_reason=None,
            )
        # Status di luar rentang keberhasilan 2xx -> gagal + catat status (Req 3.4).
        return FetchResult(
            url=url,
            ok=False,
            status_code=status,
            html=None,
            failure_reason=f"kode status {status} di luar rentang keberhasilan 2xx",
        )
