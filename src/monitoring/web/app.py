"""Aplikasi Web Dashboard (FastAPI) untuk Monitoring_System.

Modul ini menyediakan *app factory* :func:`create_app` yang menerima sebuah
objek repository (biasanya :class:`monitoring.infra.repository.Repository`,
tetapi cukup apa pun yang menyediakan ``list_websites_with_status()`` async)
dan mengembalikan instance ``FastAPI`` yang terkonfigurasi dengan template
Jinja2.

Task 14.1 mengimplementasikan rute daftar & status website:

- ``GET /`` menampilkan daftar seluruh Monitored_Website beserta waktu
  pemeriksaan terakhir dan indikator status (Req 10.1, 10.2, 10.3).
- Website yang belum pernah diperiksa menampilkan indikasi "belum pernah
  diperiksa" (Req 10.2).
- Status sukses vs gagal dibedakan dengan indikator visual (kelas CSS/label
  berbeda) (Req 10.3).
- Bila tidak ada Monitored_Website, ditampilkan empty state (Req 10.6).
- Bila pemuatan data gagal (repository mengangkat pengecualian), ditampilkan
  indikasi kesalahan tanpa mengubah data tersimpan (Req 10.8).

Task 14.2 menambahkan rute riwayat & detail Diff:

- ``GET /websites/{id}`` menampilkan riwayat Change_Event terbaru→terlama,
  dengan empty state bila belum ada perubahan (Req 10.4, 10.7).
- ``GET /events/{id}`` menampilkan Diff yang terkait dengan Change_Event
  (Req 10.5), menangani kasus tidak ditemukan (404).

Task 14.3 menambahkan rute manajemen konfigurasi:

- ``POST /websites`` menerima field form (``domain``, ``name``, dan
  ``poll_interval_seconds`` opsional), memvalidasi domain (Req 1.4/1.1) dan
  Polling_Interval khusus bila diberikan (Req 1.6/1.9), lalu menambahkan
  Monitored_Website via ``repository.add_website``. Domain tidak valid,
  interval tidak valid, duplikat (``DuplicateWebsiteError``), maupun kapasitas
  penuh (``CapacityExceededError``) ditolak dengan pesan kesalahan tanpa
  membuat aplikasi berhenti. Pada keberhasilan, dilakukan redirect (303) ke
  ``/`` dengan indikasi konfirmasi bahwa domain berhasil ditambahkan (Req 1.1).
- ``DELETE /websites/{id}`` menghentikan pemantauan sebuah Monitored_Website
  dengan memanggil ``repository.remove_website`` dan mengembalikan indikasi
  keberhasilan (Req 1.2). Disediakan pula alias ramah-form
  ``POST /websites/{id}/delete`` karena form HTML tidak dapat mengirim metode
  DELETE secara langsung.

Tambahan aksi dashboard:

- ``POST /websites/{id}/check`` ("Cek Sekarang") menjalankan pemeriksaan segera
  di luar jadwal periodik dengan memanggil ``orchestrator.check_website``, lalu
  mengembalikan ringkasan hasil sebagai JSON agar frontend dapat menampilkan
  indikator loading (Req 8.4). Pemeriksaan ganda untuk website yang SAMA
  dicegah lewat penanda *in-flight* pada ``app.state`` (HTTP 409); bila
  orchestrator tidak tersedia rute merosot anggun dengan HTTP 503; kegagalan
  orchestrator dikembalikan sebagai HTTP 500 tanpa menghentikan aplikasi.
- ``POST /websites/{id}/edit`` mengubah nama dan/atau Polling_Interval khusus
  sebuah Monitored_Website (Req 1.3, 1.6, 1.9). Interval kosong berarti
  memakai Polling_Interval global (``None``). Interval tidak valid ditolak
  dengan pesan kesalahan (400) tanpa mengubah Data_Store; pada keberhasilan
  dilakukan redirect (303) ke ``/?updated=<domain>``.

Lapisan presentasi (tampilan) dashboard:

- Template Jinja2 memakai layout bersama ``base.html`` dan stylesheet statis
  ``/static/style.css`` (dilayani lewat ``StaticFiles``), sehingga dashboard
  tampil rapi tanpa bergantung pada CDN/framework eksternal (bekerja offline).
- Beberapa *filter* Jinja ditambahkan untuk memformat data secara server-side
  tanpa dependensi tambahan: waktu absolut + relatif ("30 Jul 2026 14:03
  (5 menit lalu)") pada pemeriksaan terakhir/waktu deteksi (Req 10.1, 10.4),
  serta Polling_Interval yang mudah dibaca ("6 jam", "10 menit") termasuk
  penanda "Global" saat website memakai Polling_Interval global (Req 1.6, 8.3).

Kompatibilitas: target runtime Python 3.9 (``from __future__ import
annotations`` + ``typing``).
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from monitoring.config import (
    DEFAULT_GLOBAL_INTERVAL_SECONDS,
    POLL_MAX_SECONDS,
    POLL_MIN_SECONDS,
    validate_domain,
    validate_poll_interval_seconds,
)
from monitoring.domain.classify import classify_change, describe_change
from monitoring.domain.models import Report, WebsiteConfig
from monitoring.infra.repository import (
    CapacityExceededError,
    DuplicateWebsiteError,
)
from monitoring.web import auth
from monitoring.web.charts import (
    avatar_style,
    donut_chart_svg,
    line_chart_svg,
    sparkline_svg,
)
from monitoring.web.reports_csv import (
    ALERTS_CSV_HEADER,
    CHANGES_CSV_HEADER,
    VALID_REPORT_TYPES,
    WEBSITES_CSV_HEADER,
    alert_csv_row,
    build_export_filename,
    build_stored_filename,
    change_event_csv_row,
    resolve_report_path,
    stream_csv,
    website_overview_csv_row,
    write_csv_file,
)

# Jumlah baris per halaman untuk pagination server-side (Alerts, Tahap 3).
ALERTS_PAGE_SIZE = 10
# Nilai status Alert yang valid untuk tab filter pada halaman Alerts.
VALID_ALERT_STATUSES = ("unread", "read", "resolved")
# Nilai severity Alert yang valid.
VALID_ALERT_SEVERITIES = ("critical", "warning", "info")

# Jumlah baris per halaman untuk pagination server-side (Websites, Tahap 1).
WEBSITES_PAGE_SIZE = 10
# Jumlah perubahan terbaru yang ditampilkan pada panel "Top Changes Detected".
TOP_CHANGES_LIMIT = 5
# Jumlah baris per halaman untuk pagination server-side (Content Changes &
# Pages, ContentMonitor Tahap 2).
CHANGES_PAGE_SIZE = 10
PAGES_PAGE_SIZE = 10
# Nilai tipe perubahan yang valid untuk filter `type` pada Content Changes.
VALID_CHANGE_TYPES = ("added", "removed", "updated")
# Nilai status halaman yang valid untuk tab filter `status` pada Pages.
VALID_PAGE_STATUSES = ("all", "active", "paused", "broken")

# Kode status HTTP eksplisit agar tidak bergantung pada konstanta pihak lain.
HTTP_NOT_FOUND = 404
HTTP_BAD_REQUEST = 400
HTTP_SEE_OTHER = 303
HTTP_CONFLICT = 409
HTTP_INTERNAL_ERROR = 500
HTTP_SERVICE_UNAVAILABLE = 503

# Direktori template Jinja2 & aset statis (berdampingan dengan modul ini).
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Singkatan nama bulan Bahasa Indonesia untuk format tanggal yang mudah dibaca.
_MONTHS_ID = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "Mei",
    "Jun",
    "Jul",
    "Agu",
    "Sep",
    "Okt",
    "Nov",
    "Des",
)


# --- Helper presentasi (dipakai sebagai filter Jinja) --------------------- #


def format_datetime_id(value: Optional[datetime]) -> str:
    """Format ``datetime`` menjadi teks absolut "30 Jul 2026 14:03".

    Dipakai pada kolom "Pemeriksaan Terakhir" (Req 10.1) dan waktu deteksi
    Change_Event (Req 10.4/10.5). Nilai ``None`` menghasilkan tanda hubung.
    """
    if not isinstance(value, datetime):
        return "—"
    return "{day:02d} {month} {year} {hour:02d}:{minute:02d}".format(
        day=value.day,
        month=_MONTHS_ID[value.month - 1],
        year=value.year,
        hour=value.hour,
        minute=value.minute,
    )


def format_relative_id(value: Optional[datetime]) -> str:
    """Format ``datetime`` menjadi teks relatif, mis. "5 menit lalu".

    Perhitungan dilakukan sepenuhnya di server memakai ``datetime.now``
    sehingga tidak dibutuhkan dependensi maupun JavaScript tambahan. Nilai
    naif (tanpa timezone) maupun sadar-timezone keduanya didukung.
    """
    if not isinstance(value, datetime):
        return ""
    now = datetime.now(value.tzinfo) if value.tzinfo is not None else datetime.now()
    seconds = (now - value).total_seconds()
    if seconds < 0:
        # Waktu di masa depan (mis. jam server bergeser): jangan tampilkan
        # angka negatif yang membingungkan.
        return "baru saja"
    if seconds < 10:
        return "baru saja"
    if seconds < 60:
        return "{0} detik lalu".format(int(seconds))
    minutes = int(seconds // 60)
    if minutes < 60:
        return "{0} menit lalu".format(minutes)
    hours = int(seconds // 3600)
    if hours < 24:
        return "{0} jam lalu".format(hours)
    days = int(seconds // 86400)
    if days < 30:
        return "{0} hari lalu".format(days)
    if days < 365:
        return "{0} bulan lalu".format(days // 30)
    return "{0} tahun lalu".format(days // 365)


def format_datetime_with_relative_id(value: Optional[datetime]) -> str:
    """Gabungkan waktu absolut + relatif: "30 Jul 2026 14:03 (5 menit lalu)"."""
    if not isinstance(value, datetime):
        return "—"
    return "{0} ({1})".format(format_datetime_id(value), format_relative_id(value))


def format_duration_id(seconds: Optional[int]) -> str:
    """Format durasi detik menjadi teks mudah dibaca ("6 jam", "10 menit").

    Contoh: ``21600`` -> "6 jam", ``600`` -> "10 menit", ``90`` -> "1 menit
    30 detik". Dipakai untuk menampilkan Polling_Interval (Req 1.6, 8.3).
    """
    if seconds is None:
        return "—"
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if total <= 0:
        return "0 detik"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append("{0} jam".format(hours))
    if minutes:
        parts.append("{0} menit".format(minutes))
    if secs:
        parts.append("{0} detik".format(secs))
    return " ".join(parts)


def format_interval_id(seconds: Optional[int]) -> str:
    """Format Polling_Interval efektif sebuah Monitored_Website.

    Bila website tidak memiliki Polling_Interval khusus (``None``), ia memakai
    Polling_Interval global sehingga ditampilkan sebagai "Global (6 jam)"
    (Req 1.6, 8.3). Bila ada interval khusus, tampilkan durasinya saja.
    """
    if seconds is None:
        return "Global ({0})".format(
            format_duration_id(DEFAULT_GLOBAL_INTERVAL_SECONDS)
        )
    return format_duration_id(seconds)


def _register_presentation_helpers(templates: Jinja2Templates) -> None:
    """Daftarkan filter & konstanta presentasi pada environment Jinja.

    Semua pemformatan dihitung di server (tanpa dependensi baru) agar template
    tetap sederhana dan hasil render dapat diuji langsung.
    """
    env = templates.env
    env.filters["fmt_dt"] = format_datetime_id
    env.filters["fmt_rel"] = format_relative_id
    env.filters["fmt_dt_rel"] = format_datetime_with_relative_id
    env.filters["fmt_duration"] = format_duration_id
    env.filters["fmt_interval"] = format_interval_id
    env.filters["sparkline_svg"] = sparkline_svg
    env.filters["avatar_letter"] = lambda seed: avatar_style(seed)[0]
    env.filters["avatar_color"] = lambda seed: avatar_style(seed)[1]
    env.filters["counts_only"] = lambda pairs: [c for _, c in pairs]
    env.filters["describe_change"] = describe_change
    env.filters["classify_change"] = classify_change
    env.globals["product_name"] = "ContentMonitor"
    env.globals["global_interval_seconds"] = DEFAULT_GLOBAL_INTERVAL_SECONDS
    env.globals["global_interval_label"] = format_duration_id(
        DEFAULT_GLOBAL_INTERVAL_SECONDS
    )
    env.globals["poll_min_seconds"] = POLL_MIN_SECONDS
    env.globals["poll_max_seconds"] = POLL_MAX_SECONDS
    env.globals["line_chart_svg"] = line_chart_svg


async def _sidebar_unread_alerts_count(repository: Any) -> int:
    """Hitung jumlah Alert 'unread' untuk badge sidebar (Tahap 3, bagian G).

    Kegagalan pemuatan (repository mengangkat pengecualian, mis. skema Alert
    belum ada pada repository palsu di pengujian halaman lain) dilewati
    dengan aman -> ``0``, sehingga sidebar tetap dapat dirender di seluruh
    halaman tanpa bergantung pada dukungan Alert penuh dari setiap fake
    repository pengujian.
    """
    try:
        return await repository.count_unread_alerts()
    except Exception:
        return 0


async def _render(
    request: Request,
    templates: Jinja2Templates,
    name: str,
    context: dict,
    status_code: int = 200,
) -> HTMLResponse:
    """Render sebuah template dengan konteks bersama (Tahap 3, bagian G).

    Menambahkan ``sidebar_unread_alerts`` ke SETIAP konteks render sehingga
    badge jumlah unread di sebelah menu "Alerts" pada sidebar (``base.html``)
    selalu tersedia tanpa perlu diulang di setiap rute. Dipusatkan di sini
    (bukan per-rute) agar konsisten dan mudah dipertahankan.
    """
    repository = request.app.state.repository
    merged = dict(context)
    merged.setdefault(
        "sidebar_unread_alerts", await _sidebar_unread_alerts_count(repository)
    )
    # Username yang sedang login (disematkan oleh middleware auth) agar
    # sidebar dapat menampilkan info pengguna + tombol logout.
    merged.setdefault(
        "current_username", getattr(request.state, "username", None)
    )
    return templates.TemplateResponse(request, name, merged, status_code=status_code)


def _safe_redirect_target(next_url: Optional[str], default: str) -> str:
    """Kembalikan ``next_url`` bila aman (path relatif lokal), selain itu ``default``.

    Dipakai oleh aksi Jeda/Lanjutkan agar pengguna dikembalikan ke halaman
    asal (Overview atau Websites) setelah aksi (Req desain umum
    Post/Redirect/Get). Hanya path yang dimulai dengan "/" dan TIDAK dimulai
    dengan "//" (mencegah *open redirect* ke domain eksternal) yang diterima.
    """
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return default


async def _load_websites_context(
    request: Request, q: Optional[str], page: int
) -> dict:
    """Muat konteks render halaman Websites: search + pagination server-side.

    Alur:

    1. Ambil ``website_stats()`` (ringkasan per website, sudah termasuk jumlah
       halaman, status, last_change, sparkline 7 hari).
    2. Filter berdasarkan ``q`` (dicocokkan pada nama ATAU domain, tanpa
       memandang huruf besar/kecil) bila diberikan.
    3. Jepit ``page`` ke rentang valid (>= 1, dan tidak melebihi jumlah
       halaman yang tersedia) sehingga halaman di luar rentang tidak
       menghasilkan error, cukup menampilkan halaman terakhir yang valid
       (atau daftar kosong bila tidak ada hasil sama sekali).
    4. Potong hasil sesuai ``WEBSITES_PAGE_SIZE`` untuk halaman yang diminta.

    Mengembalikan dict konteks template; kunci ``load_error`` menandakan
    kegagalan pemuatan data (repository mengangkat pengecualian) tanpa mutasi
    apa pun ke Data_Store.
    """
    repository = request.app.state.repository
    try:
        stats = await repository.website_stats()
    except Exception:
        return {
            "load_error": True,
            "rows": [],
            "q": q or "",
            "page": 1,
            "total_pages": 1,
            "total_count": 0,
            "range_start": 0,
            "range_end": 0,
            "has_any_website": False,
            "summary_total": 0,
            "summary_success": 0,
            "summary_failure": 0,
            "summary_never": 0,
        }

    query = (q or "").strip().lower()
    if query:
        filtered = [
            item
            for item in stats
            if query in item.website.name.lower()
            or query in item.website.domain.lower()
        ]
    else:
        filtered = stats

    total_count = len(filtered)
    total_pages = max(1, (total_count + WEBSITES_PAGE_SIZE - 1) // WEBSITES_PAGE_SIZE)
    safe_page = max(1, min(int(page or 1), total_pages))
    start_idx = (safe_page - 1) * WEBSITES_PAGE_SIZE
    end_idx = start_idx + WEBSITES_PAGE_SIZE
    rows = filtered[start_idx:end_idx]

    # Ringkasan status TIDAK terpengaruh oleh search/pagination (dihitung atas
    # seluruh website), dipertahankan untuk kartu ringkasan
    # data-summary="total|success|failure|never" (test lama Tahap sebelum
    # ContentMonitor bergantung pada penanda ini).
    summary_total = len(stats)
    summary_success = sum(1 for item in stats if item.is_success)
    summary_failure = sum(1 for item in stats if item.is_failure)
    summary_never = sum(1 for item in stats if item.never_checked)

    return {
        "load_error": False,
        "rows": rows,
        "q": q or "",
        "page": safe_page,
        "total_pages": total_pages,
        "total_count": total_count,
        "range_start": start_idx + 1 if rows else 0,
        "range_end": start_idx + len(rows),
        "has_any_website": len(stats) > 0,
        "summary_total": summary_total,
        "summary_success": summary_success,
        "summary_failure": summary_failure,
        "summary_never": summary_never,
    }


def _parse_date_range(
    start: Optional[str], end: Optional[str]
) -> "tuple[datetime, datetime, bool]":
    """Uraikan filter tanggal ``start``/``end`` (format "YYYY-MM-DD").

    Mengembalikan tuple ``(range_start, range_end, used_default)`` dengan
    rentang setengah-terbuka ``[range_start, range_end)`` yang konsisten
    dengan penyimpanan ``detected_at``. ``range_end`` digeser +1 hari agar
    hari akhir yang dipilih pengguna ikut tercakup penuh (inklusif secara
    tampilan meski setengah-terbuka secara implementasi).

    Bawaan (bila ``start``/``end`` tidak diberikan): 7 hari terakhir hingga
    akhir hari ini.

    Ketahanan input (Req desain umum): format tanggal tidak valid ATAU
    ``end < start`` -> KEMBALI ke rentang bawaan 7 hari, TIDAK mengangkat
    error (``used_default=True`` menandakan pemanggil harus menampilkan
    peringatan halus, bukan error 500).
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    default_end = today_start + timedelta(days=1)
    default_start = default_end - timedelta(days=7)

    if not start and not end:
        return default_start, default_end, False

    try:
        parsed_start = (
            datetime.strptime(start, "%Y-%m-%d") if start else default_start
        )
        parsed_end = (
            datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
            if end
            else default_end
        )
    except (ValueError, TypeError):
        return default_start, default_end, True

    if parsed_end <= parsed_start:
        # end < start (atau sama, yang berarti rentang kosong) -> bawaan.
        return default_start, default_end, True

    return parsed_start, parsed_end, False


