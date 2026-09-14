# Deploy ke Server Linux

Aplikasi ini adalah satu proses Python (FastAPI + SQLite). Scheduler monitoring
dan web dashboard jalan bareng di satu event loop, jadi tidak perlu worker/DB
terpisah. Data disimpan di satu file SQLite.

> **PENTING soal keamanan:** dashboard TIDAK punya autentikasi. Siapa pun yang
> bisa menjangkau host:port bisa melihat dan mengubah daftar website. Selalu
> jalankan dengan bind ke `127.0.0.1` dan taruh reverse proxy (Nginx) dengan
> login di depannya. Jangan expose port aplikasi langsung ke internet.

Ada dua cara. Pilih salah satu.

---

## Prasyarat

- Server Linux dengan Python 3.9+ (`python3 --version`)
- Akses sudo
- (Opsional) domain yang mengarah ke server kalau mau HTTPS

---

## Opsi A — systemd (native, paling ringan) — REKOMENDASI

### 1. Salin kode ke server

Dari mesin lokal:

```bash
# ganti user@server dan path sesuai punyamu
rsync -av --exclude '.venv' --exclude '.git' --exclude '*.db*' \
  ./ user@server:/opt/monitoring/
```

Atau `git clone` langsung di server ke `/opt/monitoring`.

### 2. Buat user khusus + virtualenv

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin monitoring
sudo chown -R monitoring:monitoring /opt/monitoring

cd /opt/monitoring
sudo -u monitoring python3 -m venv .venv
sudo -u monitoring .venv/bin/pip install --upgrade pip
sudo -u monitoring .venv/bin/pip install -r requirements.txt
sudo -u monitoring .venv/bin/pip install .

# direktori data untuk file SQLite
sudo -u monitoring mkdir -p /opt/monitoring/data
```

> Kalau install `lxml` gagal, install lib sistemnya dulu:
> `sudo apt install libxml2-dev libxslt1-dev python3-dev build-essential`

### 3. Pasang service

```bash
sudo cp deploy/monitoring.service /etc/systemd/system/monitoring.service
# edit kalau user/path kamu beda:
sudo nano /etc/systemd/system/monitoring.service

sudo systemctl daemon-reload
sudo systemctl enable --now monitoring
```

Cek jalan atau tidak:

```bash
sudo systemctl status monitoring
journalctl -u monitoring -f          # lihat log realtime
curl -I http://127.0.0.1:8000/       # harus balas 200
```

### 4. Pasang reverse proxy + login (Nginx)

```bash
sudo apt install nginx apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd admin      # bikin user login
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/monitoring
sudo nano /etc/nginx/sites-available/monitoring  # ganti server_name
sudo ln -s /etc/nginx/sites-available/monitoring /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

(Opsional) HTTPS gratis:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d monitoring.contohdomain.com
```

Selesai. Buka `http://<domain-atau-IP-server>/` lalu login.

---

## Opsi B — Docker

### 1. Build image

```bash
docker build -t website-monitoring .
```

### 2. Jalankan container

```bash
docker run -d --name monitoring --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v monitoring-data:/data \
  -e MONITORING_DB_PATH=/data/monitoring.db \
  -e TELEGRAM_BOT_TOKEN=xxxxx \
  -e TELEGRAM_CHAT_ID=xxxxx \
  website-monitoring
```

Perhatikan `-p 127.0.0.1:8000:8000`: port hanya dibuka di localhost host,
bukan ke publik. Reverse proxy Nginx tetap dipasang seperti Opsi A langkah 4
(proxy_pass ke `http://127.0.0.1:8000`).

Log & kontrol:

```bash
docker logs -f monitoring
docker restart monitoring
docker stop monitoring
```

---

## Variabel lingkungan

| Variabel              | Default            | Keterangan                                   |
| --------------------- | ------------------ | -------------------------------------------- |
| `MONITORING_HOST`     | `127.0.0.1`        | Alamat bind. Biarkan localhost + reverse proxy. |
| `MONITORING_PORT`     | `8000`             | Port aplikasi.                               |
| `MONITORING_DB_PATH`  | `monitoring.db`    | Path file SQLite. Pakai path absolut di server. |
| `TELEGRAM_BOT_TOKEN`  | (kosong)           | Token bot; kalau kosong notifikasi dimatikan. |
| `TELEGRAM_CHAT_ID`    | (kosong)           | Chat/channel tujuan notifikasi.              |
| `MONITORING_SECRET_KEY` | (auto)           | Kunci penanda cookie login. Bila kosong, dibuat otomatis & disimpan di DB. Set manual bila ingin konsisten lintas re-install. |

---

## Login dashboard (akun pengguna)

Dashboard punya gerbang login. **Gerbang aktif otomatis begitu ada minimal
satu akun.** Sebelum akun pertama dibuat, dashboard tetap terbuka (agar tidak
terkunci saat setup).

Kelola akun lewat CLI (password diketik interaktif, tidak terlihat):

```bash
# systemd (jalankan sebagai user aplikasi, dari folder repo)
cd /opt/monitoring
.venv/bin/python -m monitoring.usertool add admin       # buat akun
.venv/bin/python -m monitoring.usertool list            # daftar akun
.venv/bin/python -m monitoring.usertool passwd admin    # ganti password
.venv/bin/python -m monitoring.usertool remove budi     # hapus akun

# docker
docker exec -it monitoring python -m monitoring.usertool add admin
```

> Setelah membuat akun pertama, buka dashboard — kamu akan diminta login.
> Untuk menambah anggota tim, cukup buat akun baru (`usertool add <nama>`).
> Akun terakhir tidak bisa dihapus (mencegah terkunci dari dashboard).

Karena login menandatangani cookie dengan secret key, disarankan set
`MONITORING_SECRET_KEY` yang tetap (mis. di file service) supaya sesi login
tidak invalid saat aplikasi di-install ulang. Kalau tidak diset, key dibuat
otomatis dan disimpan di database — aman untuk pemakaian biasa.

---

## Backup

Cukup backup satu file DB (plus file WAL bila ada) secara berkala:

```bash
# systemd
cp /opt/monitoring/data/monitoring.db /backup/monitoring-$(date +%F).db

# docker
docker run --rm -v monitoring-data:/data -v "$PWD":/backup alpine \
  cp /data/monitoring.db /backup/monitoring-$(date +%F).db
```

## Update versi baru

```bash
# systemd
cd /opt/monitoring && git pull   # atau rsync ulang
sudo -u monitoring .venv/bin/pip install -r requirements.txt
sudo -u monitoring .venv/bin/pip install .
sudo systemctl restart monitoring

# docker
docker build -t website-monitoring .
docker stop monitoring && docker rm monitoring
# lalu jalankan ulang perintah docker run di atas (volume data tetap aman)
```
