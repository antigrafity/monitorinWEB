# Dockerfile untuk Website Monitoring System.
#
# Build : docker build -t website-monitoring .
# Run   : docker run -d --name monitoring \
#           -p 127.0.0.1:8000:8000 \
#           -v monitoring-data:/data \
#           -e MONITORING_DB_PATH=/data/monitoring.db \
#           -e TELEGRAM_BOT_TOKEN=xxxxx \
#           -e TELEGRAM_CHAT_ID=xxxxx \
#           website-monitoring
#
# Catatan: -p 127.0.0.1:8000:8000 sengaja bind ke localhost host saja karena
# dashboard tidak punya autentikasi. Akses dari luar lewat reverse proxy.

FROM python:3.12-slim

# lxml butuh beberapa lib sistem; slim tidak menyertakannya secara default.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependency dulu (layer cache) lalu source.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Jalankan sebagai user non-root.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data && chown appuser:appuser /data
USER appuser

ENV MONITORING_HOST=0.0.0.0 \
    MONITORING_PORT=8000 \
    MONITORING_DB_PATH=/data/monitoring.db

EXPOSE 8000
VOLUME ["/data"]

CMD ["python", "-m", "monitoring.main"]
