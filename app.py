"""
InvoiceForge — Advanced Invoice Generator
Flask backend: PDF generation, invoice history, client address book
"""

import io
import json
import sqlite3
import os
import uuid
import re
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

# ── Patch DNS resolver for systems with IPv6/local DNS timeouts ─────
try:
    import dns.resolver
    try:
        resolver = dns.resolver.get_default_resolver()
    except Exception:
        resolver = None
        
    if not resolver:
        resolver = dns.resolver.Resolver()
        dns.resolver.default_resolver = resolver
        
    if resolver:
        public_dns = ['8.8.8.8', '8.8.4.4', '1.1.1.1']
        if not resolver.nameservers:
            resolver.nameservers = public_dns
        else:
            for ip in reversed(public_dns):
                if ip not in resolver.nameservers:
                    resolver.nameservers.insert(0, ip)
        resolver.timeout = 2.0
        resolver.lifetime = 6.0
except Exception:
    pass

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
import pymongo
from urllib.parse import urlparse

class MongoRow:
    def __init__(self, doc):
        self._doc = doc or {}

    def __getitem__(self, key):
        if key == 'id':
            return self._doc.get('_id')
        val = self._doc.get(key)
        if key == 'payload' and isinstance(val, dict):
            return json.dumps(val)
        return val

    def get(self, key, default=None):
        if key == 'id':
            return self._doc.get('_id', default)
        val = self._doc.get(key, default)
        if key == 'payload' and isinstance(val, dict):
            return json.dumps(val)
        return val

    def keys(self):
        k = list(self._doc.keys())
        if '_id' in k:
            k.remove('_id')
            k.append('id')
        return k

    def values(self):
        return [self[k] for k in self.keys()]

    def items(self):
        return [(k, self[k]) for k in self.keys()]

    def __len__(self):
        return len(self.keys())

    def __iter__(self):
        return iter(self.values())

_MONGO_CLIENT = None
_MONGO_SSL_WARNING_SHOWN = False

def get_mongo_uri_and_db():
    mongo_uri = (
        os.environ.get("MONGO_URI") or 
        os.environ.get("MONGOURI") or 
        os.environ.get("DATABASE_URL") or 
        "mongodb://localhost:27017/invoiceforge"
    )
    parsed = urlparse(mongo_uri)
    db_name = parsed.path.strip('/') or "invoiceforge"
    # Ensure query parameters are not part of the database name
    if '?' in db_name:
        db_name = db_name.split('?')[0]
    return mongo_uri, db_name

def create_mongo_client(mongo_uri, timeout_ms=5000):
    try:
        import certifi
        ca_file = certifi.where()
    except ImportError:
        ca_file = None

    kwargs = {"serverSelectionTimeoutMS": timeout_ms}
    if ca_file:
        kwargs["tlsCAFile"] = ca_file

    client = pymongo.MongoClient(mongo_uri, **kwargs)
    try:
        client.admin.command('ping')
        return client, False
    except Exception as e:
        err_msg = str(e)
        is_ssl_err = (
            "CERTIFICATE_VERIFY_FAILED" in err_msg 
            or "certificate verify failed" in err_msg 
            or "unable to get local issuer certificate" in err_msg
        )
        if is_ssl_err:
            # Fall back to disabling verification
            kwargs_fallback = {"serverSelectionTimeoutMS": timeout_ms, "tlsAllowInvalidCertificates": True}
            client_fallback = pymongo.MongoClient(mongo_uri, **kwargs_fallback)
            try:
                client_fallback.admin.command('ping')
            except Exception as fallback_err:
                print(f"[Database] SSL fallback client ping failed: {fallback_err}")
            return client_fallback, True
        else:
            raise e

