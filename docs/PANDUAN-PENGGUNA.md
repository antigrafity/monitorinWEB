# Panduan Pengguna — Website Content Monitoring System

Panduan ini menjelaskan cara memakai dashboard pemantauan website sehari-hari.
Ditujukan untuk pengguna (bukan developer). Kalau kamu mencari cara memasang
aplikasi di server, lihat `deploy/README-DEPLOY.md`.

---

## Daftar Isi

1. [Apa itu aplikasi ini](#1-apa-itu-aplikasi-ini)
2. [Cara membuka dashboard](#2-cara-membuka-dashboard)
3. [Sekilas tampilan & menu](#3-sekilas-tampilan--menu)
4. [Halaman Overview](#4-halaman-overview)
5. [Halaman Websites — mengelola situs yang dipantau](#5-halaman-websites)
6. [Halaman Pages — daftar halaman terpantau](#6-halaman-pages)
7. [Halaman Content Changes — daftar semua perubahan](#7-halaman-content-changes)
8. [Halaman Alerts — peringatan penting](#8-halaman-alerts)
9. [Halaman Reports — ekspor laporan CSV](#9-halaman-reports)
10. [Halaman Settings — pengaturan](#10-halaman-settings)
11. [Notifikasi Telegram](#11-notifikasi-telegram)
12. [Pertanyaan umum (FAQ)](#12-pertanyaan-umum-faq)

---

## 1. Apa itu aplikasi ini

Aplikasi ini memantau website (khususnya situs company profile) dan memberi
tahu kamu ketika ada perubahan konten. Kamu cukup memasukkan sebuah domain,
lalu sistem otomatis:

- Menemukan semua halaman di dalam domain itu (lewat sitemap atau menelusuri
  link internal).
- Mengambil isi tiap halaman secara berkala.
- Membandingkannya dengan versi sebelumnya.
- Mencatat perubahan yang terdeteksi: teks, bagian/section, link, dan gambar
  (termasuk kalau gambar diganti diam-diam padahal nama filenya sama).

Perubahan ditampilkan di dashboard dan (opsional) dikirim ke Telegram.

Sistem berjalan otomatis di latar belakang sesuai jadwal (default tiap 6 jam),
tapi kamu juga bisa memicu pemeriksaan kapan saja lewat tombol "Cek Sekarang".

---

## 2. Cara membuka dashboard

Buka browser lalu ketik alamat berikut:

- **Dari perangkat yang sama dengan server:** `http://localhost:8000`
- **Dari perangkat lain di jaringan yang sama:** `http://<IP-SERVER>:8000`
  (contoh: `http://172.16.10.20:8000` — tanyakan IP server ke pemasang)

> Aplikasi berjalan 24/7 di server. Kamu tidak perlu menyalakan apa pun;
> cukup buka alamatnya.

### Masuk (login)

Dashboard dilindungi login. Saat membuka alamatnya, kamu akan diarahkan ke
halaman **Masuk**. Isi username dan password yang diberikan admin, lalu klik
**Masuk**.

- Setelah berhasil, kamu tetap masuk selama beberapa hari (tidak perlu login
  ulang tiap buka), kecuali kamu menekan **Keluar**.
- Tombol **Keluar** ada di kiri bawah (pojok sidebar), di bawah nama akunmu.
- Lupa password? Minta admin mengganti password akunmu (lihat panduan deploy
  untuk perintahnya).

> Admin membuat akun lewat command line di server. Tiap anggota tim sebaiknya
> punya akun sendiri.

---

## 3. Sekilas tampilan & menu

Di sisi kiri ada menu navigasi. Ini isinya:

| Menu             | Fungsinya                                                        |
| ---------------- | ---------------------------------------------------------------- |
| **Overview**     | Ringkasan cepat: angka-angka penting, grafik, perubahan terbaru. |
| **Websites**     | Kelola daftar situs yang dipantau (tambah, edit, hapus, cek).    |
| **Pages**        | Daftar semua halaman individual yang terpantau.                  |
| **Content Changes** | Daftar seluruh perubahan yang pernah terdeteksi.              |
| **Alerts**       | Peringatan penting (misal situs error, konten hilang, SSL).      |
| **Reports**      | Buat & unduh laporan dalam format CSV.                           |
| **Settings**     | Pengaturan aplikasi (interval, notifikasi, kata kunci).          |

Dashboard mendukung tema terang & gelap secara otomatis mengikuti pengaturan
sistem/browser kamu.

---

## 4. Halaman Overview

Halaman pertama yang kamu lihat. Isinya gambaran umum:

- **Kartu angka (KPI):** total website dipantau, jumlah perubahan, dan
  indikator lain.
- **Grafik "Changes Over Time":** tren perubahan dari waktu ke waktu.
- **Recent Alerts:** peringatan terbaru yang butuh perhatian.
- **Top Changes Detected:** perubahan konten yang baru terdeteksi.

Gunakan halaman ini untuk cek kondisi keseluruhan dengan cepat.

---

## 5. Halaman Websites

Ini halaman kerja utama untuk mengelola situs yang dipantau.

### Menambahkan website baru

1. Masuk ke menu **Websites**.
2. Isi formulir "Tambah Website":
   - **Domain:** ketik domainnya saja, contoh `example.com` (tanpa `https://`).
   - **Nama:** nama tampilan bebas (opsional), contoh "Situs Kompetitor A".
   - **Interval (detik):** kosongkan untuk memakai jadwal global. Kalau diisi,
     rentangnya 10–86.400 detik (10 detik s.d. 24 jam).
3. Klik tombol tambah.

Sistem akan menemukan halaman-halaman di domain itu secara otomatis pada
pemeriksaan berikutnya.

> **Batas:** minimal bisa memantau 10, maksimal 100 website.
> Domain yang formatnya salah atau sudah terdaftar akan ditolak dengan pesan.

### Tombol aksi pada tiap baris

Setiap website punya deretan tombol berbentuk ikon. Arahkan kursor ke ikon
untuk melihat namanya. Fungsinya:

| Ikon              | Fungsi                                                          |
| ----------------- | -------------------------------------------------------------- |
| **Cek Sekarang**  | Memicu pemeriksaan langsung saat itu juga (tak perlu menunggu jadwal). |
| **Riwayat**       | Melihat riwayat perubahan situs tersebut.                      |
| **Buka situs**    | Membuka situs aslinya di tab baru.                             |
| **Jeda / Lanjutkan** | Menghentikan sementara / melanjutkan pemantauan situs itu.  |
| **Hapus**         | Menghapus situs dari pemantauan (akan diminta konfirmasi).     |
| **Edit**          | Mengubah nama & interval situs.                                |

### Kolom-kolom pada tabel

- **Status:** Aktif, Jeda, atau Inactive.
- **Pemeriksaan terakhir:** kapan terakhir dicek; kalau belum pernah, akan
  ditandai.
- **Sparkline:** grafik mini aktivitas 7 hari terakhir.

### Mencari & memfilter

Ada kolom pencarian untuk menyaring berdasarkan nama/domain. Kalau situs
banyak, hasilnya dibagi per halaman (pagination).

---

## 6. Halaman Pages

Menampilkan **halaman-halaman individual** dari semua situs yang terpantau
(satu situs bisa punya banyak halaman).

Yang bisa kamu lakukan di sini:

- **Filter berdasarkan status:** All / Active / Paused / Broken (rusak/gagal
  diakses).
- **Cari** berdasarkan path atau URL.
- **Filter berdasarkan website** tertentu.
- **Buka** halaman aslinya, lihat **Riwayat**, atau **Jeda/Lanjutkan**
  pemantauan halaman itu secara individual.

Berguna kalau kamu mau fokus atau mengecualikan halaman tertentu saja.

---

## 7. Halaman Content Changes

Daftar **semua perubahan** yang pernah terdeteksi di seluruh situs, dalam satu
tempat.

- **Kartu ringkasan:** jumlah perubahan berdasarkan jenis — Ditambahkan,
  Dihapus, Diperbarui.
- **Filter:** rentang tanggal (start/end), website tertentu, jenis perubahan,
  dan pencarian teks.
- Klik sebuah perubahan untuk melihat **detailnya**.

### Halaman detail perubahan

Menampilkan perbandingan **Sebelum vs Sesudah** secara berdampingan:

- Baris teks yang **dihapus** (kiri) vs yang **ditambahkan** (kanan).
- Perubahan **link** (ditambah/dihapus).
- Perubahan **section** (bagian di bawah heading).
- Perubahan **gambar** (ditambah, dihapus, atau berubah isinya).

Ini yang menjawab pertanyaan "apa persisnya yang berubah?".

---

## 8. Halaman Alerts

Berbeda dari Content Changes (yang mencatat semua perubahan), **Alerts** hanya
menampilkan hal-hal yang perlu perhatian khusus, misalnya:

- Halaman gagal diakses.
- Penghapusan konten dalam jumlah signifikan.
- Perubahan judul/meta halaman.
- Sertifikat SSL akan segera kadaluarsa.

### Tingkat keparahan (severity)

- **Critical** — perlu perhatian segera.
- **Warning** — perlu diperhatikan.
- **Info** — sekadar pemberitahuan.

### Yang bisa dilakukan

- **Filter:** All / Unread / Critical / Warning / Info, plus pencarian &
  filter per website.
- **Lihat Halaman:** buka halaman terkait alert.
- **Tandai Dibaca:** menandai alert sudah dilihat.
- **Tandai Selesai:** menandai alert sudah ditangani.

---

## 9. Halaman Reports

Untuk membuat dan mengunduh laporan dalam format **CSV** (bisa dibuka di Excel
/ Google Sheets). Aplikasi ini tidak membuat PDF.

### Ekspor cepat

Tersedia tombol unduh langsung:

- **Unduh Riwayat Perubahan (CSV)** — sesuai rentang tanggal yang dipilih.
- **Unduh Alert (CSV)**
- **Unduh Ringkasan Website (CSV)**

### Generate & simpan laporan

1. Pilih **Jenis Laporan** (Riwayat Perubahan / Alert / Ringkasan Website).
2. Atur rentang tanggal bila perlu.
3. Klik generate — laporan dibuat, disimpan, dan muncul di tabel **Riwayat
   Laporan** di bawah.

Di tabel Riwayat Laporan kamu bisa **Download** ulang atau **Hapus** laporan
yang tersimpan.

---

## 10. Halaman Settings

Pusat pengaturan aplikasi, terbagi dalam beberapa tab.

### Pengaturan umum & pemantauan

- **Nama instance:** label untuk instalasi ini.
- **Interval pemantauan global (menit):** seberapa sering situs dicek secara
  default. Rentang 1–1440 menit (1 menit s.d. 24 jam). Default 6 jam.
- **Retensi snapshot:** berapa banyak versi lama tiap halaman yang disimpan.
- **Retensi Riwayat Perubahan (hari):** perubahan lebih lama dari nilai ini
  akan dibersihkan otomatis (1–365 hari).

Klik **Simpan Perubahan** setelah mengubah.

### Notifikasi (Telegram)

Menampilkan status apakah notifikasi Telegram aktif, dan tombol **Kirim Tes
Notifikasi** untuk mengecek koneksi. (Pengaturan token dilakukan saat
pemasangan — lihat bagian berikutnya.)

### Kata kunci (Keywords)

Kamu bisa menambahkan kata kunci pemantauan per website, misalnya untuk
memastikan kata tertentu **harus ada** atau **tidak boleh ada** di halaman.
Tersedia tombol tambah dan hapus kata kunci.

---

## 11. Notifikasi Telegram

Kalau diaktifkan, tiap kali ada perubahan terdeteksi, sistem mengirim pesan
Telegram berisi: URL halaman, nama website, waktu deteksi, dan ringkasan
perubahan (berapa teks ditambah/dihapus, link ditambah/dihapus, gambar
berubah).

Notifikasi bersifat **opsional**. Kalau kredensial Telegram belum diatur,
pemantauan tetap berjalan normal — hanya pesan Telegram yang tidak dikirim.

Untuk mengaktifkan, pemasang perlu mengatur dua nilai saat instalasi:
`TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` (lihat `deploy/README-DEPLOY.md`).

---

## 12. Pertanyaan umum (FAQ)

**Seberapa sering situs diperiksa?**
Default tiap 6 jam. Bisa diubah global di Settings, atau per-website di kolom
Interval. Butuh cek segera? Pakai tombol "Cek Sekarang".

**Kenapa website baru belum ada perubahannya?**
Pemeriksaan pertama dipakai sebagai baseline (titik awal). Perubahan baru
terdeteksi mulai pemeriksaan berikutnya, saat ada yang berbeda dari baseline.

**Apa bedanya "Content Changes" dan "Alerts"?**
Content Changes = catatan semua perubahan (netral). Alerts = hanya hal penting
yang butuh perhatian (error, konten hilang banyak, SSL mau habis, dll).

**Apa arti status "Broken" di halaman Pages?**
Halaman itu gagal diakses saat pemeriksaan terakhir (misal error atau timeout).

**Kalau server mati, apa data hilang?**
Tidak. Semua tersimpan di database. Saat server nyala lagi, data & riwayat
tetap ada dan pemantauan lanjut otomatis.

**Bisa memantau berapa situs?**
Minimal 10, maksimal 100 situs sekaligus.

**Situs yang butuh JavaScript berat bisa dipantau?**
Versi ini dirancang untuk situs HTML statis (company profile). Situs yang
sangat bergantung pada JavaScript mungkin tidak terbaca sempurna.

**Bagaimana cara menambah akun untuk anggota tim baru?**
Admin membuat akun lewat command line di server (`usertool add <nama>`). Tiap
orang sebaiknya punya akun sendiri. Lihat panduan deploy untuk detailnya.

**Saya lupa password, bagaimana?**
Minta admin mereset lewat perintah `usertool passwd <username>` di server.
Tidak ada reset password mandiri lewat halaman web pada versi ini.

---

*Butuh bantuan pemasangan atau update aplikasi? Lihat `deploy/README-DEPLOY.md`.*
