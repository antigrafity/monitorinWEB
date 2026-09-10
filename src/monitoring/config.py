"""Konstanta konfigurasi dan logika validasi untuk Monitoring_System.

Modul ini berisi konstanta level modul serta fungsi validasi konfigurasi.
Validasi Polling_Interval akan ditambahkan pada task berikutnya sesuai design.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

# --- Polling_Interval per-website (detik) --- Req 1.6, 1.9
POLL_MIN_SECONDS = 10
POLL_MAX_SECONDS = 86400

# --- Polling_Interval global (menit) --- Req 8.5, 8.6
POLL_MIN_MINUTES = 1
POLL_MAX_MINUTES = 1440

# --- Nilai bawaan Polling_Interval global --- Req 8.3 (6 jam)
# 6 jam = 21600 detik = 360 menit (masih dalam rentang POLL_MIN_MINUTES..
# POLL_MAX_MINUTES). Interval yang panjang dipilih sebagai bawaan karena
# pemeriksaan otomatis bersifat latar belakang; pemeriksaan segera dapat
# dilakukan kapan pun lewat tombol "Cek Sekarang" pada Dashboard.
DEFAULT_GLOBAL_INTERVAL_SECONDS = 21600

# --- Batas konkurensi Fetcher (permintaan bersamaan) --- Req 3.1
FETCH_CONCURRENCY_MIN = 1
FETCH_CONCURRENCY_MAX = 100
DEFAULT_FETCH_CONCURRENCY = 10

# --- Batas waktu Fetcher per HTTP request (detik) --- Req 3.5
FETCH_TIMEOUT_MIN_SECONDS = 1
FETCH_TIMEOUT_MAX_SECONDS = 300
DEFAULT_FETCH_TIMEOUT_SECONDS = 30

# --- Unduhan gambar --- Req 7.1, 7.5
# Batas waktu per unduhan gambar (detik), batas ukuran maksimum (byte), dan
# jumlah percobaan maksimum (total tries) sebelum menandai URL gambar gagal.
IMAGE_TIMEOUT_SECONDS = 30
IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
IMAGE_MAX_RETRIES = 3

# --- Page_Discovery --- Req 2.2, 2.6
# Kedalaman maksimum penelusuran link internal dari halaman utama (homepage
# = kedalaman 0). URL yang berada lebih dalam dari batas ini tidak disertakan.
MAX_CRAWL_DEPTH = 5
# Batas jumlah URL halaman yang dikumpulkan per Monitored_Website. Setelah
# batas tercapai, penemuan halaman lebih lanjut dihentikan (Req 2.6).
MAX_URLS_PER_WEBSITE = 5000
# Kandidat path sitemap yang dicoba (minimal /sitemap.xml) (Req 2.1).
SITEMAP_PATHS = ("/sitemap.xml",)

# --- Notifikasi Telegram --- Req 9.1, 9.3, 9.4
# Jumlah percobaan maksimum pengiriman notifikasi (total tries) dan jeda
# minimal antar percobaan (detik). Bila seluruh percobaan gagal, Notifier
# mencatat "tidak terkirim" dan mempertahankan Change_Event (Req 9.4).
NOTIFY_MAX_ATTEMPTS = 3
NOTIFY_RETRY_DELAY_SECONDS = 5

# --- Kapasitas Monitored_Website --- Req 1.5, 1.8
MAX_WEBSITES = 100

# --- Retensi Snapshot per halaman --- Req 5.4, 11.4
# Jumlah Snapshot terbaru yang dipertahankan untuk setiap URL. Setiap
# pemeriksaan menambah satu baris Snapshot per halaman, sehingga tanpa retensi
# ukuran Data_Store bertambah tanpa batas. Snapshot yang lebih lama dari
# ``SNAPSHOT_RETENTION_PER_URL`` terbaru dipangkas; Snapshot terbaru sebuah URL
# SELALU dipertahankan agar baseline perbandingan tidak pernah hilang (Req 5.4)
# dan riwayat Change_Event tidak pernah disentuh oleh pemangkasan (Req 11.4).
SNAPSHOT_RETENTION_PER_URL = 5

# --- Batas panjang domain --- Req 1.4
DOMAIN_MIN_LEN = 1
DOMAIN_MAX_LEN = 253

# --- Ambang Alert (ContentMonitor Tahap 3) -------------------------------- #
# Jumlah blok teks dihapus minimum agar dianggap "penghapusan konten
# signifikan" (severity 'warning'). Alert juga dipicu bila proporsi blok
# halaman yang hilang mencapai SIGNIFICANT_REMOVAL_RATIO (50%), yang mana pun
# tercapai lebih dulu.
SIGNIFICANT_REMOVAL_BLOCKS = 5
SIGNIFICANT_REMOVAL_RATIO = 0.5

# Ambang hari tersisa sebelum sertifikat SSL kadaluarsa untuk memicu Alert.
# < SSL_EXPIRY_CRITICAL_DAYS -> severity 'critical'; selain itu (masih
# < SSL_EXPIRY_WARNING_DAYS) -> severity 'warning'.
SSL_EXPIRY_WARNING_DAYS = 30
SSL_EXPIRY_CRITICAL_DAYS = 7


class ValidationResult(NamedTuple):
    """Hasil validasi konfigurasi.

    ``ok`` menandakan apakah masukan valid. ``error_message`` berisi
    penjelasan format yang diharapkan bila masukan ditolak, dan ``None``
    bila masukan valid.
    """

    ok: bool
    error_message: Optional[str] = None


# Pesan kesalahan yang menjelaskan format domain yang diharapkan (Req 1.4).
DOMAIN_FORMAT_ERROR = (
    "Format domain tidak valid: domain harus sepanjang "
    f"{DOMAIN_MIN_LEN}\u2013{DOMAIN_MAX_LEN} karakter, terdiri atas label "
    "tak-kosong yang dipisahkan tanda titik, dan setiap label hanya boleh "
    "berisi huruf, angka, atau tanda hubung."
)


def validate_domain(domain: str) -> ValidationResult:
    """Validasi format domain sesuai Req 1.4 / Property 1.

    Sebuah domain dianggap valid jika dan hanya jika:

    - panjangnya ``DOMAIN_MIN_LEN``..``DOMAIN_MAX_LEN`` (1\u2013253) karakter;
    - tersusun atas label tak-kosong yang dipisahkan tanda titik
      (tidak ada label kosong akibat titik di awal/akhir atau titik ganda);
    - setiap label hanya berisi huruf (A\u2013Z, a\u2013z), angka (0\u20139),
      atau tanda hubung ("-").

    Bila tidak valid, kembalikan ``ValidationResult`` dengan ``ok=False`` dan
    pesan kesalahan yang menjelaskan format yang diharapkan.
    """
    if not isinstance(domain, str):
        return ValidationResult(False, DOMAIN_FORMAT_ERROR)

    if len(domain) < DOMAIN_MIN_LEN or len(domain) > DOMAIN_MAX_LEN:
        return ValidationResult(False, DOMAIN_FORMAT_ERROR)

    labels = domain.split(".")
    for label in labels:
        if not label:
            # Label kosong: titik di awal/akhir atau titik berturut-turut.
            return ValidationResult(False, DOMAIN_FORMAT_ERROR)
        for ch in label:
            if not (ch.isascii() and (ch.isalnum() or ch == "-")):
                return ValidationResult(False, DOMAIN_FORMAT_ERROR)

    return ValidationResult(True, None)


# Pesan kesalahan Polling_Interval per-website dalam detik (Req 1.9).
POLL_INTERVAL_SECONDS_ERROR = (
    "Polling_Interval tidak valid: nilai harus berupa bilangan bulat dalam "
    f"rentang {POLL_MIN_SECONDS}\u2013{POLL_MAX_SECONDS} detik. Nilai ditolak "
    "dan Polling_Interval sebelumnya dipertahankan."
)

# Pesan kesalahan Polling_Interval global dalam menit (Req 8.5, 8.6).
POLL_INTERVAL_MINUTES_ERROR = (
    "Polling_Interval tidak valid: nilai harus berupa bilangan bulat dalam "
    f"rentang {POLL_MIN_MINUTES}\u2013{POLL_MAX_MINUTES} menit. Nilai ditolak "
    "dan Polling_Interval sebelumnya dipertahankan."
)


def _is_valid_integer(value: object) -> bool:
    """Kembalikan True hanya bila ``value`` berupa bilangan bulat sejati.

    Catatan penting: dalam Python ``bool`` merupakan subclass ``int``
    (``True == 1``, ``False == 0``). Untuk keperluan validasi Polling_Interval,
    nilai boolean TIDAK dianggap sebagai bilangan bulat yang valid dan harus
    ditolak (Req 8.6). Nilai ``float`` (termasuk yang berupa bilangan bulat
    seperti ``30.0``), string, ``None``, dan tipe lain juga ditolak.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def validate_poll_interval_seconds(value: object) -> ValidationResult:
    """Validasi Polling_Interval khusus per-website dalam detik (Req 1.6, 1.9).

    Menerima ``value`` jika dan hanya jika ia berupa bilangan bulat sejati
    (bukan ``bool``, bukan ``float``, bukan string) dalam rentang
    ``POLL_MIN_SECONDS``..``POLL_MAX_SECONDS`` (10..86400), inklusif.

    Bila ditolak, kembalikan ``ValidationResult`` dengan ``ok=False`` dan pesan
    kesalahan; pemanggil bertanggung jawab mempertahankan Polling_Interval
    sebelumnya (Req 1.9).
    """
    if not _is_valid_integer(value):
        return ValidationResult(False, POLL_INTERVAL_SECONDS_ERROR)
    if value < POLL_MIN_SECONDS or value > POLL_MAX_SECONDS:
        return ValidationResult(False, POLL_INTERVAL_SECONDS_ERROR)
    return ValidationResult(True, None)


def validate_poll_interval_minutes(value: object) -> ValidationResult:
    """Validasi Polling_Interval global dalam menit (Req 8.5, 8.6).

    Menerima ``value`` jika dan hanya jika ia berupa bilangan bulat sejati
    (bukan ``bool``, bukan ``float``, bukan string) dalam rentang
    ``POLL_MIN_MINUTES``..``POLL_MAX_MINUTES`` (1..1440), inklusif.

    Bila ditolak, kembalikan ``ValidationResult`` dengan ``ok=False`` dan pesan
    kesalahan; pemanggil bertanggung jawab mempertahankan Polling_Interval
    sebelumnya (Req 8.5, 8.6).
    """
    if not _is_valid_integer(value):
        return ValidationResult(False, POLL_INTERVAL_MINUTES_ERROR)
    if value < POLL_MIN_MINUTES or value > POLL_MAX_MINUTES:
        return ValidationResult(False, POLL_INTERVAL_MINUTES_ERROR)
    return ValidationResult(True, None)