def get_db():
    global _MONGO_CLIENT, _MONGO_SSL_WARNING_SHOWN
    if "db" not in g:
        mongo_uri, db_name = get_mongo_uri_and_db()
        if _MONGO_CLIENT is None:
            # Mask password in URI for logs
            safe_uri = mongo_uri
            if "@" in mongo_uri:
                try:
                    scheme, remainder = mongo_uri.split("://", 1)
                    credentials, host_port_path = remainder.split("@", 1)
                    if ":" in credentials:
                        user, password = credentials.split(":", 1)
                        safe_uri = f"{scheme}://{user}:******@{host_port_path}"
                except Exception:
                    safe_uri = "mongodb://******"
            
            try:
                _MONGO_CLIENT, used_fallback = create_mongo_client(mongo_uri)
                if used_fallback and not _MONGO_SSL_WARNING_SHOWN:
                    print("[Database] Warning: Connected to MongoDB using SSL fallback (tlsAllowInvalidCertificates=True)")
                    _MONGO_SSL_WARNING_SHOWN = True
                elif not used_fallback:
                    print(f"[Database] Client connection established securely to: {safe_uri}")
            except Exception as e:
                # If both fail, print error and fall back to default init behaviour
                print(f"[Database] Connection failure: {str(e)}")
                _MONGO_CLIENT = pymongo.MongoClient(mongo_uri, tlsAllowInvalidCertificates=True)
                
        g.db = _MONGO_CLIENT[db_name]
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    pass

def init_db():
    mongo_uri, db_name = get_mongo_uri_and_db()
    # Mask password in URI for logs
    safe_uri = mongo_uri
    if "@" in mongo_uri:
        try:
            scheme, remainder = mongo_uri.split("://", 1)
            credentials, host_port_path = remainder.split("@", 1)
            if ":" in credentials:
                user, password = credentials.split(":", 1)
                safe_uri = f"{scheme}://{user}:******@{host_port_path}"
        except Exception:
            safe_uri = "mongodb://******"
            
    print(f"[Database] Connecting to MongoDB: {safe_uri}")
    try:
        client, used_fallback = create_mongo_client(mongo_uri)
        if used_fallback:
            print(f"[Database] Connected successfully (with tlsAllowInvalidCertificates=True) to database: '{db_name}'")
        else:
            print(f"[Database] Successfully connected securely to database: '{db_name}'")
        
        db = client[db_name]
        db.invoices.create_index("number")
        db.invoices.create_index("created_at")
        db.clients.create_index("name")
        print("[Database] Collection indexes initialized/verified")
        client.close()
    except Exception as e:
        print(f"[Database] Connection verification failed: {str(e)}")

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
    tax_total = sum(float(i.get("quantity",1)) * float(i.get("unit_price",0)) * (float(i.get("tax_rate",0))/100.0) for i in items)
    discount = float(data.get("discount", 0))
    discount_val = subtotal * (discount / 100.0)
    grand_total = subtotal + tax_total - discount_val
    return subtotal, tax_total, discount_val, grand_total

