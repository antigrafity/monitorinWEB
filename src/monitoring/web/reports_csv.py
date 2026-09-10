"""Helper ekspor CSV & Riwayat Laporan untuk halaman Reports (ContentMonitor
Tahap 5).

Modul ini menyediakan fungsi MURNI (tanpa akses Data_Store) untuk:

- Membangun header & baris CSV untuk 3 jenis ekspor: ``changes``, ``alerts``,
  ``websites`` (format kolom sesuai HANDOFF.md bagian Tahap 5).
- Mitigasi CSV injection: nilai string yang dimulai dengan ``=``, ``+``,
  ``-``, atau ``@`` diawali tanda kutip tunggal (``'``) sehingga TIDAK
  dieksekusi sebagai formula ketika berkas dibuka di Excel/Google Sheets.
- Streaming baris CSV (memakai modul ``csv`` standar, bukan f-string manual)
  agar koma/tanda kutip/newline pada teks (mis. deskripsi diff multi-baris)
  di-escape dengan benar dan tetap valid saat diparse ulang.
- Sanitasi nama berkas & resolusi path yang AMAN terhadap path traversal
  (nama berkas yang tersimpan HARUS berada di dalam direktori ``reports/``).

Ditulis sebagai modul terpisah dari ``web/app.py`` (yang berisi *routing*)
agar logika pembentukan CSV dapat diuji unit tanpa perlu membangun aplikasi
FastAPI penuh.

Python 3.9 (``from __future__ import annotations`` + ``typing``); hanya
memakai pustaka standar (``csv``, ``re``, ``pathlib``) -- TANPA dependensi
baru (Keputusan #3 HANDOFF.md: format laporan CSV saja).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, List, Optional, Sequence

from monitoring.domain.classify import classify_change, describe_change

# Karakter awal yang dapat memicu eksekusi formula pada Excel/Google Sheets
# bila nilai sel CSV dibuka tanpa diawasi (CSV injection / formula injection).
CSV_INJECTION_PREFIXES = ("=", "+", "-", "@")

# Header kolom untuk masing-masing jenis ekspor (urutan sesuai HANDOFF.md).
CHANGES_CSV_HEADER: List[str] = [
    "detected_at",
    "website",
    "domain",
    "url",
    "type",
    "description",
    "text_added_count",
    "text_removed_count",
    "links_added_count",
    "links_removed_count",
    "images_changed_count",
]

ALERTS_CSV_HEADER: List[str] = [
    "triggered_at",
    "website",
    "domain",
    "url",
    "alert_type",
    "severity",
    "status",
    "title",
    "detail",
]

WEBSITES_CSV_HEADER: List[str] = [
    "name",
    "domain",
    "paused",
    "last_checked_at",
    "last_status",
    "pages_monitored",
    "changes_7d",
    "changes_total",
]

# Jenis ekspor/laporan yang didukung.
VALID_REPORT_TYPES = ("changes", "alerts", "websites")


def csv_safe_value(value: Any) -> Any:
    """Cegah CSV injection (mitigasi wajib, HANDOFF.md Tahap 5).

    Nilai ``str`` yang dimulai dengan ``=``, ``+``, ``-``, atau ``@`` diawali
    tanda kutip tunggal (``'``) sehingga aplikasi spreadsheet TIDAK
    memperlakukannya sebagai rumus. Nilai non-``str`` (int/bool/None)
    dikembalikan apa adanya -- tanda minus pada angka NEGATIF (int) TIDAK
    dianggap berbahaya karena nilai numerik murni tidak dieksekusi sebagai
    formula; hanya representasi STRING yang diawali karakter tersebut yang
    diberi mitigasi.
    """
    if isinstance(value, str) and value.startswith(CSV_INJECTION_PREFIXES):
        return "'" + value
    return value


class _EchoBuffer:
    """Pseudo-buffer untuk ``csv.writer`` yang MENGEMBALIKAN teks yang
    ditulis (bukan menyimpannya dalam memori), sehingga setiap baris CSV
    dapat di-*stream* satu per satu tanpa menahan seluruh isi ekspor di
    memori (Req efisiensi ekspor, Tahap 5 bagian B).
    """

    def write(self, value: str) -> str:
        return value


def _new_writer():
    """Buat pasangan ``(buffer, writer)`` csv baru untuk satu sesi ekspor."""
    buf = _EchoBuffer()
    return buf, csv.writer(buf)


def csv_line(writer, values: Sequence[Any]) -> str:
    """Hasilkan SATU baris CSV (termasuk ``\\r\\n``) dari ``values``, dengan
    mitigasi CSV injection diterapkan pada setiap nilai.
    """
    safe_values = [csv_safe_value(v) for v in values]
    return writer.writerow(safe_values)


# --- Pembangun baris per jenis ekspor -------------------------------------- #


def change_event_csv_row(event: Any, website: Optional[Any]) -> List[Any]:
    """Bangun satu baris CSV "changes" dari sebuah ``ChangeEvent``.

    Kolom: detected_at, website, domain, url, type, description,
    text_added_count, text_removed_count, links_added_count,
    links_removed_count, images_changed_count.
    """
    diff = event.diff
    return [
        event.detected_at.isoformat(),
        website.name if website is not None else "",
        website.domain if website is not None else "",
        event.url,
        classify_change(diff),
        describe_change(diff),
        len(diff.text_added),
        len(diff.text_removed),
        len(diff.links_added),
        len(diff.links_removed),
        len(diff.images_changed),
    ]


def alert_csv_row(alert: Any, website: Optional[Any]) -> List[Any]:
    """Bangun satu baris CSV "alerts" dari sebuah ``Alert``.

    Kolom: triggered_at, website, domain, url, alert_type, severity, status,
    title, detail.
    """
    return [
        alert.triggered_at.isoformat(),
        website.name if website is not None else "",
        website.domain if website is not None else "",
        alert.url or "",
        alert.alert_type,
        alert.severity,
        alert.status,
        alert.title,
        alert.detail or "",
    ]


def website_overview_csv_row(overview: Any, changes_total: int) -> List[Any]:
    """Bangun satu baris CSV "websites" dari sebuah ``WebsiteOverview``.

    Kolom: name, domain, paused, last_checked_at, last_status,
    pages_monitored, changes_7d, changes_total.
    """
    website = overview.website
    return [
        website.name,
        website.domain,
        "true" if website.paused else "false",
        overview.last_checked_at.isoformat() if overview.last_checked_at else "",
        overview.last_status or "",
        overview.pages_count,
        overview.changes_last_7_days,
        changes_total,
    ]


async def write_csv_file(
    path: Path, header: Sequence[str], rows: Any
) -> int:
    """Tulis berkas CSV (UTF-8 dengan BOM) ke ``path`` dan kembalikan jumlah
    baris data (TANPA header) yang ditulis.

    Dipakai oleh ``POST /reports/generate`` untuk menyimpan laporan ke
    direktori ``reports/``. Sama seperti :func:`stream_csv`: memakai modul
    ``csv`` standar (escape koma/kutip/newline otomatis) + mitigasi CSV
    injection via :func:`csv_safe_value`; ``rows`` boleh berupa iterable
    biasa ATAU async generator sehingga penulisan tetap bertahap (tidak
    memuat seluruh data ke memori sekaligus untuk tabel besar).
    """
    row_count = 0
    # newline="" sesuai rekomendasi dokumentasi modul csv (mencegah baris
    # kosong ganda pada Windows); encoding utf-8-sig menyisipkan BOM agar
    # rapi dibuka di Excel (Req Tahap 5 bagian B).
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([csv_safe_value(v) for v in header])
        async for row in _ensure_async_iter(rows):
            writer.writerow([csv_safe_value(v) for v in row])
            row_count += 1
    return row_count


async def _ensure_async_iter(rows: Any) -> AsyncIterator[Sequence[Any]]:
    """Bungkus ``rows`` (iterable biasa ATAU async iterable) menjadi async
    iterator seragam, sehingga :func:`stream_csv` dapat menerima keduanya.
    """
    if hasattr(rows, "__aiter__"):
        async for row in rows:
            yield row
        return
    for row in rows:
        yield row


async def stream_csv(
    header: Sequence[str], rows: Any
) -> AsyncIterator[bytes]:
    """Hasilkan payload CSV sebagai potongan ``bytes`` secara BERTAHAP.

    - Baris pertama diberi BOM UTF-8 (``utf-8-sig``) agar rapi dibuka di
      Excel (Req Tahap 5 bagian B).
    - Setiap baris ditulis lewat modul ``csv`` (bukan f-string manual)
      sehingga koma/tanda kutip/newline pada teks di-escape dengan benar.
    - Mitigasi CSV injection diterapkan pada setiap nilai via
      :func:`csv_safe_value`.
    - ``rows`` dapat berupa iterable biasa (list) ATAU async generator
      (dipakai untuk ekspor bertahap/batch agar tidak memuat seluruh tabel
      ke memori sekaligus -- Req efisiensi ekspor).
    """
    _, writer = _new_writer()
    header_line = csv_line(writer, header)
    yield ("\ufeff" + header_line).encode("utf-8")
    async for row in _ensure_async_iter(rows):
        line = csv_line(writer, row)
        yield line.encode("utf-8")


# --- Nama berkas & resolusi path aman (anti path traversal) --------------- #

# Hanya huruf, angka, titik, garis bawah, dan tanda hubung yang diperbolehkan
# pada komponen nama berkas; karakter lain (termasuk "/", "\\", "..") diganti
# garis bawah sehingga nama berkas tidak pernah bisa "keluar" dari direktori
# ``reports/`` (Req Tahap 5 bagian C: mencegah path traversal).
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def sanitize_filename_component(value: str) -> str:
    """Sanitasi satu komponen nama berkas: hanya karakter aman yang tersisa.

    Titik/underscore di awal maupun akhir dihapus (mencegah nama seperti
    ``".."`` atau ``"."`` yang bermakna khusus pada filesystem). Hasil kosong
    (setelah sanitasi) digantikan ``"laporan"`` sebagai bawaan yang aman.
    """
    cleaned = _SAFE_FILENAME_RE.sub("_", value or "")
    cleaned = cleaned.strip("._")
    return cleaned or "laporan"


def build_export_filename(
    report_type: str, start_date: str, end_date: Optional[str] = None
) -> str:
    """Nama berkas DESKRIPTIF untuk header ``Content-Disposition`` (unduhan),
    mis. ``contentmonitor-changes-2026-07-01_2026-07-07.csv``.

    Berbeda dengan :func:`build_stored_filename` (nama berkas FISIK yang
    disimpan di ``reports/``), nama ini hanya dipakai sebagai nama unduhan
    yang ditampilkan ke pengguna (tidak perlu unik di disk).
    """
    safe_type = sanitize_filename_component(report_type)
    safe_start = sanitize_filename_component(start_date)
    if end_date:
        safe_end = sanitize_filename_component(end_date)
        return "contentmonitor-{0}-{1}_{2}.csv".format(
            safe_type, safe_start, safe_end
        )
    return "contentmonitor-{0}-{1}.csv".format(safe_type, safe_start)


def build_stored_filename(report_id: str, report_type: str) -> str:
    """Nama berkas FISIK yang disimpan di dalam direktori ``reports/``.

    Menyertakan ``report_id`` (UUID) agar SELALU unik di disk meski beberapa
    laporan dibuat dengan jenis/rentang tanggal yang sama. Nama berkas
    disanitasi lewat :func:`sanitize_filename_component` sehingga tidak
    pernah mengandung komponen direktori (Req Tahap 5 bagian C).
    """
    safe_id = sanitize_filename_component(report_id)
    safe_type = sanitize_filename_component(report_type)
    return "{0}-{1}.csv".format(safe_id, safe_type)


def resolve_report_path(reports_dir: Path, file_path: str) -> Optional[Path]:
    """Resolusi ``file_path`` (nama berkas) relatif terhadap ``reports_dir``
    dan PASTIKAN hasil akhirnya benar-benar berada DI DALAM ``reports_dir``.

    Mengembalikan ``None`` bila ``file_path`` mengandung komponen yang
    membuatnya "keluar" dari ``reports_dir`` (mis. ``"../../etc/passwd"``
    atau path absolut yang disisipkan) -- pemanggil (rute web) harus
    memperlakukan hasil ``None`` sebagai 404 (Req Tahap 5 bagian C: cegah
    path traversal, harus diuji).
    """
    base = reports_dir.resolve()
    try:
        candidate = (base / file_path).resolve()
    except (OSError, ValueError):
        return None
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate
