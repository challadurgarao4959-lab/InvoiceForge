"""
InvoiceForge — Full Test Suite
56 tests across 6 classes.
Run:  python tests.py
"""
import json, sys, re, unittest
from app import app, build_pdf, calc_totals, _strip_rl_tags, sanitize_invoice

# ── Shared sample payload ────────────────────────────────────────
SAMPLE = {
    "currency": "USD", "discount": 10,
    "notes": "Thank you!", "payment_terms": "Net 30",
    "bank_details": "Chase Bank Routing: 021000089",
    "invoice": {
        "number": "INV-TEST-001", "date": "2026-01-01",
        "due_date": "2026-02-01",  "status": "pending",
    },
    "company": {
        "name": "Pixel Studio", "tagline": "Design · Dev",
        "email": "hi@ps.io",    "phone": "+1 415 555 0100",
        "website": "ps.io",     "address": "88 Market St, SF CA",
    },
    "client": {
        "name": "Wayne Enterprises", "email": "b@wayne.com",
        "phone": "+1 212 555 8432",  "address": "Gotham NJ",
    },
    "items": [
        {"description": "Brand Design",  "notes": "3 concepts",
         "quantity": 1, "unit_price": 3500, "tax_rate": 8},
        {"description": "Web Dev",       "notes": "10 pages",
         "quantity": 1, "unit_price": 8200, "tax_rate": 8},
        {"description": "Retainer",      "notes": "Monthly",
         "quantity": 3, "unit_price": 950,  "tax_rate": 0},
    ],
}


# ════════════════════════════════════════════════════════════════
class TestCalcTotals(unittest.TestCase):
    """Unit tests for the calc_totals() helper."""

    def test_basic_math(self):
        # sub = 3500+8200+2850 = 14550
        # tax = (3500+8200)*0.08 = 936
        # disc = 14550*0.10 = 1455
        # grand = 14550+936-1455 = 14031
        sub, tax, disc, grand = calc_totals(SAMPLE)
        self.assertAlmostEqual(sub,   14550.0)
        self.assertAlmostEqual(tax,     936.0)
        self.assertAlmostEqual(disc,   1455.0)
        self.assertAlmostEqual(grand, 14031.0)

    def test_zero_items(self):
        s, t, d, g = calc_totals({"items": [], "discount": 0})
        self.assertEqual(s, 0)
        self.assertEqual(g, 0)

    def test_discount_only(self):
        data = {
            "items": [{"quantity": 1, "unit_price": 100, "tax_rate": 0}],
            "discount": 25,
        }
        s, t, d, g = calc_totals(data)
        self.assertEqual(s, 100)
        self.assertEqual(d, 25)
        self.assertEqual(g, 75)

    def test_zero_discount(self):
        data = {
            "items": [{"quantity": 2, "unit_price": 500, "tax_rate": 10}],
            "discount": 0,
        }
        s, t, d, g = calc_totals(data)
        self.assertAlmostEqual(s, 1000.0)
        self.assertAlmostEqual(t, 100.0)
        self.assertAlmostEqual(d, 0.0)
        self.assertAlmostEqual(g, 1100.0)

    def test_fractional_quantity(self):
        data = {
            "items": [{"quantity": 1.5, "unit_price": 200, "tax_rate": 0}],
            "discount": 0,
        }
        s, _, _, g = calc_totals(data)
        self.assertAlmostEqual(s, 300.0)
        self.assertAlmostEqual(g, 300.0)

    def test_multiple_tax_rates(self):
        data = {
            "items": [
                {"quantity": 1, "unit_price": 1000, "tax_rate": 5},
                {"quantity": 1, "unit_price": 1000, "tax_rate": 20},
            ],
            "discount": 0,
        }
        s, t, d, g = calc_totals(data)
        self.assertAlmostEqual(s, 2000.0)
        self.assertAlmostEqual(t,  250.0)   # 50 + 200
        self.assertAlmostEqual(g, 2250.0)


# ════════════════════════════════════════════════════════════════
class TestPDFGeneration(unittest.TestCase):
    """Tests for build_pdf() correctness and robustness."""

    def test_pdf_magic_bytes(self):
        pdf = build_pdf(SAMPLE)
        self.assertTrue(pdf.startswith(b"%PDF"), "Must begin with %PDF")

    def test_pdf_minimum_size(self):
        pdf = build_pdf(SAMPLE)
        self.assertGreater(len(pdf), 1000)

    def test_all_statuses(self):
        for status in ("paid", "pending", "overdue"):
            d = json.loads(json.dumps(SAMPLE))
            d["invoice"]["status"] = status
            pdf = build_pdf(d)
            self.assertGreater(len(pdf), 500, f"Failed for status={status}")

    def test_all_currencies(self):
        for cur in ("USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "SGD"):
            d = json.loads(json.dumps(SAMPLE))
            d["currency"] = cur
            pdf = build_pdf(d)
            self.assertGreater(len(pdf), 500, f"Failed for currency={cur}")

    def test_empty_company_and_client(self):
        minimal = {
            "currency": "USD", "discount": 0,
            "notes": "", "payment_terms": "", "bank_details": "",
            "invoice": {
                "number": "INV-MIN", "date": "", "due_date": "", "status": "pending",
            },
            "company": {}, "client": {}, "items": [],
        }
        pdf = build_pdf(minimal)
        self.assertGreater(len(pdf), 500)

    def test_many_items(self):
        d = json.loads(json.dumps(SAMPLE))
        d["items"] = [
            {"description": f"Item {i}", "notes": f"Detail {i}",
             "quantity": i, "unit_price": 100 * i, "tax_rate": 5}
            for i in range(1, 21)
        ]
        pdf = build_pdf(d)
        self.assertGreater(len(pdf), 2000)

    def test_notes_and_bank_details(self):
        d = json.loads(json.dumps(SAMPLE))
        d["notes"] = "Important payment instructions here."
        d["bank_details"] = "IBAN: GB29 NWBK 6016 1331 9268 19"
        pdf = build_pdf(d)
        self.assertGreater(len(pdf), 1000)

    def test_special_characters_in_description(self):
        d = json.loads(json.dumps(SAMPLE))
        d["items"] = [{
            "description": "Design & Development — Phase 1 (Q1/2026)",
            "notes": "Cost: $1,500 + tax @ 8%",
            "quantity": 1, "unit_price": 1500, "tax_rate": 8,
        }]
        pdf = build_pdf(d)
        self.assertGreater(len(pdf), 500)


# ════════════════════════════════════════════════════════════════
class TestSanitization(unittest.TestCase):
    """Input sanitization: blocks malicious markup from reaching ReportLab."""

    def test_strips_script_tags(self):
        result = _strip_rl_tags("<script>alert(1)</script>Clean text")
        self.assertNotIn("<script>",  result)
        self.assertNotIn("</script>", result)
        self.assertIn("Clean text",   result)

    def test_strips_img_tags(self):
        result = _strip_rl_tags('<img src=x onerror=alert(1)>hello')
        self.assertNotIn("<img", result)
        self.assertEqual(result, "hello")

    def test_strips_font_tags(self):
        result = _strip_rl_tags('<font color="red">text</font>')
        self.assertNotIn("<font", result)
        self.assertEqual(result, "text")

    def test_plain_text_unchanged(self):
        cases = ["Normal text", "Hello & World", "$1,000.00",
                 "50% off", "Design — Phase 1"]
        for s in cases:
            self.assertEqual(_strip_rl_tags(s), s)

    def test_empty_and_none(self):
        self.assertEqual(_strip_rl_tags(""),   "")
        self.assertEqual(_strip_rl_tags(None), "")

    def test_no_tags_remain(self):
        payloads = [
            "<b>bold</b>",
            "<div onclick='evil()'>text</div>",
            "<SCRIPT>XSS</SCRIPT>",
            "<<script>>nested<<</script>>",
        ]
        for p in payloads:
            result = _strip_rl_tags(p)
            self.assertFalse(
                re.search(r'<[^>]+>', result),
                f"Tag survived sanitization: {repr(result)} (input: {repr(p)})"
            )

    def test_sanitize_invoice_deep(self):
        data = {
            "company": {"name": "<script>xss</script>Pixel Studio"},
            "client":  {"name": "Wayne <b>Ent</b>"},
            "items":   [{"description": "<img src=x>Service",
                         "notes": "Fine", "quantity": 1,
                         "unit_price": 100, "tax_rate": 0}],
            "notes": "<style>body{display:none}</style>Thanks!",
        }
        clean = sanitize_invoice(data)
        self.assertNotIn("<script>", clean["company"]["name"])
        self.assertNotIn("<img",     clean["items"][0]["description"])
        self.assertNotIn("<style>",  clean["notes"])
        self.assertIn("Pixel Studio", clean["company"]["name"])
        self.assertIn("Service",      clean["items"][0]["description"])

    def test_sanitized_payload_builds_pdf(self):
        dirty = json.loads(json.dumps(SAMPLE))
        dirty["notes"] = "<script>evil()</script>Thank you!"
        dirty["items"][0]["description"] = "<b>Design</b><script>xss</script>"
        clean = sanitize_invoice(dirty)
        pdf   = build_pdf(clean)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 500)

    def test_generate_endpoint_sanitizes(self):
        c = app.test_client()
        payload = json.loads(json.dumps(SAMPLE))
        payload["notes"] = "<script>bad</script>Clean"
        r = c.post("/api/generate", json=payload)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "application/pdf")


# ════════════════════════════════════════════════════════════════
class TestErrorHandlers(unittest.TestCase):
    """HTTP error pages and security headers."""

    def setUp(self):
        self.c = app.test_client()

    def test_404_api_returns_json(self):
        r = self.c.get("/api/this-does-not-exist")
        self.assertEqual(r.status_code, 404)
        j = r.get_json()
        self.assertIn("error", j)
        self.assertIn("path",  j)

    def test_404_html_page_served(self):
        r = self.c.get("/this-page-does-not-exist")
        self.assertEqual(r.status_code, 404)
        self.assertIn(b"404",       r.data)
        self.assertIn(b"Not Found", r.data)
        self.assertIn(b'href="/"',  r.data)

    def test_405_returns_json(self):
        r = self.c.get("/api/generate")   # GET on POST-only route
        self.assertEqual(r.status_code, 405)
        self.assertIn("error", r.get_json())

    def test_400_on_empty_body(self):
        r = self.c.post("/api/generate",
                        data="", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_400_on_null_body(self):
        r = self.c.post("/api/preview",
                        data="null", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_favicon_returns_svg(self):
        r = self.c.get("/favicon.ico")
        self.assertEqual(r.status_code, 200)
        self.assertIn("svg", r.content_type)
        self.assertIn(b"<svg", r.data)

    def test_favicon_cache_header(self):
        r = self.c.get("/favicon.ico")
        self.assertIn("Cache-Control", r.headers)

    def test_security_headers_on_api(self):
        r = self.c.get("/api/health")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("X-Frame-Options"),        "SAMEORIGIN")
        self.assertIn("X-XSS-Protection", r.headers)
        self.assertIn("Referrer-Policy",   r.headers)

    def test_security_headers_on_html(self):
        r = self.c.get("/")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")

    def test_500_html_page_content(self):
        with app.test_client() as c:
            # Trigger a real render of the 500 template (read it directly)
            from flask import render_template
            with app.app_context():
                html = render_template("500.html")
        self.assertIn("500", html)
        self.assertIn("Server Error", html)


# ════════════════════════════════════════════════════════════════
class TestEnvConfig(unittest.TestCase):
    """Environment variable based configuration."""

    def test_max_items_is_int(self):
        self.assertIsInstance(app.config.get("MAX_ITEMS"), int)
        self.assertGreater(app.config["MAX_ITEMS"], 0)

    def test_max_invoices_is_int(self):
        self.assertIsInstance(app.config.get("MAX_INVOICES"), int)
        self.assertGreater(app.config["MAX_INVOICES"], 0)

    def test_secret_key_is_set(self):
        key = app.config.get("SECRET_KEY", "")
        self.assertIsInstance(key, str)
        self.assertGreater(len(key), 0)

    def test_health_reports_version(self):
        r = app.test_client().get("/api/health")
        d = r.get_json()
        self.assertIn("version", d)
        self.assertIn("status",  d)
        self.assertEqual(d["status"], "ok")


# ════════════════════════════════════════════════════════════════
class TestAPI(unittest.TestCase):
    """Full integration tests for every API endpoint."""

    def setUp(self):
        self.c = app.test_client()

    # ── helpers ────────────────────────────────────────────────
    def _save(self, overrides=None):
        d = json.loads(json.dumps(SAMPLE))
        if overrides:
            d["invoice"].update(overrides)
        r = self.c.post("/api/invoices", json=d)
        self.assertEqual(r.status_code, 200)
        return r.get_json()["id"]

    # ── health ─────────────────────────────────────────────────
    def test_health(self):
        r = self.c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")

    # ── PDF endpoints ───────────────────────────────────────────
    def test_generate_pdf_content_type(self):
        r = self.c.post("/api/generate", json=SAMPLE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "application/pdf")
        self.assertGreater(len(r.data), 1000)

    def test_preview_pdf_inline(self):
        r = self.c.post("/api/preview", json=SAMPLE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "application/pdf")

    def test_generate_rejects_empty(self):
        r = self.c.post("/api/generate",
                        data="", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    # ── invoice CRUD ────────────────────────────────────────────
    def test_save_and_retrieve(self):
        iid = self._save()
        r   = self.c.get(f"/api/invoices/{iid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["invoice"]["number"], "INV-TEST-001")

    def test_update_existing(self):
        iid = self._save()
        d   = json.loads(json.dumps(SAMPLE))
        d["_id"]   = iid
        d["notes"] = "Updated note"
        self.c.post("/api/invoices", json=d)
        r = self.c.get(f"/api/invoices/{iid}")
        self.assertEqual(r.get_json()["notes"], "Updated note")

    def test_delete(self):
        iid = self._save()
        r   = self.c.delete(f"/api/invoices/{iid}")
        self.assertTrue(r.get_json()["deleted"])
        self.assertEqual(self.c.get(f"/api/invoices/{iid}").status_code, 404)

    def test_not_found(self):
        r = self.c.get("/api/invoices/no-such-id")
        self.assertEqual(r.status_code, 404)

    def test_filter_by_status(self):
        iid = self._save({"status": "paid", "number": "INV-PAID-001"})
        ids = [i["id"] for i in
               self.c.get("/api/invoices?status=paid").get_json()]
        self.assertIn(iid, ids)

    def test_search_by_number(self):
        iid = self._save({"number": "INV-SRCH-999"})
        results = self.c.get("/api/invoices?q=SRCH-999").get_json()
        self.assertTrue(any(i["id"] == iid for i in results))

    def test_search_by_client(self):
        iid = self._save({"number": "INV-CLI-001"})
        results = self.c.get("/api/invoices?q=Wayne").get_json()
        self.assertTrue(any(i["id"] == iid for i in results))

    # ── saved PDF download ──────────────────────────────────────
    def test_saved_invoice_pdf(self):
        iid = self._save()
        r   = self.c.get(f"/api/invoices/{iid}/pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "application/pdf")

    # ── duplicate ────────────────────────────────────────────────
    def test_duplicate_creates_new(self):
        iid  = self._save({"number": "INV-DUP-001"})
        r    = self.c.post(f"/api/invoices/{iid}/duplicate")
        self.assertEqual(r.status_code, 200)
        nid  = r.get_json()["id"]
        self.assertNotEqual(nid, iid)

    def test_duplicate_increments_number(self):
        iid  = self._save({"number": "INV-DUP-010"})
        r    = self.c.post(f"/api/invoices/{iid}/duplicate")
        nnum = r.get_json()["number"]
        self.assertNotEqual(nnum, "INV-DUP-010")
        self.assertIn("INV-DUP", nnum)

    def test_duplicate_status_is_pending(self):
        iid = self._save({"number": "INV-DUP-020", "status": "paid"})
        r   = self.c.post(f"/api/invoices/{iid}/duplicate")
        nid = r.get_json()["id"]
        inv = self.c.get(f"/api/invoices/{nid}").get_json()
        self.assertEqual(inv["invoice"]["status"], "pending")

    # ── bulk operations ─────────────────────────────────────────
    def test_bulk_status_update(self):
        ids = [self._save({"number": f"INV-BLK-{i}"}) for i in range(3)]
        r   = self.c.post("/api/invoices/bulk-status",
                          json={"ids": ids, "status": "paid"})
        self.assertEqual(r.get_json()["updated"], 3)
        for iid in ids:
            inv = self.c.get(f"/api/invoices/{iid}").get_json()
            self.assertEqual(inv["invoice"]["status"], "paid")

    def test_bulk_status_invalid(self):
        r = self.c.post("/api/invoices/bulk-status",
                        json={"ids": [], "status": "invalid_status"})
        self.assertEqual(r.status_code, 400)

    def test_bulk_delete(self):
        ids = [self._save({"number": f"INV-BDEL-{i}"}) for i in range(2)]
        r   = self.c.post("/api/invoices/bulk-delete", json={"ids": ids})
        self.assertEqual(r.get_json()["deleted"], 2)
        for iid in ids:
            self.assertEqual(self.c.get(f"/api/invoices/{iid}").status_code, 404)

    def test_bulk_delete_empty_list(self):
        r = self.c.post("/api/invoices/bulk-delete", json={"ids": []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["deleted"], 0)

    # ── overdue detection ────────────────────────────────────────
    def test_mark_overdue(self):
        iid = self._save({"number": "INV-OVD-001",
                          "status": "pending", "due_date": "2020-01-01"})
        r   = self.c.post("/api/invoices/mark-overdue")
        self.assertGreaterEqual(r.get_json()["marked_overdue"], 1)
        inv = self.c.get(f"/api/invoices/{iid}").get_json()
        self.assertEqual(inv["invoice"]["status"], "overdue")

    def test_mark_overdue_skips_paid(self):
        iid = self._save({"number": "INV-OVD-002",
                          "status": "paid", "due_date": "2020-01-01"})
        self.c.post("/api/invoices/mark-overdue")
        inv = self.c.get(f"/api/invoices/{iid}").get_json()
        self.assertEqual(inv["invoice"]["status"], "paid")   # unchanged

    # ── stats + chart ────────────────────────────────────────────
    def test_stats_keys(self):
        r = self.c.get("/api/stats")
        d = r.get_json()
        for key in ("total_invoices", "total_revenue",
                    "paid", "pending", "overdue",
                    "paid_revenue", "pending_revenue", "overdue_revenue"):
            self.assertIn(key, d, f"Missing key: {key}")

    def test_stats_counts_match(self):
        n_before = self.c.get("/api/stats").get_json()["total_invoices"]
        self._save({"number": "INV-STAT-001"})
        n_after  = self.c.get("/api/stats").get_json()["total_invoices"]
        self.assertEqual(n_after, n_before + 1)

    def test_chart_revenue_list(self):
        self._save()
        r = self.c.get("/api/chart/revenue?months=6")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), list)

    def test_chart_revenue_fields(self):
        self._save()
        rows = self.c.get("/api/chart/revenue").get_json()
        if rows:
            row = rows[0]
            for field in ("month", "revenue", "count", "paid_revenue"):
                self.assertIn(field, row)

    # ── invoice numbering ────────────────────────────────────────
    def test_next_number_default(self):
        r = self.c.get("/api/next-number")
        self.assertEqual(r.status_code, 200)
        self.assertIn("number", r.get_json())

    def test_next_number_custom_prefix(self):
        r = self.c.get("/api/next-number?prefix=REC")
        self.assertIn("REC", r.get_json()["number"])

    def test_next_number_increments(self):
        self._save({"number": "SEQ-001"})
        r = self.c.get("/api/next-number?prefix=SEQ")
        self.assertEqual(r.get_json()["number"], "SEQ-002")

    # ── export ───────────────────────────────────────────────────
    def test_export_csv_headers(self):
        self._save()
        r = self.c.get("/api/export/csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("csv", r.content_type)
        self.assertIn(b"Invoice #", r.data)

    def test_export_csv_has_data_row(self):
        self._save({"number": "INV-CSV-001"})
        r    = self.c.get("/api/export/csv")
        rows = r.data.decode().strip().split("\n")
        self.assertGreater(len(rows), 1)    # header + at least one data row

    def test_export_json_is_list(self):
        self._save()
        r = self.c.get("/api/export/json")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), list)

    def test_export_json_contains_invoice(self):
        self._save({"number": "INV-JSON-001"})
        rows = self.c.get("/api/export/json").get_json()
        nums = [row.get("invoice", {}).get("number") for row in rows]
        self.assertIn("INV-JSON-001", nums)

    # ── templates ────────────────────────────────────────────────
    def test_list_templates_count(self):
        r = self.c.get("/api/templates")
        self.assertGreaterEqual(len(r.get_json()), 5)

    def test_list_templates_fields(self):
        for t in self.c.get("/api/templates").get_json():
            self.assertIn("id",   t)
            self.assertIn("name", t)

    def test_each_template_fetchable(self):
        for t in self.c.get("/api/templates").get_json():
            r = self.c.get(f"/api/templates/{t['id']}")
            self.assertEqual(r.status_code, 200, f"Template {t['id']} failed")
            d = r.get_json()
            self.assertIn("items",         d)
            self.assertIn("payment_terms", d)
            self.assertGreater(len(d["items"]), 0)

    def test_template_not_found(self):
        r = self.c.get("/api/templates/no-such-template")
        self.assertEqual(r.status_code, 404)

    # ── clients ──────────────────────────────────────────────────
    def test_client_create_and_list(self):
        r   = self.c.post("/api/clients",
                          json={"name": "Stark Industries",
                                "email": "tony@stark.com",
                                "phone": "+1 800", "address": "Malibu CA"})
        self.assertEqual(r.status_code, 200)
        cid = r.get_json()["id"]
        ids = [c["id"] for c in self.c.get("/api/clients").get_json()]
        self.assertIn(cid, ids)

    def test_client_search(self):
        self.c.post("/api/clients",
                    json={"name": "SearchableClient XYZ", "email": "s@c.com"})
        results = self.c.get("/api/clients?q=SearchableClient").get_json()
        self.assertTrue(any("SearchableClient" in c["name"] for c in results))

    def test_client_update(self):
        r   = self.c.post("/api/clients",
                          json={"name": "Old Name", "email": "old@co.com"})
        cid = r.get_json()["id"]
        self.c.post("/api/clients",
                    json={"id": cid, "name": "New Name", "email": "new@co.com"})
        clients = self.c.get("/api/clients").get_json()
        names   = [c["name"] for c in clients]
        self.assertIn("New Name", names)
        self.assertNotIn("Old Name", names)

    def test_client_delete(self):
        r   = self.c.post("/api/clients",
                          json={"name": "ToDelete Corp", "email": "del@co.com"})
        cid = r.get_json()["id"]
        self.c.delete(f"/api/clients/{cid}")
        ids = [c["id"] for c in self.c.get("/api/clients").get_json()]
        self.assertNotIn(cid, ids)

    # ── settings ─────────────────────────────────────────────────
    def test_settings_round_trip(self):
        payload = {
            "company": {"name": "RoundTrip Co", "email": "rt@co.com"},
            "num_prefix": "RT",
        }
        self.c.post("/api/settings", json=payload)
        s = self.c.get("/api/settings").get_json()
        self.assertEqual(s["company"]["name"], "RoundTrip Co")
        self.assertEqual(s["num_prefix"],      "RT")

    def test_settings_partial_update(self):
        self.c.post("/api/settings", json={"num_prefix": "INV"})
        self.c.post("/api/settings", json={"company": {"name": "New Co"}})
        s = self.c.get("/api/settings").get_json()
        self.assertEqual(s["company"]["name"], "New Co")
        self.assertEqual(s["num_prefix"],      "INV")

    # ── index page ───────────────────────────────────────────────
    def test_index_returns_html(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"InvoiceForge", r.data)
        self.assertIn(b"<html",        r.data)

    def test_index_has_static_assets(self):
        r    = self.c.get("/")
        html = r.data.decode()
        self.assertIn("print.css", html)
        self.assertIn("utils.js",  html)
        self.assertIn("favicon",   html)

    def test_index_no_jinja_tags_in_output(self):
        r    = self.c.get("/")
        html = r.data.decode()
        self.assertNotIn("{%-",    html)
        self.assertNotIn("{%",     html)
        # Double braces that escaped would appear literally
        import re as _re
        # Confirm no raw/endraw leaked
        self.assertNotIn("endraw", html)


# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("InvoiceForge — Test Suite")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromNames([
        "tests.TestCalcTotals",
        "tests.TestPDFGeneration",
        "tests.TestSanitization",
        "tests.TestErrorHandlers",
        "tests.TestEnvConfig",
        "tests.TestAPI",
    ])
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    print("\n" + "=" * 60)
    print(f"Tests run : {result.testsRun}")
    print(f"Failures  : {len(result.failures)}")
    print(f"Errors    : {len(result.errors)}")
    print(f"Skipped   : {len(result.skipped)}")
    print("=" * 60)
    print("PASSED ✓" if result.wasSuccessful() else "FAILED ✗")
    sys.exit(0 if result.wasSuccessful() else 1)
