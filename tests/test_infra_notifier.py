"""Tests untuk Notifier — ringkasan & format pesan (task 11.1).

Berisi:

- Property-based test (Hypothesis, min 100 iterasi) untuk Correctness
  Property 23: ``build_summary`` menghasilkan jumlah = panjang daftar terkait,
  dan pesan notifikasi memuat URL halaman, nama website, waktu deteksi, serta
  seluruh angka ringkasan.
- Unit test contoh untuk ``build_summary`` dan ``format_notification_message``.
"""

from __future__ import annotations

from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from monitoring.domain.models import ChangeEvent, ChangeSummary, Diff, WebsiteConfig
from monitoring.infra.notifier import build_summary, format_notification_message

# --------------------------------------------------------------------------- #
# Strategi generator
# --------------------------------------------------------------------------- #
_STR_LIST = st.lists(st.text(max_size=12), max_size=8)


@st.composite
def _diffs(draw):
    """Generator ``Diff`` acak dengan seluruh daftar bervariasi."""
    return Diff(
        text_added=draw(_STR_LIST),
        text_removed=draw(_STR_LIST),
        links_added=draw(_STR_LIST),
        links_removed=draw(_STR_LIST),
        sections_added=draw(_STR_LIST),
        sections_removed=draw(_STR_LIST),
        images_added=draw(_STR_LIST),
        images_removed=draw(_STR_LIST),
        images_changed=draw(_STR_LIST),
    )


@st.composite
def _websites(draw):
    return WebsiteConfig(
        id=draw(st.text(min_size=1, max_size=8)),
        domain=draw(st.text(min_size=1, max_size=12)),
        name=draw(st.text(min_size=1, max_size=20)),
        poll_interval_seconds=draw(st.none() | st.integers(10, 86400)),
        created_at=datetime(2024, 1, 1, 0, 0, 0),
    )


@st.composite
def _events(draw, diff):
    """Bangun ``ChangeEvent`` dari ``diff`` dengan ringkasan konsisten."""
    return ChangeEvent(
        id=draw(st.text(min_size=1, max_size=8)),
        website_id=draw(st.text(min_size=1, max_size=8)),
        url=draw(st.text(min_size=1, max_size=30)),
        detected_at=draw(st.datetimes()),
        diff=diff,
        summary=build_summary(diff),
    )


# --------------------------------------------------------------------------- #
# Property 23: Ringkasan perubahan pada notifikasi
# --------------------------------------------------------------------------- #
# Feature: website-monitoring, Property 23: Ringkasan perubahan pada notifikasi
@settings(max_examples=200)
@given(_diffs(), _websites(), st.data())
def test_summary_counts_and_message_contents(diff, website, data):
    """Validates: Requirements 9.2

    Untuk sembarang ``Diff``: ``build_summary`` menghasilkan angka yang sama
    dengan panjang daftar terkait, dan pesan notifikasi memuat URL halaman,
    nama website, waktu deteksi, serta seluruh angka ringkasan.
    """
    summary = build_summary(diff)

    # Angka ringkasan = panjang daftar terkait.
    assert summary.text_added == len(diff.text_added)
    assert summary.text_removed == len(diff.text_removed)
    assert summary.links_added == len(diff.links_added)
    assert summary.links_removed == len(diff.links_removed)
    assert summary.images_changed == len(diff.images_changed)

    event = data.draw(_events(diff))
    message = format_notification_message(event, website)

    # Pesan memuat URL halaman, nama website, dan waktu deteksi.
    assert event.url in message
    assert website.name in message
    assert event.detected_at.isoformat() in message

    # Pesan memuat seluruh angka ringkasan.
    assert str(summary.text_added) in message
    assert str(summary.text_removed) in message
    assert str(summary.links_added) in message
    assert str(summary.links_removed) in message
    assert str(summary.images_changed) in message


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #
def test_build_summary_counts_list_lengths():
    diff = Diff(
        text_added=["a", "b"],
        text_removed=["c"],
        links_added=["l1", "l2", "l3"],
        links_removed=[],
        images_changed=["img1"],
    )
    summary = build_summary(diff)
    assert summary == ChangeSummary(
        text_added=2,
        text_removed=1,
        links_added=3,
        links_removed=0,
        images_changed=1,
    )


