"""
InvoiceForge — Advanced Invoice Generator
Flask backend: PDF generation, invoice history, client address book
"""

import io
import json
import sqlite3
import os
import uuid
from datetime import datetime, date
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template, g

# ── Load .env if present (zero-dependency dev convenience) ───────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)

app = Flask(__name__)

# ── Config from environment ───────────────────────────────────────
import re as _re
import os as _os

app.config.update(
    SECRET_KEY   = _os.environ.get("SECRET_KEY", "dev-change-me-in-production"),
    MAX_ITEMS    = int(_os.environ.get("MAX_ITEMS", 100)),
    MAX_INVOICES = int(_os.environ.get("MAX_INVOICES", 10000)),
)

# ── Security headers ──────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "SAMEORIGIN"
    response.headers["X-XSS-Protection"]       = "1; mode=block"
    response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    return response

# ── Input sanitization ────────────────────────────────────────────
def _strip_rl_tags(text):
    """
    Sanitize text for safe use inside ReportLab Paragraph().
    1) Strip ALL tags.
    2) Re-allow a safe whitelist: <b>, </b>, <i>, </i>, <br/>.
    """
    if not text:
        return ""
    s = str(text)
    # Remove all tags first
    s = _re.sub(r"<[^>]+>", "", s)
    return s.strip()

def sanitize_invoice(data):
    """Recursively sanitize all string fields in an invoice payload."""
    if not isinstance(data, dict):
        return {}
    def _clean(v):
        if isinstance(v, str):  return _strip_rl_tags(v)
        if isinstance(v, dict): return {k: _clean(val) for k, val in v.items()}
        if isinstance(v, list): return [_clean(i) for i in v]
        return v
    return _clean(data)


