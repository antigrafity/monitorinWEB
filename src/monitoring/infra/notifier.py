"""Notifier Telegram — fungsi murni ringkasan & format pesan (task 11.1).

Modul ini mengimplementasikan bagian *pure function* dari komponen Notifier
sesuai bagian *Components and Interfaces* pada design:

- ``build_summary(diff)`` — menghitung jumlah perubahan (teks ditambah/dihapus,
  link ditambah/dihapus, gambar berubah) dari sebuah ``Diff`` (Req 9.2).
- ``format_notification_message(event, website)`` — membangun teks pesan
  notifikasi yang memuat URL halaman, nama Monitored_Website, waktu deteksi,
  dan seluruh angka ringkasan (Req 9.2).

Pengiriman ke Telegram Bot API beserta retry/jeda (task 11.2) diimplementasikan
oleh kelas :class:`Notifier`. Fungsi ``build_summary`` dan
``format_notification_message`` tetap bersifat murni (tanpa I/O) sehingga mudah
diuji, sementara ``Notifier.notify`` menangani sisi I/O (HTTP ke Telegram Bot
API) beserta pengulangan dan jeda.

Perilaku pengiriman (Req 9.1, 9.3, 9.4):

- Kirim pesan melalui Telegram Bot API (``POST .../sendMessage``) memakai
  ``httpx`` dalam waktu ≤ 60 detik sejak Change_Event (Req 9.1).
- Bila pengiriman gagal, ulangi hingga maksimum 3 percobaan (total tries)
  dengan jeda minimal 5 detik antar percobaan (Req 9.3).
- Bila seluruh percobaan gagal, catat indikasi "tidak terkirim" dan kembalikan
  ``False`` TANPA mengangkat pengecualian sehingga Change_Event tetap
  dipertahankan dan siklus pemeriksaan tidak terhenti (Req 9.4).

Catatan kompatibilitas: target runtime Python 3.9. Modul memakai
``from __future__ import annotations`` dan ``typing.Optional`` untuk anotasi
yang aman lintas versi.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

import httpx

from ..config import NOTIFY_MAX_ATTEMPTS, NOTIFY_RETRY_DELAY_SECONDS
from ..domain.models import Alert, ChangeEvent, ChangeSummary, Diff, WebsiteConfig

logger = logging.getLogger(__name__)


def build_summary(diff: Diff) -> ChangeSummary:
    """Hitung ringkasan jumlah perubahan dari ``Diff`` (Req 9.2).

    Fungsi murni: setiap angka ringkasan sama dengan panjang daftar terkait
    pada ``Diff``:

    - ``text_added``    = ``len(diff.text_added)``
    - ``text_removed``  = ``len(diff.text_removed)``
    - ``links_added``   = ``len(diff.links_added)``
    - ``links_removed`` = ``len(diff.links_removed)``
    - ``images_changed``= ``len(diff.images_changed)``
    """
    return ChangeSummary(
        text_added=len(diff.text_added),
        text_removed=len(diff.text_removed),
        links_added=len(diff.links_added),
        links_removed=len(diff.links_removed),
        images_changed=len(diff.images_changed),
    )


def format_notification_message(event: ChangeEvent, website: WebsiteConfig) -> str:
    """Bangun teks pesan notifikasi untuk sebuah ``ChangeEvent`` (Req 9.2).

    Pesan memuat: nama Monitored_Website, URL halaman, waktu deteksi, dan
    ringkasan perubahan (jumlah teks ditambah/dihapus, link ditambah/dihapus,
    serta gambar berubah). Ringkasan diambil dari ``event.summary`` bila
    tersedia; bila ``None`` dihitung ulang dari ``event.diff`` via
    ``build_summary`` sehingga pesan selalu lengkap.

    Fungsi ini murni dan mengembalikan ``str`` biasa (pengiriman Telegram
    ditangani terpisah pada task 11.2).
    """
    summary = event.summary if event.summary is not None else build_summary(event.diff)

    lines = [
        "🔔 Perubahan terdeteksi",
        f"Website: {website.name}",
        f"URL: {event.url}",
        f"Waktu deteksi: {event.detected_at.isoformat()}",
        "Ringkasan perubahan:",
        f"- Teks ditambah: {summary.text_added}",
        f"- Teks dihapus: {summary.text_removed}",
        f"- Link ditambah: {summary.links_added}",
        f"- Link dihapus: {summary.links_removed}",
        f"- Gambar berubah: {summary.images_changed}",
    ]
    return "\n".join(lines)


def format_critical_alerts_message(
    alerts: List[Alert], website: WebsiteConfig
) -> str:
    """Bangun pesan ringkas satu-baris-per-alert untuk Alert 'critical'
    (ContentMonitor Tahap 3, bagian E).

    Merangkum SELURUH Alert 'critical' dari SATU siklus pemeriksaan SATU
    website ke dalam SATU pesan (bukan satu pesan per Alert) agar notifikasi
    Telegram tidak menjadi spam. Mengembalikan string kosong bila ``alerts``
    kosong (pemanggil sebaiknya tidak mengirim pesan kosong).
    """
    if not alerts:
        return ""
    lines = [
        "🚨 {0} alert kritis pada {1}".format(len(alerts), website.name),
    ]
    for alert in alerts:
        location = f" ({alert.url})" if alert.url else ""
        lines.append(f"- {alert.title}{location}")
    return "\n".join(lines)


TELEGRAM_API_BASE = "https://api.telegram.org"


class Notifier:
    """Pengirim notifikasi perubahan melalui Telegram Bot API (Req 9.1, 9.3, 9.4).

    Notifier memformat pesan via :func:`format_notification_message` lalu
    mengirimnya sebagai ``POST {base}/bot{token}/sendMessage`` dengan body JSON
    ``{"chat_id": ..., "text": ...}`` memakai ``httpx``. Respons dengan kode
    status 2xx dianggap berhasil; status non-2xx maupun error/timeout dianggap
    percobaan gagal yang dapat diulang.

    Pengiriman diulang hingga ``max_attempts`` percobaan (total tries, bawaan
    3) dengan jeda ``delay_seconds`` (bawaan 5 detik) antar percobaan (Req 9.3).
    Jeda TIDAK diterapkan setelah percobaan terakhir. Bila seluruh percobaan
    gagal, ``notify`` mencatat indikasi "tidak terkirim" dan mengembalikan
    ``False`` TANPA mengangkat pengecualian, sehingga Change_Event tetap
    dipertahankan dan siklus tidak terhenti (Req 9.4).

    Untuk keperluan pengujian:

    - sebuah ``client`` (``httpx.AsyncClient``) atau ``transport``
      (``httpx.BaseTransport``, mis. ``httpx.MockTransport``) dapat di-inject
      sehingga respons Telegram dapat disimulasikan tanpa jaringan nyata. Bila
      ``client`` di-inject, Notifier tidak menutupnya (kepemilikan pemanggil);
      bila tidak, Notifier membuat ``AsyncClient`` sekali pakai per
      ``notify``.
    - ``delay_seconds`` dapat diatur ke 0 agar uji berjalan cepat tanpa
      menunggu jeda nyata.
    - sebuah fungsi ``sleep`` (bawaan :func:`asyncio.sleep`) dapat di-inject
      untuk merekam pemanggilan jeda pada pengujian.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
        delay_seconds: float = NOTIFY_RETRY_DELAY_SECONDS,
        max_attempts: int = NOTIFY_MAX_ATTEMPTS,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        api_base: str = TELEGRAM_API_BASE,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = client
        self._transport = transport
        self._delay_seconds = delay_seconds
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._api_base = api_base.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @property
    def delay_seconds(self) -> float:
        """Jeda antar percobaan pengiriman dalam detik (Req 9.3)."""
        return self._delay_seconds

    @property
    def max_attempts(self) -> int:
        """Jumlah percobaan maksimum pengiriman (total tries) (Req 9.3)."""
        return self._max_attempts

    @property
    def send_message_url(self) -> str:
        """Endpoint ``sendMessage`` Telegram Bot API untuk token terkonfigurasi."""
        return f"{self._api_base}/bot{self._bot_token}/sendMessage"

    async def notify(self, event: ChangeEvent, website: WebsiteConfig) -> bool:
        """Kirim notifikasi Telegram untuk sebuah Change_Event (Req 9.1, 9.3, 9.4).

        Memformat pesan (memuat URL halaman, nama Monitored_Website, waktu
        deteksi, dan ringkasan jumlah — Req 9.2) lalu mengirimnya via Telegram
        Bot API. Mengembalikan ``True`` bila salah satu percobaan berhasil
        (status 2xx), atau ``False`` bila seluruh ``max_attempts`` percobaan
        gagal. Tidak pernah mengangkat pengecualian sehingga Change_Event tetap
        dipertahankan dan siklus tidak terhenti (Req 9.4).
        """
        text = format_notification_message(event, website)
        payload = {"chat_id": self._chat_id, "text": text}

        if self._client is not None:
            return await self._send_with_retries(self._client, payload, event.id, event.url)

        client = httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(self._timeout_seconds),
        )
        try:
            return await self._send_with_retries(
                client, payload, event.id, event.url
            )
        finally:
            await client.aclose()

    async def notify_text(self, text: str) -> bool:
        """Kirim teks pesan bebas ke Telegram (ContentMonitor Tahap 3).

        Dipakai untuk ringkasan Alert 'critical' per siklus per website
        (:func:`format_critical_alerts_message`), berbeda dari :meth:`notify`
        yang khusus memformat sebuah ``ChangeEvent``. Mengikuti kebijakan
        retry/jeda yang sama (Req 9.3, 9.4): TIDAK pernah mengangkat exception,
        mengembalikan ``False`` bila seluruh percobaan gagal.
        """
        payload = {"chat_id": self._chat_id, "text": text}
        if self._client is not None:
            return await self._send_with_retries(self._client, payload, "alert-summary", "-")
        client = httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(self._timeout_seconds),
        )
        try:
            return await self._send_with_retries(
                client, payload, "alert-summary", "-"
            )
        finally:
            await client.aclose()

    async def _send_with_retries(
        self,
        client: httpx.AsyncClient,
        payload: dict,
        log_id: str = "?",
        log_url: str = "?",
    ) -> bool:
        """Jalankan pengiriman dengan pengulangan & jeda (Req 9.3, 9.4).

        Melakukan hingga ``max_attempts`` percobaan. Antar percobaan yang gagal
        diberi jeda ``delay_seconds`` (jeda TIDAK diterapkan setelah percobaan
        terakhir). Mengembalikan ``True`` pada keberhasilan pertama; bila
        seluruh percobaan gagal mengembalikan ``False`` tanpa mengangkat
        pengecualian.
        """
        last_reason = "tidak ada percobaan yang dijalankan"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await client.post(
                    self.send_message_url,
                    json=payload,
                    timeout=httpx.Timeout(self._timeout_seconds),
                )
            except httpx.HTTPError as exc:
                last_reason = f"kegagalan permintaan: {exc!r}"
            else:
                status = response.status_code
                if 200 <= status < 300:
                    return True
                last_reason = (
                    f"kode status {status} di luar rentang keberhasilan 2xx"
                )

            # Jeda minimal antar percobaan, kecuali setelah percobaan terakhir
            # (Req 9.3).
            if attempt < self._max_attempts:
                await self._sleep(self._delay_seconds)

        # Seluruh percobaan gagal: catat "tidak terkirim" & pertahankan event
        # dengan mengembalikan False tanpa mengangkat pengecualian (Req 9.4).
        logger.warning(
            "Notifikasi Telegram tidak terkirim untuk %s (URL %s) "
            "setelah %d percobaan; alasan terakhir: %s",
            log_id,
            log_url,
            self._max_attempts,
            last_reason,
        )
        return False
