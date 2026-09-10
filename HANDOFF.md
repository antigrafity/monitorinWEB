# Serah-Terima: Rebranding ke ContentMonitor

Dokumen ini berisi **konteks proyek**, **keputusan yang sudah diambil**, **rincian pekerjaan**, dan **prompt siap-tempel** untuk setiap tahap. Setiap prompt bersifat mandiri (self-contained) sehingga bisa ditempel di sesi baru tanpa perlu riwayat percakapan sebelumnya.

---

## 1. Konteks Sistem Saat Ini

**Apa ini:** sistem pemantau **perubahan konten** website (bukan uptime monitor). Pengguna memasukkan sebuah domain, sistem otomatis menemukan seluruh halaman di domain itu, mengambil konten + gambar secara berkala, lalu melaporkan apa yang berubah.

**Stack:** Python 3.9.6 · asyncio · httpx · BeautifulSoup+lxml · SQLite (aiosqlite) · FastAPI + Jinja2 · Hypothesis (property-based testing)

**Status:** selesai & berjalan. **546 test lolos** (Tahap 1-3 selesai: Overview/Websites, Content Changes/Pages, Alerts + tracking Title/Meta/SSL). Dashboard aktif di `http://127.0.0.1:8000`.

**Cara jalanin:**
```bash
cd "/Users/macbook/Documents/WEB DEVELOPMENT/MonitoringWebsite"
.venv/bin/python -m monitoring.main          # dashboard di :8000
.venv/bin/python -m pytest -q                # 352 test
```

**Struktur:**
```
src/monitoring/
  config.py                 konstanta + validasi (interval, domain, retensi)
  main.py                   entry point: scheduler + dashboard 1 event loop
  domain/                   LAPISAN MURNI (tanpa I/O) — target property test
    models.py               WebsiteConfig, Snapshot, Diff, ChangeEvent, ChangeSummary
    normalizer.py           normalize_html, extract_text_blocks, extract_links,
                            extract_image_urls, split_sections
    hashing.py              content_hash, image_hash (SHA-256)
    detector.py             detect_changes -> DetectionResult
    urls.py                 normalize_url, is_same_host, dedup_urls, parse_sitemap
    serialization.py        JSON round-trip
  infra/                    LAPISAN I/O
    repository.py           Repository (SQLite) + WebsiteStatusView + LoadState
    fetcher.py              Fetcher (fetch_page, fetch_image) + make_semaphore
    discovery.py            discover_pages (sitemap -> fallback crawl)
    notifier.py             Notifier Telegram + build_summary
    scheduler.py            Scheduler (interval efektif, hot-reload config)
  app/orchestrator.py       CheckOrchestrator.check_website (pipeline 1 website)
  web/
    app.py                  create_app(repository, orchestrator=None) + filter Jinja
    templates/              base.html, index.html, website_history.html, event_detail.html
    static/                 style.css, app.js (buatan sendiri, TANPA CDN)
tests/                      37 file test
monitoring.db               SQLite (data nyata: 3 website terpantau)
```

**Skema database (SQLite, file `monitoring.db`):**
```sql
website_config(id TEXT PK, domain TEXT UNIQUE, name TEXT, poll_interval_seconds INTEGER NULL,
               created_at TEXT, last_checked_at TEXT NULL, last_status TEXT NULL)
snapshot(url TEXT, website_id TEXT, normalized_text TEXT, content_hash TEXT,
         links_json TEXT, image_hashes_json TEXT, checked_at TEXT, sections_json TEXT NULL,
         PRIMARY KEY(url, checked_at))
change_event(id TEXT PK, website_id TEXT, url TEXT, detected_at TEXT,
             diff_json TEXT, summary_json TEXT)
app_config(key TEXT PK, value TEXT)
```

**Method Repository yang tersedia:**
`connect/close`, `add_website`, `remove_website`, `update_website`, `list_websites`,
`list_websites_with_status`, `get_website`, `count_websites`, `save_snapshot`,
`get_latest_snapshot`, `prune_snapshots`, `save_change_event`, `list_change_events`,
`get_change_event`, `update_last_check`, `load_latest_snapshots`, `load_state`,
`set_app_config`, `get_app_config`

**Rute dashboard yang ada:**
```
GET    /                          daftar website + status
GET    /websites/{id}             riwayat perubahan
GET    /events/{id}               detail diff (Sebelum vs Sesudah)
POST   /websites                  tambah (form: domain, name, poll_interval_seconds)
POST   /websites/{id}/edit        ubah nama & interval
POST   /websites/{id}/delete      hapus (form-friendly)
DELETE /websites/{id}             hapus (JSON)
POST   /websites/{id}/check       "Cek Sekarang" -> JSON ringkasan
```

---

## 2. Keputusan yang Sudah Dikunci

| # | Keputusan | Nilai |
|---|---|---|
| 1 | Manajemen halaman | **Tetap otomatis** (auto-discovery). Halaman TIDAK dikelola manual, TIDAK ada interval per-halaman. Halaman "Pages" bersifat daftar/read-only + jeda. |
| 2 | Interval pemeriksaan | **Tetap 6 jam** (global, `DEFAULT_GLOBAL_INTERVAL_SECONDS = 21600`) |
| 3 | Format laporan | **CSV saja** — TIDAK pakai PDF (hindari dependensi baru) |
| 4 | Login / Users | **Ditunda.** Fokus tampilan dulu. Dashboard tetap tanpa autentikasi. |
| 5 | Notifikasi | **Telegram saja.** TIDAK ada email/SMTP. |
| 6 | Deteksi harga | **Di-skip.** Website target tidak punya harga — fokus perubahan teks. |
| 7 | Keywords | **Ya, dipakai** (pantau kata kunci hilang/muncul) — dikerjakan di tahap akhir |
| 8 | Nama produk | **ContentMonitor** (ganti dari "Website Monitoring") |
| 9 | Warna aksen | Indigo/violet sesuai mockup |

