# ── InvoiceForge — Docker Image ──────────────────────────────────
FROM python:3.11-slim

# System deps (fonts for ReportLab)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir flask reportlab gunicorn

# Copy application
COPY . .

# Persistent data volume
VOLUME ["/app/data"]

EXPOSE 8000

ENV FLASK_ENV=production \
    PYTHONUNBUFFERED=1

CMD ["gunicorn", "wsgi:application", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
