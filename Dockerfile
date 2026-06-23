# ── InvoiceForge — Production Docker Image ──────────────────────────
# Use an official, stable Python slim base image for minimal footprint
FROM python:3.11-slim

# Set environment variables:
# - PYTHONDONTWRITEBYTECODE: Prevents Python from writing .pyc files to disk
# - PYTHONUNBUFFERED: Prevents Python from buffering stdout/stderr (useful for container logs)
# - FLASK_ENV: Configures Flask to run in production mode
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production

# Install system dependencies needed for libraries (e.g. freetype and png for ReportLab fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

# Establish working directory
WORKDIR /app

# Create a non-privileged system user for running the application securely
RUN groupadd -g 10001 appgroup && \
    useradd -u 10000 -g appgroup -m -s /bin/bash appuser

# Copy dependency definition and install packages first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Set correct ownership for application directory
RUN chown -R appuser:appgroup /app

# Switch to the non-privileged user
USER appuser

# Expose server port
EXPOSE 8000

# Health check configuration using Python's native urllib (avoids installing curl/wget in slim image)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

# Run production WSGI server with Gunicorn configuration file
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:application"]
