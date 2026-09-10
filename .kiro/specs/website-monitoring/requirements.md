# Requirements Document

## Introduction

Dokumen ini mendefinisikan kebutuhan untuk **Website Content Monitoring System**, sebuah sistem yang memantau hingga sekitar 10 website (situs company profile berbasis HTML statis) dan mendeteksi serta melacak perubahan konten yang terjadi pada situs-situs tersebut.

Pengguna cukup memasukkan sebuah domain, lalu sistem secara otomatis menemukan dan meng-crawl seluruh halaman di dalam domain tersebut, mengambil konten, menormalisasi menjadi teks bersih, menyimpan snapshot, dan membandingkannya dengan snapshot sebelumnya untuk mendeteksi perubahan teks, section, link, maupun gambar (termasuk kasus di mana nama/URL file gambar tetap sama tetapi isi gambarnya berubah).

Sistem hanya melaporkan perubahan ketika perubahan benar-benar terdeteksi. Notifikasi dikirim melalui Telegram bot, dan sebuah web dashboard sederhana menampilkan daftar website yang dipantau, status, riwayat perubahan, dan waktu pemeriksaan terakhir. Sistem berjalan secara terjadwal dengan interval polling yang dapat dikonfigurasi.

Target teknologi versi awal (v1): Python dengan pengambilan data secara asynchronous (paralel), HTTP requests biasa (situs bersifat HTML statis), dan penyimpanan SQLite. Sistem dirancang ringan agar mudah di-deploy di VPS.

### Ruang Lingkup v1

Termasuk: penemuan halaman otomatis per domain, deteksi perubahan teks/section/link/gambar, snapshot & riwayat, penjadwalan dengan interval yang dapat dikonfigurasi, notifikasi Telegram, dan dashboard web.

Di luar ruang lingkup v1 (kemungkinan pengembangan mendatang): deteksi perubahan visual/CSS/layout murni via screenshot; ekstraksi berbasis CSS-selector per situs untuk pemantauan section spesifik; situs yang berat JavaScript yang membutuhkan headless browser.

## Glossary

- **Monitoring_System**: Sistem keseluruhan yang memantau website dan mendeteksi perubahan konten.
- **Crawler**: Komponen yang menemukan dan mengambil seluruh halaman dalam sebuah domain.
- **Page_Discovery**: Proses menemukan daftar URL halaman dalam sebuah domain melalui sitemap dan/atau penelusuran menu/link.
- **Fetcher**: Komponen yang mengambil konten HTML dan file gambar melalui HTTP secara asynchronous.
- **Normalizer**: Komponen yang mengubah HTML mentah menjadi teks bersih dengan menghapus script, tag, dan whitespace berlebih.
- **Snapshot**: Representasi tersimpan dari konten sebuah halaman pada satu waktu, mencakup teks ternormalisasi, hash konten, daftar link, dan hash gambar.
- **Content_Hash**: Nilai hash yang dihitung dari teks ternormalisasi sebuah halaman.
- **Image_Hash**: Nilai hash yang dihitung dari isi biner sebuah file gambar.
- **Change_Detector**: Komponen yang membandingkan Snapshot baru dengan Snapshot sebelumnya dan menghasilkan diff.
- **Diff**: Deskripsi terstruktur mengenai perbedaan antara dua Snapshot (teks, section, link, gambar).
- **Change_Event**: Catatan yang dibuat ketika Change_Detector menemukan perubahan pada sebuah halaman.
- **Scheduler**: Komponen yang memicu pemeriksaan website secara periodik berdasarkan Polling_Interval.
- **Polling_Interval**: Rentang waktu antar pemeriksaan sebuah website, dapat dikonfigurasi secara global maupun per-website.
- **Notifier**: Komponen yang mengirim notifikasi perubahan melalui Telegram bot.
- **Dashboard**: Antarmuka web yang menampilkan daftar website, status, riwayat perubahan, dan waktu pemeriksaan terakhir.
- **Website_Config**: Kumpulan pengaturan website yang dipantau, dapat diubah tanpa mengubah kode.
- **Data_Store**: Basis data SQLite yang menyimpan Website_Config, Snapshot, dan riwayat Change_Event.
- **Monitored_Website**: Sebuah domain yang terdaftar dalam Website_Config untuk dipantau.

