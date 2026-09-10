"""Unit test untuk check_ssl_expiry (ContentMonitor Tahap 3, bagian D).

TIDAK MENYENTUH JARINGAN NYATA — seluruh test memakai monkeypatch untuk
mengganti ``asyncio.open_connection`` dengan objek tiruan yang mengembalikan
``StreamReader``/``StreamWriter`` palsu, dan objek ``ssl_object`` palsu dengan
``getpeercert()`` terkanal.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from monitoring.infra.ssl_check import check_ssl_expiry


class _FakeSSLObject:
    """Objek SSL tiruan dengan ``getpeercert()`` terkanal."""

    def __init__(self, cert: dict) -> None:
        self._cert = cert

    def getpeercert(self) -> dict:
        return self._cert


class _FakeWriter:
    """``StreamWriter`` tiruan yang menyimpan ``ssl_object`` untuk
    ``get_extra_info`` dan mencatat pemanggilan ``close()``.
    """

    def __init__(self, ssl_object) -> None:
        self._ssl_object = ssl_object
        self.closed = False

    def get_extra_info(self, name: str):
        if name == "ssl_object":
            return self._ssl_object
        return None

    def close(self) -> None:
        self.closed = True


async def _fake_open_connection_factory(cert: dict):
    """Bangun fungsi tiruan ``open_connection`` yang mengembalikan writer
    dengan ``ssl_object`` ber-``getpeercert()`` mengembalikan ``cert``.
    """

    async def _fake_open_connection(*args, **kwargs):
        ssl_object = _FakeSSLObject(cert)
        writer = _FakeWriter(ssl_object)
        reader = object()
        return reader, writer

    return _fake_open_connection


def _cert_with_not_after(not_after: str) -> dict:
    return {"notAfter": not_after}


# --------------------------------------------------------------------------- #
# Kasus berhasil
# --------------------------------------------------------------------------- #
async def test_check_ssl_expiry_parses_not_after_field(monkeypatch):
    cert = _cert_with_not_after("Jan  1 00:00:00 2030 GMT")
    fake_open = await _fake_open_connection_factory(cert)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    result = await check_ssl_expiry("example.com")
    assert result == datetime(2030, 1, 1, 0, 0, 0)


async def test_check_ssl_expiry_closes_writer_on_success(monkeypatch):
    cert = _cert_with_not_after("Jan  1 00:00:00 2030 GMT")
    written_writers = []

    async def fake_open_connection(*args, **kwargs):
        ssl_object = _FakeSSLObject(cert)
        writer = _FakeWriter(ssl_object)
        written_writers.append(writer)
        return object(), writer

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)

    await check_ssl_expiry("example.com")
    assert len(written_writers) == 1
    assert written_writers[0].closed is True


# --------------------------------------------------------------------------- #
# Kasus gagal -> None, tanpa exception
# --------------------------------------------------------------------------- #
async def test_check_ssl_expiry_connection_error_returns_none(monkeypatch):
    async def fake_open_connection(*args, **kwargs):
        raise OSError("koneksi ditolak (disimulasikan)")

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)

    result = await check_ssl_expiry("tidak-ada-domain.invalid")
    assert result is None


async def test_check_ssl_expiry_timeout_returns_none(monkeypatch):
    async def fake_open_connection(*args, **kwargs):
        await asyncio.sleep(10)  # akan timeout lebih dulu

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)

    result = await check_ssl_expiry("example.com", timeout=0.01)
    assert result is None


async def test_check_ssl_expiry_no_ssl_object_returns_none(monkeypatch):
    async def fake_open_connection(*args, **kwargs):
        writer = _FakeWriter(None)  # ssl_object None -> tidak bisa baca sertifikat
        return object(), writer

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)

    result = await check_ssl_expiry("example.com")
    assert result is None


async def test_check_ssl_expiry_empty_cert_returns_none(monkeypatch):
    async def fake_open_connection(*args, **kwargs):
        ssl_object = _FakeSSLObject({})  # tanpa notAfter
        writer = _FakeWriter(ssl_object)
        return object(), writer

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)

    result = await check_ssl_expiry("example.com")
    assert result is None


async def test_check_ssl_expiry_malformed_date_returns_none(monkeypatch):
    cert = _cert_with_not_after("tanggal-tidak-valid")
    fake_open = await _fake_open_connection_factory(cert)
    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    result = await check_ssl_expiry("example.com")
    assert result is None


async def test_check_ssl_expiry_empty_domain_returns_none():
    assert await check_ssl_expiry("") is None
