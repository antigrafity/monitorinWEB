"""Test untuk Fetcher.fetch_page (task 9.1).

Berisi:

- Property-based test (Hypothesis, min 100 iterasi) untuk Property 8:
  pemetaan kode status HTTP ke keberhasilan fetch (Req 3.4).
- Unit test untuk timeout dan kegagalan koneksi (Req 3.3), contoh status
  sukses/gagal spesifik (Req 3.4), serta validasi konfigurasi konkurensi &
  timeout (Req 3.1, 3.5).

Semua interaksi HTTP disimulasikan memakai ``httpx.MockTransport`` sehingga
tidak ada jaringan nyata yang tersentuh. Karena Fetcher bersifat async
sementara Hypothesis menjalankan setiap contoh secara sinkron, tiap contoh
menjalankan skenario async lewat ``asyncio.run`` di atas transport tiruan yang
segar — konsisten dengan pola pada test properti Repository.
"""

import asyncio

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.infra.fetcher import (
    ConcurrencyLimitError,
    Fetcher,
    ImageFetchResult,
    TimeoutConfigError,
    is_success_status,
    make_semaphore,
)

URL = "https://example.com/page"
IMAGE_URL = "https://example.com/image.png"


def _in_loop(fn):
    """Jalankan callable sinkron di dalam event loop.

    Pada Python 3.9, ``asyncio.Semaphore()`` mengambil event loop saat
    konstruksi sehingga harus dibuat di dalam loop yang berjalan. Helper ini
    menjalankan ``fn`` di dalam ``asyncio.run`` agar ``make_semaphore``/
    ``Fetcher`` dapat dikonstruksi seperti pada pemakaian nyata (di dalam loop).
    """

    async def runner():
        return fn()

    return asyncio.run(runner())


def _fetcher_with_handler(handler, timeout_seconds=30) -> Fetcher:
    """Bangun Fetcher yang memakai MockTransport dengan handler yang diberikan."""
    transport = httpx.MockTransport(handler)
    return Fetcher(make_semaphore(1), timeout_seconds=timeout_seconds, transport=transport)


def _image_fetcher_with_handler(
    handler, image_max_bytes=None, image_max_retries=3
) -> Fetcher:
    """Bangun Fetcher untuk pengujian unduhan gambar dengan handler tiruan.

    ``image_max_bytes`` opsional dapat diperkecil agar uji batas ukuran cepat;
    bila ``None`` dipakai nilai bawaan konstruktor (``IMAGE_MAX_BYTES``).
    """
    transport = httpx.MockTransport(handler)
    kwargs = {"transport": transport, "image_max_retries": image_max_retries}
    if image_max_bytes is not None:
        kwargs["image_max_bytes"] = image_max_bytes
    return Fetcher(make_semaphore(1), **kwargs)


# --------------------------------------------------------------------------- #
# Property 8: Pemetaan status HTTP ke keberhasilan fetch
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 8: Pemetaan status HTTP ke keberhasilan fetch
@settings(max_examples=200)
@given(status_code=st.integers(min_value=100, max_value=599))
def test_property_http_status_maps_to_fetch_success(status_code):
    """Validates: Requirements 3.4

    Untuk kode status HTTP apa pun, ``fetch_page`` menandai hasil berhasil
    (``ok=True``) jika dan hanya jika kode berada di rentang keberhasilan 2xx;
    untuk kode lain hasil ditandai gagal dan kode status dicatat.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="<html>body</html>")

    async def scenario():
        fetcher = _fetcher_with_handler(handler)
        return await fetcher.fetch_page(URL)

    result = asyncio.run(scenario())

    expected_ok = 200 <= status_code < 300
    assert result.ok is expected_ok
    # Kode status selalu dicatat ketika respons diterima (Req 3.4).
    assert result.status_code == status_code

    if expected_ok:
        assert result.html == "<html>body</html>"
        assert result.failure_reason is None
    else:
        # Gagal: body tidak dipakai, alasan kegagalan mencatat kode status.
        assert result.html is None
        assert result.failure_reason is not None
        assert str(status_code) in result.failure_reason


def test_is_success_status_boundaries():
    """Batas rentang 2xx: 200 & 299 sukses; 199 & 300 tidak (Req 3.4)."""
    assert is_success_status(200) is True
    assert is_success_status(299) is True
    assert is_success_status(199) is False
    assert is_success_status(300) is False


# --------------------------------------------------------------------------- #
# Unit test: contoh status sukses & gagal (Req 3.4)
# --------------------------------------------------------------------------- #
def test_fetch_page_success_200_returns_html():
    """Status 200 -> ok=True dengan body HTML (Req 3.4)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>halo</html>")

    async def scenario():
        return await _fetcher_with_handler(handler).fetch_page(URL)

    result = asyncio.run(scenario())
    assert result.ok is True
    assert result.status_code == 200
    assert result.html == "<html>halo</html>"
    assert result.failure_reason is None