## Requirements

### Requirement 1: Manajemen Konfigurasi Website

**User Story:** Sebagai pengguna, saya ingin menambah, menghapus, dan mengubah daftar website yang dipantau melalui konfigurasi, sehingga saya dapat mengelola target pemantauan tanpa mengubah kode.

#### Acceptance Criteria

1. WHEN pengguna menambahkan sebuah domain valid ke Website_Config, THE Monitoring_System SHALL menyimpan domain tersebut sebagai Monitored_Website di Data_Store dalam waktu maksimum 5 detik dan menampilkan konfirmasi bahwa domain berhasil ditambahkan.
2. WHEN pengguna menghapus sebuah Monitored_Website dari Website_Config, THE Monitoring_System SHALL menghentikan pemantauan domain tersebut pada siklus pemeriksaan berikutnya.
3. WHEN pengguna mengubah pengaturan sebuah Monitored_Website, THE Monitoring_System SHALL menerapkan pengaturan yang diperbarui pada siklus pemeriksaan berikutnya.
4. IF pengguna menambahkan domain yang tidak memenuhi format valid (panjang 1–253 karakter, terdiri atas label yang dipisahkan tanda titik, hanya berisi huruf, angka, dan tanda hubung), THEN THE Monitoring_System SHALL menolak entri tersebut dan menampilkan pesan kesalahan yang menjelaskan format yang diharapkan.
5. THE Monitoring_System SHALL mendukung pemantauan hingga minimal 10 dan maksimum 100 Monitored_Website secara bersamaan.
6. WHERE sebuah Monitored_Website memiliki Polling_Interval khusus dalam rentang 10 sampai 86400 detik, THE Monitoring_System SHALL menggunakan Polling_Interval tersebut alih-alih nilai global untuk website tersebut.
7. IF pengguna menambahkan domain yang sudah terdaftar sebagai Monitored_Website, THEN THE Monitoring_System SHALL menolak entri duplikat tersebut dan menampilkan pesan kesalahan.
8. IF penambahan Monitored_Website akan melebihi kapasitas maksimum 100 website, THEN THE Monitoring_System SHALL menolak penambahan tersebut dan menampilkan pesan kesalahan.
9. IF pengguna menetapkan Polling_Interval khusus di luar rentang 10 sampai 86400 detik, THEN THE Monitoring_System SHALL menolak nilai tersebut dan mempertahankan Polling_Interval sebelumnya.

### Requirement 2: Penemuan Halaman dalam Domain

**User Story:** Sebagai pengguna, saya ingin cukup memasukkan sebuah domain dan sistem menemukan seluruh halaman di dalamnya, sehingga saya tidak perlu mendaftarkan setiap URL secara manual.

#### Acceptance Criteria

1. WHEN sebuah Monitored_Website akan diperiksa, THE Page_Discovery SHALL meminta sitemap domain dan, apabila sitemap berhasil diambil dalam waktu maksimum 30 detik dan berisi format daftar URL yang valid, mengumpulkan daftar URL halaman dari sitemap tersebut.
2. IF sitemap tidak berhasil diambil dalam waktu 30 detik atau tidak berisi format daftar URL yang valid untuk sebuah Monitored_Website, THEN THE Page_Discovery SHALL menemukan URL halaman dengan menelusuri menu dan link internal mulai dari halaman utama domain sampai kedalaman maksimum 5 tingkat dari halaman utama.
3. THE Page_Discovery SHALL membatasi penemuan halaman hanya pada URL yang berada pada host yang sama persis dengan domain Monitored_Website, dan SHALL mengabaikan URL yang berada pada subdomain berbeda maupun domain eksternal.
4. WHEN Page_Discovery menemukan URL halaman, THE Page_Discovery SHALL menghilangkan URL duplikat sehingga setiap URL unik muncul tepat satu kali dalam daftar URL sebelum halaman diambil.
5. IF sebuah halaman produk atau kategori ditemukan melalui link internal, THEN THE Page_Discovery SHALL menyertakan halaman tersebut dalam daftar URL yang akan diperiksa.
6. THE Page_Discovery SHALL membatasi jumlah URL halaman yang dikumpulkan per Monitored_Website hingga maksimum 5.000 URL, dan WHEN batas tersebut tercapai, THE Page_Discovery SHALL menghentikan penemuan halaman lebih lanjut.
7. IF halaman utama domain tidak dapat diakses dalam waktu 30 detik saat penelusuran link internal, THEN THE Page_Discovery SHALL menghentikan proses penemuan untuk Monitored_Website tersebut dan menghasilkan indikasi kegagalan yang menyatakan domain tidak dapat diakses tanpa mengubah daftar URL yang sudah tersimpan sebelumnya.

