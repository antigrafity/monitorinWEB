"""Utilitas autentikasi untuk dashboard (fitur login multi-user).

Modul ini murni memakai pustaka standar Python (``hashlib``, ``hmac``,
``secrets``, ``base64``) sehingga TIDAK menambah dependency eksternal. Dua
tanggung jawab utama:

1. **Hashing password** — password pengguna TIDAK PERNAH disimpan mentah.
   Disimpan sebagai hash PBKDF2-HMAC-SHA256 dengan salt acak per pengguna,
   dengan format string ``pbkdf2_sha256$<iterasi>$<salt_b64>$<hash_b64>``.
   Verifikasi memakai perbandingan waktu-konstan (``hmac.compare_digest``).

2. **Session cookie bertanda tangan** — setelah login berhasil, identitas
   pengguna disimpan dalam cookie yang ditandatangani HMAC-SHA256 memakai
   *secret key* server. Cookie berisi ``username`` + timestamp terbit, lalu
   ditandatangani agar tidak dapat dipalsukan klien. Cookie kedaluwarsa
   setelah ``SESSION_MAX_AGE_SECONDS``.

Kompatibel Python 3.9 (``from __future__ import annotations`` + ``typing``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

# --- Parameter hashing password ------------------------------------------- #
_PBKDF2_ALGORITHM = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 240_000
_SALT_BYTES = 16

# --- Parameter session cookie --------------------------------------------- #
# Nama cookie & masa berlaku session (detik). 7 hari cukup nyaman untuk LAN
# tanpa terlalu sering login ulang.
SESSION_COOKIE_NAME = "monitoring_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


# --- Hashing password ------------------------------------------------------ #

def hash_password(password: str) -> str:
    """Hitung hash PBKDF2 dari ``password`` dengan salt acak.

    Mengembalikan string berformat
    ``pbkdf2_sha256$<iterasi>$<salt_b64>$<hash_b64>`` yang aman disimpan di
    basis data. Fungsi ini yang dipakai saat membuat/mengganti password.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return "{algo}${iters}${salt}${hash}".format(
        algo=_PBKDF2_ALGORITHM,
        iters=_PBKDF2_ITERATIONS,
        salt=base64.b64encode(salt).decode("ascii"),
        hash=base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """Verifikasi ``password`` terhadap hash ``stored``.

    Kembalikan ``True`` hanya bila cocok. Toleran terhadap format hash yang
    rusak/tak dikenal (mengembalikan ``False`` alih-alih mengangkat
    pengecualian). Perbandingan bersifat waktu-konstan.
    """
    try:
        algo, iters_s, salt_b64, hash_b64 = stored.split("$")
        if algo != _PBKDF2_ALGORITHM:
            return False
        iterations = int(iters_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(derived, expected)


# --- Session cookie bertanda tangan ---------------------------------------- #

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: str, secret_key: str) -> str:
    """Hitung tanda tangan HMAC-SHA256 (base64url) untuk ``payload``."""
    digest = hmac.new(
        secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).digest()
    return _b64url_encode(digest)


def create_session_token(username: str, secret_key: str) -> str:
    """Buat token session bertanda tangan untuk ``username``.

    Format: ``<username_b64>.<issued_at>.<signature>``. Bagian yang
    ditandatangani adalah ``<username_b64>.<issued_at>`` sehingga klien tidak
    dapat mengubah username maupun waktu terbit tanpa merusak tanda tangan.
    """
    username_b64 = _b64url_encode(username.encode("utf-8"))
    issued_at = str(int(time.time()))
    payload = "{0}.{1}".format(username_b64, issued_at)
    signature = _sign(payload, secret_key)
    return "{0}.{1}".format(payload, signature)


def verify_session_token(
    token: str,
    secret_key: str,
    max_age_seconds: int = SESSION_MAX_AGE_SECONDS,
) -> Optional[str]:
    """Verifikasi token session; kembalikan ``username`` bila valid.

    Mengembalikan ``None`` bila: format salah, tanda tangan tidak cocok, atau
    token sudah kedaluwarsa. Verifikasi tanda tangan bersifat waktu-konstan.
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    username_b64, issued_at_s, signature = parts
    payload = "{0}.{1}".format(username_b64, issued_at_s)

    expected_sig = _sign(payload, secret_key)
    if not hmac.compare_digest(expected_sig, signature):
        return None

    try:
        issued_at = int(issued_at_s)
        username = _b64url_decode(username_b64).decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError):
        return None

    if max_age_seconds > 0 and (time.time() - issued_at) > max_age_seconds:
        return None

    return username


def generate_secret_key() -> str:
    """Hasilkan *secret key* acak yang kuat untuk menandatangani cookie."""
    return secrets.token_urlsafe(48)