def test_fetch_page_404_marked_failed_and_records_status():
    """Status 404 -> ok=False dan kode status dicatat (Req 3.4)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async def scenario():
        return await _fetcher_with_handler(handler).fetch_page(URL)

    result = asyncio.run(scenario())
    assert result.ok is False
    assert result.status_code == 404
    assert result.html is None
    assert "404" in result.failure_reason


def test_fetch_page_500_marked_failed_and_records_status():
    """Status 500 -> ok=False dan kode status dicatat (Req 3.4)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async def scenario():
        return await _fetcher_with_handler(handler).fetch_page(URL)

    result = asyncio.run(scenario())
    assert result.ok is False
    assert result.status_code == 500
    assert result.html is None


# --------------------------------------------------------------------------- #
# Unit test: timeout & kegagalan koneksi (Req 3.3)
# --------------------------------------------------------------------------- #
def test_fetch_page_timeout_marked_failed_without_raising():
    """Timeout -> ok=False, tanpa mengangkat pengecualian (Req 3.3)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("waktu habis", request=request)

    async def scenario():
        return await _fetcher_with_handler(handler).fetch_page(URL)

    result = asyncio.run(scenario())
    assert result.ok is False
    assert result.status_code is None
    assert result.html is None
    assert result.failure_reason is not None
    assert "timeout" in result.failure_reason.lower()


def test_fetch_page_connection_error_marked_failed_without_raising():
    """Error koneksi -> ok=False, tanpa mengangkat pengecualian (Req 3.3)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("koneksi ditolak", request=request)

    async def scenario():
        return await _fetcher_with_handler(handler).fetch_page(URL)

    result = asyncio.run(scenario())
    assert result.ok is False
    assert result.status_code is None
    assert result.html is None
    assert result.failure_reason is not None


# --------------------------------------------------------------------------- #
# Validasi konfigurasi konkurensi (Req 3.1) & timeout (Req 3.5)
# --------------------------------------------------------------------------- #
def test_make_semaphore_default_and_valid_range():
    """make_semaphore menerima nilai bawaan dan batas rentang 1..100 (Req 3.1)."""
    assert _in_loop(lambda: make_semaphore()._value) == 10
    assert _in_loop(lambda: make_semaphore(1)._value) == 1
    assert _in_loop(lambda: make_semaphore(100)._value) == 100


@pytest.mark.parametrize("bad", [0, -1, 101, 1000, True, 10.0, "10"])
def test_make_semaphore_rejects_out_of_range_or_non_integer(bad):
    """make_semaphore menolak nilai di luar 1..100 atau bukan integer (Req 3.1)."""
    with pytest.raises(ConcurrencyLimitError):
        make_semaphore(bad)


def test_fetcher_default_timeout_is_30_seconds():
    """Timeout bawaan Fetcher adalah 30 detik (Req 3.5)."""
    assert _in_loop(lambda: Fetcher(make_semaphore(1)).timeout_seconds) == 30


@pytest.mark.parametrize("good", [1, 30, 300])
def test_fetcher_accepts_timeout_in_range(good):
    """Fetcher menerima timeout dalam rentang 1..300 detik (Req 3.5)."""
    assert (
        _in_loop(
            lambda: Fetcher(make_semaphore(1), timeout_seconds=good).timeout_seconds
        )
        == good
    )