### Requirement 3: Pengambilan Konten secara Asynchronous

**User Story:** Sebagai pengguna, saya ingin sistem mengambil banyak halaman dari beberapa situs secara paralel, sehingga pemeriksaan selesai secara efisien.

#### Acceptance Criteria

1. WHEN sebuah siklus pemeriksaan dimulai, THE Fetcher SHALL mengambil halaman dari beberapa Monitored_Website secara asynchronous dan paralel dengan batas konkurensi yang dapat dikonfigurasi bernilai bawaan 10 permintaan bersamaan dalam rentang 1 sampai 100.
2. WHEN Fetcher meminta sebuah halaman, THE Fetcher SHALL menggunakan HTTP request tanpa headless browser.
3. IF sebuah HTTP request gagal atau melebihi batas waktu, THEN THE Fetcher SHALL menandai halaman tersebut sebagai gagal diambil beserta alasan kegagalan, mempertahankan Snapshot sebelumnya, dan melanjutkan pemeriksaan halaman lain tanpa menghentikan siklus.
4. WHEN Fetcher menerima respons dengan kode status di luar rentang status keberhasilan permintaan, THE Fetcher SHALL menandai halaman tersebut sebagai gagal diambil dan mencatat kode status tersebut.
5. THE Fetcher SHALL menerapkan batas waktu permintaan yang dapat dikonfigurasi untuk setiap HTTP request bernilai bawaan 30 detik dalam rentang 1 sampai 300 detik.

### Requirement 4: Normalisasi Konten

**User Story:** Sebagai pengguna, saya ingin konten dibersihkan sebelum dibandingkan, sehingga perubahan yang tidak berarti tidak memicu deteksi palsu.

#### Acceptance Criteria

1. WHEN sebuah halaman HTML berhasil diambil, THE Normalizer SHALL menghapus seluruh elemen script dan style beserta konten di dalamnya sehingga tidak ada teks dari elemen tersebut yang tersisa pada keluaran.
2. WHEN Normalizer memproses konten HTML, THE Normalizer SHALL menghapus seluruh tag HTML dan mendekode entitas HTML (misalnya &amp;, &lt;, &nbsp;) menjadi karakter yang setara sehingga keluaran hanya berisi teks tanpa markup.
3. WHEN Normalizer menghasilkan teks, THE Normalizer SHALL mengganti setiap rangkaian karakter whitespace berturut-turut (spasi, tab, dan baris baru) menjadi satu karakter spasi tunggal serta menghapus whitespace di awal dan akhir teks.
4. WHEN Normalizer selesai memproses sebuah halaman, THE Normalizer SHALL menghasilkan keluaran teks yang identik secara karakter demi karakter untuk dua masukan HTML yang identik.
5. IF konten HTML yang diproses tidak dapat diurai (malformed atau bukan HTML valid), THEN THE Normalizer SHALL menghasilkan teks ternormalisasi dari konten teks yang dapat diekstrak dan menandai bahwa proses penguraian tidak sempurna tanpa menghentikan proses.
6. IF konten yang diberikan kepada Normalizer kosong (0 karakter), THEN THE Normalizer SHALL menghasilkan teks ternormalisasi kosong tanpa menimbulkan kesalahan.

### Requirement 5: Snapshot dan Hashing Konten

**User Story:** Sebagai pengguna, saya ingin sistem menyimpan snapshot setiap halaman, sehingga perubahan dapat dibandingkan terhadap kondisi sebelumnya.

#### Acceptance Criteria