async def _load_changes_context(
    request: Request,
    start: Optional[str],
    end: Optional[str],
    website_id: Optional[str],
    change_type: Optional[str],
    q: Optional[str],
    page: int,
) -> dict:
    """Muat konteks render halaman Content Changes (Tahap 2).

    Pendekatan filter TIPE (added/removed/updated) — lihat juga docstring
    :meth:`Repository.list_change_events_filtered`: karena tipe diturunkan
    dari ``Diff`` (fungsi murni ``classify_change``, bukan kolom SQL), filter
    tipe TIDAK BISA didorong ke SQL. Pendekatan yang dipakai di sini:

    1. Terapkan filter tanggal/website/URL (``q``) di SQL seperti biasa.
    2. Bila TIDAK ada filter tipe aktif: pagination biasa (``LIMIT``/``OFFSET``
       langsung dari SQL) — jalur cepat, tidak memuat lebih dari satu halaman.
    3. Bila ADA filter tipe aktif: SQL tidak dapat memberi tahu berapa banyak
       baris yang cocok filter tipe tanpa memuat & mengklasifikasikan tiap
       baris. Karena volume Change_Event per website bersifat wajar (dibatasi
       oleh retensi & rentang tanggal yang dipilih pengguna, biasanya hari/
       minggu), seluruh baris yang cocok filter tanggal/website/URL diambil
       (TANPA ``LIMIT`` SQL), diklasifikasikan & difilter tipe di Python,
       lalu barulah ``total`` dihitung ulang dari hasil terfilter dan
       potongan halaman (slice) diterapkan di Python. Ini menjamin pagination
       tetap benar (total & rentang halaman mencerminkan hasil SETELAH filter
       tipe) dengan trade-off memuat lebih banyak baris ke memori saat filter
       tipe dikombinasikan dengan rentang tanggal yang sangat lebar.

    ``q`` mencari pada URL (SQL ``LIKE``) ATAU deskripsi Diff (Python, karena
    deskripsi diturunkan dari ``diff_json``); kedua pencarian digabung dengan
    OR sehingga hasil pencarian tidak lebih sempit dari yang diharapkan
    pengguna. Ketika ``q`` diberikan, deskripsi juga perlu dihitung untuk
    seluruh baris kandidat sebelum filter dapat diterapkan, sehingga jalur
    "ada filter tambahan di Python" turut aktif untuk ``q`` (serupa filter
    tipe di atas).
    """
    repository = request.app.state.repository
    range_start, range_end, used_default_range = _parse_date_range(start, end)

    website_filter = website_id or None
    type_filter = change_type if change_type in VALID_CHANGE_TYPES else None
    query = (q or "").strip()

    empty_context = {
        "load_error": True,
        "rows": [],
        "start": range_start.date().isoformat(),
        "end": (range_end - timedelta(days=1)).date().isoformat(),
        "website": website_filter or "",
        "type": change_type or "",
        "q": query,
        "page": 1,
        "total_pages": 1,
        "total_count": 0,
        "range_start_idx": 0,
        "range_end_idx": 0,
        "used_default_range": used_default_range,
        "has_any_change_ever": False,
        "kpi_total": 0,
        "kpi_added": 0,
        "kpi_removed": 0,
        "kpi_updated": 0,
        "websites": [],
    }

    try:
        websites = await repository.list_websites()
        # Cek keberadaan Change_Event SAMA SEKALI (lintas rentang waktu) agar
        # empty state dapat membedakan "belum ada perubahan" vs "tidak ada
        # hasil untuk filter ini". Dibatasi 1 baris agar ringan.
        any_ever = await repository.list_change_events_filtered(
            datetime.min + timedelta(days=1),
            datetime.now() + timedelta(days=3650),
            limit=1,
            offset=0,
        )
        has_any_change_ever = len(any_ever) > 0

        needs_python_filter = bool(type_filter) or bool(query)

        if not needs_python_filter:
            # Jalur cepat: SQL menerapkan seluruh filter + pagination.
            total_count = await repository.count_change_events_filtered(
                range_start, range_end, website_id=website_filter
            )
            total_pages = max(
                1, (total_count + CHANGES_PAGE_SIZE - 1) // CHANGES_PAGE_SIZE
            )
            safe_page = max(1, min(int(page or 1), total_pages))
            offset = (safe_page - 1) * CHANGES_PAGE_SIZE
            events = await repository.list_change_events_filtered(
                range_start,
                range_end,
                website_id=website_filter,
                limit=CHANGES_PAGE_SIZE,
                offset=offset,
            )
            # KPI dihitung dari rentang tanggal aktif (Req deskripsi tugas),
            # TIDAK terpengaruh filter website/tipe/search agar konsisten.
            kpi_events = await repository.list_change_events_between(
                range_start, range_end
            )
        else:
            # Filter tipe/search: ambil seluruh kandidat rentang+website+url,
            # klasifikasikan & filter di Python, lalu hitung ulang total &
            # potong halaman secara manual (lihat docstring).
            candidates = await repository.list_change_events_filtered(
                range_start,
                range_end,
                website_id=website_filter,
                limit=1_000_000,
                offset=0,
            )
            filtered = []
            for evt in candidates:
                evt_type = classify_change(evt.diff)
                if type_filter and evt_type != type_filter:
                    continue
                if query:
                    desc = describe_change(evt.diff)
                    if (
                        query.lower() not in evt.url.lower()
                        and query.lower() not in desc.lower()
                    ):
                        continue
                filtered.append(evt)
            total_count = len(filtered)
            total_pages = max(
                1, (total_count + CHANGES_PAGE_SIZE - 1) // CHANGES_PAGE_SIZE
            )
            safe_page = max(1, min(int(page or 1), total_pages))
            start_idx = (safe_page - 1) * CHANGES_PAGE_SIZE
            events = filtered[start_idx : start_idx + CHANGES_PAGE_SIZE]
            kpi_events = await repository.list_change_events_between(
                range_start, range_end
            )
    except Exception:
        return empty_context

    kpi_total = len(kpi_events)
    kpi_added = sum(1 for e in kpi_events if classify_change(e.diff) == "added")
    kpi_removed = sum(
        1 for e in kpi_events if classify_change(e.diff) == "removed"
    )
    kpi_updated = kpi_total - kpi_added - kpi_removed

    website_cache: dict = {}
    rows = []
    for evt in events:
        website = website_cache.get(evt.website_id)
        if website is None:
            website = await repository.get_website(evt.website_id)
            website_cache[evt.website_id] = website
        rows.append(
            {
                "event": evt,
                "website": website,
                "change_type": classify_change(evt.diff),
                "description": describe_change(evt.diff),
            }
        )

    range_start_idx = (
        (safe_page - 1) * CHANGES_PAGE_SIZE + 1 if rows else 0
    )
    range_end_idx = (safe_page - 1) * CHANGES_PAGE_SIZE + len(rows)

    return {
        "load_error": False,
        "rows": rows,
        "start": range_start.date().isoformat(),
        "end": (range_end - timedelta(days=1)).date().isoformat(),
        "website": website_filter or "",
        "type": change_type or "",
        "q": query,
        "page": safe_page,
        "total_pages": total_pages,
        "total_count": total_count,
        "range_start_idx": range_start_idx,
        "range_end_idx": range_end_idx,
        "used_default_range": used_default_range,
        "has_any_change_ever": has_any_change_ever,
        "kpi_total": kpi_total,
        "kpi_added": kpi_added,
        "kpi_removed": kpi_removed,
        "kpi_updated": kpi_updated,
        "websites": websites,
    }


