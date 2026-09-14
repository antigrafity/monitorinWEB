"""Repository / Data_Store berbasis SQLite untuk Monitoring_System.

Modul ini mengimplementasikan lapisan infrastruktur persistensi menggunakan
``aiosqlite``. Task 8.1 fokus pada fondasi:

- Inisialisasi skema (``website_config``, ``snapshot``, ``change_event``,
  ``app_config``) beserta index sesuai bagian *Skema SQLite (Data_Store)* pada
  design.
- Mengaktifkan mode WAL (``PRAGMA journal_mode=WAL``) agar pembacaan dan
  penulisan tidak saling memblokir dan persistensi lintas restart terjamin
  (Req 11.1).
- Menyerialkan seluruh penulisan melalui satu koneksi writer tunggal yang
  dijaga ``asyncio.Lock`` untuk menghindari kontensi penulisan.

Operasi CRUD penuh (website, snapshot, change_event) adalah task 8.2/8.3.
Di sini disediakan pula helper baca/tulis ``app_config`` minimal yang berguna
untuk smoke test persistensi.

Kompatibilitas: target runtime Python 3.9 (memakai ``from __future__ import
annotations`` dan ``typing.Optional`` alih-alih sintaks union PEP 604).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import aiosqlite

from monitoring.config import MAX_WEBSITES, SNAPSHOT_RETENTION_PER_URL
from monitoring.domain.models import (
    Alert,
    ChangeEvent,
    Keyword,
    Report,
    Snapshot,
    WebsiteConfig,
)
from monitoring.domain.serialization import (
    diff_from_dict,
    diff_to_dict,
    summary_from_dict,
    summary_to_dict,
)

# Path DB bawaan bila pemanggil tidak menyediakan lokasi khusus.
DEFAULT_DB_PATH = "monitoring.db"


def _decode_sections(raw: Optional[str]) -> List[str]:
    """Dekode kolom ``sections_json`` menjadi daftar blok section.

    Toleran terhadap basis data lama maupun nilai rusak: ``NULL``, string
    kosong, JSON tidak valid, atau JSON yang bukan array semuanya dipetakan ke
    ``[]`` alih-alih mengangkat pengecualian. Dengan demikian kolom baru ini
    tidak pernah menjadi penyebab baris Snapshot dianggap rusak.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item if isinstance(item, str) else str(item) for item in data]


class DuplicateWebsiteError(Exception):
    """Diangkat ketika domain yang ditambahkan sudah terdaftar (Req 1.7).

    Menyimpan ``domain`` yang duplikat agar pemanggil dapat menampilkan pesan
    kesalahan yang jelas.
    """

    def __init__(self, domain: str) -> None:
        self.domain = domain
        super().__init__(
            f"Domain '{domain}' sudah terdaftar sebagai Monitored_Website; "
            "entri duplikat ditolak."
        )


class CapacityExceededError(Exception):
    """Diangkat ketika penambahan akan melebihi kapasitas maksimum (Req 1.8).

    Menyimpan ``max_websites`` (batas kapasitas yang berlaku) agar pemanggil
    dapat menampilkan pesan kesalahan yang jelas.
    """

    def __init__(self, max_websites: int) -> None:
        self.max_websites = max_websites
        super().__init__(
            f"Kapasitas maksimum {max_websites} Monitored_Website telah "
            "tercapai; penambahan ditolak."
        )


class DuplicateUserError(Exception):
    """Diangkat ketika username akun yang ditambahkan sudah terdaftar."""

    def __init__(self, username: str) -> None:
        self.username = username
        super().__init__(
            f"Pengguna '{username}' sudah terdaftar; entri duplikat ditolak."
        )


@dataclass(frozen=True)
class AppUser:
    """Akun pengguna dashboard (fitur login multi-user).

    ``password_hash`` menyimpan hash PBKDF2 (lihat ``monitoring.web.auth``),
    BUKAN password mentah.
    """

    id: str
    username: str
    password_hash: str
    created_at: datetime


def _row_to_user(row: Tuple) -> AppUser:
    """Petakan baris ``app_user`` menjadi :class:`AppUser`."""
    return AppUser(
        id=row[0],
        username=row[1],
        password_hash=row[2],
        created_at=datetime.fromisoformat(row[3]),
    )


@dataclass(frozen=True)
class WebsiteStatusView:
    """Tampilan gabungan sebuah Monitored_Website beserta status pemeriksaan
    terakhir untuk keperluan Dashboard (Req 10.2, 10.3).

    Membawa objek :class:`WebsiteConfig` apa adanya plus dua kolom status yang
    hidup pada tabel ``website_config`` namun tidak ikut dikembalikan oleh
    :meth:`Repository.list_websites`:

    - ``last_checked_at``: waktu pemeriksaan terakhir. ``None`` bila website
      belum pernah diperiksa (Req 10.2).
    - ``last_status``: status pemeriksaan terakhir ('success' | 'failure').
      ``None`` bila belum pernah diperiksa (Req 10.3).

    Properti bantu ``never_checked`` dan ``is_success`` memudahkan template
    menampilkan indikasi/indikator visual yang tepat.
    """

    website: WebsiteConfig
    last_checked_at: Optional[datetime]
    last_status: Optional[str]

    @property
    def never_checked(self) -> bool:
        """True bila website belum pernah diperiksa (Req 10.2)."""
        return self.last_checked_at is None

    @property
    def is_success(self) -> bool:
        """True bila status pemeriksaan terakhir 'success' (Req 10.3)."""
        return self.last_status == "success"

    @property
    def is_failure(self) -> bool:
        """True bila status pemeriksaan terakhir 'failure' (Req 10.3)."""
        return self.last_status == "failure"


@dataclass(frozen=True)
class WebsiteOverview:
    """Ringkasan sebuah Monitored_Website untuk halaman Overview & Websites
    (ContentMonitor Tahap 1).

    Menggabungkan :class:`WebsiteConfig` dengan status pemeriksaan terakhir,
    jumlah halaman terpantau, waktu perubahan terakhir, jumlah perubahan 7
    hari terakhir, dan daftar hitungan perubahan harian 7 hari (untuk
    sparkline SVG pada tabel).
    """

    website: WebsiteConfig
    last_checked_at: Optional[datetime]
    last_status: Optional[str]
    pages_count: int
    last_change_at: Optional[datetime]
    changes_last_7_days: int
    # Daftar (tanggal "YYYY-MM-DD", jumlah) 7 hari terakhir, terurut menaik.
    daily_counts_7_days: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def never_checked(self) -> bool:
        """True bila website belum pernah diperiksa (Req 10.2)."""
        return self.last_checked_at is None

    @property
    def is_success(self) -> bool:
        """True bila status pemeriksaan terakhir 'success' (Req 10.3)."""
        return self.last_status == "success"

    @property
    def is_failure(self) -> bool:
        """True bila status pemeriksaan terakhir 'failure' (Req 10.3)."""
        return self.last_status == "failure"

    @property
    def status_label(self) -> str:
        """Label status ditampilkan: "Active"/"Paused"/"Inactive" (Tahap 1).

        - "Paused" bila website sedang dijeda (``website.paused``), terlepas
          dari riwayat pemeriksaan.
        - "Inactive" bila belum pernah diperiksa (``last_checked_at is None``).
        - "Active" selain itu.
        """
        if self.website.paused:
            return "Paused"
        if self.never_checked:
            return "Inactive"
        return "Active"


@dataclass(frozen=True)
class PageState:
    """Status per-halaman: jeda & kegagalan terakhir (ContentMonitor Tahap 2).

    Baris ``page_state`` bersifat opsional — bila sebuah URL belum pernah
    dijeda maupun gagal diperiksa, tidak ada baris untuknya sama sekali
    (Repository memperlakukan URL tanpa baris sebagai ``paused=False`` dan
    ``last_error=None``).
    """

    url: str
    website_id: str
    paused: bool = False
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None


@dataclass(frozen=True)
class PageOverview:
    """Ringkasan sebuah halaman untuk halaman Pages (ContentMonitor Tahap 2).

    Menggabungkan URL unik dari ``snapshot`` dengan status jeda/kegagalan dari
    ``page_state`` dan waktu perubahan terakhir dari ``change_event``.
    """

    url: str
    website: WebsiteConfig
    last_checked_at: Optional[datetime]
    last_change_at: Optional[datetime]
    page_paused: bool = False
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None

    @property
    def is_broken(self) -> bool:
        """True bila pemeriksaan terakhir pada halaman ini gagal (Tahap 2)."""
        return bool(self.last_error)

    @property
    def is_paused(self) -> bool:
        """True bila halaman ATAU website induknya dijeda (Tahap 2)."""
        return bool(self.page_paused or self.website.paused)

    @property
    def status_label(self) -> str:
        """Label status: "Broken" > "Paused" > "Active" (Tahap 2).

        "Broken" diprioritaskan di atas "Paused" karena kegagalan pemeriksaan
        tetap relevan untuk ditindaklanjuti walau halaman/website-nya sedang
        dijeda.
        """
        if self.is_broken:
            return "Broken"
        if self.is_paused:
            return "Paused"
        return "Active"


@dataclass
class LoadState:
    """Hasil pemuatan data saat Monitoring_System dimulai ulang (Req 11.3, 11.6).

    Membawa:

    - ``websites``: daftar :class:`WebsiteConfig` yang berhasil dimuat;
    - ``snapshots``: pemetaan ``url -> Snapshot`` berisi Snapshot paling baru
      per halaman yang berhasil dimuat;
    - ``load_errors``: daftar pesan indikasi kesalahan pemuatan. Kosong bila
      seluruh data termuat tanpa masalah. Baris data yang gagal-muat/rusak
      dilewati dan pesannya dicatat di sini alih-alih menghentikan operasi
      (Req 11.6).

    ``has_errors`` menjadi indikator observable yang dapat dipermukaan ke
    Dashboard/log tanpa perlu memeriksa panjang daftar secara manual.
    """

    websites: List[WebsiteConfig] = field(default_factory=list)
    snapshots: Dict[str, Snapshot] = field(default_factory=dict)
    load_errors: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """True bila terdapat setidaknya satu kesalahan pemuatan (Req 11.6)."""
        return len(self.load_errors) > 0