1. WHEN teks ternormalisasi sebuah halaman tersedia, THE Monitoring_System SHALL menghitung Content_Hash dari teks ternormalisasi tersebut sedemikian rupa sehingga teks ternormalisasi yang identik menghasilkan Content_Hash yang identik dan teks yang berbeda menghasilkan Content_Hash yang berbeda.
2. WHEN sebuah halaman diperiksa, THE Monitoring_System SHALL menyimpan Snapshot yang mencakup teks ternormalisasi, Content_Hash, daftar link, dan daftar Image_Hash di Data_Store, dengan daftar link maupun daftar Image_Hash direpresentasikan sebagai daftar kosong apabila tidak ada.
3. WHEN sebuah Snapshot disimpan, THE Monitoring_System SHALL mengaitkan Snapshot tersebut dengan URL halaman dan waktu pemeriksaan berupa timestamp yang memuat tanggal dan waktu.
4. THE Monitoring_System SHALL mempertahankan paling tidak Snapshot valid paling baru dari setiap halaman dan SHALL tidak menghapusnya sampai Snapshot baru berhasil disimpan, agar tersedia untuk perbandingan berikutnya.
5. IF penyimpanan Snapshot gagal, THEN THE Monitoring_System SHALL mempertahankan Snapshot sebelumnya tanpa perubahan dan menghasilkan indikasi kegagalan penyimpanan.

### Requirement 6: Deteksi Perubahan Teks, Section, dan Link

**User Story:** Sebagai pengguna, saya ingin sistem mendeteksi perubahan teks, section, dan link serta menampilkan apa yang berubah, sehingga saya memahami perubahan yang terjadi.

#### Acceptance Criteria

1. WHEN sebuah Snapshot baru tersedia dan Snapshot sebelumnya ada, THE Change_Detector SHALL membandingkan Content_Hash baru dengan Content_Hash sebelumnya untuk menentukan apakah terjadi perubahan.
2. IF Content_Hash baru berbeda dari Content_Hash sebelumnya, THEN THE Change_Detector SHALL menghasilkan Diff yang menunjukkan baris teks ternormalisasi yang ditambahkan dan baris teks yang dihapus.
3. WHEN Change_Detector membandingkan dua Snapshot, THE Change_Detector SHALL mendeteksi link yang ditambahkan dan link yang dihapus berdasarkan perbandingan URL link, dan menyertakan perubahan link tersebut dalam Diff.
4. WHEN Change_Detector membandingkan dua Snapshot, THE Change_Detector SHALL mendeteksi section konten yang ditambahkan dan section yang dihapus, di mana sebuah section adalah blok teks di bawah sebuah heading, dan menyertakan perubahan section tersebut dalam Diff.
5. WHEN sebuah perubahan terdeteksi pada sebuah halaman, THE Change_Detector SHALL membuat Change_Event yang mencatat URL halaman, waktu deteksi, dan Diff.
6. IF Content_Hash baru sama dengan Content_Hash sebelumnya dan seluruh Image_Hash tidak berubah, THEN THE Change_Detector SHALL memperlakukan halaman tersebut sebagai tidak berubah dan tidak membuat Change_Event.
7. IF tidak ada Snapshot sebelumnya untuk sebuah halaman, THEN THE Change_Detector SHALL memperlakukan Snapshot baru sebagai baseline awal dan tidak membuat Change_Event.

### Requirement 7: Deteksi Perubahan Gambar

**User Story:** Sebagai pengguna, saya ingin sistem mendeteksi perubahan gambar termasuk saat URL gambar tetap sama tetapi isinya berubah, sehingga penggantian gambar yang tersembunyi tetap terdeteksi.

#### Acceptance Criteria

1. WHEN sebuah halaman diambil, THE Fetcher SHALL mengunduh setiap file gambar yang direferensikan pada halaman tersebut dengan batas waktu maksimum 30 detik dan ukuran maksimum 10 MB per gambar.
2. WHEN sebuah file gambar berhasil diunduh secara utuh, THE Monitoring_System SHALL menghitung Image_Hash dari isi biner gambar tersebut sedemikian rupa sehingga isi gambar yang identik menghasilkan Image_Hash yang identik dan isi yang berbeda menghasilkan Image_Hash yang berbeda.
3. WHEN Change_Detector membandingkan dua Snapshot, THE Change_Detector SHALL mendeteksi gambar yang ditambahkan dan gambar yang dihapus berdasarkan keberadaan URL gambar pada masing-masing Snapshot, dan menyertakan perubahan tersebut dalam Diff.
4. IF sebuah URL gambar tetap sama antara dua Snapshot tetapi Image_Hash-nya berbeda, THEN THE Change_Detector SHALL memperlakukan gambar tersebut sebagai berubah dan menyertakan perubahan itu dalam Diff.
5. IF pengunduhan sebuah file gambar gagal atau melebihi batas waktu setelah maksimum 3 percobaan, THEN THE Fetcher SHALL menandai URL gambar tersebut sebagai gagal diunduh dan melanjutkan pemrosesan gambar lain pada halaman yang sama.
6. IF perhitungan Image_Hash untuk sebuah gambar gagal, THEN THE Monitoring_System SHALL menandai gambar tersebut sebagai gagal diproses dan melanjutkan pemrosesan gambar lain tanpa menghentikan pemeriksaan halaman.