@pytest.mark.parametrize("bad", [0, -5, 301, 1000, True, 30.0, "30"])
def test_fetcher_rejects_timeout_out_of_range_or_non_integer(bad):
    """Fetcher menolak timeout di luar 1..300 atau bukan integer (Req 3.5)."""
    with pytest.raises(TimeoutConfigError):
        _in_loop(lambda: Fetcher(make_semaphore(1), timeout_seconds=bad))


# --------------------------------------------------------------------------- #
# Unit test: unduhan gambar fetch_image (Req 7.1, 7.5)
# --------------------------------------------------------------------------- #
def test_fetch_image_success_returns_content_bytes():
    """Status 200 dalam batas ukuran -> ok=True dengan isi biner (Req 7.1)."""
    payload = b"\x89PNG\r\n\x1a\n" + b"data-gambar"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def scenario():
        return await _image_fetcher_with_handler(handler).fetch_image(IMAGE_URL)

    result = asyncio.run(scenario())
    assert isinstance(result, ImageFetchResult)
    assert result.ok is True
    assert result.content == payload
    assert result.failure_reason is None


def test_fetch_image_retries_then_succeeds_on_third_attempt():
    """Gagal dua kali lalu berhasil pada percobaan ke-3 -> ok=True (Req 7.1)."""
    payload = b"isi-gambar-oke"
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("koneksi gagal", request=request)
        return httpx.Response(200, content=payload)

    async def scenario():
        return await _image_fetcher_with_handler(handler).fetch_image(IMAGE_URL)

    result = asyncio.run(scenario())
    assert calls["n"] == 3
    assert result.ok is True
    assert result.content == payload
    assert result.failure_reason is None


def test_fetch_image_exhausts_retries_and_marks_failed_without_raising():
    """Selalu gagal -> ok=False setelah 3 percobaan, tanpa mengangkat (Req 7.5)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("koneksi selalu gagal", request=request)

    async def scenario():
        return await _image_fetcher_with_handler(handler).fetch_image(IMAGE_URL)

    result = asyncio.run(scenario())
    assert calls["n"] == 3
    assert result.ok is False
    assert result.content is None
    assert result.failure_reason is not None


def test_fetch_image_non_2xx_status_retries_then_fails():
    """Status non-2xx dianggap percobaan gagal & diulang -> ok=False (Req 7.5)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    async def scenario():
        return await _image_fetcher_with_handler(handler).fetch_image(IMAGE_URL)

    result = asyncio.run(scenario())
    assert calls["n"] == 3
    assert result.ok is False
    assert result.content is None
    assert "500" in result.failure_reason


def test_fetch_image_exceeds_size_limit_by_actual_bytes():
    """Isi melebihi batas ukuran -> ok=False (terlalu besar), isi tak disimpan."""
    # Batas kecil (8 byte) agar uji cepat; isi 20 byte melebihi batas.
    oversized = b"x" * 20
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=oversized)

    async def scenario():
        fetcher = _image_fetcher_with_handler(handler, image_max_bytes=8)
        return await fetcher.fetch_image(IMAGE_URL)

    result = asyncio.run(scenario())
    assert result.ok is False
    assert result.content is None
    assert "besar" in result.failure_reason.lower()
    # Kelebihan ukuran bersifat definitif -> tidak diulang (satu percobaan).
    assert calls["n"] == 1


def test_fetch_image_exceeds_size_limit_by_content_length_header():
    """Content-Length melebihi batas -> ok=False tanpa mengunduh isi besar."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Header Content-Length menyatakan ukuran melebihi batas.
        return httpx.Response(
            200, content=b"kecil", headers={"content-length": "1048576"}
        )

    async def scenario():
        fetcher = _image_fetcher_with_handler(handler, image_max_bytes=8)
        return await fetcher.fetch_image(IMAGE_URL)

    result = asyncio.run(scenario())
    assert result.ok is False
    assert result.content is None
    assert "besar" in result.failure_reason.lower()


def test_fetch_image_timeout_marked_failed_without_raising():
    """Timeout tiap percobaan -> ok=False setelah 3 percobaan (Req 7.5)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("waktu habis", request=request)

    async def scenario():
        return await _image_fetcher_with_handler(handler).fetch_image(IMAGE_URL)

    result = asyncio.run(scenario())
    assert calls["n"] == 3
    assert result.ok is False
    assert result.content is None
    assert "timeout" in result.failure_reason.lower()