# Pernyataan DDL skema sesuai design ("Skema SQLite (Data_Store)").
# Semua tabel dibuat IF NOT EXISTS agar inisialisasi bersifat idempoten
# sehingga aman dipanggil pada setiap start (Req 11.1, 11.3).
_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS website_config (
        id TEXT PRIMARY KEY,
        domain TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        poll_interval_seconds INTEGER,
        created_at TEXT NOT NULL,
        last_checked_at TEXT,
        last_status TEXT,
        paused INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshot (
        url TEXT NOT NULL,
        website_id TEXT NOT NULL REFERENCES website_config(id),
        normalized_text TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        links_json TEXT NOT NULL,
        image_hashes_json TEXT NOT NULL,
        checked_at TEXT NOT NULL,
        sections_json TEXT,
        PRIMARY KEY (url, checked_at)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snapshot_latest ON snapshot(url, checked_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS change_event (
        id TEXT PRIMARY KEY,
        website_id TEXT NOT NULL REFERENCES website_config(id),
        url TEXT NOT NULL,
        detected_at TEXT NOT NULL,
        diff_json TEXT NOT NULL,
        summary_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_website ON change_event(website_id, detected_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS app_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # Status per-halaman (jeda + pencatatan kegagalan) — ContentMonitor
    # Tahap 2. Tabel baru sehingga cukup CREATE TABLE IF NOT EXISTS (tanpa
    # migrasi ALTER TABLE) meski basis data lama sudah berisi data nyata.
    """
    CREATE TABLE IF NOT EXISTS page_state (
        url TEXT PRIMARY KEY,
        website_id TEXT NOT NULL,
        paused INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        last_error_at TEXT
    )
    """,
    # Alert (ContentMonitor Tahap 3). Tabel baru -> cukup CREATE TABLE IF NOT
    # EXISTS meski basis data lama sudah berisi data nyata.
    """
    CREATE TABLE IF NOT EXISTS alert (
        id TEXT PRIMARY KEY,
        website_id TEXT NOT NULL,
        url TEXT,
        alert_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        detail TEXT,
        triggered_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'unread'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_alert_website_triggered "
    "ON alert(website_id, triggered_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alert_status ON alert(status)",
    # Keyword & Keyword State (ContentMonitor Tahap 4).
    """
    CREATE TABLE IF NOT EXISTS keyword (
        id TEXT PRIMARY KEY,
        website_id TEXT NOT NULL,
        keyword TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'must_exist',
        created_at TEXT NOT NULL,
        UNIQUE(website_id, keyword)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS keyword_state (
        website_id TEXT NOT NULL,
        url TEXT NOT NULL,
        keyword_id TEXT NOT NULL,
        last_status TEXT NOT NULL,
        PRIMARY KEY(website_id, url, keyword_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_keyword_website ON keyword(website_id)",
    # Riwayat laporan CSV (ContentMonitor Tahap 5). Tabel baru -> cukup
    # CREATE TABLE IF NOT EXISTS meski basis data lama sudah berisi data nyata.
    """
    CREATE TABLE IF NOT EXISTS report (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        report_type TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        created_at TEXT NOT NULL,
        summary TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        file_path TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_report_created ON report(created_at DESC)",
    # Akun pengguna dashboard (fitur login multi-user). Tabel baru -> cukup
    # CREATE TABLE IF NOT EXISTS meski basis data lama sudah berisi data nyata.
    # Password TIDAK pernah disimpan mentah; hanya hash PBKDF2 (lihat
    # monitoring.web.auth).
    """
    CREATE TABLE IF NOT EXISTS app_user (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
)


class Repository:
    """Data_Store SQLite dengan penulisan terserialisasi.

    Siklus hidup:

    - ``await repo.connect()`` membuka koneksi writer tunggal, mengaktifkan WAL,
      dan membuat skema bila belum ada.
    - ``await repo.close()`` menutup koneksi.
    - Mendukung protokol async context manager (``async with Repository(path)
      as repo: ...``).

    Seluruh penulisan harus melewati :meth:`_execute_write` (atau helper yang
    memanggilnya) sehingga dijamin diserialkan melalui ``self._write_lock``.
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        max_websites: int = MAX_WEBSITES,
    ) -> None:
        self._db_path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None
        # Batas kapasitas Monitored_Website (Req 1.5, 1.8). Dapat dikonfigurasi
        # via konstruktor agar pengujian batas kapasitas tidak perlu menyisipkan
        # 100 baris; nilai bawaan mengikuti konstanta global MAX_WEBSITES.
        self._max_websites = int(max_websites)
        # Lock menyerialkan seluruh penulisan lewat koneksi writer tunggal.
        self._write_lock = asyncio.Lock()

    @property
    def db_path(self) -> str:
        """Lokasi file basis data SQLite yang dipakai repository ini."""
        return self._db_path

    @property
    def max_websites(self) -> int:
        """Batas kapasitas maksimum Monitored_Website yang berlaku (Req 1.8)."""
        return self._max_websites

    async def connect(self) -> "Repository":
        """Buka koneksi, aktifkan WAL, dan inisialisasi skema (idempoten).

        Aman dipanggil berulang: bila koneksi sudah terbuka, panggilan ini
        tidak melakukan apa-apa.
        """
        if self._conn is not None:
            return self

        conn = await aiosqlite.connect(self._db_path)
        # WAL: pembacaan tidak memblokir penulisan; persistensi lintas restart
        # tetap terjaga (Req 11.1).
        await conn.execute("PRAGMA journal_mode=WAL")
        # Foreign keys diaktifkan agar relasi antar tabel ditegakkan.
        await conn.execute("PRAGMA foreign_keys=ON")
        self._conn = conn
        await self._initialize_schema()
        return self

    # Alias yang lebih deskriptif; sebagian pemanggil mungkin memakai nama ini.
    async def initialize(self) -> "Repository":
        """Alias untuk :meth:`connect` (buka koneksi + siapkan skema)."""
        return await self.connect()

    async def _initialize_schema(self) -> None:
        """Buat seluruh tabel dan index bila belum ada, lalu jalankan migrasi."""
        assert self._conn is not None, "connect() harus dipanggil lebih dulu"
        async with self._write_lock:
            for statement in _SCHEMA_STATEMENTS:
                await self._conn.execute(statement)
            await self._conn.commit()
        await self._migrate_schema()

    async def _migrate_schema(self) -> None:
        """Tambahkan kolom yang belum ada pada basis data lama (idempoten).

        Basis data yang dibuat sebelum bidang ``Snapshot.sections`` ada tidak
        memiliki kolom ``sections_json``; ``CREATE TABLE IF NOT EXISTS`` tidak
        akan menambahkannya. Karena itu skema diperiksa via
        ``PRAGMA table_info(snapshot)`` dan kolom yang hilang ditambahkan
        dengan ``ALTER TABLE``. Kolom bersifat nullable sehingga baris lama
        tetap valid dan dibaca sebagai daftar section kosong.

        Aman dipanggil berulang: bila kolom sudah ada, tidak ada yang dilakukan.
        """
        await self._migrate_snapshot_sections_column()
        await self._migrate_website_paused_column()
        await self._migrate_snapshot_title_meta_columns()
        await self._migrate_keywords_tables()

    async def _migrate_keywords_tables(self) -> None:
        """Buat tabel keyword dan keyword_state bila belum ada (ContentMonitor Tahap 4)."""
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS keyword (
                    id TEXT PRIMARY KEY,
                    website_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'must_exist',
                    created_at TEXT NOT NULL,
                    UNIQUE(website_id, keyword)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS keyword_state (
                    website_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    keyword_id TEXT NOT NULL,
                    last_status TEXT NOT NULL,
                    PRIMARY KEY(website_id, url, keyword_id)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_keyword_website ON keyword(website_id)"
            )
            await conn.commit()

    async def _migrate_snapshot_sections_column(self) -> None:
        """Tambahkan kolom ``sections_json`` pada ``snapshot`` bila belum ada."""
        conn = self._require_conn()
        async with self._write_lock:
            async with conn.execute("PRAGMA table_info(snapshot)") as cur:
                rows = await cur.fetchall()
            existing = {row[1] for row in rows}
            if "sections_json" in existing:
                return
            try:
                await conn.execute(
                    "ALTER TABLE snapshot ADD COLUMN sections_json TEXT"
                )
                await conn.commit()
            except Exception:
                # Balapan/duplikat kolom tidak boleh menggagalkan startup.
                await conn.rollback()

    async def _migrate_website_paused_column(self) -> None:
        """Tambahkan kolom ``paused`` pada ``website_config`` bila belum ada.

        Basis data lama (dibuat sebelum status Aktif/Jeda per website ada
        pada ContentMonitor Tahap 1) tidak memiliki kolom ``paused``.
        ``CREATE TABLE IF NOT EXISTS`` tidak menambahkannya pada tabel yang
        sudah ada, sehingga skema diperiksa via ``PRAGMA table_info`` dan
        kolom ditambahkan lewat ``ALTER TABLE`` bila hilang. Nilai bawaan
        ``0`` (tidak dijeda) sehingga seluruh website lama otomatis dianggap
        aktif setelah migrasi — data pengguna yang sudah ada tidak berubah.

        Aman dipanggil berulang: bila kolom sudah ada, tidak ada yang dilakukan.
        """
        conn = self._require_conn()
        async with self._write_lock:
            async with conn.execute("PRAGMA table_info(website_config)") as cur:
                rows = await cur.fetchall()
            existing = {row[1] for row in rows}
            if "paused" in existing:
                return
            try:
                await conn.execute(
                    "ALTER TABLE website_config ADD COLUMN paused "
                    "INTEGER NOT NULL DEFAULT 0"
                )
                await conn.commit()
            except Exception:
                # Balapan/duplikat kolom tidak boleh menggagalkan startup.
                await conn.rollback()

    async def _migrate_snapshot_title_meta_columns(self) -> None:
        """Tambahkan kolom ``title``/``meta_description`` pada ``snapshot``
        bila belum ada (ContentMonitor Tahap 3).

        Basis data lama (dibuat sebelum pelacakan judul & meta description
        ada) tidak memiliki kolom ini; ditambahkan lewat ``ALTER TABLE``
        (nullable) sehingga baris lama dibaca sebagai ``None`` tanpa error.
        Aman dipanggil berulang.
        """
        conn = self._require_conn()
        async with self._write_lock:
            async with conn.execute("PRAGMA table_info(snapshot)") as cur:
                rows = await cur.fetchall()
            existing = {row[1] for row in rows}
            for column in ("title", "meta_description"):
                if column in existing:
                    continue
                try:
                    await conn.execute(
                        f"ALTER TABLE snapshot ADD COLUMN {column} TEXT"
                    )
                    await conn.commit()
                except Exception:
                    # Balapan/duplikat kolom tidak boleh menggagalkan startup.
                    await conn.rollback()

    async def close(self) -> None:
        """Tutup koneksi writer bila terbuka."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "Repository belum terhubung. Panggil await connect() lebih dulu."
            )
        return self._conn

    async def _execute_write(self, sql: str, params: tuple = ()) -> None:
        """Jalankan satu pernyataan tulis dengan penulisan terserialisasi.

        Seluruh penulisan melewati ``self._write_lock`` sehingga hanya satu
        penulisan berjalan pada satu waktu melalui koneksi writer tunggal.
        """
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute(sql, params)
            await conn.commit()

    # --- Operasi CRUD Website_Config (task 8.2) --------------------------- #

    async def add_website(self, cfg: WebsiteConfig) -> None:
        """Simpan sebuah Monitored_Website baru (Req 1.1).

        Menolak entri bila:

        - kapasitas saat ini sudah mencapai ``max_websites`` -> mengangkat
          :class:`CapacityExceededError` (Req 1.8); pemeriksaan kapasitas
          dilakukan lebih dulu agar penambahan yang akan melebihi batas ditolak
          tanpa menyentuh basis data;
        - domain sudah terdaftar -> mengangkat :class:`DuplicateWebsiteError`
          (Req 1.7).

        Pemeriksaan kapasitas, pemeriksaan duplikat, dan penyisipan dilakukan
        secara atomik di bawah ``self._write_lock`` untuk menghindari kondisi
        balapan (TOCTOU) melalui koneksi writer tunggal. Batasan UNIQUE pada
        kolom ``domain`` menjadi jaring pengaman terakhir terhadap duplikat.
        """
        conn = self._require_conn()
        async with self._write_lock:
            # Kapasitas (Req 1.8).
            async with conn.execute("SELECT COUNT(*) FROM website_config") as cur:
                (count,) = await cur.fetchone()
            if count >= self._max_websites:
                raise CapacityExceededError(self._max_websites)

            # Duplikat domain (Req 1.7).
            async with conn.execute(
                "SELECT 1 FROM website_config WHERE domain = ?", (cfg.domain,)
            ) as cur:
                exists = await cur.fetchone()
            if exists is not None:
                raise DuplicateWebsiteError(cfg.domain)

            # Simpan row (Req 1.1). last_checked_at & last_status dibiarkan NULL
            # (belum pernah diperiksa). ``paused`` mengikuti nilai pada ``cfg``
            # (bawaan False -> aktif).
            await conn.execute(
                "INSERT INTO website_config "
                "(id, domain, name, poll_interval_seconds, created_at, paused) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cfg.id,
                    cfg.domain,
                    cfg.name,
                    cfg.poll_interval_seconds,
                    cfg.created_at.isoformat(),
                    1 if cfg.paused else 0,
                ),
            )
            await conn.commit()

    async def remove_website(self, website_id: str) -> None:
        """Hapus sebuah Monitored_Website berdasarkan ``id`` (Req 1.2).

        Bila ``website_id`` tidak ada, operasi tidak berpengaruh (idempoten).
        """
        await self._execute_write(
            "DELETE FROM website_config WHERE id = ?", (website_id,)
        )

    async def update_website(self, cfg: WebsiteConfig) -> None:
        """Perbarui pengaturan sebuah Monitored_Website (Req 1.3).

        Memperbarui ``name`` dan ``poll_interval_seconds`` untuk baris dengan
        ``id`` yang cocok. ``domain`` dan ``created_at`` tidak diubah.
        """
        await self._execute_write(
            "UPDATE website_config SET name = ?, poll_interval_seconds = ? "
            "WHERE id = ?",
            (cfg.name, cfg.poll_interval_seconds, cfg.id),
        )

    async def list_websites(self) -> List[WebsiteConfig]:
        """Kembalikan seluruh Monitored_Website terurut waktu pembuatan menaik."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, domain, name, poll_interval_seconds, created_at, paused "
            "FROM website_config ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            WebsiteConfig(
                id=row[0],
                domain=row[1],
                name=row[2],
                poll_interval_seconds=row[3],
                created_at=datetime.fromisoformat(row[4]),
                paused=bool(row[5]),
            )
            for row in rows
        ]

    async def list_websites_with_status(self) -> List[WebsiteStatusView]:
        """Kembalikan seluruh Monitored_Website beserta status pemeriksaan
        terakhir untuk Dashboard (Req 10.1, 10.2, 10.3).

        Berbeda dengan :meth:`list_websites`, kolom ``last_checked_at`` dan
        ``last_status`` pada tabel ``website_config`` ikut dibaca dan dibungkus
        ke dalam :class:`WebsiteStatusView`. Website yang belum pernah diperiksa
        memiliki ``last_checked_at`` / ``last_status`` bernilai ``None``
        (Req 10.2). Hasil terurut waktu pembuatan menaik agar tampilan stabil.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, domain, name, poll_interval_seconds, created_at, "
            "last_checked_at, last_status, paused "
            "FROM website_config ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        views: List[WebsiteStatusView] = []
        for row in rows:
            website = WebsiteConfig(
                id=row[0],
                domain=row[1],
                name=row[2],
                poll_interval_seconds=row[3],
                created_at=datetime.fromisoformat(row[4]),
                paused=bool(row[7]),
            )
            last_checked_at = (
                datetime.fromisoformat(row[5]) if row[5] is not None else None
            )
            views.append(
                WebsiteStatusView(
                    website=website,
                    last_checked_at=last_checked_at,
                    last_status=row[6],
                )
            )
        return views

    async def get_website(self, website_id: str) -> Optional[WebsiteConfig]:
        """Kembalikan sebuah Monitored_Website berdasarkan ``id``.

        Berguna untuk menampilkan nama/domain pada header halaman riwayat
        Dashboard (Req 10.4). Kembalikan ``None`` bila ``website_id`` tidak
        ditemukan sehingga pemanggil dapat menampilkan indikasi tidak ditemukan.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, domain, name, poll_interval_seconds, created_at, paused "
            "FROM website_config WHERE id = ?",
            (website_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return WebsiteConfig(
            id=row[0],
            domain=row[1],
            name=row[2],
            poll_interval_seconds=row[3],
            created_at=datetime.fromisoformat(row[4]),
            paused=bool(row[5]),
        )

    async def count_websites(self) -> int:
        """Kembalikan jumlah Monitored_Website terdaftar saat ini (Req 1.8)."""
        conn = self._require_conn()
        async with conn.execute("SELECT COUNT(*) FROM website_config") as cur:
            (count,) = await cur.fetchone()
        return int(count)

    # --- Operasi Snapshot & Change_Event (task 8.3) ----------------------- #

    async def save_snapshot(self, snap: Snapshot) -> bool:
        """Simpan sebuah Snapshot baru (Req 5.2, 5.3, 5.4, 5.5).

        Karena PRIMARY KEY ``(url, checked_at)``, penyisipan Snapshot dengan
        ``checked_at`` baru menambah baris baru tanpa menghapus Snapshot lama;
        dengan demikian Snapshot valid sebelumnya tetap dipertahankan sampai
        yang baru berhasil tersimpan (Req 5.4).

        ``links`` disimpan sebagai JSON array (``links_json``) dan
        ``image_hashes`` sebagai JSON object (``image_hashes_json``);
        ``checked_at`` disimpan sebagai string ISO 8601 (Req 5.2, 5.3).

        Mengembalikan ``True`` bila penyimpanan berhasil. Bila penyimpanan
        gagal (mis. pelanggaran constraint atau error I/O), transaksi
        di-rollback sehingga data sebelumnya tidak berubah dan fungsi
        mengembalikan ``False`` sebagai indikator kegagalan (Req 5.5).
        """
        conn = self._require_conn()
        links_json = json.dumps(list(snap.links))
        image_hashes_json = json.dumps(dict(snap.image_hashes))
        sections_json = json.dumps(list(snap.sections))
        async with self._write_lock:
            try:
                await conn.execute(
                    "INSERT INTO snapshot "
                    "(url, website_id, normalized_text, content_hash, "
                    "links_json, image_hashes_json, checked_at, sections_json, "
                    "title, meta_description) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        snap.url,
                        snap.website_id,
                        snap.normalized_text,
                        snap.content_hash,
                        links_json,
                        image_hashes_json,
                        snap.checked_at.isoformat(),
                        sections_json,
                        snap.title,
                        snap.meta_description,
                    ),
                )
                await conn.commit()
                return True
            except Exception:
                # Pertahankan data sebelumnya tanpa perubahan (Req 5.5).
                await conn.rollback()
                return False

    async def get_latest_snapshot(self, url: str) -> Optional[Snapshot]:
        """Kembalikan Snapshot paling baru untuk sebuah URL (Req 5.4, 11.3).

        Snapshot dipilih berdasarkan ``checked_at`` terbesar (paling baru).
        Kembalikan ``None`` bila belum ada Snapshot untuk URL tersebut.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT url, website_id, normalized_text, content_hash, "
            "links_json, image_hashes_json, checked_at, sections_json, "
            "title, meta_description "
            "FROM snapshot WHERE url = ? ORDER BY checked_at DESC LIMIT 1",
            (url,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Snapshot(
            url=row[0],
            website_id=row[1],
            normalized_text=row[2],
            content_hash=row[3],
            checked_at=datetime.fromisoformat(row[6]),
            links=list(json.loads(row[4])),
            image_hashes=dict(json.loads(row[5])),
            sections=_decode_sections(row[7]),
            title=row[8] if len(row) > 8 else None,
            meta_description=row[9] if len(row) > 9 else None,
        )

    async def prune_snapshots(
        self,
        url: Optional[str] = None,
        keep: int = SNAPSHOT_RETENTION_PER_URL,
    ) -> int:
        """Pangkas Snapshot lama, sisakan ``keep`` terbaru per URL (Req 5.4, 11.4).

        Setiap pemeriksaan menambah satu baris Snapshot per halaman sehingga
        Data_Store bertambah tanpa batas bila tidak dipangkas. Metode ini
        menghapus baris Snapshot yang berada di luar ``keep`` baris terbaru
        (urut ``checked_at`` DESC) untuk setiap URL.

        Jaminan:

        - Snapshot TERBARU sebuah URL tidak pernah dihapus, sehingga baseline
          perbandingan selalu tersedia (Req 5.4). Nilai ``keep`` di bawah 1
          dinaikkan menjadi 1 untuk menegakkan jaminan ini.
        - HANYA tabel ``snapshot`` yang disentuh; baris ``change_event``
          (riwayat perubahan) tidak pernah dihapus atau diubah (Req 11.4).
        - Penghapusan melewati ``self._write_lock`` sehingga terserialkan
          bersama seluruh penulisan lain melalui koneksi writer tunggal.

        Args:
            url: Bila diberikan, pemangkasan dibatasi pada URL tersebut. Bila
                ``None``, seluruh URL dipangkas masing-masing hingga ``keep``.
            keep: Jumlah Snapshot terbaru per URL yang dipertahankan.

        Returns:
            Jumlah baris Snapshot yang terhapus. ``0`` bila tidak ada yang
            perlu dipangkas atau bila penghapusan gagal (kegagalan pemeliharaan
            tidak boleh menghentikan pemeriksaan; data lama dibiarkan apa adanya).
        """
        conn = self._require_conn()
        keep = max(1, int(keep))

        # Sebuah baris dipangkas bila terdapat >= keep baris LAIN pada URL yang
        # sama dengan checked_at lebih baru (artinya baris ini berada di luar
        # jendela retensi). Baris terbaru selalu memiliki hitungan 0 -> aman.
        sql = (
            "DELETE FROM snapshot WHERE rowid IN ("
            "  SELECT s.rowid FROM snapshot AS s"
            "  WHERE ("
            "    SELECT COUNT(*) FROM snapshot AS s2"
            "    WHERE s2.url = s.url AND s2.checked_at > s.checked_at"
            "  ) >= ?"
        )
        params: tuple = (keep,)
        if url is not None:
            sql += " AND s.url = ?"
            params = (keep, url)
        sql += ")"

        async with self._write_lock:
            try:
                cursor = await conn.execute(sql, params)
                rowcount = cursor.rowcount
                deleted = rowcount if rowcount and rowcount > 0 else 0
                await conn.commit()
                return int(deleted)
            except Exception:
                await conn.rollback()
                return 0

    async def save_change_event(self, evt: ChangeEvent) -> bool:
        """Simpan sebuah Change_Event untuk keperluan riwayat (Req 11.2, 11.5).

        ``diff`` disimpan sebagai ``diff_json`` dan ``summary`` sebagai
        ``summary_json``; ``detected_at`` disimpan sebagai string ISO 8601.

        Mengembalikan ``True`` bila berhasil; bila gagal, transaksi
        di-rollback dan mengembalikan ``False`` tanpa mengubah data yang sudah
        tersimpan (Req 11.5).
        """
        conn = self._require_conn()
        diff_json = json.dumps(diff_to_dict(evt.diff))
        summary_json = json.dumps(summary_to_dict(evt.summary))
        async with self._write_lock:
            try:
                await conn.execute(
                    "INSERT INTO change_event "
                    "(id, website_id, url, detected_at, diff_json, summary_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        evt.id,
                        evt.website_id,
                        evt.url,
                        evt.detected_at.isoformat(),
                        diff_json,
                        summary_json,
                    ),
                )
                await conn.commit()
                return True
            except Exception:
                await conn.rollback()
                return False

    async def list_change_events(self, website_id: str) -> List[ChangeEvent]:
        """Kembalikan riwayat Change_Event sebuah website (Req 10.4).

        Terurut menurun berdasarkan ``detected_at`` (terbaru ke terlama),
        memanfaatkan index ``idx_event_website``.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, website_id, url, detected_at, diff_json, summary_json "
            "FROM change_event WHERE website_id = ? ORDER BY detected_at DESC",
            (website_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            ChangeEvent(
                id=row[0],
                website_id=row[1],
                url=row[2],
                detected_at=datetime.fromisoformat(row[3]),
                diff=diff_from_dict(json.loads(row[4])),
                summary=summary_from_dict(json.loads(row[5])),
            )
            for row in rows
        ]

    async def get_change_event(self, event_id: str) -> Optional[ChangeEvent]:
        """Kembalikan sebuah Change_Event berdasarkan ``id`` (Req 10.5).

        Digunakan oleh rute detail Dashboard (``GET /events/{id}``) untuk
        menampilkan Diff yang terkait. Kembalikan ``None`` bila ``event_id``
        tidak ditemukan sehingga pemanggil dapat menampilkan indikasi tidak
        ditemukan (404).
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, website_id, url, detected_at, diff_json, summary_json "
            "FROM change_event WHERE id = ?",
            (event_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return ChangeEvent(
            id=row[0],
            website_id=row[1],
            url=row[2],
            detected_at=datetime.fromisoformat(row[3]),
            diff=diff_from_dict(json.loads(row[4])),
            summary=summary_from_dict(json.loads(row[5])),
        )

    async def update_last_check(
        self, website_id: str, ts: datetime, status: str
    ) -> None:
        """Perbarui waktu & status pemeriksaan terakhir sebuah website.

        Menyetel ``last_checked_at`` (ISO 8601) dan ``last_status``
        ('success' | 'failure') untuk baris dengan ``id`` yang cocok
        (Req 10.2, 10.3, 12.1). Bila ``website_id`` tidak ada, operasi tidak
        berpengaruh.
        """
        ts_value = ts.isoformat() if isinstance(ts, datetime) else str(ts)
        await self._execute_write(
            "UPDATE website_config SET last_checked_at = ?, last_status = ? "
            "WHERE id = ?",
            (ts_value, status, website_id),
        )

    async def set_website_paused(self, website_id: str, paused: bool) -> None:
        """Setel status Aktif/Jeda sebuah Monitored_Website (ContentMonitor Tahap 1).

        Website yang dijeda (``paused=True``) dilewati oleh Scheduler pada
        siklus penjadwalan berikutnya (tidak dihapus/dihentikan, hanya tidak
        dijadwalkan). Bila ``website_id`` tidak ada, operasi tidak berpengaruh.
        """
        await self._execute_write(
            "UPDATE website_config SET paused = ? WHERE id = ?",
            (1 if paused else 0, website_id),
        )

    # --- Query agregasi Overview & Websites (ContentMonitor Tahap 1) ------ #

    async def count_pages_monitored(
        self, website_id: Optional[str] = None
    ) -> int:
        """Hitung jumlah URL unik pada tabel ``snapshot`` (kartu KPI "Pages Monitored").

        Dihitung memakai ``COUNT(DISTINCT url)`` (agregat SQL) tanpa memuat
        seluruh baris Snapshot ke Python. Bila ``website_id`` diberikan,
        hitungan dibatasi pada halaman milik website tersebut.
        """
        conn = self._require_conn()
        if website_id is None:
            sql = "SELECT COUNT(DISTINCT url) FROM snapshot"
            params: tuple = ()
        else:
            sql = "SELECT COUNT(DISTINCT url) FROM snapshot WHERE website_id = ?"
            params = (website_id,)
        async with conn.execute(sql, params) as cur:
            (count,) = await cur.fetchone()
        return int(count)

    async def count_change_events_between(
        self,
        start: datetime,
        end: datetime,
        website_id: Optional[str] = None,
    ) -> int:
        """Hitung jumlah Change_Event pada rentang ``[start, end)`` (kartu KPI).

        Rentang bersifat setengah-terbuka: ``start`` inklusif, ``end``
        eksklusif, dibandingkan sebagai string ISO 8601 (konsisten dengan
        penyimpanan ``detected_at``). Dihitung dengan ``COUNT(*)`` agregat SQL.
        """
        conn = self._require_conn()
        sql = (
            "SELECT COUNT(*) FROM change_event "
            "WHERE detected_at >= ? AND detected_at < ?"
        )
        params: tuple = (start.isoformat(), end.isoformat())
        if website_id is not None:
            sql += " AND website_id = ?"
            params = params + (website_id,)
        async with conn.execute(sql, params) as cur:
            (count,) = await cur.fetchone()
        return int(count)

    async def daily_change_counts(
        self,
        start: datetime,
        end: datetime,
        website_id: Optional[str] = None,
    ) -> List[Tuple[str, int]]:
        """Hitung jumlah Change_Event per hari pada rentang ``[start, end)``.

        Dipakai untuk chart "Content Changes Over Time" (line chart) dan
        sparkline per website. Mengembalikan daftar pasangan ``(tanggal,
        jumlah)`` dengan kunci tanggal berformat ``"YYYY-MM-DD"``, terurut
        menaik. Hari TANPA perubahan tetap disertakan dengan nilai ``0`` agar
        sumbu-x chart tidak memiliki celah.

        Query agregat SQL (``GROUP BY`` atas substring tanggal ``detected_at``)
        digunakan untuk menghitung hari yang memiliki data; hari kosong
        kemudian disisipkan di Python berdasarkan rentang tanggal (bukan
        dengan memuat seluruh baris Change_Event, hanya hasil agregat per
        hari yang sudah ringkas).
        """
        conn = self._require_conn()
        sql = (
            "SELECT substr(detected_at, 1, 10) AS day, COUNT(*) "
            "FROM change_event WHERE detected_at >= ? AND detected_at < ?"
        )
        params: tuple = (start.isoformat(), end.isoformat())
        if website_id is not None:
            sql += " AND website_id = ?"
            params = params + (website_id,)
        sql += " GROUP BY day"
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        counts = {row[0]: int(row[1]) for row in rows}

        # Sisipkan hari yang tidak memiliki perubahan sebagai 0 (Tahap 1).
        result: List[Tuple[str, int]] = []
        day = start.date()
        end_day = end.date()
        while day < end_day:
            key = day.isoformat()
            result.append((key, counts.get(key, 0)))
            day = day + timedelta(days=1)
        return result

    async def recent_change_events(
        self, limit: int = 5, website_id: Optional[str] = None
    ) -> List[ChangeEvent]:
        """Kembalikan Change_Event terbaru (lintas website bila ``website_id``
        bernilai ``None``), untuk panel "Top Changes Detected" (Tahap 1).

        Terurut menurun berdasarkan ``detected_at`` (terbaru dulu), dibatasi
        ``limit`` baris via ``LIMIT`` SQL (tidak memuat seluruh tabel).
        """
        conn = self._require_conn()
        sql = (
            "SELECT id, website_id, url, detected_at, diff_json, summary_json "
            "FROM change_event"
        )
        params: tuple = ()
        if website_id is not None:
            sql += " WHERE website_id = ?"
            params = (website_id,)
        sql += " ORDER BY detected_at DESC LIMIT ?"
        params = params + (int(limit),)
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            ChangeEvent(
                id=row[0],
                website_id=row[1],
                url=row[2],
                detected_at=datetime.fromisoformat(row[3]),
                diff=diff_from_dict(json.loads(row[4])),
                summary=summary_from_dict(json.loads(row[5])),
            )
            for row in rows
        ]

    async def list_change_events_between(
        self,
        start: datetime,
        end: datetime,
        website_id: Optional[str] = None,
    ) -> List[ChangeEvent]:
        """Kembalikan Change_Event pada rentang ``[start, end)`` (Tahap 1).

        Dipakai untuk menghitung legenda "Added/Removed/Updated" pada chart
        Overview: berbeda dengan :meth:`count_change_events_between` (yang
        hanya mengembalikan jumlah), fungsi ini mengembalikan objek
        :class:`ChangeEvent` lengkap (termasuk Diff) karena tipe perubahan
        diturunkan dari Diff via ``classify_change`` di lapisan presentasi,
        bukan dari kolom SQL. Query dibatasi pada rentang tanggal yang
        diminta (biasanya 7 hari) sehingga tidak memuat seluruh tabel.
        Terurut menurun berdasarkan ``detected_at``.
        """
        conn = self._require_conn()
        sql = (
            "SELECT id, website_id, url, detected_at, diff_json, summary_json "
            "FROM change_event WHERE detected_at >= ? AND detected_at < ?"
        )
        params: tuple = (start.isoformat(), end.isoformat())
        if website_id is not None:
            sql += " AND website_id = ?"
            params = params + (website_id,)
        sql += " ORDER BY detected_at DESC"
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            ChangeEvent(
                id=row[0],
                website_id=row[1],
                url=row[2],
                detected_at=datetime.fromisoformat(row[3]),
                diff=diff_from_dict(json.loads(row[4])),
                summary=summary_from_dict(json.loads(row[5])),
            )
            for row in rows
        ]

    async def website_stats(self) -> List["WebsiteOverview"]:
        """Kembalikan ringkasan per website untuk Overview & Websites (Tahap 1).

        Untuk setiap Monitored_Website, gabungkan:

        - :class:`WebsiteConfig` (termasuk ``paused``) + status pemeriksaan
          terakhir dari ``website_config``;
        - jumlah halaman terpantau (``COUNT(DISTINCT url)`` pada ``snapshot``);
        - waktu perubahan terakhir (``MAX(detected_at)`` pada ``change_event``);
        - jumlah perubahan 7 hari terakhir;
        - daftar hitungan perubahan harian 7 hari terakhir (untuk sparkline).

        Seluruh agregasi memakai SQL (``COUNT``/``MAX``/``GROUP BY``); tidak
        ada baris Snapshot/Change_Event mentah yang dimuat ke Python di luar
        yang sudah diringkas per hari.
        """
        conn = self._require_conn()
        websites = await self.list_websites()
        if not websites:
            return []

        # Status pemeriksaan terakhir per website (satu query untuk semua).
        async with conn.execute(
            "SELECT id, last_checked_at, last_status FROM website_config"
        ) as cur:
            status_rows = await cur.fetchall()
        status_by_id = {
            row[0]: (
                datetime.fromisoformat(row[1]) if row[1] is not None else None,
                row[2],
            )
            for row in status_rows
        }

        # Jumlah halaman terpantau per website (satu query agregat untuk semua).
        async with conn.execute(
            "SELECT website_id, COUNT(DISTINCT url) FROM snapshot "
            "GROUP BY website_id"
        ) as cur:
            pages_rows = await cur.fetchall()
        pages_by_id = {row[0]: int(row[1]) for row in pages_rows}

        # Waktu perubahan terakhir per website (satu query agregat untuk semua).
        async with conn.execute(
            "SELECT website_id, MAX(detected_at) FROM change_event "
            "GROUP BY website_id"
        ) as cur:
            last_change_rows = await cur.fetchall()
        last_change_by_id = {
            row[0]: (datetime.fromisoformat(row[1]) if row[1] else None)
            for row in last_change_rows
        }

        # Hitungan perubahan harian 7 hari terakhir per website, dihitung
        # dengan satu query agregat ter-GROUP BY (website_id, hari) untuk
        # seluruh website sekaligus (bukan N query terpisah).
        now = datetime.now()
        start = (now - timedelta(days=7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        async with conn.execute(
            "SELECT website_id, substr(detected_at, 1, 10) AS day, COUNT(*) "
            "FROM change_event WHERE detected_at >= ? AND detected_at < ? "
            "GROUP BY website_id, day",
            (start.isoformat(), end.isoformat()),
        ) as cur:
            daily_rows = await cur.fetchall()
        daily_by_id: Dict[str, Dict[str, int]] = {}
        for wid, day, cnt in daily_rows:
            daily_by_id.setdefault(wid, {})[day] = int(cnt)

        day_keys: List[str] = []
        d = start.date()
        end_day = end.date()
        while d < end_day:
            day_keys.append(d.isoformat())
            d = d + timedelta(days=1)

        stats: List[WebsiteOverview] = []
        for website in websites:
            last_checked_at, last_status = status_by_id.get(
                website.id, (None, None)
            )
            day_counts = daily_by_id.get(website.id, {})
            daily_counts_7_days = [
                (key, day_counts.get(key, 0)) for key in day_keys
            ]
            stats.append(
                WebsiteOverview(
                    website=website,
                    last_checked_at=last_checked_at,
                    last_status=last_status,
                    pages_count=pages_by_id.get(website.id, 0),
                    last_change_at=last_change_by_id.get(website.id),
                    changes_last_7_days=sum(c for _, c in daily_counts_7_days),
                    daily_counts_7_days=daily_counts_7_days,
                )
            )
        return stats

    # --- Query filter Content Changes (ContentMonitor Tahap 2) ------------ #

    async def list_change_events_filtered(
        self,
        start: datetime,
        end: datetime,
        website_id: Optional[str] = None,
        url_query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[ChangeEvent]:
        """Kembalikan Change_Event terfilter untuk halaman Content Changes.

        Filter yang didukung LANGSUNG di SQL: rentang tanggal ``[start, end)``
        (setengah-terbuka, konsisten dengan
        :meth:`count_change_events_between`), ``website_id`` (opsional), dan
        ``url_query`` (opsional, dicocokkan case-insensitive pada kolom
        ``url`` via ``LIKE`` — pencarian pada deskripsi Diff TIDAK dilakukan
        di sini karena deskripsi diturunkan dari ``diff_json`` di lapisan
        presentasi; lihat catatan pada pemanggil di ``web/app.py`` tentang
        bagaimana pencarian deskripsi digabung dengan pencarian URL ini).

        CATATAN PENTING mengenai filter TIPE (added/removed/updated): tipe
        perubahan diturunkan dari ``Diff`` via ``classify_change`` (fungsi
        murni di ``domain/classify.py``), BUKAN kolom SQL — sehingga TIDAK
        dapat difilter di sini. Pemanggil (lapisan presentasi) yang perlu
        memfilter per tipe harus mengambil batch tanpa filter tipe lebih dulu
        (lihat :meth:`count_change_events_filtered` untuk pendekatan serupa
        pada penghitungan), memfilter di Python, lalu menerapkan
        limit/offset-nya sendiri. Method ini HANYA menerapkan filter yang bisa
        dilakukan di SQL (tanggal/website/URL) plus ``LIMIT``/``OFFSET`` biasa;
        pagination yang benar saat filter tipe ikut aktif adalah tanggung
        jawab pemanggil (lihat ``_load_changes_context`` di ``web/app.py``).

        Terurut menurun berdasarkan ``detected_at`` (terbaru dulu).
        """
        conn = self._require_conn()
        sql = (
            "SELECT id, website_id, url, detected_at, diff_json, summary_json "
            "FROM change_event WHERE detected_at >= ? AND detected_at < ?"
        )
        params: list = [start.isoformat(), end.isoformat()]
        if website_id is not None:
            sql += " AND website_id = ?"
            params.append(website_id)
        if url_query:
            sql += " AND LOWER(url) LIKE ?"
            params.append("%{0}%".format(url_query.lower()))
        sql += " ORDER BY detected_at DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        async with conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [
            ChangeEvent(
                id=row[0],
                website_id=row[1],
                url=row[2],
                detected_at=datetime.fromisoformat(row[3]),
                diff=diff_from_dict(json.loads(row[4])),
                summary=summary_from_dict(json.loads(row[5])),
            )
            for row in rows
        ]

    async def count_change_events_filtered(
        self,
        start: datetime,
        end: datetime,
        website_id: Optional[str] = None,
        url_query: Optional[str] = None,
    ) -> int:
        """Hitung jumlah Change_Event terfilter (tanpa filter tipe, lihat
        catatan pada :meth:`list_change_events_filtered`).

        Dihitung dengan ``COUNT(*)`` agregat SQL atas filter tanggal/website/
        URL yang sama.
        """
        conn = self._require_conn()
        sql = (
            "SELECT COUNT(*) FROM change_event "
            "WHERE detected_at >= ? AND detected_at < ?"
        )
        params: list = [start.isoformat(), end.isoformat()]
        if website_id is not None:
            sql += " AND website_id = ?"
            params.append(website_id)
        if url_query:
            sql += " AND LOWER(url) LIKE ?"
            params.append("%{0}%".format(url_query.lower()))
        async with conn.execute(sql, tuple(params)) as cur:
            (count,) = await cur.fetchone()
        return int(count)

    async def iter_change_events_for_export(
        self,
        start: datetime,
        end: datetime,
        website_id: Optional[str] = None,
        batch_size: int = 500,
    ):
        """Hasilkan Change_Event pada rentang ``[start, end)`` secara BERTAHAP
        (generator async) untuk ekspor CSV (ContentMonitor Tahap 5).

        Dipakai oleh ``GET /reports/export?type=changes`` agar ekspor tidak
        memuat seluruh tabel ke memori sekaligus saat jumlah baris besar:
        baris diambil per ``batch_size`` lewat ``LIMIT``/``OFFSET`` SQL, dan
        pemanggil (rute web) menstream setiap baris CSV segera setelah
        diproses tanpa menahan seluruh hasil di memori Python.
        """
        offset = 0
        while True:
            batch = await self.list_change_events_filtered(
                start,
                end,
                website_id=website_id,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                return
            for evt in batch:
                yield evt
            offset += len(batch)
            if len(batch) < batch_size:
                return

    # --- Status per-halaman: jeda & kegagalan (ContentMonitor Tahap 2) ---- #

    async def set_page_paused(self, url: str, paused: bool) -> None:
        """Setel status jeda sebuah halaman secara individual (Tahap 2).

        Berbeda dengan jeda per website, jeda per halaman disimpan pada tabel
        ``page_state`` (bukan ``website_config``) karena halaman ditemukan
        otomatis dan tidak memiliki baris konfigurasi tersendiri. Baris
        ``page_state`` dibuat bila belum ada (``INSERT ... ON CONFLICT``);
        ``website_id`` diperlukan untuk baris baru sehingga method ini
        menerima parameter tambahan lewat pencarian Snapshot terbaru bila
        ``website_id`` belum diketahui pemanggil — namun untuk kesederhanaan
        API, pemanggil pada rute web SELALU sudah mengetahui ``website_id``
        dari daftar ``list_pages`` dan cukup memanggil helper ini dengan URL.
        Bila baris belum ada dan ``website_id`` tidak diketahui, method ini
        akan mencoba menemukannya dari ``snapshot`` sehingga panggilan tetap
        idempoten dan tidak gagal.
        """
        conn = self._require_conn()
        async with self._write_lock:
            async with conn.execute(
                "SELECT website_id FROM page_state WHERE url = ?", (url,)
            ) as cur:
                row = await cur.fetchone()
            website_id = row[0] if row is not None else None
            if website_id is None:
                async with conn.execute(
                    "SELECT website_id FROM snapshot WHERE url = ? LIMIT 1",
                    (url,),
                ) as cur:
                    snap_row = await cur.fetchone()
                website_id = snap_row[0] if snap_row is not None else ""
            await conn.execute(
                "INSERT INTO page_state (url, website_id, paused) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET paused=excluded.paused",
                (url, website_id, 1 if paused else 0),
            )
            await conn.commit()

    async def record_page_error(
        self, url: str, website_id: str, reason: str
    ) -> None:
        """Catat kegagalan pemeriksaan sebuah halaman (status "Broken", Tahap 2).

        Menyetel ``last_error`` + ``last_error_at`` (waktu saat ini) pada
        baris ``page_state`` (dibuat bila belum ada). Dipanggil oleh
        ``CheckOrchestrator`` saat sebuah halaman gagal diambil; kegagalan
        menyimpan catatan ini TIDAK BOLEH menghentikan siklus pemeriksaan —
        pemanggil bertanggung jawab membungkusnya dengan try/except (selaras
        pola kegagalan non-fatal lain pada Repository).
        """
        await self._execute_write(
            "INSERT INTO page_state (url, website_id, paused, last_error, "
            "last_error_at) VALUES (?, ?, 0, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET "
            "last_error=excluded.last_error, "
            "last_error_at=excluded.last_error_at",
            (url, website_id, reason, datetime.now().isoformat()),
        )

    async def clear_page_error(self, url: str) -> None:
        """Bersihkan catatan kegagalan sebuah halaman (Tahap 2).

        Dipanggil saat halaman berhasil diambil kembali. Bila belum ada baris
        ``page_state`` untuk URL tersebut, operasi tidak berpengaruh (tidak
        ada error untuk dibersihkan, dan tidak perlu membuat baris baru).
        """
        await self._execute_write(
            "UPDATE page_state SET last_error = NULL, last_error_at = NULL "
            "WHERE url = ?",
            (url,),
        )

    async def get_page_states(
        self, website_id: Optional[str] = None
    ) -> List[PageState]:
        """Kembalikan seluruh baris ``page_state`` (opsional difilter website).

        Hanya mengembalikan URL yang MEMILIKI baris ``page_state`` (pernah
        dijeda atau pernah gagal); URL lain dianggap ``paused=False`` dan
        ``last_error=None`` secara implisit oleh pemanggil (mis. :meth:`list_pages`).
        """
        conn = self._require_conn()
        sql = (
            "SELECT url, website_id, paused, last_error, last_error_at "
            "FROM page_state"
        )
        params: tuple = ()
        if website_id is not None:
            sql += " WHERE website_id = ?"
            params = (website_id,)
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            PageState(
                url=row[0],
                website_id=row[1],
                paused=bool(row[2]),
                last_error=row[3],
                last_error_at=(
                    datetime.fromisoformat(row[4]) if row[4] else None
                ),
            )
            for row in rows
        ]

    async def list_pages(
        self, website_id: Optional[str] = None
    ) -> List[PageOverview]:
        """Kembalikan ringkasan seluruh halaman terpantau (Tahap 2, halaman Pages).

        Menggabungkan tiga sumber:

        - URL unik + ``website_id`` + waktu pemeriksaan terakhir dari
          ``snapshot`` (``MAX(checked_at)`` per URL, satu query agregat);
        - waktu perubahan terakhir dari ``change_event`` (``MAX(detected_at)``
          per URL, satu query agregat);
        - status jeda/kegagalan dari ``page_state`` (:meth:`get_page_states`).

        ``WebsiteConfig`` lengkap disertakan pada setiap baris (bukan hanya
        ``website_id``) sehingga status "Paused" akibat website dijeda dapat
        ditentukan tanpa query tambahan per baris (``PageOverview.is_paused``).
        Halaman milik website yang sudah dihapus dilewati (tidak mungkin
        ditampilkan tanpa nama/domain yang valid).
        """
        conn = self._require_conn()

        sql = (
            "SELECT url, website_id, MAX(checked_at) FROM snapshot AS s"
        )
        params: tuple = ()
        if website_id is not None:
            sql += " WHERE website_id = ?"
            params = (website_id,)
        sql += " GROUP BY url, website_id"
        async with conn.execute(sql, params) as cur:
            snap_rows = await cur.fetchall()

        if not snap_rows:
            return []

        change_sql = "SELECT url, MAX(detected_at) FROM change_event"
        change_params: tuple = ()
        if website_id is not None:
            change_sql += " WHERE website_id = ?"
            change_params = (website_id,)
        change_sql += " GROUP BY url"
        async with conn.execute(change_sql, change_params) as cur:
            change_rows = await cur.fetchall()
        last_change_by_url = {
            row[0]: (datetime.fromisoformat(row[1]) if row[1] else None)
            for row in change_rows
        }

        page_states = await self.get_page_states(website_id=website_id)
        state_by_url = {ps.url: ps for ps in page_states}

        websites = await self.list_websites()
        website_by_id = {w.id: w for w in websites}

        pages: List[PageOverview] = []
        for url, wid, checked_at in snap_rows:
            website = website_by_id.get(wid)
            if website is None:
                # Website sudah dihapus: tidak ada nama/domain valid, lewati.
                continue
            state = state_by_url.get(url)
            pages.append(
                PageOverview(
                    url=url,
                    website=website,
                    last_checked_at=(
                        datetime.fromisoformat(checked_at) if checked_at else None
                    ),
                    last_change_at=last_change_by_url.get(url),
                    page_paused=state.paused if state is not None else False,
                    last_error=state.last_error if state is not None else None,
                    last_error_at=(
                        state.last_error_at if state is not None else None
                    ),
                )
            )
        return pages

    # --- Pemuatan data saat restart (task 8.4) ---------------------------- #

    async def load_latest_snapshots(self) -> Tuple[Dict[str, Snapshot], List[str]]:
        """Muat Snapshot paling baru untuk setiap halaman (Req 11.3, 11.6).

        Mengembalikan pasangan ``(snapshots, load_errors)`` di mana:

        - ``snapshots`` adalah pemetaan ``url -> Snapshot`` berisi Snapshot
          dengan ``checked_at`` terbaru untuk setiap URL berbeda (Req 11.3);
        - ``load_errors`` adalah daftar pesan kesalahan pemuatan.

        Ketahanan terhadap kegagalan/kerusakan data (Req 11.6):

        - Bila kueri ke Data_Store gagal (mis. error I/O), fungsi tidak
          mengangkat pengecualian melainkan mengembalikan snapshot kosong
          beserta satu pesan kesalahan pemuatan.
        - Bila sebuah baris rusak (JSON ``links_json``/``image_hashes_json``
          tidak valid, atau ``checked_at`` tidak dapat diurai), baris tersebut
          dilewati dan kesalahannya dicatat; baris valid lain tetap dimuat
          sehingga operasi berlanjut tanpa berhenti.

        Snapshot terbaru per URL dipilih memakai subkueri ``MAX(checked_at)``;
        karena PRIMARY KEY ``(url, checked_at)``, nilai maksimum tersebut unik
        untuk tiap URL.
        """
        conn = self._require_conn()
        snapshots: Dict[str, Snapshot] = {}
        load_errors: List[str] = []

        try:
            async with conn.execute(
                "SELECT url, website_id, normalized_text, content_hash, "
                "links_json, image_hashes_json, checked_at, sections_json, "
                "title, meta_description "
                "FROM snapshot AS s "
                "WHERE checked_at = ("
                "  SELECT MAX(checked_at) FROM snapshot AS s2 WHERE s2.url = s.url"
                ")"
            ) as cur:
                rows = await cur.fetchall()
        except Exception as exc:  # Kegagalan pemuatan tingkat Data_Store (Req 11.6).
            load_errors.append(
                f"Gagal memuat Snapshot dari Data_Store: {exc}"
            )
            return snapshots, load_errors

        for row in rows:
            url = row[0]
            try:
                snap = Snapshot(
                    url=row[0],
                    website_id=row[1],
                    normalized_text=row[2],
                    content_hash=row[3],
                    checked_at=datetime.fromisoformat(row[6]),
                    links=list(json.loads(row[4])),
                    image_hashes=dict(json.loads(row[5])),
                    sections=_decode_sections(row[7]),
                    title=row[8] if len(row) > 8 else None,
                    meta_description=row[9] if len(row) > 9 else None,
                )
            except Exception as exc:  # Baris rusak dilewati, lanjut (Req 11.6).
                load_errors.append(
                    f"Snapshot rusak untuk URL '{url}' dilewati saat pemuatan: {exc}"
                )
                continue
            snapshots[url] = snap

        return snapshots, load_errors

    async def _load_websites_tolerant(
        self,
    ) -> Tuple[List[WebsiteConfig], List[str]]:
        """Muat Website_Config secara toleran terhadap baris rusak (Req 11.3, 11.6).

        Serupa :meth:`list_websites`, namun kegagalan kueri maupun baris rusak
        (mis. ``created_at`` tidak dapat diurai) tidak menghentikan operasi:
        baris rusak dilewati dan dicatat sebagai kesalahan pemuatan.
        """
        conn = self._require_conn()
        websites: List[WebsiteConfig] = []
        load_errors: List[str] = []

        try:
            async with conn.execute(
                "SELECT id, domain, name, poll_interval_seconds, created_at, "
                "paused "
                "FROM website_config ORDER BY created_at ASC"
            ) as cur:
                rows = await cur.fetchall()
        except Exception as exc:  # Kegagalan pemuatan tingkat Data_Store (Req 11.6).
            load_errors.append(
                f"Gagal memuat Website_Config dari Data_Store: {exc}"
            )
            return websites, load_errors

        for row in rows:
            try:
                websites.append(
                    WebsiteConfig(
                        id=row[0],
                        domain=row[1],
                        name=row[2],
                        poll_interval_seconds=row[3],
                        created_at=datetime.fromisoformat(row[4]),
                        paused=bool(row[5]),
                    )
                )
            except Exception as exc:  # Baris rusak dilewati, lanjut (Req 11.6).
                load_errors.append(
                    f"Website_Config rusak untuk id '{row[0]}' dilewati "
                    f"saat pemuatan: {exc}"
                )

        return websites, load_errors

    async def load_state(self) -> LoadState:
        """Muat seluruh state persisten saat restart (Req 11.3, 11.6).

        Memuat ``Website_Config`` dan Snapshot paling baru per halaman dari
        Data_Store, lalu mengembalikan :class:`LoadState` yang menggabungkan
        keduanya beserta seluruh indikasi kesalahan pemuatan.

        Fungsi ini tidak pernah mengangkat pengecualian akibat data
        gagal-muat/rusak; sebaliknya, kesalahan dikumpulkan pada
        ``LoadState.load_errors`` (dan tercermin pada ``has_errors``) sehingga
        Monitoring_System dapat melanjutkan operasi tanpa berhenti (Req 11.6).
        """
        websites, website_errors = await self._load_websites_tolerant()
        snapshots, snapshot_errors = await self.load_latest_snapshots()
        return LoadState(
            websites=websites,
            snapshots=snapshots,
            load_errors=[*website_errors, *snapshot_errors],
        )

    # --- Alert (ContentMonitor Tahap 3) ------------------------------------ #

    async def save_alert(self, alert: Alert) -> bool:
        """Simpan sebuah Alert baru (ContentMonitor Tahap 3).

        Mengembalikan ``True`` bila berhasil. Bila penyimpanan gagal (mis.
        pelanggaran constraint atau error I/O), transaksi di-rollback dan
        fungsi mengembalikan ``False`` TANPA mengangkat exception ke pemanggil
        (konsisten dengan :meth:`save_snapshot`/:meth:`save_change_event`).
        """
        conn = self._require_conn()
        async with self._write_lock:
            try:
                await conn.execute(
                    "INSERT INTO alert "
                    "(id, website_id, url, alert_type, severity, title, "
                    "detail, triggered_at, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        alert.id,
                        alert.website_id,
                        alert.url,
                        alert.alert_type,
                        alert.severity,
                        alert.title,
                        alert.detail,
                        alert.triggered_at.isoformat(),
                        alert.status,
                    ),
                )
                await conn.commit()
                return True
            except Exception:
                await conn.rollback()
                return False

    @staticmethod
    def _row_to_alert(row) -> Alert:
        return Alert(
            id=row[0],
            website_id=row[1],
            url=row[2],
            alert_type=row[3],
            severity=row[4],
            title=row[5],
            detail=row[6],
            triggered_at=datetime.fromisoformat(row[7]),
            status=row[8],
        )

    async def list_alerts_filtered(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        website_id: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[Alert]:
        """Kembalikan Alert terfilter untuk halaman Alerts (Tahap 3).

        Seluruh filter (``status``, ``severity``, ``website_id``, ``q`` —
        dicocokkan case-insensitive pada ``title``/``detail``/``url`` via
        ``LIKE``) diterapkan langsung di SQL dan dapat dikombinasikan bebas.
        Terurut menurun berdasarkan ``triggered_at`` (terbaru dulu), dibatasi
        ``limit``/``offset``.
        """
        conn = self._require_conn()
        sql = (
            "SELECT id, website_id, url, alert_type, severity, title, "
            "detail, triggered_at, status FROM alert WHERE 1=1"
        )
        params: list = []
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if severity is not None:
            sql += " AND severity = ?"
            params.append(severity)
        if website_id is not None:
            sql += " AND website_id = ?"
            params.append(website_id)
        if q:
            like = "%{0}%".format(q.lower())
            sql += (
                " AND (LOWER(title) LIKE ? OR LOWER(COALESCE(detail, '')) LIKE ? "
                "OR LOWER(COALESCE(url, '')) LIKE ?)"
            )
            params.extend([like, like, like])
        sql += " ORDER BY triggered_at DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        async with conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [self._row_to_alert(row) for row in rows]

    async def count_alerts_filtered(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        website_id: Optional[str] = None,
        q: Optional[str] = None,
    ) -> int:
        """Hitung jumlah Alert terfilter (filter sama seperti
        :meth:`list_alerts_filtered`, tanpa ``limit``/``offset``).
        """
        conn = self._require_conn()
        sql = "SELECT COUNT(*) FROM alert WHERE 1=1"
        params: list = []
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if severity is not None:
            sql += " AND severity = ?"
            params.append(severity)
        if website_id is not None:
            sql += " AND website_id = ?"
            params.append(website_id)
        if q:
            like = "%{0}%".format(q.lower())
            sql += (
                " AND (LOWER(title) LIKE ? OR LOWER(COALESCE(detail, '')) LIKE ? "
                "OR LOWER(COALESCE(url, '')) LIKE ?)"
            )
            params.extend([like, like, like])
        async with conn.execute(sql, tuple(params)) as cur:
            (count,) = await cur.fetchone()
        return int(count)

    async def count_alerts_between(
        self,
        start: datetime,
        end: datetime,
        website_id: Optional[str] = None,
    ) -> int:
        """Hitung jumlah Alert dengan ``triggered_at`` pada rentang
        ``[start, end)`` (kartu KPI "Total Alerts" pada halaman Reports,
        ContentMonitor Tahap 5).

        Rentang bersifat setengah-terbuka, konsisten dengan
        :meth:`count_change_events_between`. Dihitung dengan ``COUNT(*)``
        agregat SQL.
        """
        conn = self._require_conn()
        sql = (
            "SELECT COUNT(*) FROM alert "
            "WHERE triggered_at >= ? AND triggered_at < ?"
        )
        params: tuple = (start.isoformat(), end.isoformat())
        if website_id is not None:
            sql += " AND website_id = ?"
            params = params + (website_id,)
        async with conn.execute(sql, params) as cur:
            (count,) = await cur.fetchone()
        return int(count)

    async def count_alerts_by_status(self) -> Dict[str, int]:
        """Hitung jumlah Alert per ``status`` ('unread'/'read'/'resolved').

        Mengembalikan dict yang HANYA berisi status dengan jumlah > 0 (status
        yang tidak ada baris sama sekali tidak muncul sebagai kunci); pemanggil
        yang membutuhkan bawaan 0 dapat memakai ``dict.get(key, 0)``.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT status, COUNT(*) FROM alert GROUP BY status"
        ) as cur:
            rows = await cur.fetchall()
        return {row[0]: int(row[1]) for row in rows}

    async def count_alerts_by_severity(self) -> Dict[str, int]:
        """Hitung jumlah Alert per ``severity`` ('critical'/'warning'/'info').

        Sama seperti :meth:`count_alerts_by_status`, hanya berisi kunci yang
        memiliki jumlah > 0.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT severity, COUNT(*) FROM alert GROUP BY severity"
        ) as cur:
            rows = await cur.fetchall()
        return {row[0]: int(row[1]) for row in rows}

    async def mark_alert_status(self, alert_id: str, status: str) -> None:
        """Setel ``status`` sebuah Alert ('unread'/'read'/'resolved') (Tahap 3).

        Bila ``alert_id`` tidak ada, operasi tidak berpengaruh (idempoten).
        """
        await self._execute_write(
            "UPDATE alert SET status = ? WHERE id = ?", (status, alert_id)
        )

    async def count_unread_alerts(self) -> int:
        """Hitung jumlah Alert dengan ``status = 'unread'`` (badge sidebar)."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT COUNT(*) FROM alert WHERE status = 'unread'"
        ) as cur:
            (count,) = await cur.fetchone()
        return int(count)

    async def recent_alerts(self, limit: int = 5) -> List[Alert]:
        """Kembalikan Alert terbaru lintas seluruh website (panel "Recent
        Alerts" pada Overview), terurut menurun berdasarkan ``triggered_at``.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, website_id, url, alert_type, severity, title, "
            "detail, triggered_at, status FROM alert "
            "ORDER BY triggered_at DESC LIMIT ?",
            (int(limit),),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_alert(row) for row in rows]

    async def iter_alerts_for_export(
        self,
        start: datetime,
        end: datetime,
        website_id: Optional[str] = None,
        batch_size: int = 500,
    ):
        """Hasilkan Alert dengan ``triggered_at`` pada rentang ``[start, end)``
        secara BERTAHAP (generator async) untuk ekspor CSV (Tahap 5).

        Serupa :meth:`iter_change_events_for_export`: mengambil per
        ``batch_size`` baris lewat SQL ``LIMIT``/``OFFSET`` agar tidak memuat
        seluruh tabel Alert ke memori sekaligus.
        """
        conn = self._require_conn()
        offset = 0
        while True:
            sql = (
                "SELECT id, website_id, url, alert_type, severity, title, "
                "detail, triggered_at, status FROM alert "
                "WHERE triggered_at >= ? AND triggered_at < ?"
            )
            params: list = [start.isoformat(), end.isoformat()]
            if website_id is not None:
                sql += " AND website_id = ?"
                params.append(website_id)
            sql += " ORDER BY triggered_at DESC LIMIT ? OFFSET ?"
            params.extend([int(batch_size), int(offset)])
            async with conn.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
            if not rows:
                return
            for row in rows:
                yield self._row_to_alert(row)
            offset += len(rows)
            if len(rows) < batch_size:
                return

    # --- Keyword & Keyword State (ContentMonitor Tahap 4) ------------------- #

    def _row_to_keyword(self, row: Any) -> Keyword:
        """Konversi baris SQL menjadi dataclass Keyword."""
        return Keyword(
            id=str(row[0]),
            website_id=str(row[1]),
            keyword=str(row[2]),
            mode=str(row[3]),
            created_at=datetime.fromisoformat(str(row[4])),
        )

    async def add_keyword(
        self,
        id: str,
        website_id: str,
        keyword: str,
        mode: str = "must_exist",
        created_at: Optional[datetime] = None,
    ) -> Keyword:
        """Tambahkan kata kunci pemantauan pada website terkait."""
        if mode not in ("must_exist", "must_not_exist"):
            raise ValueError(
                f"mode kata kunci tidak valid: {mode!r} "
                "(harus 'must_exist' atau 'must_not_exist')"
            )
        kw_id = str(id)
        web_id = str(website_id)
        kw_text = str(keyword).strip()
        kw_mode = str(mode)
        kw_created = (created_at or datetime.now()).isoformat()

        await self._execute_write(
            "INSERT INTO keyword (id, website_id, keyword, mode, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(website_id, keyword) DO UPDATE SET mode=excluded.mode",
            (kw_id, web_id, kw_text, kw_mode, kw_created),
        )
        return Keyword(
            id=kw_id,
            website_id=web_id,
            keyword=kw_text,
            mode=kw_mode,
            created_at=datetime.fromisoformat(kw_created),
        )

    async def remove_keyword(self, keyword_id: str) -> bool:
        """Hapus kata kunci beserta state pemantauannya."""
        conn = self._require_conn()
        try:
            async with self._write_lock:
                await conn.execute(
                    "DELETE FROM keyword_state WHERE keyword_id = ?",
                    (str(keyword_id),),
                )
                cur = await conn.execute(
                    "DELETE FROM keyword WHERE id = ?",
                    (str(keyword_id),),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
                return bool(deleted)
        except Exception:
            await conn.rollback()
            return False

    async def list_keywords(self, website_id: Optional[str] = None) -> List[Keyword]:
        """Ambil daftar kata kunci, opsional disaring per website."""
        conn = self._require_conn()
        query = (
            "SELECT id, website_id, keyword, mode, created_at "
            "FROM keyword"
        )
        params: Tuple[Any, ...] = ()
        if website_id is not None:
            query += " WHERE website_id = ?"
            params = (str(website_id),)
        query += " ORDER BY created_at ASC"

        async with conn.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_keyword(row) for row in rows]

    async def get_keyword_state(
        self, website_id: str, url: str, keyword_id: str
    ) -> Optional[str]:
        """Ambil status terakhir kata kunci pada sebuah halaman (untuk dedup alert)."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT last_status FROM keyword_state "
            "WHERE website_id = ? AND url = ? AND keyword_id = ?",
            (str(website_id), str(url), str(keyword_id)),
        ) as cur:
            row = await cur.fetchone()
        return str(row[0]) if row is not None else None

    async def set_keyword_state(
        self, website_id: str, url: str, keyword_id: str, last_status: str
    ) -> None:
        """Simpan status terakhir kata kunci pada halaman (untuk dedup alert)."""
        await self._execute_write(
            "INSERT INTO keyword_state (website_id, url, keyword_id, last_status) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(website_id, url, keyword_id) DO UPDATE SET "
            "last_status=excluded.last_status",
            (str(website_id), str(url), str(keyword_id), str(last_status)),
        )

    # --- Retensi & Prune Riwayat Perubahan (ContentMonitor Tahap 4) --------- #

    async def prune_change_events(self, older_than_days: int = 90) -> int:
        """Hapus Change_Event yang lebih lama dari ``older_than_days`` hari (Req Tahap 4).

        HANYA baris dari tabel ``change_event`` yang dihapus. Tabel lain seperti
        ``snapshot``, ``website_config``, maupun ``alert`` sama sekali tidak
        disentuh.
        """
        conn = self._require_conn()
        days = max(1, int(older_than_days))
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            async with self._write_lock:
                cursor = await conn.execute(
                    "DELETE FROM change_event WHERE detected_at < ?",
                    (cutoff,),
                )
                deleted = cursor.rowcount
                await conn.commit()
                return int(deleted) if deleted is not None and deleted >= 0 else 0
        except Exception:
            await conn.rollback()
            return 0

    # --- Query agregasi untuk Reports (ContentMonitor Tahap 5) ------------ #

    async def most_active_website_between(
        self, start: datetime, end: datetime
    ) -> Optional[Tuple[str, int]]:
        """Kembalikan ``(website_id, jumlah_perubahan)`` paling aktif pada
        rentang ``[start, end)``, atau ``None`` bila tidak ada Change_Event
        sama sekali pada rentang tersebut.

        Dihitung dengan ``GROUP BY`` + ``ORDER BY COUNT(*) DESC LIMIT 1``
        (agregat SQL, tidak memuat baris Change_Event mentah).
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT website_id, COUNT(*) AS cnt FROM change_event "
            "WHERE detected_at >= ? AND detected_at < ? "
            "GROUP BY website_id ORDER BY cnt DESC LIMIT 1",
            (start.isoformat(), end.isoformat()),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return (row[0], int(row[1]))

    async def count_change_events_total_by_website(self) -> Dict[str, int]:
        """Kembalikan pemetaan ``website_id -> jumlah Change_Event`` SELURUH
        waktu (untuk kolom ``changes_total`` pada ekspor CSV "websites").

        Dihitung dengan satu query agregat (``GROUP BY``) untuk seluruh
        website sekaligus.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT website_id, COUNT(*) FROM change_event GROUP BY website_id"
        ) as cur:
            rows = await cur.fetchall()
        return {row[0]: int(row[1]) for row in rows}

    # --- Riwayat Laporan CSV (ContentMonitor Tahap 5) ---------------------- #

    async def save_report(self, report: Report) -> bool:
        """Simpan metadata sebuah laporan CSV yang baru dihasilkan.

        Mengembalikan ``True`` bila berhasil; kegagalan (mis. error I/O)
        di-rollback dan mengembalikan ``False`` TANPA mengangkat exception
        (konsisten dengan :meth:`save_snapshot`/:meth:`save_alert`).
        """
        conn = self._require_conn()
        async with self._write_lock:
            try:
                await conn.execute(
                    "INSERT INTO report "
                    "(id, name, report_type, period_start, period_end, "
                    "created_at, summary, row_count, file_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        report.id,
                        report.name,
                        report.report_type,
                        report.period_start,
                        report.period_end,
                        report.created_at.isoformat(),
                        report.summary,
                        int(report.row_count),
                        report.file_path,
                    ),
                )
                await conn.commit()
                return True
            except Exception:
                await conn.rollback()
                return False

    @staticmethod
    def _row_to_report(row) -> Report:
        return Report(
            id=row[0],
            name=row[1],
            report_type=row[2],
            period_start=row[3],
            period_end=row[4],
            created_at=datetime.fromisoformat(row[5]),
            summary=row[6],
            row_count=int(row[7]),
            file_path=row[8],
        )

    async def list_reports(self) -> List[Report]:
        """Kembalikan seluruh Riwayat Laporan, terurut menurun berdasarkan
        ``created_at`` (terbaru dulu), untuk tabel "Riwayat Laporan".
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, name, report_type, period_start, period_end, "
            "created_at, summary, row_count, file_path FROM report "
            "ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_report(row) for row in rows]

    async def get_report(self, report_id: str) -> Optional[Report]:
        """Ambil satu laporan berdasarkan ``id``, atau ``None`` bila tidak ada."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, name, report_type, period_start, period_end, "
            "created_at, summary, row_count, file_path FROM report "
            "WHERE id = ?",
            (report_id,),
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_report(row) if row is not None else None

    async def delete_report(self, report_id: str) -> bool:
        """Hapus baris metadata laporan (TIDAK menghapus berkas CSV -- itu
        tanggung jawab pemanggil di lapisan web, sebelum/sesudah memanggil
        method ini). Mengembalikan ``True`` bila sebuah baris terhapus.
        """
        conn = self._require_conn()
        try:
            async with self._write_lock:
                cursor = await conn.execute(
                    "DELETE FROM report WHERE id = ?", (report_id,)
                )
                deleted = cursor.rowcount
                await conn.commit()
                return bool(deleted and deleted > 0)
        except Exception:
            await conn.rollback()
            return False

    # --- Helper app_config minimal (berguna untuk smoke test persistensi) ---

    async def set_app_config(self, key: str, value: str) -> None:
        """Simpan/timpa sebuah pasangan konfigurasi aplikasi (Req 11.1)."""
        await self._execute_write(
            "INSERT INTO app_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def get_app_config(self, key: str) -> Optional[str]:
        """Ambil nilai konfigurasi aplikasi berdasarkan ``key``.

        Kembalikan ``None`` bila kunci tidak ditemukan.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT value FROM app_config WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return row[0]

    # --- Akun pengguna dashboard (fitur login multi-user) ---------------- #

    async def add_user(
        self, username: str, password_hash: str
    ) -> "AppUser":
        """Buat akun pengguna baru dengan ``password_hash`` yang sudah dihitung.

        Pemanggil bertanggung jawab menghasilkan ``password_hash`` (lihat
        ``monitoring.web.auth.hash_password``); repository TIDAK pernah
        menyentuh password mentah. Username unik (case-sensitive di level DB
        via batasan UNIQUE); duplikat mengangkat :class:`DuplicateUserError`.
        """
        conn = self._require_conn()
        user = AppUser(
            id=str(uuid4()),
            username=username,
            password_hash=password_hash,
            created_at=datetime.now(),
        )
        async with self._write_lock:
            async with conn.execute(
                "SELECT 1 FROM app_user WHERE username = ?", (username,)
            ) as cur:
                if await cur.fetchone() is not None:
                    raise DuplicateUserError(username)
            await conn.execute(
                "INSERT INTO app_user (id, username, password_hash, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    user.id,
                    user.username,
                    user.password_hash,
                    user.created_at.isoformat(),
                ),
            )
            await conn.commit()
        return user

    async def get_user_by_username(self, username: str) -> Optional["AppUser"]:
        """Ambil akun pengguna berdasarkan username; ``None`` bila tidak ada."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, username, password_hash, created_at "
            "FROM app_user WHERE username = ?",
            (username,),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_user(row) if row is not None else None

    async def list_users(self) -> List["AppUser"]:
        """Kembalikan seluruh akun pengguna terurut berdasarkan username."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, username, password_hash, created_at "
            "FROM app_user ORDER BY username"
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_user(row) for row in rows]

    async def count_users(self) -> int:
        """Hitung jumlah akun pengguna terdaftar."""
        conn = self._require_conn()
        async with conn.execute("SELECT COUNT(*) FROM app_user") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def remove_user(self, username: str) -> bool:
        """Hapus akun pengguna; kembalikan ``True`` bila ada yang terhapus."""
        conn = self._require_conn()
        async with self._write_lock:
            cursor = await conn.execute(
                "DELETE FROM app_user WHERE username = ?", (username,)
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def update_user_password(
        self, username: str, password_hash: str
    ) -> bool:
        """Perbarui hash password akun; ``True`` bila akun ditemukan & diubah."""
        conn = self._require_conn()
        async with self._write_lock:
            cursor = await conn.execute(
                "UPDATE app_user SET password_hash = ? WHERE username = ?",
                (password_hash, username),
            )
            await conn.commit()
            return cursor.rowcount > 0

    # --- Protokol async context manager ---

    async def __aenter__(self) -> "Repository":
        return await self.connect()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
