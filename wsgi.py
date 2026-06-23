"""
WSGI entry point for production deployment.

Usage:
  gunicorn wsgi:application -w 4 -b 0.0.0.0:8000
  waitress-serve --port=8000 wsgi:application  (Windows)
"""
from app import app as application

if __name__ == "__main__":
    application.run()
