"""Hasher domain — fungsi murni untuk menghitung hash konten (task 5).

Menghitung nilai hash deterministik memakai ``hashlib.sha256`` (representasi
hex) sesuai Requirement 5 dan 7:

- ``content_hash(normalized_text)`` menghitung Content_Hash dari teks
  ternormalisasi sebuah halaman: teks identik -> hash identik, teks berbeda
  -> hash berbeda (Req 5.1).
- ``image_hash(content)`` menghitung Image_Hash dari isi biner sebuah gambar:
  isi identik -> hash identik, isi berbeda -> hash berbeda (Req 7.2).

Modul ini murni (tanpa I/O) sehingga menjadi target property-based testing.
Kompatibel Python 3.9 melalui ``from __future__ import annotations``.
"""

from __future__ import annotations

import hashlib


def content_hash(normalized_text: str) -> str:
    """Hitung Content_Hash dari teks ternormalisasi (Req 5.1).

    Teks di-encode sebagai UTF-8 lalu di-hash dengan SHA-256. Karena SHA-256
    bersifat deterministik, teks yang identik selalu menghasilkan hash yang
    identik; sebaliknya, teks yang berbeda menghasilkan hash yang berbeda
    (collision SHA-256 praktis mustahil).

    Args:
        normalized_text: Teks ternormalisasi sebuah halaman.

    Returns:
        Digest SHA-256 dalam bentuk string heksadesimal (64 karakter).
    """
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


def image_hash(content: bytes) -> str:
    """Hitung Image_Hash dari isi biner sebuah gambar (Req 7.2).

    Isi biner di-hash langsung dengan SHA-256. Isi yang identik selalu
    menghasilkan hash yang identik; isi yang berbeda menghasilkan hash yang
    berbeda (collision SHA-256 praktis mustahil).

    Args:
        content: Isi biner file gambar.

    Returns:
        Digest SHA-256 dalam bentuk string heksadesimal (64 karakter).
    """
    return hashlib.sha256(content).hexdigest()