# ── Database ─────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "data" / "invoices.db"
DB_PATH.parent.mkdir(exist_ok=True)

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS invoices (
            id          TEXT PRIMARY KEY,
            number      TEXT NOT NULL,
            client_name TEXT,
            status      TEXT DEFAULT 'pending',
            total       REAL DEFAULT 0,
            currency    TEXT DEFAULT 'USD',
            issue_date  TEXT,
            due_date    TEXT,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clients (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            email      TEXT,
            phone      TEXT,
            address    TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    db.commit()
    db.close()

init_db()

# ── Color Palette ────────────────────────────────────────────────
BRAND_DARK    = colors.HexColor("#0F172A")
BRAND_MID     = colors.HexColor("#1E3A5F")
BRAND_ACCENT  = colors.HexColor("#3B82F6")
BRAND_LIGHT   = colors.HexColor("#EFF6FF")
BRAND_MUTED   = colors.HexColor("#64748B")
BRAND_BORDER  = colors.HexColor("#CBD5E1")
WHITE         = colors.white
SUCCESS_GREEN = colors.HexColor("#10B981")
WARNING_AMBER = colors.HexColor("#F59E0B")
DANGER_RED    = colors.HexColor("#EF4444")

CURRENCY_SYMBOLS = {"USD":"$","EUR":"€","GBP":"£","INR":"₹","JPY":"¥","CAD":"$","AUD":"$","SGD":"$","AED":"د.إ","CHF":"CHF"}

def status_color(status):
    return {"paid": SUCCESS_GREEN, "pending": WARNING_AMBER, "overdue": DANGER_RED}.get(status.lower(), BRAND_MUTED)

def calc_totals(data):
    items = data.get("items", [])
    subtotal = sum(float(i.get("quantity",1)) * float(i.get("unit_price",0)) for i in items)
    tax_total = sum(
        float(i.get("quantity",1)) * float(i.get("unit_price",0)) * float(i.get("tax_rate",0)) / 100
        for i in items
    )
    disc_pct = float(data.get("discount", 0))
    disc_amt = subtotal * disc_pct / 100
    grand    = subtotal + tax_total - disc_amt
    return subtotal, tax_total, disc_amt, grand

# ── PDF Builder ──────────────────────────────────────────────────
def build_pdf(data: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=14*mm, bottomMargin=14*mm)
    W = A4[0] - 36*mm

    styles = getSampleStyleSheet()
    def ps(name, **kw):
        return ParagraphStyle(name, parent=styles["Normal"], **kw)

    S_COMPANY = ps("company", fontSize=22, textColor=WHITE, fontName="Helvetica-Bold", leading=26)
    S_TAGLINE = ps("tagline", fontSize=9, textColor=colors.HexColor("#93C5FD"), fontName="Helvetica", leading=12)
    S_INVOICE = ps("invoice", fontSize=28, textColor=WHITE, fontName="Helvetica-Bold", leading=32, alignment=TA_RIGHT)
    S_LABEL   = ps("label",   fontSize=7, textColor=BRAND_MUTED, fontName="Helvetica-Bold", leading=10, spaceBefore=4)
    S_VALUE   = ps("value",   fontSize=10, textColor=BRAND_DARK, fontName="Helvetica", leading=13)
    S_TH      = ps("th",      fontSize=8, textColor=WHITE, fontName="Helvetica-Bold", leading=11)
    S_TD      = ps("td",      fontSize=9, textColor=BRAND_DARK, fontName="Helvetica", leading=12)
    S_TD_R    = ps("tdr",     fontSize=9, textColor=BRAND_DARK, fontName="Helvetica", leading=12, alignment=TA_RIGHT)
    S_TOTAL_L = ps("totall",  fontSize=10, textColor=BRAND_DARK, fontName="Helvetica", leading=14, alignment=TA_RIGHT)
    S_TOTAL_V = ps("totalv",  fontSize=10, textColor=BRAND_DARK, fontName="Helvetica-Bold", leading=14, alignment=TA_RIGHT)
    S_GRAND_L = ps("grandl",  fontSize=13, textColor=WHITE, fontName="Helvetica-Bold", leading=16, alignment=TA_RIGHT)
    S_GRAND_V = ps("grandv",  fontSize=13, textColor=WHITE, fontName="Helvetica-Bold", leading=16, alignment=TA_RIGHT)
    S_NOTE    = ps("note",    fontSize=8, textColor=BRAND_MUTED, fontName="Helvetica", leading=11)
    S_FOOTER  = ps("footer",  fontSize=7, textColor=BRAND_MUTED, fontName="Helvetica", leading=10, alignment=TA_CENTER)

    story = []
    company = data.get("company", {})
    invoice_meta = data.get("invoice", {})
    currency = data.get("currency", "USD")
    sym = CURRENCY_SYMBOLS.get(currency, "$")

    # ── Header Banner ──────────────────────────────────────────
    left_col = [
        [Paragraph(company.get("name", "Your Company"), S_COMPANY)],
        [Paragraph(company.get("tagline", ""), S_TAGLINE)],
        [Spacer(1, 6)],
        [Paragraph(company.get("address", ""), S_TAGLINE)],
        [Paragraph(company.get("email", ""), S_TAGLINE)],
        [Paragraph(company.get("phone", ""), S_TAGLINE)],
    ]
    right_col = [
        [Paragraph("INVOICE", S_INVOICE)],
        [Paragraph(f"#{invoice_meta.get('number','INV-001')}",
                   ps("invnum", fontSize=13, textColor=colors.HexColor("#93C5FD"),
                      fontName="Helvetica-Bold", leading=16, alignment=TA_RIGHT))],
    ]
    inner_style = TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
    ])
    header_data = [[
        Table(left_col,  colWidths=[W*0.55], style=inner_style),
        Table(right_col, colWidths=[W*0.45], style=inner_style),
    ]]
    header_tbl = Table(header_data, colWidths=[W*0.55, W*0.45])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),BRAND_MID),
        ("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14),
        ("TOPPADDING",(0,0),(-1,-1),14),("BOTTOMPADDING",(0,0),(-1,-1),14),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 8))

    # ── Status + Meta Row ──────────────────────────────────────
    status = invoice_meta.get("status", "pending")
    sc = status_color(status)
    status_tbl = Table([[Paragraph(
        f'<font color="white"><b>{status.upper()}</b></font>',
        ps("badge", fontSize=8, fontName="Helvetica-Bold", leading=10)
    )]], colWidths=[28*mm])
    status_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),sc),
        ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN",(0,0),(-1,-1),"LEFT"),
    ]))
    meta_right = [
        [Paragraph("ISSUE DATE",S_LABEL), Paragraph("DUE DATE",S_LABEL), Paragraph("CURRENCY",S_LABEL)],
        [Paragraph(invoice_meta.get("date",""),S_VALUE),
         Paragraph(invoice_meta.get("due_date",""),S_VALUE),
         Paragraph(currency,S_VALUE)],
    ]
    meta_tbl = Table(meta_right, colWidths=[W*0.2, W*0.2, W*0.15])
    meta_tbl.setStyle(TableStyle([
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1),
    ]))
    row2_tbl = Table([[status_tbl, Spacer(1,1), meta_tbl]],
                     colWidths=[32*mm, W*0.06, W*0.55+W*0.055])
    row2_tbl.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(row2_tbl)
    story.append(Spacer(1, 8))

    # ── Bill To / From ─────────────────────────────────────────
    client = data.get("client", {})
    def addr_block(title, entity):
        rows = [
            [Paragraph(title, ps("bt", fontSize=7, fontName="Helvetica-Bold", textColor=BRAND_ACCENT, leading=9))],
            [Paragraph(entity.get("name",""), ps("bn", fontSize=11, fontName="Helvetica-Bold", textColor=BRAND_DARK, leading=14))],
            [Paragraph(entity.get("address",""), S_NOTE)],
            [Paragraph(entity.get("email",""), S_NOTE)],
            [Paragraph(entity.get("phone",""), S_NOTE)],
        ]
        t = Table(rows, colWidths=[(W/2)-8*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),BRAND_LIGHT),
            ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
            ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
            ("LINEABOVE",(0,0),(-1,0),2,BRAND_ACCENT),
        ]))
        return t

    billing_tbl = Table(
        [[addr_block("BILL TO", client), Spacer(1,1), addr_block("FROM", company)]],
        colWidths=[(W/2)-4*mm, 8*mm, (W/2)-4*mm]
    )
    billing_tbl.setStyle(TableStyle([
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
    ]))
    story.append(billing_tbl)
    story.append(Spacer(1, 10))

    # ── Line Items ─────────────────────────────────────────────
    col_w = [W*0.45, W*0.10, W*0.18, W*0.10, W*0.17]
    header_row = [Paragraph(h, S_TH) for h in ["DESCRIPTION","QTY","UNIT PRICE","TAX %","AMOUNT"]]
    rows = [header_row]
    for i, item in enumerate(data.get("items", [])):
        qty    = float(item.get("quantity", 1))
        price  = float(item.get("unit_price", 0))
        tax    = float(item.get("tax_rate", 0))
        amount = qty * price
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        desc_inner = [[Paragraph(item.get("description",""), S_TD)]]
        if item.get("notes"):
            desc_inner.append([Paragraph(item.get("notes",""), S_NOTE)])
        desc_cell = Table(desc_inner, colWidths=[W*0.45],
                          style=TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
                                            ("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1)]))
        rows.append([desc_cell, Paragraph(qty_str,S_TD),
                     Paragraph(f"{sym}{price:,.2f}",S_TD_R),
                     Paragraph(f"{tax:.1f}%",S_TD),
                     Paragraph(f"{sym}{amount:,.2f}",S_TD_R)])

    items_tbl = Table(rows, colWidths=col_w, repeatRows=1)
    item_style = [
        ("BACKGROUND",(0,0),(-1,0),BRAND_DARK),
        ("TOPPADDING",(0,0),(-1,0),8),("BOTTOMPADDING",(0,0),(-1,0),8),
        ("LEFTPADDING",(0,0),(-1,0),8),("RIGHTPADDING",(0,0),(-1,0),8),
        ("ALIGN",(1,0),(-1,0),"RIGHT"),
        ("TOPPADDING",(0,1),(-1,-1),7),("BOTTOMPADDING",(0,1),(-1,-1),7),
        ("LEFTPADDING",(0,1),(-1,-1),8),("RIGHTPADDING",(0,1),(-1,-1),8),
        ("ALIGN",(2,1),(-1,-1),"RIGHT"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("GRID",(0,1),(-1,-1),0.5,BRAND_BORDER),
        ("LINEBELOW",(0,0),(-1,0),1.5,BRAND_ACCENT),
    ]
    for i in range(1, len(rows)):
        bg = WHITE if i % 2 == 1 else colors.HexColor("#F8FAFC")
        item_style.append(("BACKGROUND",(0,i),(-1,i),bg))
    items_tbl.setStyle(TableStyle(item_style))
    story.append(items_tbl)
    story.append(Spacer(1, 8))

    # ── Totals ─────────────────────────────────────────────────
    subtotal, tax_total, disc_amt, grand_total = calc_totals(data)
    disc_pct = float(data.get("discount", 0))

    totals_data = [
        [Paragraph("Subtotal", S_TOTAL_L),  Paragraph(f"{sym}{subtotal:,.2f}", S_TOTAL_V)],
        [Paragraph("Tax",      S_TOTAL_L),  Paragraph(f"{sym}{tax_total:,.2f}", S_TOTAL_V)],
        [Paragraph(f"Discount ({disc_pct:.0f}%)", S_TOTAL_L),
         Paragraph(f"- {sym}{disc_amt:,.2f}", S_TOTAL_V)],
    ]
    totals_tbl = Table(totals_data, colWidths=[W*0.82, W*0.18])
    totals_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"RIGHT"),
        ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("LINEBELOW",(0,-1),(-1,-1),0.5,BRAND_BORDER),
    ]))
    story.append(totals_tbl)
    story.append(Spacer(1, 4))

    grand_tbl = Table(
        [[Paragraph("TOTAL DUE", S_GRAND_L), Paragraph(f"{sym}{grand_total:,.2f}", S_GRAND_V)]],
        colWidths=[W*0.82, W*0.18]
    )
    grand_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),BRAND_MID),
        ("ALIGN",(0,0),(-1,-1),"RIGHT"),
        ("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10),
    ]))
    story.append(grand_tbl)
    story.append(Spacer(1, 12))

    # ── Notes & Payment Terms ──────────────────────────────────
    notes         = data.get("notes", "")
    payment_terms = data.get("payment_terms", "")
    note_rows = []
    if notes:
        note_rows.append(Paragraph("NOTES", ps("nl", fontSize=7, fontName="Helvetica-Bold", textColor=BRAND_ACCENT, leading=9)))
        note_rows.append(Paragraph(notes, S_NOTE))
    if payment_terms:
        if note_rows: note_rows.append(Spacer(1, 4))
        note_rows.append(Paragraph("PAYMENT TERMS", ps("nl2", fontSize=7, fontName="Helvetica-Bold", textColor=BRAND_ACCENT, leading=9)))
        note_rows.append(Paragraph(payment_terms, S_NOTE))
    if data.get("bank_details"):
        if note_rows: note_rows.append(Spacer(1, 4))
        note_rows.append(Paragraph("BANK / PAYMENT DETAILS", ps("nl3", fontSize=7, fontName="Helvetica-Bold", textColor=BRAND_ACCENT, leading=9)))
        note_rows.append(Paragraph(data["bank_details"], S_NOTE))
    if note_rows:
        note_data = [[r] for r in note_rows]
        note_tbl = Table(note_data, colWidths=[W*0.6])
        note_tbl.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),BRAND_LIGHT),
            ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            ("LINEABOVE",(0,0),(-1,0),2,BRAND_ACCENT),
        ]))
        story.append(note_tbl)
        story.append(Spacer(1, 10))

    # ── Footer ─────────────────────────────────────────────────
    story.append(HRFlowable(width=W, thickness=0.5, color=BRAND_BORDER))
    story.append(Spacer(1, 5))
    story.append(Paragraph(
        f"{company.get('name','')}  ·  {company.get('email','')}  ·  "
        f"{company.get('website','')}  ·  Generated {datetime.now().strftime('%B %d, %Y')}",
        S_FOOTER
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ══════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")

# ── PDF endpoints ─────────────────────────────────────────────────
@app.route("/api/generate", methods=["POST"])
def generate():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "No data provided"}), 400
        data = sanitize_invoice(data)
        pdf_bytes = build_pdf(data)
        inv_num   = data.get("invoice", {}).get("number", "invoice")
        return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                         as_attachment=True, download_name=f"invoice-{inv_num}.pdf")
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/api/preview", methods=["POST"])
def preview():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "No data provided"}), 400
        data = sanitize_invoice(data)
        pdf_bytes = build_pdf(data)
        return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=False)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

