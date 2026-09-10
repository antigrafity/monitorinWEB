"""Model domain untuk Monitoring_System.

Berisi dataclass murni (``frozen=True``) yang merepresentasikan entitas inti
sesuai bagian *Data Models* pada design: ``WebsiteConfig``, ``Snapshot``,
``Diff``, ``ChangeEvent``, dan ``ChangeSummary``.

Catatan kompatibilitas: target runtime adalah Python 3.9, sehingga sintaks
union PEP 604 (``str | None``) tidak dievaluasi secara eager. Modul memakai
``from __future__ import annotations`` agar anotasi diperlakukan sebagai string
dan menggunakan ``typing.Optional`` untuk kejelasan.

Serialisasi ke/dari JSON sengaja TIDAK diimplementasikan di sini; itu adalah
task terpisah (2.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class WebsiteConfig:
    """Konfigurasi sebuah Monitored_Website (Req 1.4, 8.2)."""

    id: str  # UUID
    domain: str  # domain tervalidasi (Req 1.4)
    name: str  # nama tampilan Monitored_Website
    # None -> pakai Polling_Interval global (Req 8.2)
    poll_interval_seconds: Optional[int]
    created_at: datetime
    # True -> pemantauan website ini dijeda; Scheduler tidak menjadwalkannya
    # (ContentMonitor Tahap 1, status Aktif/Jeda per website).
    paused: bool = False


@dataclass(frozen=True)
class Snapshot:
    """Representasi tersimpan konten sebuah halaman pada satu waktu (Req 5.2, 5.3).

    ``links`` dan ``image_hashes`` diberi default kosong (``[]`` dan ``{}``)
    sehingga halaman tanpa link/gambar tetap direpresentasikan sebagai koleksi
    kosong, bukan ``None`` (Req 5.2).

    Bidang dengan default diletakkan setelah bidang tanpa default agar sesuai
    aturan dataclass; secara semantik urutan ini setara dengan design.
    """

    url: str
    website_id: str
    normalized_text: str
    content_hash: str
    checked_at: datetime  # timestamp tanggal & waktu pemeriksaan (Req 5.3)
    links: List[str] = field(default_factory=list)  # [] bila tidak ada (Req 5.2)
    # url -> image_hash; {} bila tidak ada (Req 5.2)
    image_hashes: Dict[str, str] = field(default_factory=dict)
    # Blok section (hasil ``Section.as_block()``) pada saat Snapshot diambil.
    # Dipersist bersama Snapshot sehingga diff section dapat dihitung antar
    # pemeriksaan (Req 6.4); [] bila halaman tidak memiliki heading.
    sections: List[str] = field(default_factory=list)
    # Judul halaman (``<title>``) dan meta description pada saat Snapshot
    # diambil (ContentMonitor Tahap 3). ``None`` bila halaman tidak memiliki
    # elemen tersebut. Dipersist bersama Snapshot agar perubahan title/meta
    # dapat dideteksi antar pemeriksaan tanpa menyimpan ulang HTML mentah.
    title: Optional[str] = None
    meta_description: Optional[str] = None


@dataclass(frozen=True)
class Diff:
    """Deskripsi terstruktur perbedaan antara dua Snapshot (Req 6.2, 6.3, 6.4, 7.3, 7.4)."""

    text_added: List[str] = field(default_factory=list)
    text_removed: List[str] = field(default_factory=list)
    links_added: List[str] = field(default_factory=list)
    links_removed: List[str] = field(default_factory=list)
    sections_added: List[str] = field(default_factory=list)
    sections_removed: List[str] = field(default_factory=list)
    images_added: List[str] = field(default_factory=list)
    images_removed: List[str] = field(default_factory=list)
    # URL sama, tetapi Image_Hash berbeda (Req 7.4)
    images_changed: List[str] = field(default_factory=list)
    # Judul halaman (<title>) berubah: pasangan (judul_lama, judul_baru).
    # ``None`` bila judul tidak berubah (ContentMonitor Tahap 3).
    title_changed: Optional[Tuple[str, str]] = None
    # Meta description berubah: pasangan (meta_lama, meta_baru). ``None`` bila
    # meta description tidak berubah (ContentMonitor Tahap 3).
    meta_changed: Optional[Tuple[str, str]] = None


@dataclass(frozen=True)
class ChangeSummary:
    """Ringkasan jumlah perubahan untuk notifikasi (Req 9.2)."""

    text_added: int
    text_removed: int
    links_added: int
    links_removed: int
    images_changed: int


@dataclass(frozen=True)
class ChangeEvent:
    """Catatan yang dibuat ketika Change_Detector menemukan perubahan (Req 6.5)."""

    id: str
    website_id: str
    url: str
    detected_at: datetime  # waktu deteksi (Req 6.5)
    diff: Diff
    summary: ChangeSummary


@dataclass(frozen=True)
class Alert:
    """Sebuah Alert yang dibangkitkan dari kondisi pemantauan (ContentMonitor
    Tahap 3): halaman gagal diakses, penghapusan konten signifikan, judul/meta
    berubah, struktur heading berubah, konten baru, sitemap tak dapat diakses,
    atau SSL akan kadaluarsa.

    Attributes:
        id: UUID Alert.
        website_id: ID Monitored_Website terkait.
        url: URL halaman terkait bila relevan (mis. sitemap-level alert
            tidak memiliki URL halaman spesifik -> ``None``).
        alert_type: Kategori Alert (mis. "page_unreachable",
            "significant_removal", "title_changed", "meta_changed",
            "heading_structure_changed", "new_content", "sitemap_unreachable",
            "ssl_expiring").
        severity: ``'critical'`` | ``'warning'`` | ``'info'``.
        title: Judul singkat Alert (Bahasa Indonesia) untuk ditampilkan.
        detail: Rincian tambahan (opsional), mis. kode status atau nilai
            lama/baru.
        triggered_at: Waktu Alert dibangkitkan.
        status: ``'unread'`` | ``'read'`` | ``'resolved'``; bawaan ``'unread'``.
    """

    id: str
    website_id: str
    alert_type: str
    severity: str
    title: str
    triggered_at: datetime
    url: Optional[str] = None
    detail: Optional[str] = None
    status: str = "unread"


@dataclass(frozen=True)
class Keyword:
    """Kata kunci yang dipantau pada sebuah Monitored_Website (ContentMonitor Tahap 4).

    Attributes:
        id: UUID Kata kunci.
        website_id: ID Monitored_Website terkait.
        keyword: Teks kata kunci yang dipantau (pencocokan case-insensitive).
        mode: ``'must_exist'`` (alert kalau HILANG) | ``'must_not_exist'``
            (alert kalau MUNCUL). Bawaan ``'must_exist'``.
        created_at: Timestamp kapan kata kunci ditambahkan.
    """

    id: str
    website_id: str
    keyword: str
    created_at: datetime
    mode: str = "must_exist"


@dataclass(frozen=True)
class Report:
    """Metadata sebuah laporan CSV yang telah dihasilkan (ContentMonitor Tahap 5).

    Baris ``report`` mencatat riwayat laporan yang sudah pernah dibuat via
    ``POST /reports/generate`` sehingga dapat diunduh ulang atau dihapus dari
    halaman Reports tanpa perlu membuat ulang berkasnya.

    Attributes:
        id: UUID laporan.
        name: Nama tampilan laporan (Bahasa Indonesia), mis. "Laporan
            Perubahan 2026-07-01 s/d 2026-07-07".
        report_type: ``'changes'`` | ``'alerts'`` | ``'websites'``.
        period_start: Tanggal awal periode laporan (format "YYYY-MM-DD").
        period_end: Tanggal akhir periode laporan (format "YYYY-MM-DD").
        created_at: Waktu laporan dibuat.
        summary: Ringkasan singkat isi laporan, mis. "56 perubahan".
        row_count: Jumlah baris data (tanpa header) pada berkas CSV.
        file_path: Nama berkas (BUKAN path lengkap) CSV tersimpan di dalam
            direktori ``reports/``. Disimpan sebagai nama berkas saja (sudah
            disanitasi saat dibuat) sehingga path lengkap SELALU direkonstruksi
            ulang relatif terhadap direktori ``reports/`` yang berlaku saat
            diunduh -- mencegah path traversal (lihat ``web/app.py``).
    """

    id: str
    name: str
    report_type: str
    period_start: str
    period_end: str
    created_at: datetime
    summary: str
    row_count: int
    file_path: str


@dataclass(frozen=True)
class KeywordMatch:
    """Hasil pencocokan satu kata kunci terhadap blok teks sebuah halaman.

    Attributes:
        keyword: Objek :class:`Keyword` yang dicocokkan.
        found: True bila kata kunci terdeteksi pada minimal satu blok teks
            halaman; False bila tidak terdeteksi di blok mana pun.
    """

    keyword: Keyword
    found: bool