async def _load_pages_context(
    request: Request,
    status: Optional[str],
    website_id: Optional[str],
    q: Optional[str],
    page: int,
) -> dict:
    """Muat konteks render halaman Pages (Tahap 2).

    Halaman ditemukan otomatis (tidak dikelola manual); konteks ini murni
    menyusun daftar/pemantauan + tab filter status (All/Active/Paused/
    Broken) dengan jumlah masing-masing, search (nama domain/URL), filter
    website, dan pagination server-side.

    Jumlah pada tiap tab dihitung dari SELURUH halaman (setelah filter
    website+search, tetapi SEBELUM filter tab status) sehingga jumlah tab
    tetap konsisten/stabil ketika pengguna berpindah tab.
    """
    repository = request.app.state.repository
    website_filter = website_id or None
    query = (q or "").strip().lower()
    status_filter = status if status in VALID_PAGE_STATUSES else "all"

    empty_context = {
        "load_error": True,
        "rows": [],
        "status": "all",
        "website": website_filter or "",
        "q": q or "",
        "page": 1,
        "total_pages": 1,
        "total_count": 0,
        "range_start_idx": 0,
        "range_end_idx": 0,
        "has_any_page": False,
        "count_all": 0,
        "count_active": 0,
        "count_paused": 0,
        "count_broken": 0,
        "websites": [],
    }

    try:
        websites = await repository.list_websites()
        pages = await repository.list_pages(website_id=website_filter)
    except Exception:
        return empty_context

    has_any_page = len(pages) > 0

    if query:
        pages = [p for p in pages if query in p.url.lower()]

    count_all = len(pages)
    count_active = sum(
        1 for p in pages if not p.is_paused and not p.is_broken
    )
    count_paused = sum(1 for p in pages if p.is_paused and not p.is_broken)
    count_broken = sum(1 for p in pages if p.is_broken)

    if status_filter == "active":
        filtered = [p for p in pages if not p.is_paused and not p.is_broken]
    elif status_filter == "paused":
        filtered = [p for p in pages if p.is_paused and not p.is_broken]
    elif status_filter == "broken":
        filtered = [p for p in pages if p.is_broken]
    else:
        filtered = pages

    total_count = len(filtered)
    total_pages = max(1, (total_count + PAGES_PAGE_SIZE - 1) // PAGES_PAGE_SIZE)
    safe_page = max(1, min(int(page or 1), total_pages))
    start_idx = (safe_page - 1) * PAGES_PAGE_SIZE
    rows = filtered[start_idx : start_idx + PAGES_PAGE_SIZE]

    return {
        "load_error": False,
        "rows": rows,
        "status": status_filter,
        "website": website_filter or "",
        "q": q or "",
        "page": safe_page,
        "total_pages": total_pages,
        "total_count": total_count,
        "range_start_idx": start_idx + 1 if rows else 0,
        "range_end_idx": start_idx + len(rows),
        "has_any_page": has_any_page,
        "count_all": count_all,
        "count_active": count_active,
        "count_paused": count_paused,
        "count_broken": count_broken,
        "websites": websites,
    }


# --- Halaman Alerts (ContentMonitor Tahap 3) ------------------------------ #


async def _load_alerts_context(
    request: Request,
    tab: Optional[str],
    website_id: Optional[str],
    q: Optional[str],
    page: int,
) -> dict:
    """Muat konteks render halaman Alerts (Tahap 3).

    Tab (``all``/``unread``/``critical``/``warning``/``info``) menentukan
    filter ``status``/``severity`` yang diteruskan ke SQL:

    - ``unread`` -> filter ``status='unread'``.
    - ``critical``/``warning``/``info`` -> filter ``severity``.
    - ``all`` (bawaan) -> tanpa filter status/severity.

    Jumlah pada tiap tab dihitung dari SELURUH Alert (setelah filter
    website+search, SEBELUM filter tab) via
    ``repository.count_alerts_filtered`` per kombinasi, sehingga jumlah tab
    tetap konsisten saat berpindah tab. Search (`q`) & filter website
    diterapkan langsung di SQL (lihat
    :meth:`Repository.list_alerts_filtered`).
    """
    repository = request.app.state.repository
    website_filter = website_id or None
    query = (q or "").strip()
    tab_filter = tab if tab in ("all", "unread", "critical", "warning", "info") else "all"

    empty_context = {
        "load_error": True,
        "rows": [],
        "tab": "all",
        "website": website_filter or "",
        "q": q or "",
        "page": 1,
        "total_pages": 1,
        "total_count": 0,
        "range_start_idx": 0,
        "range_end_idx": 0,
        "has_any_alert": False,
        "count_all": 0,
        "count_unread": 0,
        "count_critical": 0,
        "count_warning": 0,
        "count_info": 0,
        "websites": [],
    }

    status_filter = "unread" if tab_filter == "unread" else None
    severity_filter = tab_filter if tab_filter in VALID_ALERT_SEVERITIES else None

    try:
        websites = await repository.list_websites()
        website_by_id = {w.id: w for w in websites}

        # "has_any_alert" membedakan "belum ada alert sama sekali" vs "tidak
        # ada hasil untuk filter ini" -> SENGAJA tidak menyertakan `q` (Req
        # desain umum empty state, selaras Pages/Content Changes).
        has_any_alert = (
            await repository.count_alerts_filtered(website_id=website_filter) > 0
        )

        count_all = await repository.count_alerts_filtered(
            website_id=website_filter, q=query or None
        )
        count_unread = await repository.count_alerts_filtered(
            status="unread", website_id=website_filter, q=query or None
        )
        count_critical = await repository.count_alerts_filtered(
            severity="critical", website_id=website_filter, q=query or None
        )
        count_warning = await repository.count_alerts_filtered(
            severity="warning", website_id=website_filter, q=query or None
        )
        count_info = await repository.count_alerts_filtered(
            severity="info", website_id=website_filter, q=query or None
        )

        total_count = await repository.count_alerts_filtered(
            status=status_filter,
            severity=severity_filter,
            website_id=website_filter,
            q=query or None,
        )
        total_pages = max(1, (total_count + ALERTS_PAGE_SIZE - 1) // ALERTS_PAGE_SIZE)
        safe_page = max(1, min(int(page or 1), total_pages))
        offset = (safe_page - 1) * ALERTS_PAGE_SIZE
        alerts = await repository.list_alerts_filtered(
            status=status_filter,
            severity=severity_filter,
            website_id=website_filter,
            q=query or None,
            limit=ALERTS_PAGE_SIZE,
            offset=offset,
        )
    except Exception:
        return empty_context

    rows = [
        {"alert": alert, "website": website_by_id.get(alert.website_id)}
        for alert in alerts
    ]
    range_start_idx = (safe_page - 1) * ALERTS_PAGE_SIZE + 1 if rows else 0
    range_end_idx = (safe_page - 1) * ALERTS_PAGE_SIZE + len(rows)

    return {
        "load_error": False,
        "rows": rows,
        "tab": tab_filter,
        "website": website_filter or "",
        "q": query,
        "page": safe_page,
        "total_pages": total_pages,
        "total_count": total_count,
        "range_start_idx": range_start_idx,
        "range_end_idx": range_end_idx,
        "has_any_alert": has_any_alert,
        "count_all": count_all,
        "count_unread": count_unread,
        "count_critical": count_critical,
        "count_warning": count_warning,
        "count_info": count_info,
        "websites": websites,
    }


# --- Halaman Reports (ContentMonitor Tahap 5) -------------------------- #

# Direktori bawaan tempat berkas CSV laporan disimpan (relatif terhadap root
# proyek saat dijalankan sebagai `python -m monitoring.main`). Dapat
# di-override lewat parameter ``reports_dir`` pada :func:`create_app` (mis.
# ``tmp_path`` pada pengujian) agar test TIDAK menulis ke direktori nyata.
DEFAULT_REPORTS_DIR = "reports"


def _parse_single_date_or_none(value: Optional[str]) -> "Optional[datetime]":
    """Uraikan satu tanggal "YYYY-MM-DD"; kembalikan ``None`` bila kosong.

    Berbeda dengan :func:`_parse_date_range` (yang SELALU jatuh ke bawaan
    pada input tidak valid), fungsi ini mengangkat ``ValueError`` pada format
    salah -- dipakai oleh rute ekspor CSV yang HARUS menolak rentang tidak
    valid dengan HTTP 400 (Req Tahap 5 bagian B), bukan diam-diam memakai
    bawaan seperti pada halaman HTML.
    """
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d")


def _parse_export_date_range(
    start: Optional[str], end: Optional[str]
) -> "tuple[datetime, datetime]":
    """Uraikan rentang tanggal ekspor CSV; TOLAK (mengangkat ``ValueError``)
    bila format salah atau ``end < start`` (Req Tahap 5 bagian B: rentang
    tanggal tidak valid -> 400 dengan pesan jelas, JANGAN 500).

    Bawaan (bila ``start``/``end`` tidak diberikan): 7 hari terakhir, sama
    seperti :func:`_parse_date_range` namun TANPA jatuh diam-diam ke bawaan
    saat nilai yang diberikan tidak valid.
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    default_end = today_start + timedelta(days=1)
    default_start = default_end - timedelta(days=7)

    parsed_start = _parse_single_date_or_none(start) or default_start
    parsed_end_raw = _parse_single_date_or_none(end)
    parsed_end = (
        parsed_end_raw + timedelta(days=1) if parsed_end_raw else default_end
    )

    if parsed_end <= parsed_start:
        raise ValueError(
            "Rentang tanggal tidak valid: tanggal akhir harus sama dengan "
            "atau setelah tanggal awal."
        )
    return parsed_start, parsed_end


async def _load_reports_context(
    request: Request, start: Optional[str], end: Optional[str]
) -> dict:
    """Muat konteks render halaman Reports (ContentMonitor Tahap 5).

    Menghitung 4 kartu KPI (Total Changes + persentase vs periode
    sebelumnya, Average Changes/Day, Most Active Website, Total Alerts +
    persentase vs periode sebelumnya), data chart "Changes Over Time" (line
    chart, memakai ``daily_change_counts``), data "Changes by Type" (donut
    Added/Removed/Updated), dan "Riwayat Laporan" (daftar ``Report``
    tersimpan).

    Rentang tanggal tidak valid pada HALAMAN (berbeda dengan rute ekspor)
    jatuh ke bawaan 7 hari terakhir dengan peringatan halus
    (``used_default_range``), konsisten dengan :func:`_parse_date_range`
    yang dipakai halaman Content Changes -- TIDAK pernah error 500.
    """
    repository = request.app.state.repository
    range_start, range_end, used_default_range = _parse_date_range(start, end)
    period_days = max(1, (range_end - range_start).days)
    prev_start = range_start - (range_end - range_start)
    prev_end = range_start

    empty_context = {
        "load_error": True,
        "start": range_start.date().isoformat(),
        "end": (range_end - timedelta(days=1)).date().isoformat(),
        "used_default_range": used_default_range,
        "kpi_total_changes": 0,
        "kpi_total_changes_pct": 0,
        "kpi_avg_changes_per_day": 0,
        "kpi_most_active_name": None,
        "kpi_most_active_count": 0,
        "kpi_total_alerts": 0,
        "kpi_total_alerts_pct": 0,
        "chart_labels": [],
        "chart_counts": [],
        "donut_added": 0,
        "donut_removed": 0,
        "donut_updated": 0,
        "reports": [],
    }

    try:
        total_changes = await repository.count_change_events_between(
            range_start, range_end
        )
        prev_total_changes = await repository.count_change_events_between(
            prev_start, prev_end
        )
        total_alerts = await repository.count_alerts_between(
            range_start, range_end
        )
        prev_total_alerts = await repository.count_alerts_between(
            prev_start, prev_end
        )
        most_active = await repository.most_active_website_between(
            range_start, range_end
        )
        daily_counts = await repository.daily_change_counts(
            range_start, range_end
        )
        range_events = await repository.list_change_events_between(
            range_start, range_end
        )
        reports = await repository.list_reports()
    except Exception:
        return empty_context

    def _pct_change(current: int, previous: int) -> int:
        if previous > 0:
            return round((current - previous) / previous * 100)
        return 100 if current > 0 else 0

    most_active_name = None
    most_active_count = 0
    if most_active is not None:
        website_id, count = most_active
        website = await repository.get_website(website_id)
        most_active_name = website.name if website else website_id
        most_active_count = count

    donut_added = donut_removed = donut_updated = 0
    for evt in range_events:
        change_type = classify_change(evt.diff)
        if change_type == "added":
            donut_added += 1
        elif change_type == "removed":
            donut_removed += 1
        else:
            donut_updated += 1

    return {
        "load_error": False,
        "start": range_start.date().isoformat(),
        "end": (range_end - timedelta(days=1)).date().isoformat(),
        "used_default_range": used_default_range,
        "kpi_total_changes": total_changes,
        "kpi_total_changes_pct": _pct_change(total_changes, prev_total_changes),
        "kpi_avg_changes_per_day": round(total_changes / period_days, 1),
        "kpi_most_active_name": most_active_name,
        "kpi_most_active_count": most_active_count,
        "kpi_total_alerts": total_alerts,
        "kpi_total_alerts_pct": _pct_change(total_alerts, prev_total_alerts),
        "chart_labels": [day for day, _ in daily_counts],
        "chart_counts": [count for _, count in daily_counts],
        "donut_added": donut_added,
        "donut_removed": donut_removed,
        "donut_updated": donut_updated,
        "reports": reports,
    }


def create_app(
    repository: Any,
    orchestrator: Optional[Any] = None,
    reports_dir: Optional[str] = None,
    require_auth: bool = True,
) -> FastAPI:
    """Buat dan konfigurasikan aplikasi FastAPI Dashboard.

    Parameter ``repository`` cukup menyediakan coroutine
    ``list_websites_with_status()`` yang mengembalikan daftar objek dengan
    atribut ``website`` (punya ``domain`` & ``name``), ``last_checked_at``,
    ``last_status``, serta properti ``never_checked``/``is_success``/
    ``is_failure``.

    Parameter ``orchestrator`` bersifat opsional (kompatibel ke belakang dengan
    pemanggilan ``create_app(repository)``). Bila diberikan, ia cukup
    menyediakan coroutine ``check_website(website)`` yang mengembalikan sebuah
    ``CheckResult``; rute "Cek Sekarang" (``POST /websites/{id}/check``)
    memakainya untuk memeriksa segera di luar jadwal (Req 8.4). Bila tidak
    diberikan, rute tersebut merosot anggun dengan HTTP 503 sehingga setup
    tanpa orchestrator (mis. pengujian tampilan) tetap dapat berjalan.

    Parameter ``reports_dir`` (ContentMonitor Tahap 5) menentukan direktori
    tempat berkas CSV laporan disimpan/diunduh. Bawaan ``"reports"`` (relatif
    terhadap direktori kerja saat aplikasi dijalankan); pengujian dapat
    meng-override dengan ``tmp_path`` agar TIDAK menulis ke direktori nyata.
    Direktori dibuat (``mkdir(parents=True, exist_ok=True)``) sekali di sini
    bila belum ada.
    """
    app = FastAPI(title="ContentMonitor Dashboard")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    _register_presentation_helpers(templates)
    templates.env.globals["donut_chart_svg"] = donut_chart_svg

    # Aset statis (CSS/JS milik sendiri, tanpa CDN) agar dashboard tetap
    # tampil rapi walau tidak ada koneksi internet.
    if os.path.isdir(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Simpan referensi agar mudah diakses/di-override saat pengujian bila perlu.
    app.state.repository = repository
    app.state.orchestrator = orchestrator
    app.state.templates = templates
    # Penanda pemeriksaan manual yang sedang berjalan (per website id) untuk
    # mencegah pemeriksaan ganda bersamaan pada website yang sama.
    app.state.checks_in_flight = set()
    # Direktori penyimpanan laporan CSV (Tahap 5). TIDAK dibuat di sini
    # (lazy: hanya dibuat saat benar-benar diperlukan oleh
    # ``POST /reports/generate``) agar memanggil ``create_app()`` tanpa
    # pernah menyentuh rute Reports TIDAK menciptakan direktori ``reports/``
    # secara tak terduga (mis. pada test/aplikasi lain yang tidak memakai
    # fitur ini sama sekali).
    app.state.reports_dir = Path(reports_dir or DEFAULT_REPORTS_DIR)

    # --- Autentikasi (fitur login multi-user) ---------------------------- #
    # ``require_auth`` mengaktifkan gerbang login. Bahkan bila True, gerbang
    # HANYA berlaku ketika sudah ada minimal satu akun pengguna terdaftar
    # (lihat middleware di bawah). Dengan begitu:
    #   - Sebelum user pertama dibuat, dashboard tetap dapat diakses (mencegah
    #     lock-out saat setup awal) dan seluruh test lama yang memakai DB
    #     kosong tetap berjalan tanpa perlu login.
    #   - Begitu admin membuat user pertama (via CLI), gerbang aktif otomatis.
    app.state.require_auth = require_auth
    # Secret key untuk menandatangani cookie session. Prioritas: env var
    # MONITORING_SECRET_KEY; bila tidak ada, pakai/simpan satu di app_config
    # agar konsisten lintas restart.
    app.state.secret_key = None  # diisi lazy pada request pertama (butuh await)

    async def _get_secret_key() -> str:
        """Ambil (atau buat lalu simpan) secret key untuk cookie session."""
        if app.state.secret_key:
            return app.state.secret_key
        key = os.environ.get("MONITORING_SECRET_KEY")
        if not key:
            key = await repository.get_app_config("secret_key")
        if not key:
            key = auth.generate_secret_key()
            try:
                await repository.set_app_config("secret_key", key)
            except Exception:
                # Bila gagal menyimpan (mis. repo test tanpa app_config),
                # tetap pakai key di memori untuk sesi berjalan.
                pass
        app.state.secret_key = key
        return key

    app.state.get_secret_key = _get_secret_key

    async def _current_user(request: Request) -> Optional[str]:
        """Kembalikan username dari cookie session yang valid, atau None."""
        token = request.cookies.get(auth.SESSION_COOKIE_NAME)
        if not token:
            return None
        secret = await _get_secret_key()
        return auth.verify_session_token(token, secret)

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        """Blokir akses ke halaman bila belum login (bila gerbang aktif).

        Gerbang dilewati untuk: aplikasi tanpa require_auth, path publik
        (/login, /logout, /static), dan ketika belum ada user terdaftar.
        """
        path = request.url.path
        public = (
            path == "/login"
            or path == "/logout"
            or path.startswith("/static")
        )
        if not app.state.require_auth or public:
            return await call_next(request)

        # Gerbang hanya aktif bila sudah ada minimal satu akun.
        try:
            has_users = await repository.count_users() > 0
        except Exception:
            has_users = False
        if not has_users:
            return await call_next(request)

        username = await _current_user(request)
        if username is None:
            nxt = request.url.path
            if request.url.query:
                nxt += "?" + request.url.query
            from urllib.parse import quote

            return RedirectResponse(
                url="/login?next=" + quote(nxt, safe=""),
                status_code=HTTP_SEE_OTHER,
            )
        # Sematkan username agar template/rute lain bisa memakainya.
        request.state.username = username
        return await call_next(request)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: Optional[str] = None) -> HTMLResponse:
        """Tampilkan formulir login. Bila sudah login, alihkan ke tujuan."""
        if await _current_user(request) is not None:
            return RedirectResponse(
                url=_safe_redirect_target(next, "/"), status_code=HTTP_SEE_OTHER
            )
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "next": next or "", "username": ""},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: Optional[str] = Form(None),
    ) -> Any:
        """Proses login: verifikasi kredensial, set cookie session bila cocok."""
        user = await repository.get_user_by_username(username.strip())
        ok = user is not None and auth.verify_password(password, user.password_hash)
        if not ok:
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": "Username atau password salah.",
                    "next": next or "",
                    "username": username,
                },
                status_code=HTTP_BAD_REQUEST,
            )
        secret = await _get_secret_key()
        token = auth.create_session_token(user.username, secret)
        response = RedirectResponse(
            url=_safe_redirect_target(next, "/"), status_code=HTTP_SEE_OTHER
        )
        response.set_cookie(
            auth.SESSION_COOKIE_NAME,
            token,
            max_age=auth.SESSION_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/logout")
    @app.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        """Hapus cookie session lalu alihkan ke halaman login."""
        response = RedirectResponse(url="/login", status_code=HTTP_SEE_OTHER)
        response.delete_cookie(auth.SESSION_COOKIE_NAME)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request) -> HTMLResponse:
        """Halaman Overview ContentMonitor: KPI, chart, top changes (Tahap 1).

        Menampilkan:

        - 4 kartu KPI: Total Websites, Pages Monitored, Changes Detected
          (7 hari terakhir + persentase vs 7 hari sebelumnya), dan Alerts
          (placeholder 0 — fitur Alerts menyusul di Tahap 3).
        - Chart "Content Changes Over Time" (line chart SVG 7 hari terakhir)
          beserta legenda Total/Added/Removed/Updated.
        - Panel "Top Changes Detected" (5 perubahan terbaru lintas website).
        - Panel "Recent Alerts" (placeholder empty state, Tahap 3).
        - Tabel "Monitored Websites" (maks 5 baris + tautan "Lihat semua
          website").

        Bila pemuatan data gagal, tampilkan indikasi kesalahan tanpa mengubah
        data tersimpan (Req 10.8 — prinsip yang sama dipertahankan pada
        Overview).
        """
        repository = request.app.state.repository
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        range_end = today_start + timedelta(days=1)
        range_start = range_end - timedelta(days=7)
        prev_start = range_start - timedelta(days=7)

        try:
            total_websites = len(await repository.list_websites())
            pages_monitored = await repository.count_pages_monitored()
            changes_this_week = await repository.count_change_events_between(
                range_start, range_end
            )
            changes_prev_week = await repository.count_change_events_between(
                prev_start, range_start
            )
            daily_counts = await repository.daily_change_counts(
                range_start, range_end
            )
            week_events = await repository.list_change_events_between(
                range_start, range_end
            )
            recent_events = await repository.recent_change_events(
                limit=TOP_CHANGES_LIMIT
            )
            stats = await repository.website_stats()
            # Alerts (Tahap 3): kartu KPI "Alerts" = jumlah unread + jumlah
            # critical (Req deskripsi tugas bagian G), lalu panel "Recent
            # Alerts" menampilkan beberapa Alert terbaru lintas website.
            alerts_unread = await repository.count_alerts_filtered(status="unread")
            alerts_critical = await repository.count_alerts_filtered(
                severity="critical"
            )
            alerts_needing_attention = alerts_unread + alerts_critical
            recent_alerts_raw = await repository.recent_alerts(limit=5)
        except Exception:
            return await _render(
                request,
                templates,
                "index.html",
                {"load_error": True},
                status_code=500,
            )

        # Legenda chart: hitung tipe perubahan dari event dalam rentang 7 hari
        # memakai classify_change (murni, tanpa I/O).
        added_count = removed_count = updated_count = 0
        for evt in week_events:
            change_type = classify_change(evt.diff)
            if change_type == "added":
                added_count += 1
            elif change_type == "removed":
                removed_count += 1
            else:
                updated_count += 1

        # Persentase perubahan minggu ini vs minggu sebelumnya (panah naik/turun).
        if changes_prev_week > 0:
            change_pct = round(
                (changes_this_week - changes_prev_week) / changes_prev_week * 100
            )
        elif changes_this_week > 0:
            change_pct = 100
        else:
            change_pct = 0

        # Perkaya 5 perubahan terbaru dengan info website (domain/nama) untuk
        # avatar + label pada panel "Top Changes Detected".
        top_changes = []
        website_cache: dict = {}
        for evt in recent_events:
            website = website_cache.get(evt.website_id)
            if website is None:
                website = await repository.get_website(evt.website_id)
                website_cache[evt.website_id] = website
            top_changes.append(
                {
                    "event": evt,
                    "website": website,
                    "change_type": classify_change(evt.diff),
                    "description": describe_change(evt.diff),
                }
            )

        chart_labels = [day for day, _ in daily_counts]
        chart_counts = [count for _, count in daily_counts]

        # Perkaya Alert terbaru dengan info website (domain/nama) untuk
        # avatar + label pada panel "Recent Alerts".
        recent_alerts = []
        for alert in recent_alerts_raw:
            website = website_cache.get(alert.website_id)
            if website is None:
                website = await repository.get_website(alert.website_id)
                website_cache[alert.website_id] = website
            recent_alerts.append({"alert": alert, "website": website})

        return await _render(
            request,
            templates,
            "index.html",
            {
                "load_error": False,
                "total_websites": total_websites,
                "pages_monitored": pages_monitored,
                "changes_this_week": changes_this_week,
                "change_pct": change_pct,
                "chart_labels": chart_labels,
                "chart_counts": chart_counts,
                "legend_added": added_count,
                "legend_removed": removed_count,
                "legend_updated": updated_count,
                "legend_total": added_count + removed_count + updated_count,
                "top_changes": top_changes,
                "website_stats": stats[:5],
                "website_stats_total": len(stats),
                "alerts_needing_attention": alerts_needing_attention,
                "recent_alerts": recent_alerts,
            },
        )

    @app.get("/websites", response_class=HTMLResponse)
    async def websites_page(
        request: Request,
        q: Optional[str] = None,
        page: int = 1,
        added: Optional[str] = None,
        updated: Optional[str] = None,
    ) -> HTMLResponse:
        """Halaman Websites ContentMonitor: tabel penuh + search + pagination
        + form tambah website (Tahap 1).

        Query param ``q`` memfilter berdasarkan nama/domain (server-side,
        case-insensitive). Query param ``page`` (1-indexed, 10 baris/halaman)
        membatasi baris yang dirender; halaman di luar rentang dijepit ke
        rentang valid TANPA error (Req desain umum ketahanan input).
        """
        context = await _load_websites_context(request, q=q, page=page)
        context["added"] = added
        context["updated"] = updated
        status_code = 500 if context["load_error"] else 200
        return await _render(
            request, templates, "websites.html", context, status_code=status_code
        )

    @app.get("/websites/{website_id}", response_class=HTMLResponse)
    async def website_history(request: Request, website_id: str) -> HTMLResponse:
        """Tampilkan riwayat Change_Event sebuah Monitored_Website (Req 10.4, 10.7).

        Riwayat ditampilkan terbaru→terlama; pengurutan menurun berdasarkan
        ``detected_at`` dijamin oleh ``Repository.list_change_events`` (Req 10.4).
        Bila website belum memiliki Change_Event, ditampilkan empty state
        "belum ada perubahan tercatat" (Req 10.7). Nama/domain website disertakan
        pada header halaman.

        Bila website tidak ditemukan, ditampilkan indikasi tidak ditemukan (404).
        Bila pemuatan data gagal, ditampilkan indikasi kesalahan tanpa mengubah
        data tersimpan (Req 10.8).
        """
        repository = request.app.state.repository
        try:
            website = await repository.get_website(website_id)
            if website is None:
                return await _render(
                    request,
                    templates,
                    "website_history.html",
                    {"not_found": True, "website_id": website_id},
                    status_code=HTTP_NOT_FOUND,
                )
            events = await repository.list_change_events(website_id)
        except Exception:
            # Req 10.8: indikasi kesalahan pemuatan; hanya baca, tanpa mutasi.
            return await _render(
                request,
                templates,
                "website_history.html",
                {"load_error": True, "website_id": website_id},
                status_code=500,
            )

        return await _render(
            request,
            templates,
            "website_history.html",
            {
                "website": website,
                "events": events,
                "load_error": False,
                "not_found": False,
            },
        )

    @app.get("/events/{event_id}", response_class=HTMLResponse)
    async def event_detail(request: Request, event_id: str) -> HTMLResponse:
        """Tampilkan Diff yang terkait dengan sebuah Change_Event (Req 10.5).

        Menampilkan seluruh bidang Diff: teks ditambah/dihapus, link
        ditambah/dihapus, section ditambah/dihapus, serta gambar
        ditambah/dihapus/berubah.

        Bila ``event_id`` tidak ditemukan, ditampilkan indikasi tidak ditemukan
        (404). Bila pemuatan data gagal, ditampilkan indikasi kesalahan tanpa
        mengubah data tersimpan (Req 10.8).
        """
        repository = request.app.state.repository
        try:
            event = await repository.get_change_event(event_id)
        except Exception:
            return await _render(
                request,
                templates,
                "event_detail.html",
                {"load_error": True, "event_id": event_id},
                status_code=500,
            )

        if event is None:
            return await _render(
                request,
                templates,
                "event_detail.html",
                {"not_found": True, "event_id": event_id},
                status_code=HTTP_NOT_FOUND,
            )

        return await _render(
            request,
            templates,
            "event_detail.html",
            {"event": event, "diff": event.diff, "not_found": False, "load_error": False},
        )

    # --- Manajemen konfigurasi (task 14.3; dipindah ke halaman Websites pada
    # ContentMonitor Tahap 1) ------------------------------------------------ #

    async def _render_index_error(
        request: Request, message: str, context_key: str = "add_error"
    ) -> HTMLResponse:
        """Render ulang halaman Websites dengan indikasi kesalahan aksi.

        Digunakan ketika ``POST /websites`` ditolak (domain/interval tidak
        valid, duplikat, atau kapasitas penuh) maupun ketika
        ``POST /websites/{id}/edit`` ditolak (interval tidak valid). Formulir
        tambah website & tabel pengelolaan kini berada di halaman Websites
        (Tahap 1), sehingga pesan kesalahan dirender di sana. Konteks tabel
        (pencarian/pagination) tetap dimuat agar halaman lengkap; bila
        pemuatan juga gagal, daftar kosong dipakai sehingga pesan kesalahan
        tetap dapat ditampilkan. Penolakan aksi tidak mengubah Data_Store.

        ``context_key`` menentukan kunci konteks template yang membawa pesan
        (``add_error`` untuk penambahan, ``edit_error`` untuk pengubahan).
        """
        context = await _load_websites_context(request, q=None, page=1)
        context["added"] = None
        context["updated"] = None
        context[context_key] = message
        return await _render(
            request,
            templates,
            "websites.html",
            context,
            status_code=HTTP_BAD_REQUEST,
        )

    async def _render_add_error(request: Request, message: str) -> HTMLResponse:
        """Alias historis untuk kesalahan penambahan (lihat _render_index_error)."""
        return await _render_index_error(request, message, "add_error")

    @app.post("/websites")
    async def add_website(
        request: Request,
        domain: str = Form(...),
        name: Optional[str] = Form(None),
        poll_interval_seconds: Optional[str] = Form(None),
    ) -> Any:
        """Tambahkan sebuah Monitored_Website baru (Req 1.1, 1.4, 1.6, 1.7, 1.8).

        Alur:

        1. Validasi format domain via :func:`validate_domain`; bila tidak valid,
           tolak dengan pesan kesalahan yang menjelaskan format yang diharapkan
           (Req 1.4/1.1).
        2. Bila ``poll_interval_seconds`` diberikan (tidak kosong), parse sebagai
           bilangan bulat lalu validasi rentang 10..86400 detik; bila tidak
           valid tolak dengan pesan (Req 1.6/1.9). Bila kosong, simpan ``None``
           (memakai Polling_Interval global).
        3. Bangun :class:`WebsiteConfig` (``id`` via ``uuid4``, ``created_at``
           = waktu saat ini) dan panggil ``repository.add_website``.
        4. Duplikat (:class:`DuplicateWebsiteError`) maupun kapasitas penuh
           (:class:`CapacityExceededError`) ditolak dengan pesan kesalahan
           tanpa membuat aplikasi berhenti (Req 1.7, 1.8).
        5. Pada keberhasilan, redirect (303) ke ``/`` dengan indikasi konfirmasi
           bahwa domain berhasil ditambahkan (Req 1.1).
        """
        repository = request.app.state.repository

        # 1. Validasi domain (Req 1.4/1.1).
        domain = (domain or "").strip()
        domain_result = validate_domain(domain)
        if not domain_result.ok:
            return await _render_add_error(request, domain_result.error_message)

        # 2. Polling_Interval khusus (opsional) (Req 1.6/1.9).
        parsed_interval: Optional[int] = None
        if poll_interval_seconds is not None and str(poll_interval_seconds).strip():
            raw = str(poll_interval_seconds).strip()
            try:
                parsed_interval = int(raw)
            except (TypeError, ValueError):
                # Non-integer: pakai pesan validasi rentang interval.
                interval_result = validate_poll_interval_seconds(raw)
                return await _render_add_error(
                    request, interval_result.error_message
                )
            interval_result = validate_poll_interval_seconds(parsed_interval)
            if not interval_result.ok:
                return await _render_add_error(
                    request, interval_result.error_message
                )

        # 3. Bangun konfigurasi & simpan (Req 1.1).
        cfg = WebsiteConfig(
            id=str(uuid4()),
            domain=domain,
            name=(name or "").strip() or domain,
            poll_interval_seconds=parsed_interval,
            created_at=datetime.now(),
        )
        try:
            await repository.add_website(cfg)
        except (DuplicateWebsiteError, CapacityExceededError) as exc:
            # Req 1.7 / 1.8: tolak dengan pesan kesalahan, jangan berhenti.
            return await _render_add_error(request, str(exc))

        # 5. Konfirmasi via Post/Redirect/Get (Req 1.1). Form tambah website
        # kini berada di halaman Websites (Tahap 1).
        return RedirectResponse(
            url=f"/websites?added={cfg.domain}", status_code=HTTP_SEE_OTHER
        )

    @app.delete("/websites/{website_id}")
    async def delete_website(request: Request, website_id: str) -> JSONResponse:
        """Hentikan pemantauan sebuah Monitored_Website (Req 1.2).

        Memanggil ``repository.remove_website`` (idempoten) dan mengembalikan
        indikasi keberhasilan berupa JSON. Pemantauan domain akan berhenti pada
        siklus pemeriksaan berikutnya (Req 1.2).
        """
        repository = request.app.state.repository
        await repository.remove_website(website_id)
        return JSONResponse(
            {
                "deleted": True,
                "website_id": website_id,
                "message": "Monitored_Website berhasil dihapus.",
            }
        )

    @app.post("/websites/{website_id}/delete")
    async def delete_website_form(
        request: Request, website_id: str
    ) -> RedirectResponse:
        """Alias ramah-form untuk menghapus Monitored_Website (Req 1.2).

        Form HTML tidak dapat mengirim metode DELETE secara langsung, sehingga
        rute POST ini disediakan agar tombol hapus per baris pada dashboard
        dapat bekerja. Setelah penghapusan, redirect (303) kembali ke ``/``.
        """
        repository = request.app.state.repository
        await repository.remove_website(website_id)
        return RedirectResponse(url="/", status_code=HTTP_SEE_OTHER)

    # --- Aksi "Cek Sekarang" (pemeriksaan manual segera) ------------------ #

    @app.post("/websites/{website_id}/check")
    async def check_website_now(
        request: Request, website_id: str
    ) -> JSONResponse:
        """Jalankan pemeriksaan segera untuk satu Monitored_Website (Req 8.4).

        Rute ini dipanggil oleh tombol "Cek Sekarang" pada dashboard, sehingga
        seluruh respons berbentuk JSON (bukan redirect) agar frontend dapat
        menampilkan indikator loading lalu merender ringkasan hasil.

        Perilaku:

        1. Bila ``website_id`` tidak ditemukan -> HTTP 404 dengan pesan JSON.
        2. Bila aplikasi dibuat tanpa ``orchestrator`` -> HTTP 503 dengan pesan
           JSON yang jelas (merosot anggun, aplikasi tetap hidup).
        3. Bila pemeriksaan untuk website yang SAMA sedang berjalan -> HTTP 409
           dengan pesan bahwa pemeriksaan sedang berlangsung. Penanda in-flight
           disimpan pada ``app.state.checks_in_flight`` dan SELALU dibersihkan
           pada blok ``finally``.
        4. Pada keberhasilan -> HTTP 200 dengan ringkasan ``CheckResult``.
        5. Bila orchestrator mengangkat pengecualian -> HTTP 500 dengan
           ``{"ok": false, "error": ...}``; pengecualian tidak pernah dibiarkan
           menghentikan aplikasi (selaras Req 6.x/9.4 tentang ketahanan).
        """
        repository = request.app.state.repository
        orchestrator = getattr(request.app.state, "orchestrator", None)

        # 1. Pastikan website ada (Req 1.2/8.4).
        try:
            website = await repository.get_website(website_id)
        except Exception as exc:  # noqa: BLE001 - jangan sampai app berhenti
            return JSONResponse(
                {
                    "ok": False,
                    "website_id": website_id,
                    "error": "Gagal memuat Monitored_Website: {0}".format(exc),
                },
                status_code=HTTP_INTERNAL_ERROR,
            )
        if website is None:
            return JSONResponse(
                {
                    "ok": False,
                    "website_id": website_id,
                    "error": "Monitored_Website tidak ditemukan.",
                },
                status_code=HTTP_NOT_FOUND,
            )

        # 2. Orchestrator tidak tersedia -> merosot anggun (503).
        if orchestrator is None:
            return JSONResponse(
                {
                    "ok": False,
                    "website_id": website_id,
                    "error": (
                        "Pemeriksaan manual tidak tersedia: orchestrator tidak "
                        "dikonfigurasi pada aplikasi ini."
                    ),
                },
                status_code=HTTP_SERVICE_UNAVAILABLE,
            )

        # 3. Cegah pemeriksaan ganda bersamaan untuk website yang sama (409).
        in_flight = request.app.state.checks_in_flight
        if website_id in in_flight:
            return JSONResponse(
                {
                    "ok": False,
                    "website_id": website_id,
                    "error": (
                        "Pemeriksaan untuk website ini sedang berjalan; "
                        "tunggu hingga selesai."
                    ),
                },
                status_code=HTTP_CONFLICT,
            )
        in_flight.add(website_id)
        try:
            result = await orchestrator.check_website(website)
        except Exception as exc:  # noqa: BLE001 - 5. jangan crash aplikasi
            return JSONResponse(
                {
                    "ok": False,
                    "website_id": website_id,
                    "error": "Pemeriksaan gagal: {0}".format(exc),
                },
                status_code=HTTP_INTERNAL_ERROR,
            )
        finally:
            # Selalu bersihkan penanda in-flight.
            in_flight.discard(website_id)

        # 4. Ringkas CheckResult menjadi payload JSON.
        return JSONResponse(
            {
                "ok": True,
                "website_id": website_id,
                "domain": getattr(website, "domain", None),
                "status": getattr(result, "status", None),
                "pages_checked": getattr(result, "pages_checked", 0),
                "changes_detected": getattr(result, "changes_detected", 0),
                "notifications_sent": getattr(result, "notifications_sent", 0),
                "page_failures": getattr(result, "page_failures", 0),
                "image_failures": getattr(result, "image_failures", 0),
                "snapshot_save_failures": getattr(
                    result, "snapshot_save_failures", 0
                ),
                "event_save_failures": getattr(result, "event_save_failures", 0),
                "alerts_created": getattr(result, "alerts_created", 0),
                "last_check_updated": getattr(result, "last_check_updated", None),
                "discovery_failed": getattr(result, "discovery_failed", None),
                "errors": list(getattr(result, "errors", []) or []),
            }
        )

    # --- Aksi Edit (ubah nama & Polling_Interval) ------------------------- #

    @app.post("/websites/{website_id}/edit")
    async def edit_website(
        request: Request,
        website_id: str,
        name: Optional[str] = Form(None),
        poll_interval_seconds: Optional[str] = Form(None),
    ) -> Any:
        """Ubah nama dan/atau Polling_Interval sebuah Monitored_Website (Req 1.3).

        Alur:

        1. Ambil konfigurasi yang ada; bila tidak ditemukan -> HTTP 404.
        2. Bila ``poll_interval_seconds`` tidak kosong, parse sebagai bilangan
           bulat lalu validasi rentang 10..86400 detik (Req 1.6/1.9). Bila
           tidak valid, render ulang ``index.html`` dengan ``edit_error`` dan
           HTTP 400 tanpa mengubah Data_Store. Bila kosong, simpan ``None``
           sehingga website memakai Polling_Interval global.
        3. Bila ``name`` kosong, nama yang ada dipertahankan (tidak dikosongkan).
        4. Bangun konfigurasi baru via ``dataclasses.replace`` lalu panggil
           ``repository.update_website``. Perubahan berlaku pada siklus
           pemeriksaan berikutnya (Req 1.3).
        5. Pada keberhasilan, redirect (303) ke ``/?updated=<domain>`` sebagai
           konfirmasi (pola Post/Redirect/Get).
        """
        repository = request.app.state.repository

        # 1. Website harus ada.
        existing = await repository.get_website(website_id)
        if existing is None:
            return JSONResponse(
                {
                    "ok": False,
                    "website_id": website_id,
                    "error": "Monitored_Website tidak ditemukan.",
                },
                status_code=HTTP_NOT_FOUND,
            )

        # 2. Polling_Interval khusus (opsional) (Req 1.6/1.9).
        parsed_interval: Optional[int] = None
        if poll_interval_seconds is not None and str(poll_interval_seconds).strip():
            raw = str(poll_interval_seconds).strip()
            try:
                parsed_interval = int(raw)
            except (TypeError, ValueError):
                interval_result = validate_poll_interval_seconds(raw)
                return await _render_index_error(
                    request, interval_result.error_message, "edit_error"
                )
            interval_result = validate_poll_interval_seconds(parsed_interval)
            if not interval_result.ok:
                return await _render_index_error(
                    request, interval_result.error_message, "edit_error"
                )

        # 3. Nama kosong -> pertahankan nama yang ada.
        new_name = (name or "").strip() or existing.name

        # 4. Simpan perubahan (Req 1.3).
        cfg = replace(
            existing, name=new_name, poll_interval_seconds=parsed_interval
        )
        await repository.update_website(cfg)

        # 5. Konfirmasi via Post/Redirect/Get. Pengaturan website kini dikelola
        # dari halaman Websites (Tahap 1).
        return RedirectResponse(
            url="/websites?updated={0}".format(cfg.domain),
            status_code=HTTP_SEE_OTHER,
        )

    # --- Aksi Jeda/Lanjutkan (status Aktif/Jeda per website, Tahap 1) ------ #

    @app.post("/websites/{website_id}/pause")
    async def pause_website(
        request: Request,
        website_id: str,
        next: Optional[str] = Form(None),  # noqa: A002 - nama field form
    ) -> RedirectResponse:
        """Jeda pemantauan sebuah Monitored_Website (ContentMonitor Tahap 1).

        Memanggil ``repository.set_website_paused(website_id, True)``.
        Scheduler melewati website yang dijeda pada siklus penjadwalan
        berikutnya (tidak dihapus, hanya tidak dijadwalkan). Redirect (303)
        kembali ke halaman asal (``next``, bila diberikan dan berupa path
        relatif) atau ke ``/websites`` sebagai bawaan.
        """
        repository = request.app.state.repository
        await repository.set_website_paused(website_id, True)
        return RedirectResponse(
            url=_safe_redirect_target(next, "/websites"),
            status_code=HTTP_SEE_OTHER,
        )

    @app.post("/websites/{website_id}/resume")
    async def resume_website(
        request: Request,
        website_id: str,
        next: Optional[str] = Form(None),  # noqa: A002 - nama field form
    ) -> RedirectResponse:
        """Lanjutkan pemantauan sebuah Monitored_Website yang dijeda (Tahap 1).

        Memanggil ``repository.set_website_paused(website_id, False)``.
        Redirect (303) kembali ke halaman asal (``next``) atau ``/websites``.
        """
        repository = request.app.state.repository
        await repository.set_website_paused(website_id, False)
        return RedirectResponse(
            url=_safe_redirect_target(next, "/websites"),
            status_code=HTTP_SEE_OTHER,
        )

    # --- Halaman Content Changes (ContentMonitor Tahap 2) ----------------- #

    @app.get("/changes", response_class=HTMLResponse)
    async def changes_page(
        request: Request,
        start: Optional[str] = None,
        end: Optional[str] = None,
        website: Optional[str] = None,
        type: Optional[str] = None,  # noqa: A002 - nama query param
        q: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Halaman Content Changes: daftar SEMUA perubahan lintas website
        (ContentMonitor Tahap 2).

        4 kartu KPI (Semua/Ditambahkan/Dihapus/Diperbarui) dihitung dari
        rentang tanggal AKTIF (setelah divalidasi), tidak terpengaruh filter
        website/tipe/search lainnya. Filter ``start``/``end``/``website``/
        ``type``/``q`` dapat dikombinasikan bebas; rentang tanggal tidak
        valid (format salah atau ``end < start``) jatuh ke bawaan 7 hari
        terakhir dengan peringatan halus (``used_default_range``), TIDAK
        pernah error 500. Pendekatan filter tipe di Python dijelaskan pada
        :func:`_load_changes_context`.
        """
        context = await _load_changes_context(
            request,
            start=start,
            end=end,
            website_id=website,
            change_type=type,
            q=q,
            page=page,
        )
        status_code = 500 if context["load_error"] else 200
        return await _render(
            request, templates, "changes.html", context, status_code=status_code
        )

    # --- Halaman Pages (ContentMonitor Tahap 2) ---------------------------- #

    @app.get("/pages", response_class=HTMLResponse)
    async def pages_page(
        request: Request,
        status: Optional[str] = None,
        website: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Halaman Pages: daftar/pemantauan seluruh halaman terpantau
        (ContentMonitor Tahap 2).

        Halaman ditemukan OTOMATIS oleh crawler (Keputusan #1 HANDOFF.md) —
        TIDAK ada tombol tambah/hapus halaman maupun interval per-halaman;
        Interval yang ditampilkan adalah interval EFEKTIF website induknya.
        Tab ``status`` (all/active/paused/broken) memfilter + menghitung
        jumlah pada tiap tab; search (``q``) mencocokkan path/URL; ``website``
        membatasi ke satu Monitored_Website; ``page`` mengatur pagination.
        """
        context = await _load_pages_context(
            request, status=status, website_id=website, q=q, page=page
        )
        status_code = 500 if context["load_error"] else 200
        return await _render(
            request, templates, "pages.html", context, status_code=status_code
        )

    @app.post("/pages/pause")
    async def pause_page(
        request: Request,
        url: str = Form(...),
        next: Optional[str] = Form(None),  # noqa: A002 - nama field form
    ) -> RedirectResponse:
        """Jeda pemantauan satu halaman secara individual (Tahap 2).

        Memanggil ``repository.set_page_paused(url, True)``. Redirect (303)
        kembali ke halaman asal (``next``, biasanya ``/pages`` dengan filter
        yang sedang aktif dipertahankan lewat query string) atau ``/pages``
        sebagai bawaan.
        """
        repository = request.app.state.repository
        await repository.set_page_paused(url, True)
        return RedirectResponse(
            url=_safe_redirect_target(next, "/pages"),
            status_code=HTTP_SEE_OTHER,
        )

    @app.post("/pages/resume")
    async def resume_page(
        request: Request,
        url: str = Form(...),
        next: Optional[str] = Form(None),  # noqa: A002 - nama field form
    ) -> RedirectResponse:
        """Lanjutkan pemantauan satu halaman yang sebelumnya dijeda (Tahap 2).

        Memanggil ``repository.set_page_paused(url, False)``. Redirect (303)
        kembali ke halaman asal (``next``) atau ``/pages``.
        """
        repository = request.app.state.repository
        await repository.set_page_paused(url, False)
        return RedirectResponse(
            url=_safe_redirect_target(next, "/pages"),
            status_code=HTTP_SEE_OTHER,
        )

    # --- Halaman Alerts (ContentMonitor Tahap 3) --------------------------- #

    @app.get("/alerts", response_class=HTMLResponse)
    async def alerts_page(
        request: Request,
        tab: Optional[str] = None,
        website: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Halaman Alerts: daftar seluruh Alert lintas website (Tahap 3).

        Tab All/Unread/Critical/Warning/Info dengan jumlah masing-masing;
        search (`q`), filter website, dan pagination server-side (10/halaman).
        """
        context = await _load_alerts_context(
            request, tab=tab, website_id=website, q=q, page=page
        )
        status_code = 500 if context["load_error"] else 200
        return await _render(
            request, templates, "alerts.html", context, status_code=status_code
        )

    @app.post("/alerts/{alert_id}/read")
    async def mark_alert_read(
        request: Request,
        alert_id: str,
        next: Optional[str] = Form(None),  # noqa: A002 - nama field form
    ) -> RedirectResponse:
        """Tandai sebuah Alert sebagai 'read' (Tahap 3).

        Redirect (303) kembali ke halaman asal (``next``, biasanya ``/alerts``
        dengan filter aktif dipertahankan lewat query string) atau ``/alerts``.
        """
        repository = request.app.state.repository
        await repository.mark_alert_status(alert_id, "read")
        return RedirectResponse(
            url=_safe_redirect_target(next, "/alerts"),
            status_code=HTTP_SEE_OTHER,
        )

    @app.post("/alerts/{alert_id}/resolve")
    async def resolve_alert(
        request: Request,
        alert_id: str,
        next: Optional[str] = Form(None),  # noqa: A002 - nama field form
    ) -> RedirectResponse:
        """Tandai sebuah Alert sebagai 'resolved' (Tahap 3).

        Redirect (303) kembali ke halaman asal (``next``) atau ``/alerts``.
        """
        repository = request.app.state.repository
        await repository.mark_alert_status(alert_id, "resolved")
        return RedirectResponse(
            url=_safe_redirect_target(next, "/alerts"),
            status_code=HTTP_SEE_OTHER,
        )

    # --- Halaman Settings (ContentMonitor Tahap 4) ----------------------- #

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(
        request: Request,
        tab: Optional[str] = None,
        saved: Optional[str] = None,
        deleted: Optional[str] = None,
    ) -> HTMLResponse:
        """Halaman Settings: General / Monitoring / Notifications / Keywords (Tahap 4)."""
        repository = request.app.state.repository
        active_tab = tab if tab in ("general", "monitoring", "notifications", "keywords") else "general"

        # Baca konfigurasi dari app_config
        instance_name = (await repository.get_app_config("instance_name")) or "ContentMonitor"
        poll_raw = await repository.get_app_config("global_poll_interval_seconds")
        try:
            poll_seconds = int(poll_raw) if poll_raw else DEFAULT_GLOBAL_INTERVAL_SECONDS
        except ValueError:
            poll_seconds = DEFAULT_GLOBAL_INTERVAL_SECONDS
        poll_interval_minutes = max(1, poll_seconds // 60)

        snapshot_retention_raw = await repository.get_app_config("snapshot_retention")
        try:
            snapshot_retention = int(snapshot_retention_raw) if snapshot_retention_raw else 5
        except ValueError:
            snapshot_retention = 5

        change_retention_raw = await repository.get_app_config("change_event_retention_days")
        try:
            change_retention_days = int(change_retention_raw) if change_retention_raw else 90
        except ValueError:
            change_retention_days = 90

        # Telegram: cek keberadaan kredensial via env (TIDAK tampilkan nilainya)
        telegram_configured = bool(
            os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")
        )

        # Keywords (tab keywords): ambil semua kata kunci + enrichment website name
        keywords_enriched: list = []
        websites: list = []
        if active_tab == "keywords":
            try:
                websites = await repository.list_websites()
                all_kws = await repository.list_keywords()
                web_map = {w.id: w for w in websites}
                for kw in all_kws:
                    w = web_map.get(kw.website_id)
                    keywords_enriched.append({
                        "keyword": kw,
                        "website_name": w.name if w else kw.website_id,
                    })
            except Exception:
                pass

        context = {
            "active_nav": "settings",
            "tab": active_tab,
            "saved": bool(saved),
            "deleted": bool(deleted),
            "error_message": "",
            "instance_name": instance_name,
            "poll_interval_minutes": poll_interval_minutes,
            "snapshot_retention": snapshot_retention,
            "change_retention_days": change_retention_days,
            "telegram_configured": telegram_configured,
            "keywords": keywords_enriched,
            "websites": websites,
        }
        return await _render(request, templates, "settings.html", context)

    @app.post("/settings", response_class=HTMLResponse)
    async def settings_save(
        request: Request,
        tab: str = Form("general"),
        instance_name: Optional[str] = Form(None),
        poll_interval_minutes: Optional[int] = Form(None),
        snapshot_retention: Optional[int] = Form(None),
        change_retention_days: Optional[int] = Form(None),
    ) -> RedirectResponse:
        """Simpan pengaturan dari form Settings (Tahap 4)."""
        repository = request.app.state.repository

        if tab == "general" and instance_name is not None:
            await repository.set_app_config("instance_name", instance_name.strip()[:100])

        if tab == "monitoring":
            if poll_interval_minutes is not None:
                clamped = max(1, min(1440, poll_interval_minutes))
                await repository.set_app_config(
                    "global_poll_interval_seconds", str(clamped * 60)
                )
            if snapshot_retention is not None:
                clamped_sr = max(2, min(100, snapshot_retention))
                await repository.set_app_config(
                    "snapshot_retention", str(clamped_sr)
                )
            if change_retention_days is not None:
                clamped_cr = max(1, min(365, change_retention_days))
                await repository.set_app_config(
                    "change_event_retention_days", str(clamped_cr)
                )

        return RedirectResponse(
            url=f"/settings?tab={tab}&saved=1",
            status_code=HTTP_SEE_OTHER,
        )

    @app.post("/settings/test-notification")
    async def settings_test_notification(request: Request) -> JSONResponse:
        """Kirim notifikasi tes Telegram (Tahap 4)."""
        notifier = getattr(request.app.state, "notifier", None)
        if notifier is None:
            return JSONResponse({"ok": False, "error": "Notifier tidak tersedia."})
        notify_text = getattr(notifier, "notify_text", None)
        if notify_text is None:
            return JSONResponse({"ok": False, "error": "Notifier tidak mendukung notify_text."})
        try:
            await notify_text("🔔 Tes notifikasi ContentMonitor berhasil!")
            return JSONResponse({"ok": True})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)})

    @app.post("/settings/keywords")
    async def settings_add_keyword(
        request: Request,
        website_id: str = Form(...),
        keyword: str = Form(...),
        mode: str = Form("must_exist"),
    ) -> RedirectResponse:
        """Tambahkan kata kunci pemantauan (Tahap 4)."""
        repository = request.app.state.repository
        kw_text = keyword.strip()
        if kw_text and mode in ("must_exist", "must_not_exist"):
            try:
                await repository.add_keyword(
                    id=str(uuid4()),
                    website_id=website_id,
                    keyword=kw_text,
                    mode=mode,
                )
            except Exception:
                pass
        return RedirectResponse(
            url="/settings?tab=keywords&saved=1",
            status_code=HTTP_SEE_OTHER,
        )

    @app.post("/settings/keywords/delete")
    async def settings_delete_keyword(
        request: Request,
        keyword_id: str = Form(...),
    ) -> RedirectResponse:
        """Hapus kata kunci pemantauan (Tahap 4)."""
        repository = request.app.state.repository
        try:
            await repository.remove_keyword(keyword_id)
        except Exception:
            pass
        return RedirectResponse(
            url="/settings?tab=keywords&deleted=1",
            status_code=HTTP_SEE_OTHER,
        )

    # --- Halaman Reports (ContentMonitor Tahap 5) -------------------------- #

    @app.get("/reports", response_class=HTMLResponse)
    async def reports_page(
        request: Request,
        start: Optional[str] = None,
        end: Optional[str] = None,
        generated: Optional[str] = None,
        deleted: Optional[str] = None,
    ) -> HTMLResponse:
        """Halaman Reports: KPI, chart, donut, & Riwayat Laporan (Tahap 5)."""
        context = await _load_reports_context(request, start=start, end=end)
        context["generated"] = generated
        context["deleted"] = deleted
        status_code = 500 if context["load_error"] else 200
        return await _render(
            request, templates, "reports.html", context, status_code=status_code
        )

    async def _export_rows(
        repository: Any,
        export_type: str,
        range_start: datetime,
        range_end: datetime,
        website_filter: Optional[str],
    ):
        """Generator baris CSV untuk ``export_type`` (bertahap, Req efisiensi)."""
        if export_type == "changes":
            website_cache: dict = {}
            async for evt in repository.iter_change_events_for_export(
                range_start, range_end, website_id=website_filter
            ):
                website = website_cache.get(evt.website_id)
                if website is None:
                    website = await repository.get_website(evt.website_id)
                    website_cache[evt.website_id] = website
                yield change_event_csv_row(evt, website)
        elif export_type == "alerts":
            website_cache = {}
            async for alert in repository.iter_alerts_for_export(
                range_start, range_end, website_id=website_filter
            ):
                website = website_cache.get(alert.website_id)
                if website is None:
                    website = await repository.get_website(alert.website_id)
                    website_cache[alert.website_id] = website
                yield alert_csv_row(alert, website)
        else:  # "websites" -- SELALU seluruh website, tidak difilter.
            stats = await repository.website_stats()
            totals = await repository.count_change_events_total_by_website()
            for overview in stats:
                yield website_overview_csv_row(
                    overview, totals.get(overview.website.id, 0)
                )

    @app.get("/reports/export")
    async def export_report(
        request: Request,
        type: str,  # noqa: A002 - nama query param
        start: Optional[str] = None,
        end: Optional[str] = None,
        website: Optional[str] = None,
    ) -> Any:
        """Ekspor CSV langsung (unduhan, TANPA disimpan ke Riwayat Laporan):

        - ``?type=changes`` -> riwayat perubahan
        - ``?type=alerts``  -> alert
        - ``?type=websites`` -> ringkasan per website (tidak menerima
          ``start``/``end``/``website``, selalu SELURUH website)

        Rentang tanggal tidak valid (format salah atau ``end < start``) ->
        HTTP 400 dengan pesan jelas (Req Tahap 5 bagian B), TIDAK PERNAH 500.
        Baris di-stream bertahap (``StreamingResponse``) sehingga tabel besar
        tidak dimuat seluruhnya ke memori.
        """
        if type not in VALID_REPORT_TYPES:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Jenis ekspor tidak dikenal: {0!r} (harus salah "
                    "satu dari {1}).".format(type, VALID_REPORT_TYPES),
                },
                status_code=HTTP_BAD_REQUEST,
            )

        repository = request.app.state.repository
        website_filter = website or None

        if type == "websites":
            # Ekspor "websites" bersifat snapshot SELURUH website saat ini;
            # tidak ada rentang tanggal yang relevan untuk nama berkas.
            filename = build_export_filename(
                "websites", datetime.now().date().isoformat()
            )
            rows = _export_rows(repository, type, datetime.min, datetime.max, website_filter)
            header = WEBSITES_CSV_HEADER
        else:
            try:
                range_start, range_end = _parse_export_date_range(start, end)
            except ValueError as exc:
                return JSONResponse(
                    {"ok": False, "error": str(exc)}, status_code=HTTP_BAD_REQUEST
                )
            filename = build_export_filename(
                type,
                range_start.date().isoformat(),
                (range_end - timedelta(days=1)).date().isoformat(),
            )
            rows = _export_rows(
                repository, type, range_start, range_end, website_filter
            )
            header = CHANGES_CSV_HEADER if type == "changes" else ALERTS_CSV_HEADER

        return StreamingResponse(
            stream_csv(header, rows),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="{0}"'.format(
                    filename
                )
            },
        )

    @app.post("/reports/generate")
    async def generate_report(
        request: Request,
        type: str = Form(...),  # noqa: A002 - nama field form
        start: Optional[str] = Form(None),
        end: Optional[str] = Form(None),
        website: Optional[str] = Form(None),
    ) -> Any:
        """Generate laporan CSV, simpan ke ``reports/``, catat metadata ke
        tabel ``report``, lalu redirect (303) ke ``/reports?generated=<id>``
        (Req Tahap 5 bagian C).

        Rentang tanggal tidak valid -> HTTP 400 (render ulang halaman Reports
        dengan pesan kesalahan), TIDAK PERNAH 500.
        """
        if type not in VALID_REPORT_TYPES:
            context = await _load_reports_context(request, start=None, end=None)
            context["generate_error"] = "Jenis laporan tidak dikenal."
            return await _render(
                request, templates, "reports.html", context, status_code=HTTP_BAD_REQUEST
            )

        repository = request.app.state.repository
        website_filter = website or None

        if type == "websites":
            range_start_iso = datetime.now().date().isoformat()
            range_end_iso = range_start_iso
            range_start, range_end = datetime.min, datetime.max
            header = WEBSITES_CSV_HEADER
        else:
            try:
                range_start, range_end = _parse_export_date_range(start, end)
            except ValueError as exc:
                context = await _load_reports_context(request, start=None, end=None)
                context["generate_error"] = str(exc)
                return await _render(
                    request,
                    templates,
                    "reports.html",
                    context,
                    status_code=HTTP_BAD_REQUEST,
                )
            range_start_iso = range_start.date().isoformat()
            range_end_iso = (range_end - timedelta(days=1)).date().isoformat()
            header = CHANGES_CSV_HEADER if type == "changes" else ALERTS_CSV_HEADER

        report_id = str(uuid4())
        reports_dir: Path = request.app.state.reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)
        stored_filename = build_stored_filename(report_id, type)
        stored_path = reports_dir / stored_filename

        rows = _export_rows(repository, type, range_start, range_end, website_filter)
        row_count = await write_csv_file(stored_path, header, rows)

        type_label = {
            "changes": "Perubahan",
            "alerts": "Alert",
            "websites": "Website",
        }[type]
        if type == "websites":
            name = "Laporan Ringkasan Website ({0})".format(range_start_iso)
            summary = "{0} website".format(row_count)
        else:
            name = "Laporan {0} {1} s/d {2}".format(
                type_label, range_start_iso, range_end_iso
            )
            noun = "perubahan" if type == "changes" else "alert"
            summary = "{0} {1}".format(row_count, noun)

        report = Report(
            id=report_id,
            name=name,
            report_type=type,
            period_start=range_start_iso,
            period_end=range_end_iso,
            created_at=datetime.now(),
            summary=summary,
            row_count=row_count,
            file_path=stored_filename,
        )
        await repository.save_report(report)

        return RedirectResponse(
            url="/reports?generated={0}".format(report_id),
            status_code=HTTP_SEE_OTHER,
        )

    @app.get("/reports/{report_id}/download")
    async def download_report(request: Request, report_id: str) -> Any:
        """Unduh berkas CSV tersimpan sebuah laporan (Req Tahap 5 bagian C).

        - Laporan (baris metadata) tidak ditemukan -> HTTP 404.
        - Berkas fisik hilang dari disk (mis. dihapus manual) -> HTTP 404
          dengan pesan jelas, JANGAN 500.
        - Path hasil resolusi HARUS berada di dalam direktori ``reports/``
          (:func:`resolve_report_path`) -- mencegah path traversal via
          ``file_path`` yang (seharusnya tidak mungkin, tapi) rusak/dimanipulasi.
        """
        repository = request.app.state.repository
        report = await repository.get_report(report_id)
        if report is None:
            return JSONResponse(
                {"ok": False, "error": "Laporan tidak ditemukan."},
                status_code=HTTP_NOT_FOUND,
            )

        reports_dir: Path = request.app.state.reports_dir
        resolved = resolve_report_path(reports_dir, report.file_path)
        if resolved is None or not resolved.is_file():
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Berkas laporan tidak ditemukan di penyimpanan.",
                },
                status_code=HTTP_NOT_FOUND,
            )

        download_name = build_export_filename(
            report.report_type, report.period_start, report.period_end
        )
        return FileResponse(
            path=str(resolved),
            media_type="text/csv",
            filename=download_name,
        )

    @app.post("/reports/{report_id}/delete")
    async def delete_report(request: Request, report_id: str) -> RedirectResponse:
        """Hapus baris metadata laporan + berkas fisiknya (Req Tahap 5 bagian C).

        Berkas yang sudah tidak ada di disk DIABAIKAN (tidak menganggapnya
        error) -- tujuan akhir (baris + berkas tidak ada lagi) tetap tercapai.
        """
        repository = request.app.state.repository
        report = await repository.get_report(report_id)
        if report is not None:
            reports_dir: Path = request.app.state.reports_dir
            resolved = resolve_report_path(reports_dir, report.file_path)
            if resolved is not None and resolved.is_file():
                try:
                    resolved.unlink()
                except OSError:
                    pass
            await repository.delete_report(report_id)
        return RedirectResponse(
            url="/reports?deleted=1", status_code=HTTP_SEE_OTHER
        )

    return app