### Requirement 8: Penjadwalan dengan Interval yang Dapat Dikonfigurasi

**User Story:** Sebagai pengguna, saya ingin sistem memeriksa website secara periodik dengan interval yang dapat saya atur, sehingga saya dapat menyeimbangkan kesegaran data dan beban sistem.

#### Acceptance Criteria

1. THE Scheduler SHALL memicu pemeriksaan setiap Monitored_Website berdasarkan Polling_Interval yang berlaku untuk website tersebut dengan toleransi penjadwalan maksimum 5 detik.
2. WHERE tidak ada Polling_Interval khusus untuk sebuah Monitored_Website, THE Scheduler SHALL menggunakan Polling_Interval global.
3. THE Monitoring_System SHALL menggunakan Polling_Interval global bawaan sebesar 6 jam ketika pengguna belum menetapkan nilai.
4. WHEN pengguna mengubah Polling_Interval, THE Scheduler SHALL menerapkan interval baru pada penjadwalan siklus berikutnya dalam waktu maksimum 10 detik tanpa menghentikan pemeriksaan yang sedang berjalan.
5. IF pengguna menetapkan Polling_Interval di luar rentang 1 sampai 1440 menit, THEN THE Monitoring_System SHALL menolak nilai tersebut, mempertahankan interval sebelumnya, dan menampilkan indikasi kesalahan.
6. IF pengguna menetapkan Polling_Interval berupa nilai bukan bilangan bulat atau tidak valid, THEN THE Monitoring_System SHALL menolak nilai tersebut, mempertahankan interval sebelumnya, dan menampilkan indikasi kesalahan.

### Requirement 9: Notifikasi Telegram

**User Story:** Sebagai pengguna, saya ingin menerima notifikasi Telegram ketika terjadi perubahan, sehingga saya dapat mengetahui perubahan tanpa memeriksa dashboard secara manual.

#### Acceptance Criteria

1. WHEN sebuah Change_Event dibuat, THE Notifier SHALL mengirim pesan notifikasi melalui Telegram bot dalam waktu maksimum 60 detik sejak Change_Event tercatat.
2. WHEN Notifier mengirim notifikasi, THE Notifier SHALL menyertakan URL halaman, nama Monitored_Website, waktu deteksi, dan ringkasan perubahan berupa jumlah teks yang ditambahkan/dihapus, jumlah link yang ditambahkan/dihapus, serta jumlah gambar yang berubah.
3. IF pengiriman notifikasi Telegram gagal, THEN THE Notifier SHALL mengulang pengiriman hingga maksimum 3 percobaan dengan jeda minimal 5 detik antar percobaan.
4. IF seluruh percobaan pengiriman notifikasi gagal, THEN THE Notifier SHALL mencatat kegagalan dengan indikasi notifikasi tidak terkirim dan mempertahankan Change_Event di Data_Store tanpa menghentikan siklus.
5. WHILE tidak ada perubahan yang terdeteksi pada sebuah siklus pemeriksaan, THE Notifier SHALL menahan pengiriman notifikasi untuk siklus tersebut.

### Requirement 10: Web Dashboard

**User Story:** Sebagai pengguna, saya ingin dashboard web sederhana untuk melihat website yang dipantau, statusnya, dan riwayat perubahan, sehingga saya memiliki gambaran menyeluruh atas pemantauan.

#### Acceptance Criteria

