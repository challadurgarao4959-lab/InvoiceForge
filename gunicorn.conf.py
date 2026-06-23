# ── Gunicorn Production Configuration ──────────────────────────────────
import os
import multiprocessing

# Port and network binding
# We bind to all interfaces on the configured environment PORT (defaulting to 8000)
port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

# Worker processes configuration
# Formula: (2 * number of cores) + 1 for CPU-bound tasks
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")

# Timeout limits (extended to 120 seconds to support large PDF document generation)
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))
keepalive = 2

# Logging configuration
# "-" maps logging directly to standard output streams (stdout/stderr)
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process management
proc_name = "invoiceforge"
daemon = False