def test_build_summary_empty_diff_is_all_zero():
    summary = build_summary(Diff())
    assert summary == ChangeSummary(
        text_added=0,
        text_removed=0,
        links_added=0,
        links_removed=0,
        images_changed=0,
    )


def test_format_message_contains_all_fields():
    diff = Diff(
        text_added=["x", "y"],
        text_removed=["z"],
        links_added=["http://a"],
        links_removed=[],
        images_changed=["http://img"],
    )
    detected_at = datetime(2024, 3, 15, 9, 30, 0)
    event = ChangeEvent(
        id="evt-1",
        website_id="web-1",
        url="https://example.com/page",
        detected_at=detected_at,
        diff=diff,
        summary=build_summary(diff),
    )
    website = WebsiteConfig(
        id="web-1",
        domain="example.com",
        name="Example Site",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1),
    )
    message = format_notification_message(event, website)

    assert "https://example.com/page" in message
    assert "Example Site" in message
    assert detected_at.isoformat() in message
    # Ringkasan angka: 2 teks ditambah, 1 dihapus, 1 link ditambah, 0 dihapus, 1 gambar.
    assert "Teks ditambah: 2" in message
    assert "Teks dihapus: 1" in message
    assert "Link ditambah: 1" in message
    assert "Link dihapus: 0" in message
    assert "Gambar berubah: 1" in message


def test_format_message_recomputes_summary_when_none():
    diff = Diff(text_added=["a"], links_removed=["l1", "l2"])
    event = ChangeEvent(
        id="evt-2",
        website_id="web-2",
        url="https://site.test/x",
        detected_at=datetime(2024, 5, 1, 12, 0, 0),
        diff=diff,
        summary=None,  # dipaksa None -> dihitung ulang dari diff
    )
    website = WebsiteConfig(
        id="web-2",
        domain="site.test",
        name="Site Test",
        poll_interval_seconds=60,
        created_at=datetime(2024, 1, 1),
    )
    message = format_notification_message(event, website)
    assert "Teks ditambah: 1" in message
    assert "Link dihapus: 2" in message


# --------------------------------------------------------------------------- #
# Notifier — pengiriman + retry (task 11.2, Req 9.1, 9.3, 9.4)
# --------------------------------------------------------------------------- #
import json
import time

import httpx
import pytest

from monitoring.config import NOTIFY_MAX_ATTEMPTS, NOTIFY_RETRY_DELAY_SECONDS
from monitoring.infra.notifier import Notifier


def _make_event() -> ChangeEvent:
    diff = Diff(
        text_added=["baris baru"],
        text_removed=[],
        links_added=["https://a"],
        links_removed=[],
        images_changed=["https://img"],
    )
    return ChangeEvent(
        id="evt-9",
        website_id="web-9",
        url="https://example.com/berita",
        detected_at=datetime(2024, 6, 1, 8, 0, 0),
        diff=diff,
        summary=build_summary(diff),
    )


def _make_website() -> WebsiteConfig:
    return WebsiteConfig(
        id="web-9",
        domain="example.com",
        name="Example News",
        poll_interval_seconds=None,
        created_at=datetime(2024, 1, 1),
    )


