# Website Content Monitoring System

Sistem Python yang memantau website company profile berbasis HTML statis,
menemukan halaman secara otomatis, mengambil konten secara asynchronous,
menormalisasi menjadi teks bersih, menyimpan snapshot, dan mendeteksi
perubahan konten (teks, section, link, gambar). Perubahan dikirim via
notifikasi Telegram dan ditampilkan pada web dashboard.

## Struktur Proyek

```
src/monitoring/
  config.py        # konstanta bawaan & rentang
  domain/          # logika inti murni (model, validasi, normalizer, hasher, detector, util URL)
  infra/           # I/O (fetcher, repository/SQLite, notifier, scheduler, discovery)
  app/             # orkestrasi (CheckOrchestrator)
  web/             # dashboard FastAPI + Jinja2
tests/             # unit & property-based tests (Hypothesis)
```

## Pengembangan

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