**Elemen mockup yang SENGAJA dibuang:** "Monitor Usage 23/50 Pages" + "Upgrade Plan" (itu UI langganan SaaS, sistem ini self-hosted), profil "Admin" (belum ada login), Time Zone/Language/Theme selector di Settings (opsional, bukan prioritas).

---

## 3. Aturan Teknis Wajib (berlaku di semua tahap)

1. **Python 3.9** — WAJIB `from __future__ import annotations` + `typing.Optional/List/Dict`. JANGAN pakai `str | None` di runtime.
2. **Tanpa CDN / framework eksternal** — CSS & JS ditulis sendiri di `src/monitoring/web/static/`. Chart pakai **SVG buatan sendiri** (line chart, donut, sparkline). Harus jalan offline.
3. **Semua test harus tetap lolos.** Jalankan `.venv/bin/python -m pytest -q` sebelum selesai. Kalau ada test lama yang bentrok dengan desain baru, **update test-nya** agar tetap mengetes maksud aslinya.
4. **Jangan ubah logika bisnis** di `domain/`, `infra/`, `app/` kecuali tahap itu memang memintanya.
5. **Teks UI Bahasa Indonesia.** Komentar/docstring Bahasa Indonesia dengan referensi requirement (mis. `Req 10.1`).
6. **Migrasi database** — file `monitoring.db` sudah berisi data nyata. Kolom/tabel baru WAJIB pakai migrasi idempoten: cek `PRAGMA table_info(...)` lalu `ALTER TABLE ... ADD COLUMN`, atau `CREATE TABLE IF NOT EXISTS`. Contoh sudah ada di `Repository._migrate_schema`.
7. **Jangan hapus data pengguna** tanpa diminta.
8. Spec ada di `.kiro/specs/website-monitoring/` (requirements.md, design.md, tasks.md) — perbarui kalau ada perubahan perilaku.

---

## 4. Rincian Pekerjaan per Tahap

### Tahap 1 — Kerangka ContentMonitor + Overview + Websites
Sidebar, rebranding, halaman Overview (4 KPI, line chart 7 hari, Top Changes, tabel + sparkline), halaman Websites (tabel + search + pagination + aksi). Semua dari data yang sudah ada. **Butuh:** klasifikasi tipe perubahan (Added/Removed/Updated) + status Paused.

### Tahap 2 — Content Changes + Pages
Halaman daftar semua perubahan (filter tanggal/website/tipe + pagination) dan daftar semua halaman terpantau (tab All/Active/Paused/Broken).

### Tahap 3 — Alerts + tracking Title/Meta/SSL
Tabel alert baru (severity Critical/Warning/Info, status Unread/Read/Resolved), pencatatan kegagalan halaman, tracking `<title>` & meta description, cek masa berlaku SSL.

### Tahap 4 — Settings + Keywords
Halaman Settings (interval global, retensi data, integrasi Telegram) + fitur Keywords per website.

### Tahap 5 — Reports (CSV)
KPI laporan, chart, donut per tipe, generate & unduh CSV, riwayat laporan.

---

## 5. PROMPT SIAP-TEMPEL

> Cara pakai: buka sesi baru, tempel **satu prompt** (satu tahap), tunggu selesai & test hijau, baru lanjut tahap berikutnya. Jangan gabung dua tahap.

---

### PROMPT TAHAP 1 — Kerangka + Overview + Websites

```
Workspace: /Users/macbook/Documents/WEB DEVELOPMENT/MonitoringWebsite
Baca dulu: HANDOFF.md (bagian 1, 2, 3) dan .kiro/specs/website-monitoring/design.md

Ini proyek Python 3.9 (venv di .venv). Test: `.venv/bin/python -m pytest -q` — saat ini
352 lolos dan HARUS tetap lolos saat kamu selesai.

TUGAS: Rebranding dashboard jadi "ContentMonitor" dan bangun 2 halaman baru
(Overview + Websites) dengan layout sidebar, mengikuti gaya dashboard SaaS modern
(sidebar kiri, kartu KPI, chart, tabel bersih, aksen indigo/violet).

Ini murni lapisan presentasi + query agregasi. JANGAN ubah logika di domain/, infra/,
app/ kecuali yang disebut di bawah.

=== A. KERANGKA (base.html + CSS) ===
- Ganti nama produk jadi "ContentMonitor" di semua template & judul halaman.
- Ubah base.html: layout SIDEBAR KIRI (bukan header horizontal) + area konten kanan.
  Sidebar: logo ContentMonitor di atas, lalu menu: Overview (/), Websites (/websites),
  Pages (/pages), Content Changes (/changes), Alerts (/alerts), Reports (/reports),
  Settings (/settings). Item aktif di-highlight (background lembut + teks aksen).
  Menu yang halamannya belum ada (Pages/Changes/Alerts/Reports/Settings) tetap dirender
  tapi diberi tanda "segera" dan mengarah ke "#" — JANGAN bikin link mati 404.
- Sidebar collapse jadi menu atas pada layar < 900px (responsif).
- Header konten: judul halaman + subjudul + area aksi kanan (tombol/filter).
- Aksen indigo/violet. Pertahankan dukungan dark mode via prefers-color-scheme.
- Semua CSS di src/monitoring/web/static/style.css. TANPA CDN.

=== B. QUERY AGREGASI BARU (repository.py) ===
Tambahkan method baca-saja berikut (JANGAN ubah method yang sudah ada):
1. count_pages_monitored(website_id: Optional[str] = None) -> int
   Jumlah URL unik di tabel snapshot (opsional difilter per website).
2. count_change_events_between(start: datetime, end: datetime,
                               website_id: Optional[str] = None) -> int
3. daily_change_counts(start: datetime, end: datetime,
                       website_id: Optional[str] = None) -> List[Tuple[str, int]]
   Jumlah change_event per hari (kunci "YYYY-MM-DD"), untuk chart & sparkline.
   Hari tanpa perubahan HARUS ikut muncul dengan nilai 0.
4. recent_change_events(limit: int = 5, website_id: Optional[str] = None)
   -> List[ChangeEvent]   (terbaru dulu, lintas semua website bila None)
5. website_stats() -> List[...]  Per website: WebsiteConfig + last_checked_at +
   last_status + jumlah halaman + waktu perubahan terakhir + jumlah perubahan 7 hari
   + daftar hitungan harian 7 hari (untuk sparkline). Kembalikan dataclass baru
   (mis. WebsiteOverview) di repository.py.
Semua query pakai SQL agregat (COUNT/GROUP BY), JANGAN load semua baris ke Python.

=== C. KLASIFIKASI TIPE PERUBAHAN (fungsi murni baru) ===
Buat di src/monitoring/domain/classify.py:
  classify_change(diff: Diff) -> str  -> "added" | "removed" | "updated"
Aturan:
- Hanya ada penambahan (text_added/links_added/sections_added/images_added terisi,
  yang dihapus semua kosong)  -> "added"
- Hanya ada penghapusan -> "removed"
- Campuran, atau ada images_changed -> "updated"
- Diff kosong -> "updated" (fallback aman)
Plus: describe_change(diff: Diff) -> str yang menghasilkan deskripsi 1 baris
Bahasa Indonesia untuk ditampilkan di "Top Changes Detected", contoh:
- 1 blok teks berubah -> 'Teks diubah dari "<lama dipotong 60 char>" menjadi "<baru>"'
- hanya penambahan teks -> 'Menambahkan N bagian teks baru'
- hanya penghapusan -> 'Menghapus N bagian teks'
- gambar berubah -> 'N gambar diperbarui'
- link -> 'N link ditambahkan / dihapus'
Gabungkan maksimal 2 klausa, potong teks panjang dengan "…".
Tulis test unit + property test untuk classify_change (Hypothesis, min 100 iterasi,
tag komentar: `# Feature: contentmonitor, Property: klasifikasi tipe perubahan`).
Properti: hasil selalu salah satu dari 3 nilai; diff yang hanya berisi penambahan
selalu "added"; yang hanya penghapusan selalu "removed".