1. WHEN pengguna membuka Dashboard, THE Dashboard SHALL menampilkan daftar seluruh Monitored_Website dalam waktu maksimum 3 detik.
2. THE Dashboard SHALL menampilkan waktu pemeriksaan terakhir untuk setiap Monitored_Website, dan untuk website yang belum pernah diperiksa SHALL menampilkan indikasi belum pernah diperiksa.
3. THE Dashboard SHALL menampilkan status pemeriksaan terakhir setiap Monitored_Website dengan indikator visual yang berbeda antara pemeriksaan yang berhasil dan yang gagal.
4. WHEN pengguna memilih sebuah Monitored_Website, THE Dashboard SHALL menampilkan riwayat Change_Event untuk website tersebut terurut dari yang terbaru ke yang terlama.
5. WHEN pengguna melihat sebuah Change_Event, THE Dashboard SHALL menampilkan Diff yang terkait dengan Change_Event tersebut.
6. IF tidak ada Monitored_Website yang terdaftar, THEN THE Dashboard SHALL menampilkan status kosong yang menyatakan belum ada website yang dipantau.
7. IF sebuah Monitored_Website yang dipilih belum memiliki riwayat Change_Event, THEN THE Dashboard SHALL menampilkan status kosong yang menyatakan belum ada perubahan tercatat.
8. IF pemuatan data untuk Dashboard gagal, THEN THE Dashboard SHALL menampilkan indikasi kesalahan tanpa mengubah data yang tersimpan.

### Requirement 11: Penyimpanan dan Riwayat Perubahan

**User Story:** Sebagai pengguna, saya ingin snapshot dan riwayat perubahan tersimpan secara persisten, sehingga data pemantauan tetap tersedia lintas restart.

#### Acceptance Criteria

1. THE Data_Store SHALL menyimpan Website_Config, Snapshot, dan Change_Event menggunakan SQLite sedemikian rupa sehingga data tetap tersedia setelah Monitoring_System dimulai ulang.
2. WHEN sebuah Change_Event dibuat, THE Monitoring_System SHALL menyimpan Change_Event tersebut di Data_Store dalam waktu maksimum 5 detik untuk keperluan riwayat.
3. WHEN Monitoring_System dimulai ulang, THE Monitoring_System SHALL memuat Website_Config dan Snapshot paling baru untuk setiap Monitored_Website dari Data_Store.
4. WHEN sebuah Snapshot baru disimpan, THE Data_Store SHALL mempertahankan Change_Event historis yang sudah tercatat sebelumnya tanpa menghapus atau mengubahnya.
5. IF penyimpanan ke Data_Store gagal, THEN THE Monitoring_System SHALL mempertahankan data sebelumnya tanpa perubahan dan menghasilkan indikasi kegagalan penyimpanan.
6. IF pemuatan data dari Data_Store gagal atau data rusak saat Monitoring_System dimulai ulang, THEN THE Monitoring_System SHALL melanjutkan operasi tanpa berhenti dan menghasilkan indikasi kesalahan pemuatan.

### Requirement 12: Pelaporan Hanya Saat Terjadi Perubahan

**User Story:** Sebagai pengguna, saya ingin sistem hanya melaporkan dan mencatat ketika terjadi perubahan nyata, sehingga saya tidak dibanjiri laporan pemeriksaan rutin.

#### Acceptance Criteria

1. WHEN sebuah siklus pemeriksaan selesai tanpa perbedaan terdeteksi antara konten pemeriksaan saat ini dan baseline tersimpan, THE Monitoring_System SHALL memperbarui waktu pemeriksaan terakhir tanpa membuat Change_Event.
2. IF sebuah perbedaan terdeteksi antara konten pemeriksaan saat ini dan baseline tersimpan selama siklus pemeriksaan, THEN THE Monitoring_System SHALL membuat tepat satu Change_Event untuk halaman tersebut dan memicu notifikasi dalam waktu maksimum 60 detik.
3. WHEN sebuah Change_Event berhasil dibuat, THE Monitoring_System SHALL menampilkan Change_Event tersebut pada Dashboard hanya untuk halaman yang perubahannya terdeteksi dan SHALL tidak menampilkannya untuk halaman yang tidak berubah.
4. IF pembuatan Change_Event gagal, THEN THE Monitoring_System SHALL mempertahankan baseline tersimpan, tidak memperbarui waktu pemeriksaan terakhir, dan menampilkan indikasi kesalahan.