# ── PDF Builder ──────────────────────────────────────────────────
def build_pdf(data: dict) -> bytes:
    """
    Generates a beautifully structured PDF invoice in memory.
    Uses clean design: a top slate-blue banner, a nice metadata block,
    alternating row colors for line items, custom font styling, and
    an explicit totals block.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15*mm,
        leftMargin=15*mm,
        topMargin=15*mm,
        bottomMargin=15*mm
    )

    styles = getSampleStyleSheet()

    # Modify Normal
    styles['Normal'].textColor = BRAND_DARK
    styles['Normal'].fontSize = 9
    styles['Normal'].leading = 13

    # Add custom styles if they don't exist
    def add_style(name, parent, **kwargs):
        if name in styles:
            return styles[name]
        s = ParagraphStyle(name, parent=parent, **kwargs)
        styles.add(s)
        return s

    style_title = add_style('DocTitle', styles['Normal'], fontSize=20, leading=24, textColor=WHITE, fontName='Helvetica-Bold')
    style_subtitle = add_style('DocSub', styles['Normal'], fontSize=9, leading=13, textColor=BRAND_LIGHT)
    style_h2 = add_style('SectionH2', styles['Normal'], fontSize=11, leading=15, textColor=BRAND_MID, fontName='Helvetica-Bold')
    style_label = add_style('MetaLabel', styles['Normal'], fontSize=8, leading=10, textColor=BRAND_MUTED, fontName='Helvetica-Bold')
    style_value = add_style('MetaVal', styles['Normal'], fontSize=9, leading=12, fontName='Helvetica-Bold')
    style_bold = add_style('ValBold', styles['Normal'], fontName='Helvetica-Bold')
    style_right = add_style('AlignRight', styles['Normal'], alignment=TA_RIGHT)
    style_right_bold = add_style('AlignRightBold', styles['Normal'], alignment=TA_RIGHT, fontName='Helvetica-Bold')
    style_right_muted = add_style('AlignRightMuted', styles['Normal'], alignment=TA_RIGHT, textColor=BRAND_MUTED)
    style_center = add_style('AlignCenter', styles['Normal'], alignment=TA_CENTER)
    style_white = add_style('TextWhite', styles['Normal'], textColor=WHITE)
    style_white_right = add_style('TextWhiteRight', styles['Normal'], textColor=WHITE, alignment=TA_RIGHT)

    story = []

    # 1. Slate Top Banner
    company = data.get("company", {})
    invoice = data.get("invoice", {})
    currency = data.get("currency", "USD")
    symbol = CURRENCY_SYMBOLS.get(currency, currency)

    banner_data = [
        [
            Paragraph(company.get("name", "Pixel Studio").upper(), style_title),
            Paragraph("INVOICE", style_white_right)
        ],
        [
            Paragraph(company.get("tagline", "Design & Development Services"), style_subtitle),
            Paragraph(f"Invoice #: {invoice.get('number', 'INV-001')}", style_white_right)
        ]
    ]
    banner_table = Table(banner_data, colWidths=[110*mm, 70*mm])
    banner_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), BRAND_MID),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LEFTPADDING', (0,0), (-1,-1), 15),
        ('RIGHTPADDING', (0,0), (-1,-1), 15),
    ]))
    story.append(banner_table)
    story.append(Spacer(1, 8*mm))

    # 2. Metadata Columns (Bill To, Bill From, Dates)
    client = data.get("client", {})
    bill_from_p = Paragraph(
        f"<b>{company.get('name','')}</b><br/>"
        f"{company.get('address','')}<br/>"
        f"{company.get('email','')}<br/>"
        f"{company.get('phone','')}",
        styles['Normal']
    )
    bill_to_p = Paragraph(
        f"<b>{client.get('name','')}</b><br/>"
        f"{client.get('address','')}<br/>"
        f"{client.get('email','')}<br/>"
        f"{client.get('phone','')}",
        styles['Normal']
    )
    dates_p = Paragraph(
        f"<font color='#64748B'><b>Issue Date:</b></font> {invoice.get('date','')}<br/>"
        f"<font color='#64748B'><b>Due Date:</b></font> {invoice.get('due_date','')}<br/>"
        f"<font color='#64748B'><b>Payment Terms:</b></font> {data.get('payment_terms','Net 30')}",
        styles['Normal']
    )

    meta_data = [
        [Paragraph("FROM", style_label), Paragraph("BILL TO", style_label), Paragraph("DETAILS", style_label)],
        [bill_from_p, bill_to_p, dates_p]
    ]
    meta_table = Table(meta_data, colWidths=[60*mm, 60*mm, 60*mm])
    meta_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,0), 4),
        ('TOPPADDING', (0,1), (-1,-1), 4),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10*mm))

    # 3. Line Items Table
    items = data.get("items", [])
    table_headers = [
        Paragraph("Description", style_bold),
        Paragraph("Qty", style_center),
        Paragraph("Unit Price", style_right_bold),
        Paragraph("Tax", style_right_bold),
        Paragraph("Amount", style_right_bold)
    ]
    table_data = [table_headers]

    for index, item in enumerate(items):
        desc_text = f"<b>{item.get('description','')}</b>"
        if item.get("notes"):
            desc_text += f"<br/><font size='8' color='#64748B'>{item.get('notes')}</font>"
        
        qty = float(item.get("quantity", 1))
        price = float(item.get("unit_price", 0))
        tax_rate = float(item.get("tax_rate", 0))
        
        tax_amt = qty * price * (tax_rate / 100.0)
        amt = (qty * price) + tax_amt

        row = [
            Paragraph(desc_text, styles['Normal']),
            Paragraph(f"{qty:g}", style_center),
            Paragraph(f"{symbol}{price:,.2f}", style_right),
            Paragraph(f"{tax_rate:g}%", style_right_muted),
            Paragraph(f"{symbol}{amt:,.2f}", style_right_bold)
        ]
        table_data.append(row)

    items_table = Table(table_data, colWidths=[90*mm, 15*mm, 25*mm, 20*mm, 30*mm])
    
    # Alternating row background styles
    t_style = [
        ('BACKGROUND', (0,0), (-1,0), BRAND_LIGHT),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, BRAND_BORDER),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            t_style.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor("#F8FAFC")))
            
    items_table.setStyle(TableStyle(t_style))
    story.append(items_table)
    story.append(Spacer(1, 6*mm))

    # 4. Totals Block & Notes (Aligned side-by-side)
    subtotal, tax_val, discount_val, grand = calc_totals(data)

    notes_p = Paragraph(
        f"<font color='#64748B'><b>Payment Terms:</b></font><br/>"
        f"{data.get('payment_terms','')}<br/><br/>"
        f"<font color='#64748B'><b>Bank Details:</b></font><br/>"
        f"{data.get('bank_details','')}",
        styles['Normal']
    )

    totals_data = [
        [Paragraph("Subtotal:", styles['Normal']), Paragraph(f"{symbol}{subtotal:,.2f}", style_right)],
        [Paragraph("Tax:", styles['Normal']), Paragraph(f"{symbol}{tax_val:,.2f}", style_right)],
        [Paragraph(f"Discount ({data.get('discount',0)}%):", styles['Normal']), Paragraph(f"-{symbol}{discount_val:,.2f}", style_right)],
        [Paragraph("<b>Grand Total:</b>", style_h2), Paragraph(f"<b>{symbol}{grand:,.2f}</b>", style_right_bold)]
    ]
    totals_table = Table(totals_data, colWidths=[40*mm, 35*mm])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('LINEABOVE', (0,3), (-1,3), 1, BRAND_MID),
    ]))

    bottom_data = [
        [notes_p, totals_table]
    ]
    bottom_table = Table(bottom_data, colWidths=[105*mm, 75*mm])
    bottom_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 0),
    ]))
    
    story.append(KeepTogether([
        HRFlowable(width="100%", thickness=1, color=BRAND_BORDER, spaceBefore=4, spaceAfter=8),
        bottom_table
    ]))

    # Footer on every page
    def add_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(BRAND_MUTED)
        footer_text = (
            f"{company.get('website','')}  ·  Generated {datetime.now().strftime('%B %d, %Y')}"
        )
        canvas.drawCentredString(A4[0]/2.0, 10*mm, footer_text)
        canvas.restoreState()

    doc.build(story, onFirstPage=add_footer, onLaterPages=add_footer)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


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
    
    query = {}
    if status_filter:
        query["status"] = status_filter
    if search:
        regex = re.escape(search)
        query["$or"] = [
            {"number": {"$regex": regex, "$options": "i"}},
            {"client_name": {"$regex": regex, "$options": "i"}}
        ]
    
    docs = db.invoices.find(query).sort("created_at", -1)
    rows = [MongoRow(d) for d in docs]
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
    
    db.invoices.update_one(
        {"_id": inv_id},
        {
            "$set": {
                "number": inv.get("number",""),
                "client_name": data.get("client",{}).get("name",""),
                "status": inv.get("status","pending"),
                "total": grand,
                "currency": data.get("currency","USD"),
                "issue_date": inv.get("date",""),
                "due_date": inv.get("due_date",""),
                "payload": data,
                "updated_at": now
            },
            "$setOnInsert": {
                "created_at": now
            }
        },
        upsert=True
    )
    return jsonify({"id": inv_id, "saved": True})

@app.route("/api/invoices/<inv_id>", methods=["GET"])
def get_invoice(inv_id):
    db  = get_db()
    doc = db.invoices.find_one({"_id": inv_id})
    if not doc:
        return jsonify({"error": "Not found"}), 404
    payload = doc.get("payload") or {}
    payload["_id"] = inv_id
    return jsonify(payload)

@app.route("/api/invoices/<inv_id>", methods=["DELETE"])
def delete_invoice(inv_id):
    db = get_db()
    db.invoices.delete_one({"_id": inv_id})
    return jsonify({"deleted": True})

@app.route("/api/invoices/<inv_id>/pdf", methods=["GET"])
def download_saved_pdf(inv_id):
    db  = get_db()
    doc = db.invoices.find_one({"_id": inv_id})
    if not doc:
        return jsonify({"error": "Not found"}), 404
    data = doc.get("payload") or {}
    pdf  = build_pdf(data)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     as_attachment=True, download_name=f"invoice-{doc.get('number')}.pdf")

# ── Stats endpoint ─────────────────────────────────────────────────
@app.route("/api/stats", methods=["GET"])
def stats():
    db = get_db()
    pipeline = [
        {"$group": {
            "_id": "$status",
            "cnt": {"$sum": 1},
            "total": {"$sum": "$total"}
        }}
    ]
    aggregates = db.invoices.aggregate(pipeline)
    
    result = {"total_invoices": 0, "total_revenue": 0, "paid": 0, "pending": 0, "overdue": 0,
              "paid_revenue": 0, "pending_revenue": 0, "overdue_revenue": 0}
    for r in aggregates:
        s   = r["_id"]
        cnt = r["cnt"]
        rev = float(r["total"] or 0)
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
    
    regex = f"^{re.escape(prefix)}-"
    doc = db.invoices.find_one(
        {"number": {"$regex": regex}},
        sort=[("created_at", -1)]
    )
    if doc:
        try:
            last_n = int(doc["number"].rsplit("-", 1)[-1])
            return jsonify({"number": f"{prefix}-{last_n+1:03d}"})
        except:
            pass
    year   = datetime.now().year
    count  = db.invoices.count_documents({})
    return jsonify({"number": f"{prefix}-{year}-{count+1:03d}"})

# ── Client Address Book ────────────────────────────────────────────
@app.route("/api/clients", methods=["GET"])
def list_clients():
    db = get_db()
    q  = request.args.get("q","").strip()
    if q:
        regex = re.escape(q)
        docs = db.clients.find({"name": {"$regex": regex, "$options": "i"}}).sort("name", 1)
    else:
        docs = db.clients.find({}).sort("name", 1)
    rows = [MongoRow(d) for d in docs]
    return jsonify([dict(r) for r in rows])

@app.route("/api/clients", methods=["POST"])
def save_client():
    db   = get_db()
    data = request.get_json(force=True)
    cid  = data.get("id") or str(uuid.uuid4())
    now  = datetime.utcnow().isoformat()
    
    db.clients.update_one(
        {"_id": cid},
        {
            "$set": {
                "name": data.get("name",""),
                "email": data.get("email",""),
                "phone": data.get("phone",""),
                "address": data.get("address",""),
            },
            "$setOnInsert": {
                "created_at": now
            }
        },
        upsert=True
    )
    return jsonify({"id": cid, "saved": True})

@app.route("/api/clients/<cid>", methods=["DELETE"])
def delete_client(cid):
    db = get_db()
    db.clients.delete_one({"_id": cid})
    return jsonify({"deleted": True})

# ── Settings ───────────────────────────────────────────────────────
@app.route("/api/settings", methods=["GET"])
def get_settings():
    db   = get_db()
    docs = db.settings.find({})
    return jsonify({d["_id"]: d["value"] for d in docs})

@app.route("/api/settings", methods=["POST"])
def save_settings():
    db   = get_db()
    data = request.get_json(force=True)
    for k, v in data.items():
        db.settings.update_one({"_id": k}, {"$set": {"value": v}}, upsert=True)
    return jsonify({"saved": True})

# ── Duplicate Invoice ──────────────────────────────────────────────
@app.route("/api/invoices/<inv_id>/duplicate", methods=["POST"])
def duplicate_invoice(inv_id):
    db  = get_db()
    doc = db.invoices.find_one({"_id": inv_id})
    if not doc:
        return jsonify({"error": "Not found"}), 404
    data       = doc.get("payload") or {}
    new_id     = str(uuid.uuid4())
    now        = datetime.utcnow().isoformat()
    inv        = data.get("invoice", {})
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
    
    db.invoices.insert_one({
        "_id": new_id,
        "number": new_num,
        "client_name": data.get("client",{}).get("name",""),
        "status": "pending",
        "total": grand,
        "currency": data.get("currency","USD"),
        "issue_date": data["invoice"].get("date",""),
        "due_date": data["invoice"].get("due_date",""),
        "payload": data,
        "created_at": now,
        "updated_at": now
    })
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
        doc = db.invoices.find_one({"_id": inv_id})
        if doc:
            payload = doc.get("payload") or {}
            payload["invoice"]["status"] = status
            db.invoices.update_one(
                {"_id": inv_id},
                {"$set": {
                    "status": status,
                    "payload": payload,
                    "updated_at": now
                }}
            )
    return jsonify({"updated": len(ids)})

# ── Bulk Delete ────────────────────────────────────────────────────
@app.route("/api/invoices/bulk-delete", methods=["POST"])
def bulk_delete():
    db   = get_db()
    data = request.get_json(force=True)
    ids  = data.get("ids", [])
    db.invoices.delete_many({"_id": {"$in": ids}})
    return jsonify({"deleted": len(ids)})

# ── Auto-mark Overdue ──────────────────────────────────────────────
@app.route("/api/invoices/mark-overdue", methods=["POST"])
def mark_overdue():
    db  = get_db()
    today = date.today().isoformat()
    docs  = db.invoices.find({"status": "pending", "due_date": {"$lt": today, "$ne": ""}})
    now   = datetime.utcnow().isoformat()
    count = 0
    for doc in docs:
        payload = doc.get("payload") or {}
        payload["invoice"]["status"] = "overdue"
        db.invoices.update_one(
            {"_id": doc["_id"]},
            {"$set": {
                "status": "overdue",
                "payload": payload,
                "updated_at": now
            }}
        )
        count += 1
    return jsonify({"marked_overdue": count})

# ── CSV Export ─────────────────────────────────────────────────────
@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    import csv as csv_mod
    db   = get_db()
    docs = db.invoices.find({}).sort("created_at", -1)
    buf = io.StringIO()
    w   = csv_mod.writer(buf)
    w.writerow(["Invoice #","Client","Status","Total","Currency","Issue Date","Due Date","Created At"])
    for doc in docs:
        w.writerow([doc.get("number",""), doc.get("client_name",""), doc.get("status",""),
                    f"{float(doc.get('total') or 0):.2f}", doc.get("currency",""),
                    doc.get("issue_date",""), doc.get("due_date",""), doc.get("created_at","")])
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
    docs = db.invoices.find({}).sort("created_at", -1)
    data = [doc.get("payload") or {} for doc in docs]
    buf  = io.BytesIO(json.dumps(data, indent=2).encode())
    return send_file(buf, mimetype="application/json", as_attachment=True,
                     download_name=f"invoices-export-{date.today().isoformat()}.json")

# ── Revenue Chart Data ─────────────────────────────────────────────
@app.route("/api/chart/revenue", methods=["GET"])
def chart_revenue():
    db     = get_db()
    months = request.args.get("months", 6, type=int)
    pipeline = [
        {"$match": {"issue_date": {"$ne": "", "$exists": True, "$ne": None}}},
        {"$project": {
            "month": {"$substr": ["$issue_date", 0, 7]},
            "total": 1,
            "status": 1
        }},
        {"$group": {
            "_id": "$month",
            "revenue": {"$sum": "$total"},
            "count": {"$sum": 1},
            "paid_revenue": {"$sum": {"$cond": [{"$eq": ["$status", "paid"]}, "$total", 0]}}
        }},
        {"$sort": {"_id": -1}},
        {"$limit": months}
    ]
    results = db.invoices.aggregate(pipeline)
    rows = []
    for r in results:
        rows.append({
            "month": r["_id"],
            "revenue": float(r["revenue"] or 0),
            "count": r["count"],
            "paid_revenue": float(r["paid_revenue"] or 0)
        })
    return jsonify(list(reversed(rows)))

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
    count = db.invoices.count_documents({})
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
    # app.run(debug=True, port=5001, host="0.0.0.0")
    import os as _os2
    _port  = int(_os2.environ.get("PORT", 8080))
    _debug = _os2.environ.get("FLASK_ENV", "production") == "development"
    print(f"\n  InvoiceForge running at \033[1;34mhttp://localhost:{_port}\033[0m\n")
    app.run(debug=_debug, port=_port, host="0.0.0.0")