=== D. STATUS AKTIF/JEDA per website ===
- Tambah kolom `paused INTEGER NOT NULL DEFAULT 0` ke tabel website_config lewat
  MIGRASI idempoten (PRAGMA table_info + ALTER TABLE) — DB sudah berisi data nyata.
- Tambah field `paused: bool = False` ke WebsiteConfig (models.py) + serialisasi.
- Repository: set_website_paused(website_id, paused: bool) -> None; sertakan `paused`
  di list_websites / list_websites_with_status / get_website / website_stats.
- Scheduler (infra/scheduler.py): website dengan paused=True DILEWATI di run_iteration
  (jangan dijadwalkan). Tambah test untuk ini.
- Web: rute POST /websites/{id}/pause dan POST /websites/{id}/resume (redirect 303 ke
  halaman asal atau /websites). Tombol jeda/lanjutkan di tabel.
- Status yang ditampilkan: "Active" (tidak paused), "Paused" (paused=True),
  "Inactive" (belum pernah diperiksa / last_checked_at NULL).

=== E. HALAMAN OVERVIEW (GET /) ===Ganti isi halaman utama:
- 4 kartu KPI: Total Websites · Pages Monitored · Changes Detected (7 hari terakhir,
  plus persentase perubahan vs 7 hari sebelumnya, panah naik/turun) · Alerts
  (SEMENTARA tampilkan 0 dengan catatan "belum aktif" — fitur Alerts ada di Tahap 3).
  Tiap kartu ada ikon (inline SVG) dan keterangan kecil.
- Chart "Content Changes Over Time": LINE CHART SVG buatan sendiri, 7 hari terakhir,
  dari daily_change_counts. Ada titik data, grid halus, label tanggal, dan area
  gradien di bawah garis. Di bawahnya legenda: Total Changes, Added, Removed, Updated
  beserta angkanya (hitung dengan classify_change atas event dalam rentang).
  Chart harus menangani kasus semua nilai 0 (jangan pecah / bagi nol).
- Panel "Top Changes Detected": 5 perubahan terbaru. Tiap baris: badge avatar huruf
  depan domain (warna diturunkan deterministik dari domain), domain, path URL,
  waktu relatif, badge tipe (Added/Removed/Updated), dan deskripsi 1 baris dari
  describe_change. Link ke /events/{id}. Ada tautan "Lihat semua perubahan".
- Panel "Recent Alerts": placeholder empty state "Belum ada alert" + catatan fitur
  menyusul (Tahap 3).
- Tabel "Monitored Websites": kolom Website (avatar + nama + domain), Pages,
  Last Change (relatif), Changes (7 hari) sebagai SPARKLINE SVG, Status badge, Aksi.
  Maksimal 5 baris + tautan "Lihat semua website" ke /websites.
- Tombol "+ Add Website" di header yang mengarah ke form tambah (boleh tetap di
  halaman /websites atau panel yang bisa dibuka).

=== F. HALAMAN WEBSITES (GET /websites) ===
- Tabel penuh: Website (avatar + nama + domain), Pages, Last Change, Status,
  Changes (7D) sparkline, Aksi (Edit, Buka situs, Cek Sekarang, Jeda/Lanjutkan, Hapus).
- Search berdasarkan nama/domain (query param `q`, filter server-side).
- Pagination server-side (query param `page`, 10 baris per halaman) + teks
  "Showing X to Y of Z websites". Halaman di luar rentang jangan error.
- Form "Tambah Website" (domain, nama opsional, interval opsional) tetap ada,
  memakai rute POST /websites yang sudah ada.
