/**
 * InvoiceForge — Client Utilities
 * Standalone helpers loaded by index.html
 */

"use strict";

/* ── Number formatting ─────────────────────────────────────── */
window.IF = window.IF || {};

IF.fmt = (n, decimals = 2) =>
  Number(n || 0).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });

IF.fmtCurrency = (n, symbol = "$") => symbol + IF.fmt(n);

IF.fmtDate = (iso) => {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun",
                   "Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${months[parseInt(m, 10) - 1]} ${parseInt(d, 10)}, ${y}`;
};

/* ── DOM helpers ───────────────────────────────────────────── */
IF.gid  = (id) => document.getElementById(id);
IF.qs   = (sel, root = document) => root.querySelector(sel);
IF.qsa  = (sel, root = document) => [...root.querySelectorAll(sel)];

IF.sv   = (id, val) => { const el = IF.gid(id); if (el) el.value = val; };
IF.html = (id, val) => { const el = IF.gid(id); if (el) el.innerHTML = val; };
IF.text = (id, val) => { const el = IF.gid(id); if (el) el.textContent = val; };

IF.esc  = (s) => (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;")
                           .replace(/>/g,"&gt;").replace(/"/g,"&quot;");

/* ── Toast notifications ───────────────────────────────────── */
IF.toast = (() => {
  let timer = null;
  return (msg, type = "ok") => {
    const el   = IF.gid("toast");
    const icon = IF.gid("t-icon");
    const txt  = IF.gid("t-msg");
    if (!el) return;
    icon.textContent = type === "ok" ? "✓" : type === "err" ? "✕" : "ℹ";
    txt.textContent  = msg;
    el.className     = `toast ${type} show`;
    clearTimeout(timer);
    timer = setTimeout(() => el.classList.remove("show"), 3200);
  };
})();

/* ── Debounce ──────────────────────────────────────────────── */
IF.debounce = (fn, ms) => {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
};

/* ── API helpers ───────────────────────────────────────────── */
IF.api = {
  get:    (url)         => fetch(url).then(r => r.json()),
  post:   (url, body)   => fetch(url, { method:"POST",  headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) }).then(r => r.json()),
  del:    (url)         => fetch(url, { method:"DELETE" }).then(r => r.json()),
  blob:   (url, body)   => fetch(url, { method:"POST",  headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) }).then(r => r.blob()),
};

/* ── Download blob ─────────────────────────────────────────── */
IF.downloadBlob = (blob, filename) => {
  const a  = document.createElement("a");
  a.href   = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
};

/* ── Local storage with fallback ───────────────────────────── */
IF.store = {
  set: (k, v) => { try { localStorage.setItem("if_" + k, JSON.stringify(v)); } catch (_) {} },
  get: (k, d) => { try { const v = localStorage.getItem("if_" + k); return v !== null ? JSON.parse(v) : d; } catch (_) { return d; } },
  del: (k)    => { try { localStorage.removeItem("if_" + k); } catch (_) {} },
};

/* ── Validate email ────────────────────────────────────────── */
IF.validEmail = (e) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e);

/* ── Currency symbol map ───────────────────────────────────── */
IF.SYMS = {
  USD:"$", EUR:"€", GBP:"£", INR:"₹", JPY:"¥",
  CAD:"$", AUD:"$", SGD:"$", AED:"د.إ", CHF:"CHF",
};

/* ── Date helpers ──────────────────────────────────────────── */
IF.today     = () => new Date().toISOString().split("T")[0];
IF.daysFrom  = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().split("T")[0]; };
IF.isPast    = (iso) => iso && iso < IF.today();

/* ── Confirm wrapper (returns promise) ─────────────────────── */
IF.confirm = (msg) => Promise.resolve(window.confirm(msg));

console.info("[InvoiceForge] utils.js loaded");