# ── Invoice History ───────────────────────────────────────────────
@app.route("/api/invoices", methods=["GET"])
def list_invoices():
    db = get_db()
    status_filter = request.args.get("status")
    search = request.args.get("q", "").strip()
    sql = "SELECT id,number,client_name,status,total,currency,issue_date,due_date,created_at,updated_at FROM invoices"
    params = []
    conditions = []
    if status_filter:
        conditions.append("status = ?"); params.append(status_filter)
    if search:
        conditions.append("(number LIKE ? OR client_name LIKE ?)"); params += [f"%{search}%", f"%{search}%"]
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC"
    rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/invoices", methods=["POST"])
def save_invoice():
    db  = get_db()
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data"}), 400
    data = sanitize_invoice(data)
    inv_id = data.get("_id") or str(uuid.uuid4())
    inv    = data.get("invoice", {})
    _, _, _, grand = calc_totals(data)
    now    = datetime.utcnow().isoformat()
    existing = db.execute("SELECT id FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if existing:
        db.execute("""UPDATE invoices SET number=?,client_name=?,status=?,total=?,currency=?,
                      issue_date=?,due_date=?,payload=?,updated_at=? WHERE id=?""",
                   (inv.get("number",""), data.get("client",{}).get("name",""),
                    inv.get("status","pending"), grand, data.get("currency","USD"),
                    inv.get("date",""), inv.get("due_date",""), json.dumps(data), now, inv_id))
    else:
        db.execute("""INSERT INTO invoices (id,number,client_name,status,total,currency,
                      issue_date,due_date,payload,created_at,updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (inv_id, inv.get("number",""), data.get("client",{}).get("name",""),
                    inv.get("status","pending"), grand, data.get("currency","USD"),
                    inv.get("date",""), inv.get("due_date",""), json.dumps(data), now, now))
    db.commit()
    return jsonify({"id": inv_id, "saved": True})

@app.route("/api/invoices/<inv_id>", methods=["GET"])
def get_invoice(inv_id):
    db  = get_db()
    row = db.execute("SELECT payload FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    payload = json.loads(row["payload"])
    payload["_id"] = inv_id
    return jsonify(payload)

@app.route("/api/invoices/<inv_id>", methods=["DELETE"])
def delete_invoice(inv_id):
    db = get_db()
    db.execute("DELETE FROM invoices WHERE id=?", (inv_id,))
    db.commit()
    return jsonify({"deleted": True})

@app.route("/api/invoices/<inv_id>/pdf", methods=["GET"])
def download_saved_pdf(inv_id):
    db  = get_db()
    row = db.execute("SELECT payload,number FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    data = json.loads(row["payload"])
    pdf  = build_pdf(data)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     as_attachment=True, download_name=f"invoice-{row['number']}.pdf")

# ── Stats endpoint ─────────────────────────────────────────────────
@app.route("/api/stats", methods=["GET"])
def stats():
    db = get_db()
    rows = db.execute("SELECT status, COUNT(*) as cnt, SUM(total) as total FROM invoices GROUP BY status").fetchall()
    result = {"total_invoices": 0, "total_revenue": 0, "paid": 0, "pending": 0, "overdue": 0,
              "paid_revenue": 0, "pending_revenue": 0, "overdue_revenue": 0}
    for r in rows:
        s   = r["status"]
        cnt = r["cnt"]
        rev = r["total"] or 0
        result["total_invoices"] += cnt
        result["total_revenue"]  += rev
        if s in result:
            result[s] = cnt
            result[f"{s}_revenue"] = rev
    return jsonify(result)

# ── Next invoice number ────────────────────────────────────────────
@app.route("/api/next-number", methods=["GET"])
def next_number():
    db     = get_db()
    prefix = request.args.get("prefix", "INV")
    row    = db.execute(
        "SELECT number FROM invoices WHERE number LIKE ? ORDER BY created_at DESC LIMIT 1",
        (f"{prefix}-%",)
    ).fetchone()
    if row:
        try:
            last_n = int(row["number"].rsplit("-", 1)[-1])
            return jsonify({"number": f"{prefix}-{last_n+1:03d}"})
        except:
            pass
    year   = datetime.now().year
    count  = db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    return jsonify({"number": f"{prefix}-{year}-{count+1:03d}"})

# ── Client Address Book ────────────────────────────────────────────
@app.route("/api/clients", methods=["GET"])
def list_clients():
    db = get_db()
    q  = request.args.get("q","").strip()
    if q:
        rows = db.execute("SELECT * FROM clients WHERE name LIKE ? ORDER BY name", (f"%{q}%",)).fetchall()
    else:
        rows = db.execute("SELECT * FROM clients ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/clients", methods=["POST"])
def save_client():
    db   = get_db()
    data = request.get_json(force=True)
    cid  = data.get("id") or str(uuid.uuid4())
    now  = datetime.utcnow().isoformat()
    existing = db.execute("SELECT id FROM clients WHERE id=?", (cid,)).fetchone()
    if existing:
        db.execute("UPDATE clients SET name=?,email=?,phone=?,address=? WHERE id=?",
                   (data.get("name",""),data.get("email",""),data.get("phone",""),data.get("address",""),cid))
    else:
        db.execute("INSERT INTO clients (id,name,email,phone,address,created_at) VALUES (?,?,?,?,?,?)",
                   (cid,data.get("name",""),data.get("email",""),data.get("phone",""),data.get("address",""),now))
    db.commit()
    return jsonify({"id": cid, "saved": True})

@app.route("/api/clients/<cid>", methods=["DELETE"])
def delete_client(cid):
    db = get_db()
    db.execute("DELETE FROM clients WHERE id=?", (cid,))
    db.commit()
    return jsonify({"deleted": True})

# ── Settings ───────────────────────────────────────────────────────
@app.route("/api/settings", methods=["GET"])
def get_settings():
    db   = get_db()
    rows = db.execute("SELECT key,value FROM settings").fetchall()
    return jsonify({r["key"]: json.loads(r["value"]) for r in rows})

@app.route("/api/settings", methods=["POST"])
def save_settings():
    db   = get_db()
    data = request.get_json(force=True)
    for k, v in data.items():
        db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, json.dumps(v)))
    db.commit()
    return jsonify({"saved": True})

# ── Duplicate Invoice ──────────────────────────────────────────────
@app.route("/api/invoices/<inv_id>/duplicate", methods=["POST"])
def duplicate_invoice(inv_id):
    db  = get_db()
    row = db.execute("SELECT payload FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    data       = json.loads(row["payload"])
    new_id     = str(uuid.uuid4())
    now        = datetime.utcnow().isoformat()
    inv        = data.get("invoice", {})
    # Auto-increment the number
    old_num    = inv.get("number", "INV-001")
    try:
        parts  = old_num.rsplit("-", 1)
        new_num = f"{parts[0]}-{int(parts[1])+1:03d}" if len(parts) == 2 else old_num + "-COPY"
    except Exception:
        new_num = old_num + "-COPY"
    data["invoice"]["number"] = new_num
    data["invoice"]["status"] = "pending"
    data["invoice"]["date"]   = datetime.now().strftime("%Y-%m-%d")
    data.pop("_id", None)
    _, _, _, grand = calc_totals(data)
    db.execute("""INSERT INTO invoices (id,number,client_name,status,total,currency,
                  issue_date,due_date,payload,created_at,updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (new_id, new_num, data.get("client",{}).get("name",""),
                "pending", grand, data.get("currency","USD"),
                data["invoice"].get("date",""), data["invoice"].get("due_date",""),
                json.dumps(data), now, now))
    db.commit()
    return jsonify({"id": new_id, "number": new_num})

# ── Bulk Status Update ─────────────────────────────────────────────
@app.route("/api/invoices/bulk-status", methods=["POST"])
def bulk_status():
    db   = get_db()
    data = request.get_json(force=True)
    ids  = data.get("ids", [])
    status = data.get("status", "pending")
    if status not in ("paid", "pending", "overdue"):
        return jsonify({"error": "Invalid status"}), 400
    now = datetime.utcnow().isoformat()
    for inv_id in ids:
        row = db.execute("SELECT payload FROM invoices WHERE id=?", (inv_id,)).fetchone()
        if row:
            payload = json.loads(row["payload"])
            payload["invoice"]["status"] = status
            db.execute("UPDATE invoices SET status=?,payload=?,updated_at=? WHERE id=?",
                       (status, json.dumps(payload), now, inv_id))
    db.commit()
    return jsonify({"updated": len(ids)})

# ── Bulk Delete ────────────────────────────────────────────────────
@app.route("/api/invoices/bulk-delete", methods=["POST"])
def bulk_delete():
    db   = get_db()
    data = request.get_json(force=True)
    ids  = data.get("ids", [])
    for inv_id in ids:
        db.execute("DELETE FROM invoices WHERE id=?", (inv_id,))
    db.commit()
    return jsonify({"deleted": len(ids)})

# ── Auto-mark Overdue ──────────────────────────────────────────────
@app.route("/api/invoices/mark-overdue", methods=["POST"])
def mark_overdue():
    db  = get_db()
    today = date.today().isoformat()
    rows  = db.execute(
        "SELECT id, payload FROM invoices WHERE status='pending' AND due_date < ? AND due_date != ''",
        (today,)
    ).fetchall()
    now   = datetime.utcnow().isoformat()
    count = 0
    for row in rows:
        payload = json.loads(row["payload"])
        payload["invoice"]["status"] = "overdue"
        db.execute("UPDATE invoices SET status='overdue', payload=?, updated_at=? WHERE id=?",
                   (json.dumps(payload), now, row["id"]))
        count += 1
    db.commit()
    return jsonify({"marked_overdue": count})

# ── CSV Export ─────────────────────────────────────────────────────
@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    import csv as csv_mod
    db   = get_db()
    rows = db.execute(
        "SELECT number,client_name,status,total,currency,issue_date,due_date,created_at FROM invoices ORDER BY created_at DESC"
    ).fetchall()
    buf = io.StringIO()
    w   = csv_mod.writer(buf)
    w.writerow(["Invoice #","Client","Status","Total","Currency","Issue Date","Due Date","Created At"])
    for r in rows:
        w.writerow([r["number"], r["client_name"], r["status"],
                    f"{r['total']:.2f}", r["currency"],
                    r["issue_date"], r["due_date"], r["created_at"]])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"invoices-export-{date.today().isoformat()}.csv"
    )

# ── JSON Bulk Export ───────────────────────────────────────────────
@app.route("/api/export/json", methods=["GET"])
def export_json():
    db   = get_db()
    rows = db.execute("SELECT payload FROM invoices ORDER BY created_at DESC").fetchall()
    data = [json.loads(r["payload"]) for r in rows]
    buf  = io.BytesIO(json.dumps(data, indent=2).encode())
    return send_file(buf, mimetype="application/json", as_attachment=True,
                     download_name=f"invoices-export-{date.today().isoformat()}.json")

# ── Revenue Chart Data ─────────────────────────────────────────────
@app.route("/api/chart/revenue", methods=["GET"])
def chart_revenue():
    db     = get_db()
    months = request.args.get("months", 6, type=int)
    rows   = db.execute("""
        SELECT strftime('%Y-%m', issue_date) as month,
               SUM(total) as revenue,
               COUNT(*) as count,
               SUM(CASE WHEN status='paid' THEN total ELSE 0 END) as paid_revenue
        FROM invoices
        WHERE issue_date != '' AND issue_date IS NOT NULL
        GROUP BY month
        ORDER BY month DESC
        LIMIT ?
    """, (months,)).fetchall()
    return jsonify([dict(r) for r in reversed(rows)])

# ── Invoice Templates ──────────────────────────────────────────────
TEMPLATES = {
    "consulting": {
        "name": "Consulting Services",
        "items": [
            {"description": "Strategic Consulting", "notes": "Phase 1 — Discovery & Analysis", "quantity": 10, "unit_price": 250, "tax_rate": 0},
            {"description": "Workshop Facilitation", "notes": "Half-day workshop with stakeholders", "quantity": 4, "unit_price": 500, "tax_rate": 0},
            {"description": "Deliverables & Report", "notes": "Findings document + recommendations", "quantity": 1, "unit_price": 1500, "tax_rate": 0},
        ],
        "payment_terms": "Net 30 — Payment due within 30 days of invoice date.",
        "notes": "Thank you for choosing our consulting services. Please reference the invoice number in your payment.",
    },
    "design": {
        "name": "Design Project",
        "items": [
            {"description": "Brand Identity Design", "notes": "Logo, color palette, typography", "quantity": 1, "unit_price": 3500, "tax_rate": 8},
            {"description": "UI/UX Design", "notes": "Wireframes + high-fidelity mockups", "quantity": 1, "unit_price": 4200, "tax_rate": 8},
            {"description": "Design Revisions", "notes": "Up to 3 revision rounds", "quantity": 3, "unit_price": 400, "tax_rate": 8},
        ],
        "payment_terms": "50% upfront, 50% on delivery.",
        "notes": "All source files delivered in AI, PSD, and SVG formats upon final payment.",
    },
    "development": {
        "name": "Web Development",
        "items": [
            {"description": "Frontend Development", "notes": "React/Next.js implementation", "quantity": 40, "unit_price": 120, "tax_rate": 0},
            {"description": "Backend API Development", "notes": "REST API + database design", "quantity": 30, "unit_price": 130, "tax_rate": 0},
            {"description": "QA Testing & Deployment", "notes": "Test coverage + CI/CD setup", "quantity": 10, "unit_price": 100, "tax_rate": 0},
        ],
        "payment_terms": "Net 15 — Milestone-based: 30% start, 40% mid, 30% delivery.",
        "notes": "Includes 30-day post-launch support. Hosting and domain costs billed separately.",
    },
    "retainer": {
        "name": "Monthly Retainer",
        "items": [
            {"description": "Monthly Retainer Fee", "notes": "Ongoing support & maintenance", "quantity": 1, "unit_price": 2000, "tax_rate": 0},
            {"description": "Additional Hours (if any)", "notes": "Billed at hourly rate beyond retainer", "quantity": 0, "unit_price": 150, "tax_rate": 0},
        ],
        "payment_terms": "Due on the 1st of each month. Auto-renews monthly.",
        "notes": "Retainer includes up to 20 hours/month. Unused hours do not roll over.",
    },
    "photography": {
        "name": "Photography Services",
        "items": [
            {"description": "Photography Session", "notes": "Full-day shoot (8 hours)", "quantity": 1, "unit_price": 1800, "tax_rate": 8},
            {"description": "Photo Editing & Retouching", "notes": "Up to 50 final edited images", "quantity": 50, "unit_price": 15, "tax_rate": 8},
            {"description": "Rush Delivery (optional)", "notes": "48-hour turnaround", "quantity": 0, "unit_price": 300, "tax_rate": 8},
        ],
        "payment_terms": "50% deposit required to book. Balance due upon delivery.",
        "notes": "High-resolution files delivered via private gallery link within 7 business days.",
    },
}

@app.route("/api/templates", methods=["GET"])
def list_templates():
    return jsonify([{"id": k, "name": v["name"]} for k, v in TEMPLATES.items()])

@app.route("/api/templates/<tid>", methods=["GET"])
def get_template(tid):
    t = TEMPLATES.get(tid)
    if not t:
        return jsonify({"error": "Not found"}), 404
    return jsonify(t)

# ── Health Check ───────────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    db    = get_db()
    count = db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    return jsonify({"status": "ok", "invoices": count, "version": "2.0.0"})


# ── Error Handlers ─────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Endpoint not found", "path": request.path}), 404
    return render_template("404.html"), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(413)
def request_too_large(e):
    return jsonify({"error": "Request payload too large"}), 413

@app.errorhandler(500)
def internal_error(e):
    import traceback
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error", "detail": str(e)}), 500
    return render_template("500.html"), 500

# ── Favicon ────────────────────────────────────────────────────────
@app.route("/favicon.ico")
def favicon():
    # Inline SVG-based favicon served as ICO placeholder
    svg = (
        b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'''
        b'''<rect width="32" height="32" rx="6" fill="#1E3A5F"/>'''
        b'''<path d="M9 8h14v2H9zm0 4h10v2H9zm0 4h14v2H9zm0 4h8v2H9z"'''
        b''' fill="#3B82F6"/>'''
        b'''</svg>'''
    )
    return svg, 200, {"Content-Type": "image/svg+xml", "Cache-Control": "public,max-age=86400"}

if __name__ == "__main__":
    app.run(debug=True, port=5001, host="0.0.0.0")