- Empty state kalau belum ada website; empty state berbeda kalau hasil search kosong.
- Pertahankan tombol "Cek Sekarang" (fetch ke POST /websites/{id}/check, spinner,
  hasil inline, reload) yang sudah bekerja di static/app.js.

=== YANG WAJIB DIPERTAHANKAN (test lama bergantung pada ini) ===
Beberapa test memeriksa substring & kelas CSS pada HTML. Pertahankan di hasil render:
- Kelas `status-success` dan `status-failure`, label "Berhasil" dan "Gagal",
  teks "Belum pernah" untuk website yang belum diperiksa.
- Empty state memuat "Belum ada website yang dipantau".
- Pesan error `load_error`, `add_error`, `edit_error` dirender apa adanya
  (mengandung frasa "tidak valid", "sudah terdaftar", "kapasitas maksimum").
- Nilai `added` / `updated` muncul saat query param-nya ada.
- Kartu ringkasan bertanda `data-summary="total|success|failure|never"` (dipakai test
  test_web_dashboard_ui.py) — kalau kamu ubah struktur kartu KPI, UPDATE test itu
  supaya tetap menguji maksud yang sama.
- Filter Jinja yang sudah ada (fmt_dt, fmt_rel, fmt_dt_rel, fmt_duration, fmt_interval)
  jangan dihapus; halaman /websites/{id} dan /events/{id} harus tetap berfungsi.
- Rute, kode status, dan bentuk respons yang sudah ada JANGAN diubah.
  POST /websites/{id}/check tetap mengembalikan JSON.

=== TEST BARU ===
Buat tests/test_web_overview.py dan tests/test_web_websites_page.py:
- KPI menampilkan angka benar (total website, halaman, perubahan 7 hari).
- Line chart & sparkline merender elemen SVG; aman saat semua data 0.
- Top Changes menampilkan deskripsi + badge tipe yang benar.
- Websites: search memfilter, pagination membatasi baris & menampilkan "Showing ... of ...".
- Jeda/lanjutkan mengubah status dan scheduler melewati website yang dijeda.
- Sidebar merender semua menu dan menandai item aktif.
Pakai repository palsu (fake) untuk unit test + minimal satu test integrasi dengan
Repository nyata di tmp_path.

Jalankan `.venv/bin/python -m pytest -q` sampai SEMUA lolos, lalu laporkan
jumlah test akhir dan ringkasan perubahan.
```

---

### PROMPT TAHAP 2 — Content Changes + Pages

```
Workspace: /Users/macbook/Documents/WEB DEVELOPMENT/MonitoringWebsite
Baca dulu: HANDOFF.md (bagian 1, 2, 3). Tahap 1 sudah selesai (kerangka sidebar
ContentMonitor, Overview, Websites, classify_change/describe_change, kolom paused).

Python 3.9, venv .venv. Test: `.venv/bin/python -m pytest -q` — semua harus tetap lolos.

TUGAS: Bangun 2 halaman baru: "Content Changes" dan "Pages". Lapisan presentasi +
query agregasi. Ikuti gaya visual yang sudah dibuat di Tahap 1.

=== A. HALAMAN CONTENT CHANGES (GET /changes) ===
Daftar SEMUA perubahan konten lintas website.
- 4 kartu KPI (dihitung dari rentang tanggal aktif): Semua Perubahan · Ditambahkan ·
  Dihapus · Diperbarui (pakai classify_change dari domain/classify.py).
- Tabel kolom: Change (badge ikon tipe + judul singkat + deskripsi dari
  describe_change), Page (path URL), Website (avatar + nama + domain),
  Status (badge Added/Removed/Updated), Detected At (relatif), Aksi (lihat detail).
  Kolom "Keywords" DILEWATI dulu (fitur Keywords ada di Tahap 4).
- Filter server-side via query param:
  * `start` & `end` (rentang tanggal, format YYYY-MM-DD; default 7 hari terakhir)
  * `website` (id website, default semua)
  * `type` (added/removed/updated, default semua)
  * `q` (cari di URL / deskripsi)
  * `page` (pagination, 10 baris per halaman)
- Semua filter bisa dikombinasikan. Rentang tanggal tidak valid (end < start atau
  format salah) -> pakai default dan tampilkan peringatan halus, JANGAN error 500.
