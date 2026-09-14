"""CLI kelola akun pengguna dashboard (fitur login multi-user).

Dijalankan sebagai modul:

    python -m monitoring.usertool add <username>       # buat akun baru
    python -m monitoring.usertool list                 # daftar akun
    python -m monitoring.usertool remove <username>    # hapus akun
    python -m monitoring.usertool passwd <username>    # ganti password

Password diketik secara interaktif (tidak ditampilkan di layar) sehingga tidak
tercatat di riwayat shell. Path basis data mengikuti env ``MONITORING_DB_PATH``
(sama seperti aplikasi utama) dengan bawaan ``monitoring.db``.

Kompatibel Python 3.9.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from monitoring.infra.repository import DuplicateUserError, Repository
from monitoring.web import auth

ENV_DB_PATH = "MONITORING_DB_PATH"
DEFAULT_DB_PATH = "monitoring.db"


def _db_path() -> str:
    return os.environ.get(ENV_DB_PATH) or DEFAULT_DB_PATH


def _prompt_new_password() -> str:
    """Minta password dua kali (konfirmasi) dan pastikan cocok & tidak kosong."""
    pw1 = getpass.getpass("Password baru: ")
    if not pw1:
        print("Password tidak boleh kosong.", file=sys.stderr)
        sys.exit(1)
    pw2 = getpass.getpass("Ulangi password: ")
    if pw1 != pw2:
        print("Password tidak cocok.", file=sys.stderr)
        sys.exit(1)
    return pw1


async def _cmd_add(username: str) -> int:
    password = _prompt_new_password()
    async with Repository(_db_path()) as repo:
        try:
            await repo.add_user(username, auth.hash_password(password))
        except DuplicateUserError:
            print(f"Gagal: pengguna '{username}' sudah ada.", file=sys.stderr)
            return 1
    print(f"Pengguna '{username}' berhasil dibuat.")
    return 0


async def _cmd_list() -> int:
    async with Repository(_db_path()) as repo:
        users = await repo.list_users()
    if not users:
        print("Belum ada pengguna terdaftar.")
        return 0
    print(f"{'USERNAME':<24} DIBUAT")
    for u in users:
        print(f"{u.username:<24} {u.created_at:%Y-%m-%d %H:%M}")
    return 0


async def _cmd_remove(username: str) -> int:
    async with Repository(_db_path()) as repo:
        # Cegah menghapus akun terakhir (agar tidak terkunci dari dashboard).
        count = await repo.count_users()
        existing = await repo.get_user_by_username(username)
        if existing is None:
            print(f"Gagal: pengguna '{username}' tidak ditemukan.", file=sys.stderr)
            return 1
        if count <= 1:
            print(
                "Gagal: ini akun terakhir. Menghapusnya akan mengunci akses "
                "dashboard. Buat akun lain dulu sebelum menghapus yang ini.",
                file=sys.stderr,
            )
            return 1
        await repo.remove_user(username)
    print(f"Pengguna '{username}' dihapus.")
    return 0


async def _cmd_passwd(username: str) -> int:
    async with Repository(_db_path()) as repo:
        existing = await repo.get_user_by_username(username)
        if existing is None:
            print(f"Gagal: pengguna '{username}' tidak ditemukan.", file=sys.stderr)
            return 1
        password = _prompt_new_password()
        await repo.update_user_password(username, auth.hash_password(password))
    print(f"Password '{username}' berhasil diperbarui.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m monitoring.usertool",
        description="Kelola akun pengguna dashboard.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Buat akun pengguna baru.")
    p_add.add_argument("username")

    sub.add_parser("list", help="Tampilkan daftar akun pengguna.")

    p_rm = sub.add_parser("remove", help="Hapus akun pengguna.")
    p_rm.add_argument("username")

    p_pw = sub.add_parser("passwd", help="Ganti password akun pengguna.")
    p_pw.add_argument("username")

    args = parser.parse_args(argv)

    if args.command == "add":
        return asyncio.run(_cmd_add(args.username))
    if args.command == "list":
        return asyncio.run(_cmd_list())
    if args.command == "remove":
        return asyncio.run(_cmd_remove(args.username))
    if args.command == "passwd":
        return asyncio.run(_cmd_passwd(args.username))
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
