# InvoiceForge — Advanced Invoice Generator

A professional, deployment-ready invoice generator built with **Flask + ReportLab**.  
Create, manage, and export beautiful PDF invoices from a full-featured dark-mode web UI.

---

## Features

| Category | Details |
|---|---|
| **PDF Engine** | ReportLab — branded header, line items, tax, totals, notes, bank details |
| **Invoice Management** | Create, edit, save, duplicate, delete, bulk actions |
| **Status Tracking** | Paid / Pending / Overdue with auto-overdue detection |
| **Client Address Book** | Save clients, autocomplete on invoice form |
| **Invoice Templates** | 5 built-in: Consulting, Design, Development, Retainer, Photography |
| **Revenue Dashboard** | Stats cards, 6-month bar chart, collection rate, avg value |
| **Export** | PDF download, CSV export, JSON export (per-invoice + bulk) |
| **Keyboard Shortcuts** | Ctrl+S save, Ctrl+D download, Ctrl+P print, Ctrl+N new, Ctrl+Enter add item |
| **Drag to Reorder** | Line items are drag-and-drop sortable |
| **Multi-currency** | USD, EUR, GBP, INR, JPY, CAD, AUD, SGD, AED |
| **Settings Page** | Company defaults, numbering prefix, data management |
| **Live Preview** | PDF previewed inline as you type |
| **Mobile Sidebar** | Responsive layout with slide-out nav |

---

## Quick Start

```bash
# 1 — Install
pip install flask reportlab

# 2 — Run (development)
python app.py
# → http://localhost:5000

# 3 — Run tests
python tests.py
```

---

## Production Deployment

### Option A — Gunicorn (Linux / macOS)
```bash
pip install gunicorn
gunicorn wsgi:application -w 4 -b 0.0.0.0:8000
```

### Option B — Docker
```bash
docker build -t invoiceforge .
docker run -p 8000:8000 -v $(pwd)/data:/app/data invoiceforge
```

### Option C — Render / Railway / Fly.io
Set start command: `gunicorn wsgi:application`

### Option D — Windows (waitress)
```bash
pip install waitress
waitress-serve --port=8000 wsgi:application
```

---

## Project Structure

```
invoice-generator/
├── app.py              # Flask backend — all routes + PDF builder
├── wsgi.py             # Production WSGI entry point
├── tests.py            # 37-test suite (unit + integration)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container image
├── .dockerignore
├── .gitignore
├── templates/
│   └── index.html      # Full single-page UI (1,500 lines)
├── static/             # (empty — place custom CSS/JS here)
└── data/
    └── invoices.db     # SQLite — auto-created on first run
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Web UI |
| GET | `/api/health` | Health check + version |
| POST | `/api/generate` | Download PDF (attachment) |
| POST | `/api/preview` | Stream PDF inline |
| GET | `/api/invoices` | List invoices (`?status=`, `?q=`) |
| POST | `/api/invoices` | Save / update invoice |
| GET | `/api/invoices/:id` | Get invoice payload |
| DELETE | `/api/invoices/:id` | Delete invoice |
| GET | `/api/invoices/:id/pdf` | Download saved invoice PDF |
| POST | `/api/invoices/:id/duplicate` | Duplicate invoice |
| POST | `/api/invoices/bulk-status` | Bulk status update |
| POST | `/api/invoices/bulk-delete` | Bulk delete |
| POST | `/api/invoices/mark-overdue` | Auto-mark past-due as overdue |
| GET | `/api/stats` | Revenue + status counts |
| GET | `/api/chart/revenue` | Monthly revenue data (`?months=6`) |
| GET | `/api/next-number` | Auto-increment invoice number |
| GET | `/api/export/csv` | Download all invoices as CSV |
| GET | `/api/export/json` | Download all invoices as JSON |
| GET | `/api/templates` | List invoice templates |
| GET | `/api/templates/:id` | Get template items + terms |
| GET | `/api/clients` | List clients (`?q=`) |
| POST | `/api/clients` | Create / update client |
| DELETE | `/api/clients/:id` | Delete client |
| GET | `/api/settings` | Get settings |
| POST | `/api/settings` | Save settings |

---

## Invoice Payload Schema

```json
{
  "currency": "USD",
  "discount": 5,
  "notes": "Thank you for your business!",
  "payment_terms": "Net 30",
  "bank_details": "Chase Bank · Routing: 021000089",
  "invoice": {
    "number": "INV-2026-001",
    "date": "2026-01-15",
    "due_date": "2026-02-14",
    "status": "pending"
  },
  "company": {
    "name": "Pixel Studio Co.",
    "tagline": "Design · Development",
    "email": "hello@pixelstudio.io",
    "phone": "+1 415 555 0182",
    "website": "www.pixelstudio.io",
    "address": "88 Market St, San Francisco CA"
  },
  "client": {
    "name": "Wayne Enterprises",
    "email": "accounts@wayne.com",
    "phone": "+1 212 555 8432",
    "address": "1007 Mountain Drive, Gotham NJ"
  },
  "items": [
    {
      "description": "Brand Identity Design",
      "notes": "3 concepts, unlimited revisions",
      "quantity": 1,
      "unit_price": 3500,
      "tax_rate": 8
    }
  ]
}
```

---

## Tech Stack

- **Backend**: Python 3.11 · Flask 3 · SQLite (via stdlib `sqlite3`)
- **PDF**: ReportLab 4 (pure Python, no system dependencies)
- **Frontend**: Vanilla JS ES2020 · CSS Grid/Flexbox · No frameworks
- **Database**: SQLite (zero-config, file-based, persistent)
- **Production**: Gunicorn · Docker · WSGI-compatible
