"""Cek masa berlaku sertifikat SSL (ContentMonitor Tahap 3).

Menyediakan :func:`check_ssl_expiry`, sebuah fungsi async yang menghubungi
``domain:443`` memakai ``ssl`` + ``asyncio.open_connection`` dari pustaka
standar (TANPA dependensi baru) untuk membaca tanggal kadaluarsa (``notAfter``)
sertifikat TLS server.

Perilaku (selaras pola ketahanan modul lain):

- Berhasil -> ``datetime`` (naive, lokal) tanggal kadaluarsa sertifikat.
- Gagal apa pun (DNS, koneksi ditolak, timeout, handshake TLS gagal, sertifikat
  tidak dapat diurai) -> ``None`` TANPA mengangkat pengecualian, sehingga
  pemanggil (``CheckOrchestrator``) dapat melanjutkan siklus pemeriksaan tanpa
  terganggu.

Dipanggil SEKALI per website per siklus pemeriksaan (bukan per halaman) oleh
``CheckOrchestrator``.

Kompatibel Python 3.9 melalui ``from __future__ import annotations`` dan
``typing.Optional``.
"""

from __future__ import annotations

import asyncio
import ssl
from datetime import datetime
from typing import Optional

# Format tanggal ``notAfter`` pada sertifikat X.509 seperti dikembalikan oleh
# ``ssl.SSLSocket.getpeercert()``, mis. "Jan  1 00:00:00 2030 GMT".
_CERT_DATE_FORMAT = "%b %d %H:%M:%S %Y %Z"

# Port HTTPS standar dipakai untuk pemeriksaan sertifikat.
_HTTPS_PORT = 443


async def check_ssl_expiry(
    domain: str, timeout: float = 10.0
) -> Optional[datetime]:
    """Periksa tanggal kadaluarsa sertifikat TLS sebuah domain.

    Membuka koneksi TLS ke ``domain:443`` (SNI diset ke ``domain``) memakai
    ``asyncio.open_connection`` dengan ``ssl.create_default_context()``, lalu
    membaca bidang ``notAfter`` dari sertifikat peer.

    Args:
        domain: Nama domain (tanpa skema/port), mis. ``"example.com"``.
        timeout: Batas waktu total operasi dalam detik (bawaan 10).

    Returns:
        ``datetime`` (naive) tanggal kadaluarsa sertifikat, atau ``None`` bila
        koneksi/handshake gagal, timeout, atau sertifikat tidak dapat diurai.
        Tidak pernah mengangkat pengecualian.
    """
    if not domain:
        return None

    writer: Optional[asyncio.StreamWriter] = None
    try:
        context = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=domain,
                port=_HTTPS_PORT,
                ssl=context,
                server_hostname=domain,
            ),
            timeout=timeout,
        )
        transport = writer.get_extra_info("ssl_object")
        if transport is None:
            return None
        cert = transport.getpeercert()
        if not cert or "notAfter" not in cert:
            return None
        not_after = cert["notAfter"]
        return datetime.strptime(not_after, _CERT_DATE_FORMAT)
    except Exception:
        # Kegagalan apa pun (DNS, koneksi, timeout, TLS, parsing) -> None,
        # TANPA mengangkat pengecualian (ketahanan siklus pemeriksaan).
        return None
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