- Empty state saat tidak ada hasil (bedakan "belum ada perubahan" vs "tidak ada hasil
  untuk filter ini").
- Teks "Showing X to Y of Z changes" + kontrol pagination.

Repository (tambahan, baca-saja):
  list_change_events_filtered(start, end, website_id=None, url_query=None,
                              limit=10, offset=0) -> List[ChangeEvent]
  count_change_events_filtered(start, end, website_id=None, url_query=None) -> int
Catatan penting: filter TIPE (added/removed/updated) tidak bisa di SQL karena tipe
diturunkan dari diff_json. Lakukan filter tipe di Python SETELAH query, dan pastikan
pagination tetap benar (mis. ambil batch lalu filter lalu potong; atau hitung ulang
total setelah filter). Jelaskan pendekatan yang kamu pilih di docstring.

=== B. HALAMAN PAGES (GET /pages) ===
PENTING: halaman ditemukan OTOMATIS oleh crawler — TIDAK dikelola manual. JANGAN buat
tombol "+ Add Page" dan JANGAN buat interval per-halaman. (Keputusan no. 1 & 2 di
HANDOFF.md.) Halaman ini bersifat daftar/pemantauan + bisa dijeda per halaman.
- Tabel kolom: Page (path + URL lengkap), Website (avatar + domain),
  Interval (interval efektif WEBSITE-nya, tampilkan apa adanya, mis. "Global (6 jam)"),
  Last Change (waktu perubahan terakhir pada halaman itu), Last Checked,
  Status badge, Aksi (buka halaman, jeda/lanjutkan, lihat riwayat perubahan halaman).
- Status halaman: "Active" (punya snapshot & website tidak dijeda), "Paused"
  (halaman dijeda atau website-nya dijeda), "Broken" (pemeriksaan terakhir halaman
  gagal — lihat bagian C).
- Tab filter: All / Active / Paused / Broken (query param `status`) dengan jumlah
  di tiap tab. Search (`q`) + filter website (`website`) + pagination (`page`).

Jeda per halaman (fitur baru kecil):
- Tabel baru `page_state` lewat CREATE TABLE IF NOT EXISTS:
  page_state(url TEXT PRIMARY KEY, website_id TEXT NOT NULL, paused INTEGER NOT NULL
             DEFAULT 0, last_error TEXT NULL, last_error_at TEXT NULL)
- Repository: set_page_paused(url, paused), get_page_states(website_id=None),
  list_pages(...) untuk halaman Pages (gabungkan data dari snapshot + page_state +
  change_event: URL unik, website, last_checked, last_change).
- CheckOrchestrator (app/orchestrator.py): LEWATI URL yang paused saat memproses
  halaman. Tambah test.
- Rute: POST /pages/pause dan POST /pages/resume (form field `url`), redirect 303
  kembali ke /pages dengan filter yang sedang aktif dipertahankan.

=== C. CATAT KEGAGALAN HALAMAN (untuk status "Broken") ===
- Di CheckOrchestrator, saat sebuah halaman GAGAL diambil (fetch tidak ok), simpan
  alasannya ke page_state.last_error + last_error_at lewat method Repository baru
  `record_page_error(url, website_id, reason)`. Saat halaman BERHASIL diambil,
  bersihkan error-nya (`clear_page_error(url)`).
- Kegagalan menyimpan error TIDAK BOLEH menghentikan siklus pemeriksaan
  (bungkus try/except, konsisten dengan pola yang sudah ada).
- Status "Broken" = page_state.last_error terisi.
- Ini menyiapkan data untuk halaman Alerts di Tahap 3.

=== D. SIDEBAR ===
Aktifkan link Pages (/pages) dan Content Changes (/changes) — hapus tanda "segera"
untuk keduanya. Tandai item aktif dengan benar di kedua halaman baru.
Di Overview, tautan "Lihat semua perubahan" sekarang mengarah ke /changes.

=== ATURAN ===
- Python 3.9 (`from __future__ import annotations`, typing.Optional/List).
- Migrasi DB idempoten (DB berisi data nyata).
- Tanpa CDN; CSS lanjut di static/style.css.
- Teks UI Bahasa Indonesia; docstring Indonesia + referensi Req.
- Semua rute/kode status/bentuk respons lama JANGAN berubah.

=== TEST ===
tests/test_web_changes_page.py dan tests/test_web_pages_page.py:
- KPI per tipe benar; filter tanggal/website/tipe/search bekerja & bisa dikombinasi;
  pagination benar; rentang tanggal tidak valid tidak menyebabkan error.
- Pages: tab All/Active/Paused/Broken menghitung & memfilter dengan benar;
  jeda halaman membuat orchestrator melewatinya; halaman gagal fetch jadi "Broken";
  halaman berhasil membersihkan status Broken.
Jalankan `.venv/bin/python -m pytest -q` sampai semua lolos, laporkan jumlah akhir.
```

---

### PROMPT TAHAP 3 — Alerts + Title/Meta/SSL

```
Workspace: /Users/macbook/Documents/WEB DEVELOPMENT/MonitoringWebsite
Baca dulu: HANDOFF.md (bagian 1, 2, 3). Tahap 1 & 2 sudah selesai (sidebar
ContentMonitor, Overview, Websites, Content Changes, Pages, page_state + pencatatan
error halaman).

Python 3.9, venv .venv. `.venv/bin/python -m pytest -q` harus tetap hijau.

TUGAS: Bangun sistem Alerts + tambah pelacakan judul halaman, meta description, dan
masa berlaku SSL sebagai sumber alert.

=== A. TABEL & MODEL ALERT ===
- Tabel baru (CREATE TABLE IF NOT EXISTS):
  alert(id TEXT PRIMARY KEY, website_id TEXT NOT NULL, url TEXT NULL,
        alert_type TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,
        detail TEXT NULL, triggered_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'unread')
  Index: (website_id, triggered_at DESC) dan (status).
- severity: 'critical' | 'warning' | 'info'
- status: 'unread' | 'read' | 'resolved'
- Model `Alert` (frozen dataclass) di domain/models.py + serialisasi + round-trip test.
- Repository: save_alert(alert) -> bool, list_alerts_filtered(status=None,
  severity=None, website_id=None, q=None, limit, offset), count_alerts_by_status(),
  count_alerts_by_severity(), mark_alert_status(alert_id, status),
  count_unread_alerts(), recent_alerts(limit).
  Penyimpanan gagal JANGAN mengangkat exception ke pemanggil (kembalikan False),
  konsisten dengan save_snapshot/save_change_event.

=== B. PEMBANGKIT ALERT (fungsi murni + integrasi) ===
Buat src/monitoring/domain/alerts.py berisi fungsi MURNI yang menghasilkan daftar
Alert (tanpa I/O, tanpa akses DB) dari data yang sudah ada:
1. Halaman gagal diakses  -> severity 'critical',
   judul "Halaman tidak dapat diakses" (sertakan kode status bila ada, mis. 404).
2. Penghapusan konten signifikan -> 'warning'. Ambang: jumlah blok teks dihapus >=
   AMBANG (konstanta di config.py, mis. SIGNIFICANT_REMOVAL_BLOCKS = 5) ATAU
   >= 50% blok halaman hilang.
3. Judul halaman (<title>) berubah -> 'info', sertakan judul lama & baru.
4. Meta description berubah -> 'info'.
5. Perubahan struktur heading (section ditambah/dihapus) -> 'info'.
6. Konten baru terdeteksi (hanya penambahan) -> 'info'.
7. Sitemap tidak dapat diakses -> 'warning'.
8. SSL akan kadaluarsa -> 'warning' bila < 30 hari, 'critical' bila < 7 hari.
Semua fungsi menerima data biasa (Diff, hasil fetch, tanggal kadaluarsa SSL) dan
mengembalikan List[Alert]. Tulis unit test + property test untuk ambang penghapusan
signifikan (tag: `# Feature: contentmonitor, Property: ambang alert penghapusan`).

=== C. TRACKING TITLE & META DESCRIPTION ===
- normalizer.py: tambah fungsi murni `extract_title(html) -> Optional[str]` dan
  `extract_meta_description(html) -> Optional[str]` (whitespace dikolaps, None bila
  tidak ada, tidak pernah raise pada HTML malformed).
- Snapshot (models.py): tambah field `title: Optional[str] = None` dan
  `meta_description: Optional[str] = None` + serialisasi.
- Tabel snapshot: tambah kolom `title TEXT NULL` dan `meta_description TEXT NULL`
  lewat MIGRASI idempoten (PRAGMA table_info + ALTER TABLE). Baris lama dibaca
  sebagai None tanpa error.
- CheckOrchestrator: isi kedua field saat membangun Snapshot.
- Diff (models.py): tambah field `title_changed: Optional[Tuple[str, str]] = None`
  dan `meta_changed: Optional[Tuple[str, str]] = None` — ATAU kalau lebih rapi,
  simpan sebagai dua list (title_before/title_after). Pilih satu, konsisten di
  serialisasi + detector + tampilan. detect_changes membandingkan title & meta
  dan mengisinya. Diff lama (tanpa field ini) harus tetap bisa dideserialisasi.
- Tampilkan perubahan judul & meta di halaman /events/{id}.
- PENTING: detect_changes punya "gerbang tidak-berubah" (content_hash sama DAN semua
  image_hash sama -> dianggap tidak berubah). Karena title & meta ikut ke dalam
  normalized_text? PERIKSA: kalau title/meta TIDAK masuk normalized_text, maka
  perubahan title saja tidak akan terdeteksi. Pastikan gerbangnya juga
  memperhitungkan perubahan title/meta. Tambah test untuk kasus "hanya title berubah".

=== D. CEK SSL ===
- infra: fungsi async `check_ssl_expiry(domain, timeout=10) -> Optional[datetime]`
  memakai `ssl` + `asyncio.open_connection` dari pustaka standar (JANGAN tambah
  dependensi). Kegagalan -> None (jangan raise).
- Dipanggil sekali per website per siklus pemeriksaan (bukan per halaman).
- Hasilnya diteruskan ke pembangkit alert (bagian B no. 8).
- Test memakai mock/monkeypatch — JANGAN menyentuh jaringan nyata di test.

=== E. INTEGRASI KE ORCHESTRATOR ===
- Setelah memproses halaman, bangun alert dari kondisi yang terjadi lalu simpan.
- Kegagalan menyimpan alert TIDAK BOLEH menghentikan siklus.
- Tambahkan `alerts_created: int` ke CheckResult.
- Notifikasi Telegram: kirim juga untuk alert 'critical' (selain notifikasi perubahan
  yang sudah ada). Jangan spam — satu pesan ringkas per siklus per website yang
  merangkum alert critical.

=== F. HALAMAN ALERTS (GET /alerts) ===
- Tab: All / Unread / Critical / Warning / Info dengan jumlah masing-masing.
- Tabel: Alert (judul + detail singkat), Website, Page, Severity badge,
  Triggered At (relatif), Status badge, Aksi (tandai dibaca, tandai selesai, lihat
  halaman terkait).
- Search (`q`), filter website, pagination (10/halaman), "Showing X to Y of Z alerts".
- Rute aksi: POST /alerts/{id}/read dan POST /alerts/{id}/resolve -> redirect 303
  mempertahankan filter aktif.
- Empty state.

=== G. UPDATE OVERVIEW ===
- Kartu KPI "Alerts" sekarang menampilkan jumlah alert yang perlu perhatian
  (unread + critical) — hapus placeholder "belum aktif".
- Panel "Recent Alerts" menampilkan 4-5 alert terbaru dengan ikon severity.
- Sidebar: aktifkan link /alerts + badge jumlah unread di sebelah menu Alerts.

=== ATURAN ===
Python 3.9; migrasi DB idempoten (ada data nyata); tanpa dependensi baru; tanpa CDN;
teks UI Indonesia; rute lama tidak berubah.

=== TEST ===
tests/test_domain_alerts.py, tests/test_infra_repository_alerts.py,
tests/test_web_alerts_page.py, plus test untuk extract_title/extract_meta_description
dan deteksi "hanya title berubah".
Jalankan `.venv/bin/python -m pytest -q` sampai semua lolos, laporkan jumlah akhir.
```

---

### PROMPT TAHAP 4 — Settings + Keywords

```
Workspace: /Users/macbook/Documents/WEB DEVELOPMENT/MonitoringWebsite
Baca dulu: HANDOFF.md (bagian 1, 2, 3). Tahap 1-3 sudah selesai.

Python 3.9, venv .venv. `.venv/bin/python -m pytest -q` harus tetap hijau.

TUGAS: Halaman Settings + fitur Keywords.

=== A. HALAMAN SETTINGS (GET /settings) ===
Tab: General · Monitoring · Notifications · Keywords. (TIDAK ada tab Users —
login ditunda. TIDAK ada Integrations email — Telegram saja.)
Nilai disimpan di tabel `app_config` yang sudah ada (key-value) lewat
set_app_config/get_app_config.

Tab General:
- Nama tampilan instans (default "ContentMonitor")
- Format tanggal (pilihan) — opsional, boleh hanya tampil
Tab Monitoring:
- Polling_Interval global (input MENIT, validasi 1..1440 pakai
  validate_poll_interval_minutes yang sudah ada; disimpan sebagai detik di
  app_config key 'global_poll_interval_seconds' — key ini sudah dibaca Scheduler)
- Retensi Snapshot per halaman (default 5, dari SNAPSHOT_RETENTION_PER_URL)
- RETENSI RIWAYAT PERUBAHAN dalam hari (BARU, default 90): change_event lebih lama
  dari N hari dihapus otomatis. Implementasikan
  Repository.prune_change_events(older_than_days) yang HANYA menghapus change_event
  (jangan sentuh tabel lain) dan dipanggil sekali per siklus scheduler.
  PERINGATAN: ini menghapus data pengguna — default HARUS aman (90 hari), beri
  keterangan jelas di UI, dan tulis test bahwa event yang lebih baru TIDAK terhapus.
- Batas konkurensi fetch (default 10, rentang 1..100)
Tab Notifications:
- Status Telegram (aktif/tidak, dibaca dari env TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)
- Toggle: kirim notifikasi untuk perubahan · kirim untuk alert critical
- Tombol "Kirim tes notifikasi" -> POST /settings/test-notification, hasil JSON,
  tampilkan inline. JANGAN pernah menampilkan token di HTML.
Tab Keywords: lihat bagian B.

- Rute simpan: POST /settings (form per tab), validasi, redirect 303 ke
  /settings?tab=<tab>&saved=1. Nilai tidak valid -> render ulang dengan pesan error
  dan JANGAN menyimpan apa pun.
- Perubahan interval global harus langsung dipakai Scheduler pada siklus berikutnya
  (Scheduler sudah membaca app_config setiap iterasi — verifikasi dengan test).

=== B. FITUR KEYWORDS ===
Tujuan: pantau apakah kata kunci tertentu ADA / HILANG di halaman sebuah website.
- Tabel baru: keyword(id TEXT PK, website_id TEXT NOT NULL, keyword TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'must_exist', created_at TEXT NOT NULL,
  UNIQUE(website_id, keyword))
  mode: 'must_exist' (alert kalau HILANG) | 'must_not_exist' (alert kalau MUNCUL)
- Model `Keyword` (frozen dataclass) + serialisasi + test.
- Repository: add_keyword, remove_keyword, list_keywords(website_id=None).
- Fungsi MURNI di domain: `match_keywords(text_blocks: List[str],
  keywords: List[Keyword]) -> List[KeywordMatch]` — pencocokan case-insensitive,
  laporkan ada/tidak ada + halaman mana. Tulis property test (tag:
  `# Feature: contentmonitor, Property: pencocokan kata kunci`): kata kunci yang
  ada di salah satu blok selalu terdeteksi ada; yang tidak ada selalu terdeteksi
  hilang; pencocokan tidak peka huruf besar/kecil.
- Integrasi: CheckOrchestrator mengecek keyword per halaman; buat Alert
  (severity 'warning') dengan judul mis. `Kata kunci "diskon" tidak ditemukan`
  untuk mode must_exist yang hilang, dan `Kata kunci "gratis" muncul` untuk
  must_not_exist yang muncul. Hindari alert duplikat setiap siklus: hanya buat
  alert baru bila kondisinya BERUBAH dibanding pemeriksaan sebelumnya (simpan
  status terakhir keyword per URL, mis. tabel keyword_state).
- UI tab Keywords di Settings: pilih website, tambah keyword + mode, daftar keyword
  dengan tombol hapus. Rute POST /settings/keywords dan POST /settings/keywords/delete.
- Halaman Content Changes: tambahkan kolom "Keywords" yang menampilkan keyword yang
  cocok pada halaman terkait (chip kecil), sesuai mockup.

=== C. SIDEBAR ===
Aktifkan link /settings dan tandai aktif dengan benar.

=== ATURAN ===
Python 3.9; migrasi idempoten; tanpa dependensi baru; tanpa CDN; teks UI Indonesia;
JANGAN tampilkan token/secret di HTML; rute lama tidak berubah.

=== TEST ===
tests/test_web_settings_page.py, tests/test_domain_keywords.py,
tests/test_infra_repository_keywords.py + test bahwa perubahan interval global
diterapkan Scheduler dan bahwa prune_change_events hanya menghapus yang kedaluwarsa.
Jalankan `.venv/bin/python -m pytest -q` sampai semua lolos, laporkan jumlah akhir.
```

---

### PROMPT TAHAP 5 — Reports (CSV)

```
Workspace: /Users/macbook/Documents/WEB DEVELOPMENT/MonitoringWebsite
Baca dulu: HANDOFF.md (bagian 1, 2, 3). Tahap 1-4 sudah selesai.

Python 3.9, venv .venv. `.venv/bin/python -m pytest -q` harus tetap hijau.

TUGAS: Halaman Reports dengan ekspor CSV. FORMAT CSV SAJA — JANGAN tambah dependensi
PDF (keputusan no. 3 di HANDOFF.md). Pakai modul `csv` dari pustaka standar.

=== A. HALAMAN REPORTS (GET /reports) ===
- Rentang tanggal aktif via query param `start` & `end` (default 7 hari terakhir).
- 4 kartu KPI: Total Changes (+ % vs periode sebelumnya) · Average Changes/Day ·
  Most Active Website (nama + jumlah perubahan) · Total Alerts (+ % vs periode
  sebelumnya).
- Chart "Changes Over Time": LINE CHART SVG untuk rentang aktif (pakai helper chart
  dari Tahap 1; kalau perlu, ekstrak jadi macro/partial Jinja yang bisa dipakai
  ulang oleh Overview dan Reports).
- "Changes by Type": DONUT CHART SVG buatan sendiri (Added / Removed / Updated)
  dengan legenda: jumlah + persentase + total di tengah. Tangani total 0 tanpa
  membagi nol.
- Tabel "Riwayat Laporan": daftar laporan yang pernah dibuat (lihat bagian C).
- Tombol "Generate Report" (lihat bagian B).

=== B. EKSPOR CSV ===Rute:
  GET /reports/export?type=changes&start=&end=&website=   -> CSV riwayat perubahan
  GET /reports/export?type=alerts&start=&end=&website=     -> CSV alert
  GET /reports/export?type=websites                        -> CSV ringkasan per website
- Respons: StreamingResponse/Response dengan media_type "text/csv" dan header
  Content-Disposition attachment berisi nama file deskriptif, mis.
  contentmonitor-changes-2026-07-01_2026-07-07.csv
- Kolom CSV changes: detected_at, website, domain, url, type (added/removed/updated),
  description, text_added_count, text_removed_count, links_added_count,
  links_removed_count, images_changed_count
- Kolom CSV alerts: triggered_at, website, domain, url, alert_type, severity, status,
  title, detail
- Kolom CSV websites: name, domain, paused, last_checked_at, last_status,
  pages_monitored, changes_7d, changes_total
- WAJIB aman: tulis pakai modul `csv` (bukan f-string manual) agar koma/tanda kutip/
  newline di dalam teks di-escape dengan benar. Teks multi-baris dari diff harus
  tetap menghasilkan CSV valid.
- MITIGASI CSV INJECTION: nilai yang dimulai dengan `=`, `+`, `-`, atau `@` diawali
  tanda kutip tunggal agar tidak dieksekusi sebagai formula di Excel/Sheets.
  Tulis test khusus untuk ini.
- Encoding UTF-8 dengan BOM (utf-8-sig) supaya rapi dibuka di Excel.
- Rentang tanggal tidak valid -> 400 dengan pesan jelas, JANGAN 500.
- Ekspor harus efisien: jangan memuat seluruh tabel ke memori sekaligus bila
  jumlahnya besar (pakai batch/generator).

=== C. RIWAYAT LAPORAN ===
- Tabel baru: report(id TEXT PK, name TEXT NOT NULL, report_type TEXT NOT NULL,
  period_start TEXT NOT NULL, period_end TEXT NOT NULL, created_at TEXT NOT NULL,
  summary TEXT NOT NULL, row_count INTEGER NOT NULL, file_path TEXT NOT NULL)
- POST /reports/generate (form: type, start, end, website opsional):
  1. hasilkan CSV, 2. simpan ke direktori `reports/` di root proyek (buat bila belum
  ada; tambahkan `reports/` ke .gitignore), 3. catat metadata ke tabel report,
  4. redirect 303 ke /reports?generated=<id>.
- GET /reports/{id}/download -> kirim file CSV yang tersimpan. File hilang -> 404
  dengan pesan jelas (jangan 500).
- POST /reports/{id}/delete -> hapus baris + file (abaikan bila file sudah tidak ada),
  redirect 303. Beri konfirmasi JS sebelum menghapus.
- Tabel Riwayat Laporan menampilkan: Nama Laporan, Dibuat Pada, Ringkasan
  (mis. "56 changes, 7 alerts"), Format (badge CSV), Aksi (Download, Hapus).
- Nama file WAJIB di-sanitasi (hanya karakter aman) dan disimpan HANYA di dalam
  direktori `reports/` — cegah path traversal. Saat download, verifikasi path hasil
  resolusi benar-benar berada di dalam `reports/`. Tulis test untuk ini.

=== D. SIDEBAR ===
Aktifkan link /reports dan tandai aktif. Sekarang seluruh menu sidebar sudah nyata
kecuali yang memang ditunda.

=== ATURAN ===
Python 3.9; TANPA dependensi baru (csv & pathlib dari stdlib); tanpa CDN; migrasi
idempoten; teks UI Indonesia; rute lama tidak berubah.

=== TEST ===
tests/test_web_reports_page.py:
- KPI & donut menghitung benar; total 0 tidak error.
- CSV: header & baris benar; teks berisi koma/kutip/newline tetap valid saat diparse
  ulang dengan modul csv; mitigasi CSV injection bekerja; nama file benar.
- Generate -> file dibuat di reports/ + baris report tersimpan; download mengirim
  file; delete menghapus keduanya; file hilang -> 404.
- Path traversal pada nama/id laporan ditolak.
- Rentang tanggal tidak valid -> 400.
Pakai tmp_path untuk direktori laporan di test (jangan menulis ke reports/ nyata).
Jalankan `.venv/bin/python -m pytest -q` sampai semua lolos, laporkan jumlah akhir.
```

---

## 6. Catatan Penting

**Satu hal yang belum diselesaikan:** format penyimpanan teks snapshot baru saja
diubah (dari satu baris panjang menjadi per-blok) supaya laporan perubahan presisi.
Akibatnya, **pemeriksaan pertama untuk halaman yang sudah terpantau akan melaporkan
perubahan besar sekali** — itu efek migrasi, bukan perubahan asli di situsnya.
Setelah itu normal.

Kalau ingin bersih dari awal, reset baseline (daftar website & riwayat perubahan
tetap aman, baseline terbentuk lagi di pemeriksaan berikutnya):
```bash
# hentikan aplikasi dulu, lalu:
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('monitoring.db'); \
c.execute('DELETE FROM snapshot'); c.commit(); print('baseline direset')"
```

**Yang ditunda (bukan bug, keputusan sadar):**
- Login / autentikasi — dashboard masih **tanpa proteksi**. Jangan diekspos ke
  internet publik tanpa reverse proxy + autentikasi.
- Notifikasi email
- Deteksi harga
- Laporan PDF
- Interval per-halaman & penambahan halaman manual

**Optimasi yang belum dikerjakan:** gambar yang sama (mis. logo) diunduh ulang untuk
setiap halaman dalam satu siklus. Fungsional benar, tapi boros bandwidth. Bisa
diperbaiki dengan cache gambar per-siklus (kunci: URL gambar).