class _RecordingSleep:
    """Fungsi sleep tiruan yang merekam nilai jeda yang diminta (tanpa menunggu)."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _make_transport(status_sequence):
    """Bangun ``httpx.MockTransport`` yang membalas status sesuai urutan.

    Setiap elemen ``status_sequence`` boleh berupa kode status int (balas
    dengan status itu) atau instance ``Exception`` (diangkat untuk mensimulasi
    error jaringan). Juga merekam request yang diterima.
    """
    calls = []
    seq = list(status_sequence)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        outcome = seq[len(calls) - 1] if len(calls) <= len(seq) else seq[-1]
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, json={"ok": True})

    return httpx.MockTransport(handler), calls


@pytest.mark.asyncio
async def test_notify_success_first_attempt_posts_message():
    """Validates: Requirements 9.1, 9.2

    Sukses pada percobaan pertama -> ``True``, dan payload memuat chat_id serta
    teks pesan terformat yang dikirim ke endpoint sendMessage.
    """
    transport, calls = _make_transport([200])
    sleep = _RecordingSleep()
    notifier = Notifier(
        bot_token="TOKEN123",
        chat_id="CHAT456",
        transport=transport,
        delay_seconds=0,
        sleep=sleep,
    )
    event = _make_event()
    website = _make_website()

    result = await notifier.notify(event, website)

    assert result is True
    # Hanya satu percobaan (tidak ada retry).
    assert len(calls) == 1
    request = calls[0]
    # Endpoint sendMessage dengan token benar.
    assert request.url.path == "/botTOKEN123/sendMessage"
    body = json.loads(request.content)
    assert body["chat_id"] == "CHAT456"
    expected_text = format_notification_message(event, website)
    assert body["text"] == expected_text
    # Tidak ada jeda karena berhasil di percobaan pertama.
    assert sleep.calls == []


@pytest.mark.asyncio
async def test_notify_retries_then_succeeds():
    """Validates: Requirements 9.3

    Gagal dua kali lalu berhasil -> ``True`` dengan tepat 3 percobaan, dan jeda
    diterapkan di antara percobaan (2 kali).
    """
    transport, calls = _make_transport([500, 503, 200])
    sleep = _RecordingSleep()
    notifier = Notifier(
        bot_token="T",
        chat_id="C",
        transport=transport,
        delay_seconds=0,
        sleep=sleep,
    )

    result = await notifier.notify(_make_event(), _make_website())

    assert result is True
    assert len(calls) == 3
    # Jeda diterapkan antar percobaan yang gagal: 2 jeda untuk 3 percobaan.
    assert len(sleep.calls) == 2


@pytest.mark.asyncio
async def test_notify_all_attempts_fail_returns_false_without_raising():
    """Validates: Requirements 9.4

    Seluruh percobaan gagal -> ``False`` tanpa mengangkat pengecualian; jumlah
    percobaan sama dengan ``max_attempts`` (3).
    """
    transport, calls = _make_transport([500, 500, 500])
    sleep = _RecordingSleep()
    notifier = Notifier(
        bot_token="T",
        chat_id="C",
        transport=transport,
        delay_seconds=0,
        sleep=sleep,
    )

    result = await notifier.notify(_make_event(), _make_website())

    assert result is False
    assert len(calls) == NOTIFY_MAX_ATTEMPTS == 3
    # Jeda hanya di antara percobaan, tidak setelah percobaan terakhir.
    assert len(sleep.calls) == 2


@pytest.mark.asyncio
async def test_notify_network_error_is_retryable():
    """Validates: Requirements 9.3, 9.4

    Error jaringan (bukan hanya status non-2xx) diperlakukan sebagai percobaan
    gagal yang dapat diulang.
    """
    transport, calls = _make_transport(
        [httpx.ConnectError("boom"), httpx.ConnectError("boom"), 200]
    )
    sleep = _RecordingSleep()
    notifier = Notifier(
        bot_token="T",
        chat_id="C",
        transport=transport,
        delay_seconds=0,
        sleep=sleep,
    )

    result = await notifier.notify(_make_event(), _make_website())

    assert result is True
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_notify_default_delay_is_five_seconds():
    """Validates: Requirements 9.3

    Jeda bawaan adalah 5 detik: dengan sleep yang direkam, saat terjadi retry
    fungsi jeda dipanggil dengan nilai 5.
    """
    transport, _calls = _make_transport([500, 200])
    sleep = _RecordingSleep()
    # delay_seconds tidak di-set -> pakai bawaan (NOTIFY_RETRY_DELAY_SECONDS = 5).
    notifier = Notifier(
        bot_token="T",
        chat_id="C",
        transport=transport,
        sleep=sleep,
    )
    assert notifier.delay_seconds == NOTIFY_RETRY_DELAY_SECONDS == 5

    result = await notifier.notify(_make_event(), _make_website())

    assert result is True
    # Satu retry terjadi -> satu jeda, bernilai 5 detik.
    assert sleep.calls == [5]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_notify_completes_well_within_60s_with_mock_api():
    """Validates: Requirements 9.1

    Pengiriman via mock API selesai jauh di bawah 60 detik.
    """
    transport, _calls = _make_transport([200])
    notifier = Notifier(
        bot_token="T",
        chat_id="C",
        transport=transport,
        delay_seconds=0,
    )

    start = time.monotonic()
    result = await notifier.notify(_make_event(), _make_website())
    elapsed = time.monotonic() - start

    assert result is True
    assert elapsed < 60
