#!/usr/bin/env python3
"""
StatsPack Tech Sales Agent Portal
=================================
A two-role portal: StatsPack admins manage agents, clients, payments and
commission; agents log their own pipeline and see exactly what they have
earned and what is still owed to them.

Runs on the Python standard library alone (psycopg2 only when you point it at
Postgres). Start with:  python3 server.py
"""
import json
import mimetypes
import os
import re
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import db
import core
import mirror

VERSION = "v1.5"
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
PORT = int(os.environ.get("PORT", "8470"))

ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn

    return deco


class Ctx:
    """Everything a handler needs about the current request."""

    def __init__(self, conn, user, body, query, ip):
        self.conn = conn
        self.user = user
        self.body = body or {}
        self.query = query or {}
        self.ip = ip

    def q(self, key, default=""):
        v = self.query.get(key)
        return v[0] if isinstance(v, list) and v else (v if v is not None else default)

    def s(self, key, default="", maxlen=500):
        v = self.body.get(key, default)
        if v is None:
            v = default
        return str(v).strip()[:maxlen]

    def f(self, key, default=0.0):
        try:
            return float(self.body.get(key, default) or 0)
        except (TypeError, ValueError):
            return float(default)

    def i(self, key, default=0):
        try:
            return int(float(self.body.get(key, default) or 0))
        except (TypeError, ValueError):
            return int(default)


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__(message)


def need_admin(ctx):
    if not ctx.user:
        raise ApiError(401, "Please sign in.")
    if ctx.user["role"] != "admin":
        raise ApiError(403, "Administrator access only.")


def need_auth(ctx):
    if not ctx.user:
        raise ApiError(401, "Please sign in.")


def scope(ctx):
    """The company whose data this user may see. Super users see everything."""
    return None if ctx.user.get("is_super") else (ctx.user.get("company_id") or 1)


def same_company(ctx, row):
    s = scope(ctx)
    return s is None or (row or {}).get("company_id") == s


def need_super(ctx):
    """Permanent deletion is reserved for super users."""
    need_admin(ctx)
    if not ctx.user.get("is_super"):
        raise ApiError(403, "Only a super user can do this.")


# --------------------------------------------------------------------------
# login throttling (per IP + per email, in memory)
# --------------------------------------------------------------------------
_attempts = defaultdict(deque)
_attempt_lock = threading.Lock()
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW = 600  # seconds


def throttled(key):
    with _attempt_lock:
        q = _attempts[key]
        cutoff = time.time() - ATTEMPT_WINDOW
        while q and q[0] < cutoff:
            q.popleft()
        return len(q) >= MAX_ATTEMPTS


def record_attempt(key):
    with _attempt_lock:
        _attempts[key].append(time.time())


def clear_attempts(key):
    with _attempt_lock:
        _attempts.pop(key, None)


# ==========================================================================
# AUTH
# ==========================================================================
@route("POST", "/api/login")
def login(ctx):
    email = ctx.s("email").lower()
    password = ctx.body.get("password") or ""
    ip_key = f"ip:{ctx.ip}"
    email_key = f"em:{email}"

    if throttled(ip_key) or throttled(email_key):
        raise ApiError(429, "Too many sign-in attempts. Wait ten minutes and try again.")

    user = ctx.conn.one("SELECT * FROM users WHERE email = ?", (email,))
    if not user or not core.verify_password(password, user["password_hash"]):
        record_attempt(ip_key)
        record_attempt(email_key)
        core.audit_standalone(None, "login.failed", email, ctx.ip)
        raise ApiError(401, "Email or password is incorrect.")

    if user["status"] != "Active":
        raise ApiError(403, "This account is suspended. Contact your administrator.")

    company = ctx.conn.one("SELECT name, status FROM companies WHERE id = ?",
                           (user.get("company_id") or 1,))
    if company and company.get("status") != "Active":
        core.audit_standalone(None, "login.blocked",
                              f"{email} — company {company['name']} is suspended", ctx.ip)
        raise ApiError(403, "Your organisation's access is suspended. "
                            "Contact your StatsPack administrator.")

    clear_attempts(ip_key)
    clear_attempts(email_key)
    core.purge_sessions(ctx.conn)
    token = core.create_session(ctx.conn, user["id"], ctx.ip)
    core.audit(ctx.conn, user, "login.success", "", ctx.ip)
    return {"token": token, "user": public_user(user)}


@route("POST", "/api/logout")
def logout(ctx):
    need_auth(ctx)
    return {"ok": True}  # token removal happens in the handler wrapper


@route("GET", "/api/me")
def me(ctx):
    need_auth(ctx)
    settings = core.get_settings(ctx.conn)
    company = ctx.conn.one("SELECT * FROM companies WHERE id = ?",
                           (ctx.user.get("company_id") or 1,))
    rules = core.effective_rules(ctx.conn, ctx.user["id"], settings) \
        if ctx.user["role"] == "agent" else None
    return {"user": public_user(ctx.user), "settings": settings, "version": VERSION,
            "stages": core.STAGES, "agent_types": core.AGENT_TYPES,
            "industries": core.INDUSTRIES, "event_kinds": core.EVENT_KINDS,
            "company": {"id": company["id"], "name": company["name"],
                        "is_host": bool(company.get("is_host"))} if company else None,
            "my_rules": {"first_rate": rules[0], "recurring_rate": rules[1],
                         "window_months": rules[2], "source": rules[3]} if rules else None}


@route("PUT", "/api/me/avatar")
def set_avatar(ctx):
    """Stored as a small data URL. There is no object storage on the free tier,
    so the browser resizes to 256px before upload and we cap the size here."""
    need_auth(ctx)
    data = ctx.body.get("avatar")
    thumb = ctx.body.get("avatar_thumb") or ""
    if data in (None, ""):
        ctx.conn.execute("UPDATE users SET avatar = '', avatar_thumb = '' WHERE id = ?",
                         (ctx.user["id"],))
        core.audit(ctx.conn, ctx.user, "avatar.removed", "", ctx.ip)
        return {"ok": True, "avatar": ""}
    data = str(data)
    if not data.startswith(("data:image/png;base64,", "data:image/jpeg;base64,",
                            "data:image/webp;base64,")):
        raise ApiError(400, "Upload a PNG, JPEG or WebP image.")
    if len(data) > 400_000:
        raise ApiError(413, "That image is too large. Choose a smaller photo.")
    if thumb and (len(thumb) > 40_000 or not str(thumb).startswith("data:image/")):
        thumb = ""
    ctx.conn.execute("UPDATE users SET avatar = ?, avatar_thumb = ? WHERE id = ?",
                     (data, thumb, ctx.user["id"]))
    core.audit(ctx.conn, ctx.user, "avatar.updated", "", ctx.ip)
    return {"ok": True, "avatar": data, "avatar_thumb": thumb}


@route("GET", "/api/taxonomy")
def taxonomy(ctx):
    need_auth(ctx)
    return {"agent_types": core.AGENT_TYPES, "industries": core.INDUSTRIES,
            "stages": core.STAGES, "event_kinds": core.EVENT_KINDS}


@route("POST", "/api/change-password")
def change_password(ctx):
    need_auth(ctx)
    current = ctx.body.get("current_password") or ""
    new = ctx.body.get("new_password") or ""
    if not core.verify_password(current, ctx.user["password_hash"]):
        raise ApiError(400, "Your current password is not correct.")
    problem = core.password_problem(new)
    if problem:
        raise ApiError(400, problem)
    ctx.conn.execute(
        "UPDATE users SET password_hash = ?, must_change_pw = 0 WHERE id = ?",
        (core.hash_password(new), ctx.user["id"]),
    )
    core.audit(ctx.conn, ctx.user, "password.changed", "", ctx.ip)
    return {"ok": True}


def public_user(u, full_avatar=True):
    """Lists must not inline full-size avatars: thirty agents would mean
    several megabytes on every page load, which is punishing on mobile data."""
    return {
        "id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"],
        "status": u["status"], "country": u.get("country", ""), "phone": u.get("phone", ""),
        "must_change_pw": bool(u.get("must_change_pw")),
        "is_super": bool(u.get("is_super")),
        "company_id": u.get("company_id") or 1,
        "agent_type": u.get("agent_type", ""),
        "avatar": u.get("avatar", "") if full_avatar else "",
        "avatar_thumb": u.get("avatar_thumb", "") or (u.get("avatar", "") if full_avatar else ""),
        "has_avatar": bool(u.get("avatar")),
        "first_rate": None if u.get("first_rate") is None else float(u["first_rate"]),
        "recurring_rate": None if u.get("recurring_rate") is None else float(u["recurring_rate"]),
        "window_months": None if u.get("window_months") is None else int(u["window_months"]),
        "quota_usd": float(u.get("quota_usd") or 0),
        "start_date": u.get("start_date", ""),
    }


# ==========================================================================
# ADMIN — overview
# ==========================================================================
@route("GET", "/api/admin/overview")
def admin_overview(ctx):
    need_admin(ctx)
    conn, settings = ctx.conn, core.get_settings(ctx.conn)

    sc = scope(ctx)
    if sc is None:
        agents = conn.query("SELECT * FROM users WHERE role = 'agent' ORDER BY name")
        all_clients = conn.query("SELECT * FROM clients")
    else:
        agents = conn.query("SELECT * FROM users WHERE role = 'agent' AND company_id = ? "
                            "ORDER BY name", (sc,))
        all_clients = conn.query("SELECT * FROM clients WHERE company_id = ?", (sc,))
    ids = [c["id"] for c in all_clients]
    payments = core.client_payment_rows(conn, ids, settings)

    live = [p for p in payments if not p.get("voided")]
    collected = round(sum(float(p["amount_usd"]) for p in live), 2)
    agent_comm = round(sum(p["agent_commission_usd"] for p in payments), 2)
    company = round(collected - agent_comm, 2)

    agent_ids = [a["id"] for a in agents] or [0]
    marks = ",".join("?" for _ in agent_ids)
    paid_row = conn.one(f"SELECT COALESCE(SUM(amount_usd),0) AS t FROM payouts "
                        f"WHERE status='Paid' AND agent_id IN ({marks})", tuple(agent_ids))
    flight_row = conn.one(f"SELECT COALESCE(SUM(amount_usd),0) AS t FROM payouts "
                          f"WHERE status IN ('Pending','Approved') AND agent_id IN ({marks})",
                          tuple(agent_ids))
    paid_out = round(float(paid_row["t"] or 0), 2)
    in_flight = round(float(flight_row["t"] or 0), 2)

    by_stage = defaultdict(lambda: {"count": 0, "value": 0.0})
    for c in all_clients:
        rate = core.fx_rate(conn, c["currency"])
        by_stage[c["stage"]]["count"] += 1
        by_stage[c["stage"]]["value"] += core.to_usd(c["monthly_value"], rate)

    # monthly collection trend, last 12 months
    trend = defaultdict(float)
    for p in live:
        trend[str(p["paid_date"])[:7]] += float(p["amount_usd"])
    months = sorted(trend.keys())[-12:]

    leaderboard = []
    for a in agents:
        summ = core.agent_commission_summary(conn, a["id"], settings)
        won = conn.one("SELECT COUNT(*) AS c FROM clients WHERE agent_id = ? AND stage = 'Won'", (a["id"],))
        leaderboard.append({
            "id": a["id"], "name": a["name"], "country": a.get("country", ""),
            "status": a["status"],
            "clients_won": int(won["c"] or 0),
            "collected_usd": summ["collected_usd"],
            "earned_usd": summ["earned_usd"],
            "outstanding_usd": summ["outstanding_usd"],
            "quota_usd": float(a.get("quota_usd") or 0),
            "attainment": round(summ["collected_usd"] / float(a["quota_usd"]), 4)
                          if float(a.get("quota_usd") or 0) > 0 else None,
        })
    leaderboard.sort(key=lambda x: x["collected_usd"], reverse=True)

    return {
        "totals": {
            "agents": len(agents),
            "active_agents": len([a for a in agents if a["status"] == "Active"]),
            "clients": len(all_clients),
            "clients_won": len([c for c in all_clients if c["stage"] == "Won"]),
            "collected_usd": collected,
            "agent_commission_usd": agent_comm,
            "statspack_share_usd": company,
            "paid_out_usd": paid_out,
            "in_flight_usd": in_flight,
            "owed_usd": round(agent_comm - paid_out - in_flight, 2),
        },
        "by_stage": [{"stage": s, **by_stage[s]} for s in core.STAGES if by_stage[s]["count"]],
        "trend": [{"month": m, "amount": round(trend[m], 2)} for m in months],
        "leaderboard": leaderboard,
        "recent_payments": payments[:12],
    }


# ==========================================================================
# COMPANIES — a client organisation that manages its own agents
# ==========================================================================
@route("GET", "/api/admin/companies")
def list_companies(ctx):
    need_super(ctx)
    rows = ctx.conn.query("SELECT * FROM companies ORDER BY is_host DESC, name")
    settings = core.get_settings(ctx.conn)
    for c in rows:
        counts = ctx.conn.one(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN role = 'agent' THEN 1 ELSE 0 END) AS agents, "
            "SUM(CASE WHEN role = 'admin' THEN 1 ELSE 0 END) AS admins "
            "FROM users WHERE company_id = ?", (c["id"],))
        c["agents"] = int(counts["agents"] or 0)
        c["admins"] = int(counts["admins"] or 0)
        cl = ctx.conn.one("SELECT COUNT(*) AS n FROM clients WHERE company_id = ?", (c["id"],))
        c["clients"] = int(cl["n"] or 0)
        ids = [r["id"] for r in ctx.conn.query(
            "SELECT id FROM clients WHERE company_id = ?", (c["id"],))]
        pays = core.client_payment_rows(ctx.conn, ids, settings)
        c["collected_usd"] = round(sum(float(p["amount_usd"]) for p in pays
                                       if not p.get("voided")), 2)
        c["commission_usd"] = round(sum(p["agent_commission_usd"] for p in pays), 2)
        for k in ("first_rate", "recurring_rate"):
            c[k] = float(c[k])
    return {"companies": rows}


@route("POST", "/api/admin/companies")
def create_company(ctx):
    """Registers a client organisation plus its first administrator, who can
    then create their own agents without seeing anyone else's data."""
    need_super(ctx)
    name = ctx.s("name", maxlen=120)
    if not name:
        raise ApiError(400, "Company name is required.")
    if ctx.conn.one("SELECT id FROM companies WHERE LOWER(name) = ?", (name.lower(),)):
        raise ApiError(409, "A company with that name already exists.")

    admin_email = ctx.s("admin_email", maxlen=160).lower()
    admin_name = ctx.s("admin_name", maxlen=120)
    if not core.valid_email(admin_email):
        raise ApiError(400, "Enter a valid email for the company administrator.")
    if not admin_name:
        raise ApiError(400, "Enter a name for the company administrator.")
    if ctx.conn.one("SELECT id FROM users WHERE email = ?", (admin_email,)):
        raise ApiError(409, "An account with that email already exists.")

    first = ctx.f("first_rate", 0.60)
    recur = ctx.f("recurring_rate", 0.10)
    window = ctx.i("window_months", 12)
    for label, v in (("First-payment rate", first), ("Monthly rate", recur)):
        if not 0 <= v <= 1:
            raise ApiError(400, f"{label} must be between 0 and 1 (0.60 = 60%).")
    if not 1 <= window <= 120:
        raise ApiError(400, "Commission window must be between 1 and 120 months.")

    cid = ctx.conn.insert(
        "INSERT INTO companies (name, slug, country, contact_email, contact_phone, status, "
        "is_host, base_currency, first_rate, recurring_rate, window_months, notes, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
         ctx.s("country", maxlen=60), ctx.s("contact_email", maxlen=160),
         ctx.s("contact_phone", maxlen=40), "Active", 0,
         ctx.s("base_currency", "USD", maxlen=8).upper() or "USD",
         first, recur, window, ctx.s("notes", maxlen=2000), core.now_iso()))

    pw = core.temp_password()
    ctx.conn.insert(
        "INSERT INTO users (name, email, phone, country, role, status, password_hash, "
        "must_change_pw, is_super, company_id, agent_type, avatar, quota_usd, start_date, "
        "notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (admin_name, admin_email, ctx.s("contact_phone", maxlen=40),
         ctx.s("country", maxlen=60), "admin", "Active", core.hash_password(pw),
         1, 0, cid, "", "", 0, core.today(), "", core.now_iso()))

    core.audit(ctx.conn, ctx.user, "company.created", f"{name} — admin {admin_email}",
               ctx.ip, target=name,
               meta={"company_id": cid, "first_rate": first, "recurring_rate": recur,
                     "window_months": window, "admin_email": admin_email})
    return {"id": cid, "admin_email": admin_email, "temp_password": pw}


@route("PATCH", r"/api/admin/companies/(\d+)")
def update_company(ctx, company_id):
    need_super(ctx)
    company_id = int(company_id)
    company = ctx.conn.one("SELECT * FROM companies WHERE id = ?", (company_id,))
    if not company:
        raise ApiError(404, "Company not found.")
    fields, values, changed = [], [], {}
    for key, maxlen in (("name", 120), ("country", 60), ("contact_email", 160),
                        ("contact_phone", 40), ("notes", 2000)):
        if key in ctx.body:
            fields.append(f"{key} = ?"); values.append(ctx.s(key, maxlen=maxlen))
            changed[key] = ctx.s(key, maxlen=maxlen)
    for key in ("first_rate", "recurring_rate"):
        if key in ctx.body:
            v = ctx.f(key)
            if not 0 <= v <= 1:
                raise ApiError(400, "Commission rates are fractions between 0 and 1.")
            fields.append(f"{key} = ?"); values.append(v); changed[key] = v
    if "window_months" in ctx.body:
        v = ctx.i("window_months")
        if not 1 <= v <= 120:
            raise ApiError(400, "Commission window must be between 1 and 120 months.")
        fields.append("window_months = ?"); values.append(v); changed["window_months"] = v
    if "status" in ctx.body:
        st = ctx.s("status")
        if st not in ("Active", "Suspended"):
            raise ApiError(400, "Status must be Active or Suspended.")
        if company.get("is_host") and st != "Active":
            raise ApiError(400, "The host company cannot be suspended.")
        fields.append("status = ?"); values.append(st); changed["status"] = st
        if st == "Suspended":
            for u in ctx.conn.query("SELECT id FROM users WHERE company_id = ?", (company_id,)):
                core.purge_sessions(ctx.conn, u["id"])
    if not fields:
        raise ApiError(400, "Nothing to update.")
    values.append(company_id)
    ctx.conn.execute(f"UPDATE companies SET {', '.join(fields)} WHERE id = ?", tuple(values))
    core.audit(ctx.conn, ctx.user, "company.updated", company["name"], ctx.ip,
               target=company["name"], meta=changed)
    return {"ok": True}


# ==========================================================================
# ADMIN — agents
# ==========================================================================
@route("GET", "/api/admin/agents")
def list_agents(ctx):
    need_admin(ctx)
    settings = core.get_settings(ctx.conn)
    sc = scope(ctx)
    if sc is None:
        rows = ctx.conn.query("SELECT * FROM users WHERE role = 'agent' ORDER BY name")
    else:
        rows = ctx.conn.query(
            "SELECT * FROM users WHERE role = 'agent' AND company_id = ? ORDER BY name", (sc,))
    companies = {c["id"]: c["name"] for c in ctx.conn.query("SELECT id, name FROM companies")}

    # Search across the things you would actually remember about an agent.
    needle = ctx.q("q").strip().lower()
    if needle:
        rows = [a for a in rows if needle in " ".join(str(a.get(f) or "") for f in
                ("name", "email", "phone", "country", "agent_type", "notes")).lower()
                or needle in (companies.get(a.get("company_id"), "") or "").lower()]

    status_filter = ctx.q("status").strip()
    if status_filter:
        rows = [a for a in rows if a.get("status") == status_filter]

    type_filter = ctx.q("agent_type").strip()
    if type_filter:
        rows = [a for a in rows if a.get("agent_type") == type_filter]

    out = []
    for a in rows:
        summ = core.agent_commission_summary(ctx.conn, a["id"], settings)
        counts = ctx.conn.one(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN stage = 'Won' THEN 1 ELSE 0 END) AS won "
            "FROM clients WHERE agent_id = ?", (a["id"],))
        u = public_user(a, full_avatar=False)
        u.update({
            "clients_total": int(counts["total"] or 0),
            "clients_won": int(counts["won"] or 0),
            "collected_usd": summ["collected_usd"],
            "earned_usd": summ["earned_usd"],
            "outstanding_usd": summ["outstanding_usd"],
            "paid_out_usd": summ["paid_out_usd"],
            "notes": a.get("notes", ""),
            "company_name": companies.get(a.get("company_id"), ""),
        })
        rules = core.effective_rules(ctx.conn, a["id"], settings)
        u["rules"] = {"first_rate": rules[0], "recurring_rate": rules[1],
                      "window_months": rules[2], "source": rules[3]}
        out.append(u)
    return {"agents": out, "agent_types": core.AGENT_TYPES,
            "query": needle, "matched": len(out)}


@route("POST", "/api/admin/agents")
def create_agent(ctx):
    need_admin(ctx)
    name = ctx.s("name", maxlen=120)
    email = ctx.s("email", maxlen=160).lower()
    if not name:
        raise ApiError(400, "Agent name is required.")
    if not core.valid_email(email):
        raise ApiError(400, "That email address does not look valid.")
    if ctx.conn.one("SELECT id FROM users WHERE email = ?", (email,)):
        raise ApiError(409, "An account with that email already exists.")

    pw = ctx.body.get("password") or ""
    generated = False
    if not pw:
        pw = core.temp_password()
        generated = True
    else:
        problem = core.password_problem(pw)
        if problem:
            raise ApiError(400, problem)

    role = "admin" if ctx.s("role") == "admin" else "agent"

    # A company admin can only ever create people inside their own company.
    sc = scope(ctx)
    if sc is None:
        company_id = ctx.i("company_id", 1) or 1
        if not ctx.conn.one("SELECT id FROM companies WHERE id = ?", (company_id,)):
            raise ApiError(400, "Choose a valid company.")
    else:
        company_id = sc

    agent_type = ctx.s("agent_type", maxlen=60)
    if agent_type and agent_type not in core.AGENT_TYPES:
        raise ApiError(400, "Unknown agent type.")
    if role == "agent" and not agent_type:
        agent_type = core.AGENT_TYPES[0]

    first, recur, window = _optional_rates(ctx)

    new_id = ctx.conn.insert(
        "INSERT INTO users (name, email, phone, country, role, status, password_hash, "
        "must_change_pw, is_super, company_id, agent_type, avatar, first_rate, recurring_rate, "
        "window_months, quota_usd, start_date, notes, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, email, ctx.s("phone", maxlen=40), ctx.s("country", maxlen=60), role, "Active",
         core.hash_password(pw), 1, 0, company_id, agent_type, "",
         first, recur, window,
         ctx.f("quota_usd"), ctx.s("start_date") or core.today(),
         ctx.s("notes", maxlen=2000), core.now_iso()),
    )
    core.audit(ctx.conn, ctx.user, "agent.created", f"{name} <{email}>", ctx.ip, target=name,
               meta={"role": role, "agent_type": agent_type, "company_id": company_id,
                     "first_rate": first, "recurring_rate": recur, "window_months": window})
    return {"id": new_id, "temp_password": pw if generated else None}


def _optional_rates(ctx):
    """Per-agent commission overrides. Blank means 'inherit'."""
    def opt(key, lo, hi, cast=float):
        raw = ctx.body.get(key, None)
        if raw in (None, "", "inherit"):
            return None
        try:
            v = cast(raw)
        except (TypeError, ValueError):
            raise ApiError(400, f"{key.replace('_', ' ')} must be a number.")
        if not lo <= v <= hi:
            raise ApiError(400, f"{key.replace('_', ' ')} must be between {lo} and {hi}.")
        return v
    return (opt("first_rate", 0, 1), opt("recurring_rate", 0, 1),
            opt("window_months", 1, 120, lambda v: int(float(v))))


@route("PATCH", r"/api/admin/agents/(\d+)")
def update_agent(ctx, agent_id):
    need_admin(ctx)
    agent_id = int(agent_id)
    agent = ctx.conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not agent:
        raise ApiError(404, "Agent not found.")
    if not same_company(ctx, agent):
        raise ApiError(403, "That account belongs to another company.")

    fields, values = [], []
    if "agent_type" in ctx.body:
        at = ctx.s("agent_type", maxlen=60)
        if at and at not in core.AGENT_TYPES:
            raise ApiError(400, "Unknown agent type.")
        fields.append("agent_type = ?"); values.append(at)
    for key in ("first_rate", "recurring_rate", "window_months"):
        if key in ctx.body:
            raw = ctx.body.get(key)
            if raw in (None, "", "inherit"):
                fields.append(f"{key} = ?"); values.append(None)
            else:
                try:
                    v = int(float(raw)) if key == "window_months" else float(raw)
                except (TypeError, ValueError):
                    raise ApiError(400, f"{key.replace('_', ' ')} must be a number.")
                hi = 120 if key == "window_months" else 1
                lo = 1 if key == "window_months" else 0
                if not lo <= v <= hi:
                    raise ApiError(400, f"{key.replace('_', ' ')} must be between {lo} and {hi}.")
                fields.append(f"{key} = ?"); values.append(v)
    for key, caster, maxlen in (("name", "s", 120), ("phone", "s", 40), ("country", "s", 60),
                                ("start_date", "s", 20), ("notes", "s", 4000)):
        if key in ctx.body:
            fields.append(f"{key} = ?")
            values.append(ctx.s(key, maxlen=maxlen))
    if "quota_usd" in ctx.body:
        fields.append("quota_usd = ?")
        values.append(ctx.f("quota_usd"))
    if "status" in ctx.body:
        status = ctx.s("status")
        if status not in ("Active", "Suspended"):
            raise ApiError(400, "Status must be Active or Suspended.")
        if agent["role"] == "admin" and status != "Active":
            admins = ctx.conn.one("SELECT COUNT(*) AS c FROM users WHERE role='admin' AND status='Active'")
            if int(admins["c"]) <= 1:
                raise ApiError(400, "You cannot suspend the only active administrator.")
        fields.append("status = ?")
        values.append(status)
        if status == "Suspended":
            core.purge_sessions(ctx.conn, agent_id)
    if "email" in ctx.body:
        email = ctx.s("email", maxlen=160).lower()
        if not core.valid_email(email):
            raise ApiError(400, "That email address does not look valid.")
        clash = ctx.conn.one("SELECT id FROM users WHERE email = ? AND id <> ?", (email, agent_id))
        if clash:
            raise ApiError(409, "Another account already uses that email.")
        fields.append("email = ?")
        values.append(email)

    if not fields:
        raise ApiError(400, "Nothing to update.")
    values.append(agent_id)
    ctx.conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", tuple(values))
    core.audit(ctx.conn, ctx.user, "agent.updated", f"#{agent_id} {agent['email']}", ctx.ip)
    return {"ok": True}


@route("POST", r"/api/admin/agents/(\d+)/reset-password")
def reset_password(ctx, agent_id):
    need_admin(ctx)
    agent_id = int(agent_id)
    agent = ctx.conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not agent:
        raise ApiError(404, "Agent not found.")
    if not same_company(ctx, agent):
        raise ApiError(403, "That account belongs to another company.")
    pw = core.temp_password()
    ctx.conn.execute(
        "UPDATE users SET password_hash = ?, must_change_pw = 1 WHERE id = ?",
        (core.hash_password(pw), agent_id),
    )
    core.purge_sessions(ctx.conn, agent_id)
    core.audit(ctx.conn, ctx.user, "agent.password_reset", agent["email"], ctx.ip)
    return {"temp_password": pw}


@route("GET", r"/api/admin/agents/(\d+)/footprint")
def agent_footprint_ep(ctx, agent_id):
    """What permanent deletion would destroy. Read-only, safe to call."""
    need_super(ctx)
    agent_id = int(agent_id)
    agent = ctx.conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not agent:
        raise ApiError(404, "Agent not found.")
    return {"agent": public_user(agent),
            "footprint": core.agent_footprint(ctx.conn, agent_id)}


@route("GET", r"/api/admin/agents/(\d+)/export")
def agent_export_ep(ctx, agent_id):
    need_admin(ctx)
    target = ctx.conn.one("SELECT * FROM users WHERE id = ?", (int(agent_id),))
    if not target:
        raise ApiError(404, "Agent not found.")
    if not same_company(ctx, target):
        raise ApiError(403, "That agent belongs to another company.")
    data = core.export_agent(ctx.conn, int(agent_id))
    if not data:
        raise ApiError(404, "Agent not found.")
    core.audit(ctx.conn, ctx.user, "agent.exported", data["agent"]["email"], ctx.ip)
    return data


@route("DELETE", r"/api/admin/agents/(\d+)/purge")
def purge_agent_ep(ctx, agent_id):
    """Permanently erase an agent and every record attached to them."""
    need_super(ctx)
    agent_id = int(agent_id)
    agent = ctx.conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not agent:
        raise ApiError(404, "Agent not found.")
    if agent["id"] == ctx.user["id"]:
        raise ApiError(400, "You cannot delete your own account.")
    if agent.get("is_super"):
        raise ApiError(400, "A super user cannot be deleted. Remove their super "
                            "rights first, from another super-user account.")
    if agent["role"] == "admin":
        remaining = ctx.conn.one(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND id <> ?", (agent_id,))
        if int(remaining["c"]) < 1:
            raise ApiError(400, "That is the last administrator account.")

    # Typing the exact name is the safety catch — a click alone is not enough.
    typed = ctx.s("confirm_name")
    if typed.strip().lower() != agent["name"].strip().lower():
        raise ApiError(400, "Type the agent's full name exactly to confirm.")

    footprint = core.agent_footprint(ctx.conn, agent_id)
    counts = core.purge_agent(ctx.conn, agent_id)
    core.audit(ctx.conn, ctx.user, "agent.PURGED",
               f"{agent['name']} <{agent['email']}> — removed "
               f"{counts['clients']} client(s), {counts['payments']} payment(s), "
               f"{counts['payouts']} payout(s), {counts['reviews']} note(s); "
               f"${footprint['collected_usd']:,.2f} of collections erased", ctx.ip)
    return {"ok": True, "deleted": counts, "footprint": footprint}


@route("GET", r"/api/admin/agents/(\d+)/progress")
def agent_progress(ctx, agent_id):
    need_admin(ctx)
    agent_id = int(agent_id)
    agent = ctx.conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not agent:
        raise ApiError(404, "Agent not found.")
    if not same_company(ctx, agent):
        raise ApiError(403, "That agent belongs to another company.")
    settings = core.get_settings(ctx.conn)
    summ = core.agent_commission_summary(ctx.conn, agent_id, settings)
    rules = core.effective_rules(ctx.conn, agent_id, settings)
    clients = ctx.conn.query(
        "SELECT * FROM clients WHERE agent_id = ? ORDER BY updated_at DESC", (agent_id,))
    for c in clients:
        c["monthly_value_usd"] = core.to_usd(c["monthly_value"], core.fx_rate(ctx.conn, c["currency"]))
        paid = ctx.conn.one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount_usd),0) AS t "
            "FROM payments WHERE client_id = ? AND voided = 0", (c["id"],))
        c["payments_count"] = int(paid["n"] or 0)
        c["collected_usd"] = round(float(paid["t"] or 0), 2)

    reviews = ctx.conn.query(
        "SELECT r.*, u.name AS author_name FROM reviews r LEFT JOIN users u ON u.id = r.author_id "
        "WHERE r.agent_id = ? ORDER BY r.created_at DESC", (agent_id,))
    payouts = ctx.conn.query(
        "SELECT * FROM payouts WHERE agent_id = ? ORDER BY created_at DESC", (agent_id,))

    won = len([c for c in clients if c["stage"] == "Won"])
    lost = len([c for c in clients if c["stage"] in ("Lost", "Churned")])
    quota = float(agent.get("quota_usd") or 0)

    return {
        "agent": public_user(agent),
        "notes": agent.get("notes", ""),
        "rules": {"first_rate": rules[0], "recurring_rate": rules[1],
                  "window_months": rules[2], "source": rules[3]},
        "metrics": {
            "clients_total": len(clients),
            "clients_won": won,
            "clients_lost": lost,
            "win_rate": round(won / (won + lost), 4) if (won + lost) else None,
            "pipeline_usd": round(sum(c["monthly_value_usd"] for c in clients
                                      if c["stage"] not in ("Won", "Lost", "Churned")), 2),
            "attainment": round(summ["collected_usd"] / quota, 4) if quota > 0 else None,
            **{k: v for k, v in summ.items() if k != "payments"},
        },
        "clients": clients,
        "payments": summ["payments"],
        "reviews": reviews,
        "payouts": payouts,
    }


# ==========================================================================
# ADMIN — reviews (progress notes)
# ==========================================================================
@route("POST", "/api/admin/reviews")
def create_review(ctx):
    need_admin(ctx)
    agent_id = ctx.i("agent_id")
    target = ctx.conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not target:
        raise ApiError(404, "Agent not found.")
    if not same_company(ctx, target):
        raise ApiError(403, "That agent belongs to another company.")
    body = ctx.s("body", maxlen=4000)
    if not body:
        raise ApiError(400, "Write a note before saving.")
    rating = max(1, min(5, ctx.i("rating", 3)))
    ctx.conn.insert(
        "INSERT INTO reviews (agent_id, author_id, period, rating, body, created_at) VALUES (?,?,?,?,?,?)",
        (agent_id, ctx.user["id"], ctx.s("period", maxlen=40), rating, body, core.now_iso()),
    )
    core.audit(ctx.conn, ctx.user, "review.created", f"agent #{agent_id}", ctx.ip)
    return {"ok": True}


@route("DELETE", r"/api/admin/reviews/(\d+)")
def delete_review(ctx, review_id):
    need_admin(ctx)
    review = ctx.conn.one("SELECT * FROM reviews WHERE id = ?", (int(review_id),))
    if not review:
        raise ApiError(404, "Note not found.")
    subject = ctx.conn.one("SELECT * FROM users WHERE id = ?", (review["agent_id"],))
    if subject and not same_company(ctx, subject):
        raise ApiError(403, "That note belongs to another company.")
    ctx.conn.execute("DELETE FROM reviews WHERE id = ?", (int(review_id),))
    core.audit(ctx.conn, ctx.user, "review.deleted", f"#{review_id}", ctx.ip)
    return {"ok": True}


# ==========================================================================
# CLIENTS  (admin sees all; agents see and edit only their own)
# ==========================================================================
def _client_visible(ctx, client_id):
    c = ctx.conn.one("SELECT * FROM clients WHERE id = ?", (int(client_id),))
    if not c:
        raise ApiError(404, "Client not found.")
    if ctx.user["role"] != "admin" and c["agent_id"] != ctx.user["id"]:
        partner = ctx.conn.one(
            "SELECT id FROM collaborations WHERE client_id = ? AND partner_id = ? "
            "AND status = 'Accepted'", (c["id"], ctx.user["id"]))
        if not partner:
            raise ApiError(403, "That client belongs to another agent.")
        c["shared_with_me"] = True
    if not same_company(ctx, c):
        raise ApiError(403, "That client belongs to another company.")
    return c


@route("GET", "/api/clients")
def list_clients(ctx):
    need_auth(ctx)
    conn = ctx.conn
    if ctx.user["role"] == "admin":
        sc = scope(ctx)
        agent_filter = ctx.q("agent_id")
        if agent_filter and sc is None:
            rows = conn.query("SELECT * FROM clients WHERE agent_id = ? ORDER BY updated_at DESC",
                              (int(agent_filter),))
        elif agent_filter:
            rows = conn.query("SELECT * FROM clients WHERE agent_id = ? AND company_id = ? "
                              "ORDER BY updated_at DESC", (int(agent_filter), sc))
        elif sc is None:
            rows = conn.query("SELECT * FROM clients ORDER BY updated_at DESC")
        else:
            rows = conn.query("SELECT * FROM clients WHERE company_id = ? ORDER BY updated_at DESC",
                              (sc,))
    else:
        rows = conn.query("SELECT * FROM clients WHERE agent_id = ? ORDER BY updated_at DESC",
                          (ctx.user["id"],))
        shared = conn.query(
            "SELECT c.* FROM clients c JOIN collaborations k ON k.client_id = c.id "
            "WHERE k.partner_id = ? AND k.status = 'Accepted' ORDER BY c.updated_at DESC",
            (ctx.user["id"],))
        have = {r["id"] for r in rows}
        for r in shared:
            if r["id"] not in have:
                r["shared_with_me"] = True
                rows.append(r)

    agents = {a["id"]: a["name"] for a in conn.query("SELECT id, name FROM users")}
    for c in rows:
        c["agent_name"] = agents.get(c["agent_id"], "—")
        c["monthly_value_usd"] = core.to_usd(c["monthly_value"], core.fx_rate(conn, c["currency"]))
        p = conn.one("SELECT COUNT(*) AS n, COALESCE(SUM(amount_usd),0) AS t, MIN(paid_date) AS first_paid "
                     "FROM payments WHERE client_id = ? AND voided = 0", (c["id"],))
        c["payments_count"] = int(p["n"] or 0)
        c["collected_usd"] = round(float(p["t"] or 0), 2)
        c["first_paid"] = p["first_paid"] or ""
        if c["first_paid"]:
            fd = core.parse_date(c["first_paid"])
            window = int(float(core.get_settings(conn).get("commission_window_months", 12)))
            c["window_end"] = core.add_months(fd, window).strftime("%Y-%m-%d") if fd else ""
        else:
            c["window_end"] = ""
    return {"clients": rows}


@route("POST", "/api/clients")
def create_client(ctx):
    need_auth(ctx)
    name = ctx.s("name", maxlen=160)
    if not name:
        raise ApiError(400, "Client name is required.")
    stage = ctx.s("stage") or "Prospect"
    if stage not in core.STAGES:
        raise ApiError(400, "Unknown pipeline stage.")

    if ctx.user["role"] == "admin" and ctx.body.get("agent_id"):
        agent_id = ctx.i("agent_id")
        target = ctx.conn.one("SELECT * FROM users WHERE id = ? AND role = 'agent'", (agent_id,))
        if not target:
            raise ApiError(400, "Choose a valid agent.")
        if not same_company(ctx, target):
            raise ApiError(403, "That agent belongs to another company.")
    else:
        agent_id = ctx.user["id"]

    industry = ctx.s("industry", maxlen=80)
    if industry and industry not in core.INDUSTRIES:
        raise ApiError(400, "Unknown industry.")
    company_id = ctx.user.get("company_id") or 1
    if ctx.user["role"] == "admin" and agent_id != ctx.user["id"]:
        owner = ctx.conn.one("SELECT company_id FROM users WHERE id = ?", (agent_id,))
        company_id = (owner or {}).get("company_id") or company_id

    currency = ctx.s("currency", "USD", maxlen=8).upper() or "USD"
    if not ctx.conn.one("SELECT code FROM fx WHERE code = ?", (currency,)):
        raise ApiError(400, f"Currency {currency} is not in the rate table.")

    now = core.now_iso()
    new_id = ctx.conn.insert(
        "INSERT INTO clients (agent_id, name, contact_person, contact_email, contact_phone, "
        "country, industry, company_id, product, currency, monthly_value, stage, won_date, "
        "notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (agent_id, name, ctx.s("contact_person", maxlen=120), ctx.s("contact_email", maxlen=160),
         ctx.s("contact_phone", maxlen=40), ctx.s("country", maxlen=60), industry, company_id,
         ctx.s("product", maxlen=120), currency, ctx.f("monthly_value"), stage,
         ctx.s("won_date") if stage == "Won" else "", ctx.s("notes", maxlen=4000), now, now),
    )
    core.log_event(ctx.conn, {"id": new_id, "company_id": company_id}, ctx.user,
                   kind="stage", to_stage=stage, body="Client added to the pipeline.")
    core.audit(ctx.conn, ctx.user, "client.created", name, ctx.ip, target=name,
               meta={"client_id": new_id, "stage": stage, "industry": industry,
                     "currency": currency, "agent_id": agent_id})
    return {"id": new_id}


@route("PATCH", r"/api/clients/(\d+)")
def update_client(ctx, client_id):
    need_auth(ctx)
    client = _client_visible(ctx, client_id)
    if client.get("shared_with_me") and ctx.user["role"] != "admin":
        raise ApiError(403, "You are helping on this client, not running it. "
                            "Log activity instead, or ask the lead agent to make changes.")
    fields, values = [], []
    for key, maxlen in (("name", 160), ("contact_person", 120), ("contact_email", 160),
                        ("contact_phone", 40), ("country", 60), ("product", 120), ("notes", 4000)):
        if key in ctx.body:
            fields.append(f"{key} = ?")
            values.append(ctx.s(key, maxlen=maxlen))
    if "industry" in ctx.body:
        ind = ctx.s("industry", maxlen=80)
        if ind and ind not in core.INDUSTRIES:
            raise ApiError(400, "Unknown industry.")
        fields.append("industry = ?"); values.append(ind)
    if "monthly_value" in ctx.body:
        fields.append("monthly_value = ?")
        values.append(ctx.f("monthly_value"))
    if "currency" in ctx.body:
        cur = ctx.s("currency", maxlen=8).upper()
        if not ctx.conn.one("SELECT code FROM fx WHERE code = ?", (cur,)):
            raise ApiError(400, f"Currency {cur} is not in the rate table.")
        fields.append("currency = ?")
        values.append(cur)
    stage_moved = None
    if "stage" in ctx.body:
        stage = ctx.s("stage")
        if stage not in core.STAGES:
            raise ApiError(400, "Unknown pipeline stage.")
        if stage != client["stage"]:
            stage_moved = (client["stage"], stage)
        fields.append("stage = ?")
        values.append(stage)
        if stage == "Won" and not client.get("won_date"):
            fields.append("won_date = ?")
            values.append(ctx.s("won_date") or core.today())
    if "agent_id" in ctx.body and ctx.user["role"] == "admin":
        new_agent = ctx.i("agent_id")
        target = ctx.conn.one("SELECT * FROM users WHERE id = ?", (new_agent,))
        if not target:
            raise ApiError(400, "Choose a valid agent.")
        if not same_company(ctx, target):
            raise ApiError(403, "That agent belongs to another company.")
        fields.append("agent_id = ?")
        values.append(new_agent)
        # The client must follow its new owner's company, otherwise it becomes
        # invisible to them and still counts against the old company's totals.
        if (target.get("company_id") or 1) != (client.get("company_id") or 1):
            fields.append("company_id = ?")
            values.append(target.get("company_id") or 1)

    if not fields:
        raise ApiError(400, "Nothing to update.")
    fields.append("updated_at = ?")
    values.append(core.now_iso())
    values.append(client["id"])
    ctx.conn.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = ?", tuple(values))
    if stage_moved:
        core.log_event(ctx.conn, client, ctx.user, kind="stage",
                       from_stage=stage_moved[0], to_stage=stage_moved[1],
                       body=ctx.s("stage_note", maxlen=2000))
    core.audit(ctx.conn, ctx.user, "client.updated", client["name"], ctx.ip,
               target=client["name"],
               meta={"client_id": client["id"],
                     "stage_change": f"{stage_moved[0]} -> {stage_moved[1]}" if stage_moved else None,
                     "fields": [f.split(" =")[0] for f in fields]})
    return {"ok": True}


@route("DELETE", r"/api/clients/(\d+)")
def delete_client(ctx, client_id):
    need_admin(ctx)
    client = _client_visible(ctx, client_id)
    n = ctx.conn.one("SELECT COUNT(*) AS c FROM payments WHERE client_id = ?", (client["id"],))
    if int(n["c"]):
        raise ApiError(400, "This client has payments recorded against it. Set the stage to "
                            "Churned instead — deleting would erase commission history.")
    ctx.conn.execute("DELETE FROM client_events WHERE client_id = ?", (client["id"],))
    ctx.conn.execute("DELETE FROM collaborations WHERE client_id = ?", (client["id"],))
    ctx.conn.execute("DELETE FROM clients WHERE id = ?", (client["id"],))
    core.audit(ctx.conn, ctx.user, "client.deleted", client["name"], ctx.ip,
               target=client["name"], meta={"client_id": client["id"]})
    return {"ok": True}


@route("GET", r"/api/clients/(\d+)")
def client_detail(ctx, client_id):
    need_auth(ctx)
    client = _client_visible(ctx, client_id)
    settings = core.get_settings(ctx.conn)
    payments = core.client_payment_rows(ctx.conn, [client["id"]], settings,
                                        newest_first=False)
    client["monthly_value_usd"] = core.to_usd(client["monthly_value"],
                                              core.fx_rate(ctx.conn, client["currency"]))
    agent = ctx.conn.one("SELECT name FROM users WHERE id = ?", (client["agent_id"],))
    client["agent_name"] = agent["name"] if agent else "—"
    return {"client": client, "payments": payments,
            "timeline": core.client_timeline(ctx.conn, client["id"]),
            "progress": core.stage_progress(client["stage"]),
            "industries": core.INDUSTRIES}


@route("GET", r"/api/clients/(\d+)/timeline")
def client_timeline_ep(ctx, client_id):
    need_auth(ctx)
    client = _client_visible(ctx, client_id)
    return {"timeline": core.client_timeline(ctx.conn, client["id"]),
            "progress": core.stage_progress(client["stage"]),
            "event_kinds": core.EVENT_KINDS}


@route("POST", r"/api/clients/(\d+)/timeline")
def add_client_event(ctx, client_id):
    """An agent logs what they actually did — the dated trail behind the flowchart."""
    need_auth(ctx)
    client = _client_visible(ctx, client_id)
    kind = ctx.s("kind") or "note"
    if kind not in core.EVENT_KINDS:
        raise ApiError(400, "Unknown activity type.")
    body = ctx.s("body", maxlen=4000)
    if not body:
        raise ApiError(400, "Write something before saving.")
    due = ctx.s("due_date", maxlen=20)
    if due and not core.parse_date(due):
        raise ApiError(400, "Follow-up date must be YYYY-MM-DD.")
    eid = core.log_event(ctx.conn, client, ctx.user, kind=kind, body=body,
                         next_step=ctx.s("next_step", maxlen=400), due_date=due)
    core.audit(ctx.conn, ctx.user, "client.activity", f"{client['name']} — {kind}", ctx.ip,
               target=client["name"], meta={"client_id": client["id"], "kind": kind,
                                            "next_step": ctx.s("next_step", maxlen=400)})
    return {"id": eid}


@route("GET", r"/api/clients/(\d+)/footprint")
def client_footprint(ctx, client_id):
    """What deleting this client would destroy. Read-only."""
    need_admin(ctx)
    client = _client_visible(ctx, client_id)
    pay = ctx.conn.one("SELECT COUNT(*) AS n, COALESCE(SUM(amount_usd),0) AS t "
                       "FROM payments WHERE client_id = ? AND voided = 0", (client["id"],))
    ev = ctx.conn.one("SELECT COUNT(*) AS n FROM client_events WHERE client_id = ?",
                      (client["id"],))
    kb = ctx.conn.one("SELECT COUNT(*) AS n FROM collaborations WHERE client_id = ?",
                      (client["id"],))
    settings = core.get_settings(ctx.conn)
    rows = core.client_payment_rows(ctx.conn, [client["id"]], settings)
    return {"client": {"id": client["id"], "name": client["name"], "stage": client["stage"]},
            "footprint": {
                "payments": int(pay["n"] or 0),
                "collected_usd": round(float(pay["t"] or 0), 2),
                "commission_usd": round(sum(p["agent_commission_usd"] for p in rows), 2),
                "timeline_entries": int(ev["n"] or 0),
                "collaborations": int(kb["n"] or 0)},
            "can_delete_simply": int(pay["n"] or 0) == 0,
            "requires_super": int(pay["n"] or 0) > 0}


@route("DELETE", r"/api/clients/(\d+)/purge")
def purge_client(ctx, client_id):
    """Permanently delete a client INCLUDING its payments and commission.

    Reserved for super users: this destroys financial history, so an ordinary
    admin is limited to clients that have never paid.
    """
    need_super(ctx)
    client = _client_visible(ctx, client_id)
    typed = ctx.s("confirm_name")
    if typed.strip().lower() != client["name"].strip().lower():
        raise ApiError(400, "Type the client's name exactly to confirm.")

    settings = core.get_settings(ctx.conn)
    rows = core.client_payment_rows(ctx.conn, [client["id"]], settings)
    collected = round(sum(float(p["amount_usd"]) for p in rows if not p.get("voided")), 2)
    commission = round(sum(p["agent_commission_usd"] for p in rows), 2)
    counts = {"payments": len(rows)}

    counts["timeline"] = int((ctx.conn.one(
        "SELECT COUNT(*) AS n FROM client_events WHERE client_id = ?", (client["id"],))
        or {}).get("n") or 0)
    counts["collaborations"] = int((ctx.conn.one(
        "SELECT COUNT(*) AS n FROM collaborations WHERE client_id = ?", (client["id"],))
        or {}).get("n") or 0)

    ctx.conn.execute("DELETE FROM payments WHERE client_id = ?", (client["id"],))
    ctx.conn.execute("DELETE FROM client_events WHERE client_id = ?", (client["id"],))
    ctx.conn.execute("DELETE FROM collaborations WHERE client_id = ?", (client["id"],))
    ctx.conn.execute("DELETE FROM clients WHERE id = ?", (client["id"],))

    core.audit(ctx.conn, ctx.user, "client.PURGED",
               f"{client['name']} — {counts['payments']} payment(s), "
               f"${collected:,.2f} collected, ${commission:,.2f} commission erased",
               ctx.ip, target=client["name"],
               meta={"client_id": client["id"], "collected_usd": collected,
                     "commission_usd": commission, **counts})
    return {"ok": True, "deleted": counts, "collected_usd": collected,
            "commission_usd": commission}


@route("GET", r"/api/clients/(\d+)/export")
def client_export(ctx, client_id):
    need_admin(ctx)
    client = _client_visible(ctx, client_id)
    settings = core.get_settings(ctx.conn)
    return {"exported_at": core.now_iso(),
            "reason": "snapshot taken before deleting a client",
            "client": client,
            "payments": core.client_payment_rows(ctx.conn, [client["id"]], settings,
                                                 newest_first=False),
            "timeline": core.client_timeline(ctx.conn, client["id"]),
            "collaborations": ctx.conn.query(
                "SELECT * FROM collaborations WHERE client_id = ?", (client["id"],))}


@route("DELETE", r"/api/clients/(\d+)/timeline/(\d+)")
def delete_client_event(ctx, client_id, event_id):
    need_admin(ctx)
    client = _client_visible(ctx, client_id)
    ev = ctx.conn.one("SELECT * FROM client_events WHERE id = ? AND client_id = ?",
                      (int(event_id), client["id"]))
    if not ev:
        raise ApiError(404, "Entry not found.")
    if ev["kind"] == "stage":
        raise ApiError(400, "Stage changes are part of the audit trail and cannot be removed.")
    ctx.conn.execute("DELETE FROM client_events WHERE id = ?", (ev["id"],))
    core.audit(ctx.conn, ctx.user, "client.activity_deleted", client["name"], ctx.ip)
    return {"ok": True}


# ==========================================================================
# COLLABORATION — bringing in an agent who knows the country or the industry
# ==========================================================================
@route("GET", "/api/agents/directory")
def agent_directory_ep(ctx):
    need_auth(ctx)
    settings = core.get_settings(ctx.conn)
    return {"agents": core.agent_directory(
        ctx.conn, ctx.user.get("company_id") or 1, settings,
        exclude_id=ctx.user["id"], country=ctx.q("country") or None,
        industry=ctx.q("industry") or None),
        "industries": core.INDUSTRIES}


@route("GET", "/api/collaborations")
def list_collaborations(ctx):
    need_auth(ctx)
    if ctx.user["role"] == "admin":
        sc = scope(ctx)
        rows = ctx.conn.query("SELECT * FROM collaborations ORDER BY id DESC") if sc is None else \
            ctx.conn.query("SELECT * FROM collaborations WHERE company_id = ? ORDER BY id DESC", (sc,))
    else:
        rows = ctx.conn.query(
            "SELECT * FROM collaborations WHERE owner_id = ? OR partner_id = ? ORDER BY id DESC",
            (ctx.user["id"], ctx.user["id"]))
    names = {u["id"]: u["name"] for u in ctx.conn.query("SELECT id, name FROM users")}
    clients = {c["id"]: c["name"] for c in ctx.conn.query("SELECT id, name FROM clients")}
    for r in rows:
        r["owner_name"] = names.get(r["owner_id"], "—")
        r["partner_name"] = names.get(r["partner_id"], "—")
        r["client_name"] = clients.get(r["client_id"], "—")
        r["split_pct"] = float(r["split_pct"])
        r["i_am_partner"] = r["partner_id"] == ctx.user["id"]
    return {"collaborations": rows,
            "incoming": [r for r in rows if r["i_am_partner"] and r["status"] == "Requested"]}


@route("POST", "/api/collaborations")
def request_collaboration(ctx):
    """The lead agent asks a colleague to help, offering a share of their own
    commission. StatsPack's share is untouched."""
    need_auth(ctx)
    client = _client_visible(ctx, ctx.i("client_id"))
    if client.get("shared_with_me"):
        raise ApiError(403, "Only the lead agent can invite someone onto this client.")
    if ctx.user["role"] == "agent" and client["agent_id"] != ctx.user["id"]:
        raise ApiError(403, "That client is not yours.")

    partner_id = ctx.i("partner_id")
    partner = ctx.conn.one("SELECT * FROM users WHERE id = ?", (partner_id,))
    if not partner or partner["role"] != "agent":
        raise ApiError(400, "Choose a valid agent.")
    if partner_id == client["agent_id"]:
        raise ApiError(400, "That agent already runs this client.")
    if not same_company(ctx, partner) or partner.get("company_id") != client.get("company_id"):
        raise ApiError(403, "You can only work with agents in your own company.")
    if partner["status"] != "Active":
        raise ApiError(400, "That agent's account is not active.")

    split = ctx.f("split_pct", 0.30)
    if not 0 < split <= 0.9:
        raise ApiError(400, "The partner's share must be above 0% and no more than 90% "
                            "of your commission.")

    open_req = ctx.conn.one(
        "SELECT id, status FROM collaborations WHERE client_id = ? "
        "AND status IN ('Requested','Accepted')", (client["id"],))
    if open_req:
        raise ApiError(409, "This client already has a partnership in progress. "
                            "End it before starting another.")

    new_id = ctx.conn.insert(
        "INSERT INTO collaborations (client_id, company_id, owner_id, partner_id, status, "
        "split_pct, reason, response_note, created_at, responded_at, ended_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (client["id"], client.get("company_id") or 1, client["agent_id"], partner_id,
         "Requested", round(split, 4), ctx.s("reason", maxlen=1000), "",
         core.now_iso(), "", ""))
    core.log_event(ctx.conn, client, ctx.user, kind="note",
                   body=f"Asked {partner['name']} to co-work this client "
                        f"({split*100:.0f}% of the agent commission).")
    core.audit(ctx.conn, ctx.user, "collab.requested",
               f"{client['name']} — {partner['name']} at {split*100:.0f}%", ctx.ip,
               target=client["name"],
               meta={"collaboration_id": new_id, "client_id": client["id"],
                     "partner_id": partner_id, "split_pct": split})
    return {"id": new_id}


@route("PATCH", r"/api/collaborations/(\d+)")
def respond_collaboration(ctx, collab_id):
    need_auth(ctx)
    k = ctx.conn.one("SELECT * FROM collaborations WHERE id = ?", (int(collab_id),))
    if not k:
        raise ApiError(404, "Request not found.")
    client = ctx.conn.one("SELECT * FROM clients WHERE id = ?", (k["client_id"],))
    if client and not same_company(ctx, client):
        raise ApiError(403, "That belongs to another company.")

    status = ctx.s("status")
    if status not in ("Accepted", "Declined", "Ended"):
        raise ApiError(400, "Unknown response.")

    is_partner = k["partner_id"] == ctx.user["id"]
    is_owner = k["owner_id"] == ctx.user["id"]
    is_admin = ctx.user["role"] == "admin"

    if status in ("Accepted", "Declined"):
        if not (is_partner or is_admin):
            raise ApiError(403, "Only the invited agent can answer this request.")
        if k["status"] != "Requested":
            raise ApiError(400, f"This request was already {k['status'].lower()}.")
    else:                                     # Ended
        if not (is_partner or is_owner or is_admin):
            raise ApiError(403, "You are not part of this partnership.")
        if k["status"] != "Accepted":
            raise ApiError(400, "That partnership is not active.")

    now = core.now_iso()
    if status == "Ended":
        ctx.conn.execute("UPDATE collaborations SET status = ?, ended_at = ?, "
                         "response_note = ? WHERE id = ?",
                         (status, now, ctx.s("response_note", maxlen=1000), k["id"]))
    else:
        ctx.conn.execute("UPDATE collaborations SET status = ?, responded_at = ?, "
                         "response_note = ? WHERE id = ?",
                         (status, now, ctx.s("response_note", maxlen=1000), k["id"]))

    if client:
        core.log_event(ctx.conn, client, ctx.user, kind="note",
                       body=f"Partnership {status.lower()}"
                            + (f": {ctx.s('response_note', maxlen=300)}"
                               if ctx.s("response_note") else "."))
    core.audit(ctx.conn, ctx.user, f"collab.{status.lower()}",
               f"{(client or {}).get('name', '')} #{k['id']}", ctx.ip,
               meta={"collaboration_id": k["id"], "split_pct": float(k["split_pct"])})
    return {"ok": True, "status": status}


# ==========================================================================
# PAYMENTS — recording one is what triggers commission. Admin only.
# ==========================================================================
@route("GET", "/api/payments")
def list_payments(ctx):
    need_auth(ctx)
    settings = core.get_settings(ctx.conn)
    if ctx.user["role"] == "admin":
        sc = scope(ctx)
        rows = ctx.conn.query("SELECT id FROM clients") if sc is None else \
            ctx.conn.query("SELECT id FROM clients WHERE company_id = ?", (sc,))
    else:
        rows = ctx.conn.query("SELECT id FROM clients WHERE agent_id = ?", (ctx.user["id"],))
    payments = core.client_payment_rows(ctx.conn, [r["id"] for r in rows], settings)
    agents = {a["id"]: a["name"] for a in ctx.conn.query("SELECT id, name FROM users")}
    for p in payments:
        p["agent_name"] = agents.get(p.get("agent_id"), "—")
    return {"payments": payments}


@route("POST", "/api/payments")
def create_payment(ctx):
    need_admin(ctx)
    client = ctx.conn.one("SELECT * FROM clients WHERE id = ?", (ctx.i("client_id"),))
    if not client:
        raise ApiError(404, "Client not found.")
    if not same_company(ctx, client):
        raise ApiError(403, "That client belongs to another company.")
    amount = ctx.f("amount")
    if amount <= 0:
        raise ApiError(400, "Amount must be greater than zero.")
    paid_date = ctx.s("paid_date") or core.today()
    if not core.parse_date(paid_date):
        raise ApiError(400, "Payment date must be in YYYY-MM-DD format.")

    currency = (ctx.s("currency") or client["currency"] or "USD").upper()
    rate = core.fx_rate(ctx.conn, currency)
    amount_usd = core.to_usd(amount, rate)

    new_id = ctx.conn.insert(
        "INSERT INTO payments (client_id, amount, currency, fx_rate, amount_usd, paid_date, "
        "reference, note, voided, recorded_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (client["id"], amount, currency, rate, amount_usd, paid_date,
         ctx.s("reference", maxlen=80), ctx.s("note", maxlen=1000), 0,
         ctx.user["id"], core.now_iso()),
    )
    # a paid client is a won client
    if client["stage"] not in ("Won", "Churned"):
        ctx.conn.execute("UPDATE clients SET stage = 'Won', won_date = ?, updated_at = ? WHERE id = ?",
                         (client.get("won_date") or paid_date, core.now_iso(), client["id"]))
        core.log_event(ctx.conn, client, ctx.user, kind="stage", from_stage=client["stage"],
                       to_stage="Won", body="First payment received.")
    core.log_event(ctx.conn, client, ctx.user, kind="note",
                   body=f"Payment received: {currency} {amount:,.2f} on {paid_date}.")
    core.audit(ctx.conn, ctx.user, "payment.recorded",
               f"{client['name']} {currency} {amount:,.2f} on {paid_date}", ctx.ip,
               target=client["name"],
               meta={"payment_id": new_id, "client_id": client["id"], "amount": amount,
                     "currency": currency, "fx_rate": rate, "amount_usd": amount_usd,
                     "paid_date": paid_date, "reference": ctx.s("reference", maxlen=80)})

    settings = core.get_settings(ctx.conn)
    enriched = core.client_payment_rows(ctx.conn, [client["id"]], settings)
    this = next((p for p in enriched if p["id"] == new_id), None)
    return {"id": new_id, "payment": this}


@route("POST", r"/api/payments/(\d+)/void")
def void_payment(ctx, payment_id):
    need_admin(ctx)
    p = ctx.conn.one("SELECT * FROM payments WHERE id = ?", (int(payment_id),))
    if not p:
        raise ApiError(404, "Payment not found.")
    owner = ctx.conn.one("SELECT * FROM clients WHERE id = ?", (p["client_id"],))
    if owner and not same_company(ctx, owner):
        raise ApiError(403, "That payment belongs to another company.")
    new_state = 0 if p["voided"] else 1
    ctx.conn.execute("UPDATE payments SET voided = ?, note = ? WHERE id = ?",
                     (new_state,
                      (p.get("note") or "") + (f" | voided {core.today()}" if new_state else ""),
                      p["id"]))
    core.audit(ctx.conn, ctx.user, "payment.voided" if new_state else "payment.restored",
               f"#{payment_id}", ctx.ip)
    return {"ok": True, "voided": bool(new_state)}


# ==========================================================================
# COMMISSIONS & PAYOUTS
# ==========================================================================
@route("GET", "/api/commissions")
def commissions(ctx):
    need_auth(ctx)
    settings = core.get_settings(ctx.conn)
    if ctx.user["role"] == "admin":
        sc = scope(ctx)
        if sc is None:
            agents = ctx.conn.query("SELECT * FROM users WHERE role = 'agent' ORDER BY name")
        else:
            agents = ctx.conn.query("SELECT * FROM users WHERE role = 'agent' AND company_id = ? "
                                    "ORDER BY name", (sc,))
    else:
        agents = [ctx.user]
    out = []
    for a in agents:
        summ = core.agent_commission_summary(ctx.conn, a["id"], settings)
        r = core.effective_rules(ctx.conn, a["id"], settings)
        out.append({
            "agent_id": a["id"], "agent_name": a["name"], "country": a.get("country", ""),
            "agent_type": a.get("agent_type", ""),
            "rules": {"first_rate": r[0], "recurring_rate": r[1],
                      "window_months": r[2], "source": r[3]},
            **{k: v for k, v in summ.items() if k != "payments"},
        })
    return {"commissions": out, "rules": {
        "first_rate": float(settings["commission_first_rate"]),
        "recurring_rate": float(settings["commission_recurring_rate"]),
        "window_months": int(float(settings["commission_window_months"])),
    }}


@route("GET", r"/api/admin/agents/(\d+)/reconcile")
def reconcile_agent(ctx, agent_id):
    """Why does this agent's balance look the way it does?"""
    need_admin(ctx)
    agent_id = int(agent_id)
    agent = ctx.conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not agent:
        raise ApiError(404, "Agent not found.")
    if not same_company(ctx, agent):
        raise ApiError(403, "That agent belongs to another company.")

    settings = core.get_settings(ctx.conn)
    summ = core.agent_commission_summary(ctx.conn, agent_id, settings)
    rules = core.effective_rules(ctx.conn, agent_id, settings)
    payouts = ctx.conn.query(
        "SELECT * FROM payouts WHERE agent_id = ? ORDER BY created_at", (agent_id,))

    reasons = []
    if summ["outstanding_usd"] < -0.01:
        voided = ctx.conn.one(
            "SELECT COUNT(*) AS n FROM payments p JOIN clients c ON c.id = p.client_id "
            "WHERE c.agent_id = ? AND p.voided = 1", (agent_id,))
        if int(voided["n"] or 0):
            reasons.append(f"{int(voided['n'])} payment(s) on their clients have been voided "
                           f"since they were paid.")
        ended = ctx.conn.one(
            "SELECT COUNT(*) AS n FROM collaborations WHERE partner_id = ? AND status = 'Ended'",
            (agent_id,))
        if int(ended["n"] or 0):
            reasons.append(f"{int(ended['n'])} partnership(s) they were helping on have ended, "
                           f"so that share no longer counts towards them.")
        if agent.get("first_rate") is not None or agent.get("recurring_rate") is not None:
            reasons.append("Their commission rate has been set individually. If it was lowered "
                           "after a payout, earlier payouts were calculated at the older rate.")
        rate_changes = ctx.conn.query(
            "SELECT created_at, actor, detail FROM audit WHERE action = 'agent.updated' "
            "AND detail LIKE ? ORDER BY id DESC LIMIT 5", (f"%{agent['email']}%",))
        if not reasons:
            reasons.append("Commission was recalculated after a payout was made. Check the "
                           "activity log for changes to this agent or to their clients.")
    else:
        rate_changes = []

    return {
        "agent": public_user(agent),
        "rules": {"first_rate": rules[0], "recurring_rate": rules[1],
                  "window_months": rules[2], "source": rules[3]},
        "position": {k: v for k, v in summ.items() if k != "payments"},
        "overpaid_usd": round(abs(min(0.0, summ["outstanding_usd"])), 2),
        "reasons": reasons,
        "recent_changes": rate_changes,
        "payouts": payouts,
    }


@route("GET", "/api/payouts")
def list_payouts(ctx):
    need_auth(ctx)
    if ctx.user["role"] == "admin":
        sc = scope(ctx)
        if sc is None:
            rows = ctx.conn.query(
                "SELECT p.*, u.name AS agent_name FROM payouts p JOIN users u ON u.id = p.agent_id "
                "ORDER BY p.created_at DESC")
        else:
            rows = ctx.conn.query(
                "SELECT p.*, u.name AS agent_name FROM payouts p JOIN users u ON u.id = p.agent_id "
                "WHERE u.company_id = ? ORDER BY p.created_at DESC", (sc,))
    else:
        rows = ctx.conn.query(
            "SELECT * FROM payouts WHERE agent_id = ? ORDER BY created_at DESC", (ctx.user["id"],))
    return {"payouts": rows}


@route("POST", "/api/payouts")
def create_payout(ctx):
    need_admin(ctx)
    agent_id = ctx.i("agent_id")
    agent = ctx.conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not agent:
        raise ApiError(404, "Agent not found.")
    if not same_company(ctx, agent):
        raise ApiError(403, "That agent belongs to another company.")
    amount = ctx.f("amount_usd")
    if amount <= 0:
        raise ApiError(400, "Payout amount must be greater than zero.")

    settings = core.get_settings(ctx.conn)
    summ = core.agent_commission_summary(ctx.conn, agent_id, settings)
    if amount > summ["outstanding_usd"] + 0.01 and not ctx.body.get("force"):
        if summ["outstanding_usd"] < 0:
            over = abs(summ["outstanding_usd"])
            raise ApiError(400,
                f"{agent['name']} has already been paid ${over:,.2f} more than they have "
                f"earned so far, so there is nothing outstanding. This happens when "
                f"commission is recalculated after a payout — a corrected rate, a voided "
                f"payment, or a partnership that ended. You do not normally need to do "
                f"anything: the ${over:,.2f} is carried forward and comes off their next "
                f"commission automatically. Only override this if you intend to pay them "
                f"anyway and write off the difference.")
        raise ApiError(400,
            f"That is more than {agent['name']} is owed. They are currently owed "
            f"${summ['outstanding_usd']:,.2f}. Reduce the amount, or override if you "
            f"intend to pay more than is earned.")

    new_id = ctx.conn.insert(
        "INSERT INTO payouts (agent_id, amount_usd, period_label, status, reference, note, "
        "paid_date, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (agent_id, round(amount, 2), ctx.s("period_label", maxlen=60), "Pending",
         ctx.s("reference", maxlen=80), ctx.s("note", maxlen=1000), "",
         ctx.user["id"], core.now_iso()),
    )
    core.audit(ctx.conn, ctx.user, "payout.created", f"{agent['name']} ${amount:,.2f}", ctx.ip)
    return {"id": new_id}


@route("PATCH", r"/api/payouts/(\d+)")
def update_payout(ctx, payout_id):
    need_admin(ctx)
    p = ctx.conn.one("SELECT * FROM payouts WHERE id = ?", (int(payout_id),))
    if not p:
        raise ApiError(404, "Payout not found.")
    subject = ctx.conn.one("SELECT * FROM users WHERE id = ?", (p["agent_id"],))
    if subject and not same_company(ctx, subject):
        raise ApiError(403, "That payout belongs to another company.")
    status = ctx.s("status")
    if status not in ("Pending", "Approved", "Paid", "Cancelled"):
        raise ApiError(400, "Unknown payout status.")
    paid_date = ctx.s("paid_date") or (core.today() if status == "Paid" else p.get("paid_date", ""))
    ctx.conn.execute(
        "UPDATE payouts SET status = ?, paid_date = ?, reference = ?, note = ? WHERE id = ?",
        (status, paid_date,
         ctx.s("reference", maxlen=80) or p.get("reference", ""),
         ctx.s("note", maxlen=1000) or p.get("note", ""), p["id"]),
    )
    core.audit(ctx.conn, ctx.user, "payout.status", f"#{payout_id} -> {status}", ctx.ip)
    return {"ok": True}


@route("DELETE", r"/api/payouts/(\d+)")
def delete_payout(ctx, payout_id):
    need_admin(ctx)
    p = ctx.conn.one("SELECT * FROM payouts WHERE id = ?", (int(payout_id),))
    if not p:
        raise ApiError(404, "Payout not found.")
    subject = ctx.conn.one("SELECT * FROM users WHERE id = ?", (p["agent_id"],))
    if subject and not same_company(ctx, subject):
        raise ApiError(403, "That payout belongs to another company.")
    if p["status"] == "Paid":
        raise ApiError(400, "A paid payout cannot be deleted. Cancel it instead so the record survives.")
    ctx.conn.execute("DELETE FROM payouts WHERE id = ?", (p["id"],))
    core.audit(ctx.conn, ctx.user, "payout.deleted", f"#{payout_id}", ctx.ip)
    return {"ok": True}


# ==========================================================================
# AGENT statement
# ==========================================================================
@route("GET", "/api/agent/overview")
def agent_overview(ctx):
    need_auth(ctx)
    settings = core.get_settings(ctx.conn)
    uid = ctx.user["id"]
    summ = core.agent_commission_summary(ctx.conn, uid, settings)
    clients = ctx.conn.query("SELECT * FROM clients WHERE agent_id = ? ORDER BY updated_at DESC", (uid,))
    for c in clients:
        c["monthly_value_usd"] = core.to_usd(c["monthly_value"], core.fx_rate(ctx.conn, c["currency"]))
    won = len([c for c in clients if c["stage"] == "Won"])
    lost = len([c for c in clients if c["stage"] in ("Lost", "Churned")])
    quota = float(ctx.user.get("quota_usd") or 0)
    reviews = ctx.conn.query(
        "SELECT r.*, u.name AS author_name FROM reviews r LEFT JOIN users u ON u.id = r.author_id "
        "WHERE r.agent_id = ? ORDER BY r.created_at DESC", (uid,))
    payouts = ctx.conn.query("SELECT * FROM payouts WHERE agent_id = ? ORDER BY created_at DESC", (uid,))
    my = core.effective_rules(ctx.conn, uid, settings)

    return {
        "metrics": {
            "clients_total": len(clients),
            "clients_won": won,
            "win_rate": round(won / (won + lost), 4) if (won + lost) else None,
            "pipeline_usd": round(sum(c["monthly_value_usd"] for c in clients
                                      if c["stage"] not in ("Won", "Lost", "Churned")), 2),
            "quota_usd": quota,
            "attainment": round(summ["collected_usd"] / quota, 4) if quota > 0 else None,
            **{k: v for k, v in summ.items() if k != "payments"},
        },
        "clients": clients,
        "payments": summ["payments"],
        "reviews": reviews,
        "payouts": payouts,
        "rules": {"first_rate": my[0], "recurring_rate": my[1],
                  "window_months": my[2], "source": my[3]},
    }


# ==========================================================================
# SETTINGS, FX, AUDIT, BACKUP
# ==========================================================================
@route("GET", "/api/admin/settings")
def get_settings_ep(ctx):
    need_admin(ctx)
    rates, rows = core.get_fx(ctx.conn)
    return {"settings": core.get_settings(ctx.conn), "fx": rows}


@route("PUT", "/api/admin/settings")
def put_settings(ctx):
    need_admin(ctx)
    allowed = {"company_name", "portal_name", "support_email", "base_currency",
               "commission_first_rate", "commission_recurring_rate",
               "commission_window_months", "fx_auto", "fx_interval_hours"}
    changed = []
    for key, value in (ctx.body.get("settings") or {}).items():
        if key not in allowed:
            continue
        if key in ("commission_first_rate", "commission_recurring_rate"):
            try:
                v = float(value)
            except (TypeError, ValueError):
                raise ApiError(400, f"{key} must be a number between 0 and 1.")
            if not 0 <= v <= 1:
                raise ApiError(400, "Commission rates are fractions between 0 and 1 (0.60 = 60%).")
            value = f"{v:.4f}"
        if key == "fx_auto":
            value = "1" if str(value) in ("1", "true", "True", "on") else "0"
        if key == "fx_interval_hours":
            try:
                v = int(float(value))
            except (TypeError, ValueError):
                raise ApiError(400, "Refresh interval must be a whole number of hours.")
            if not 1 <= v <= 168:
                raise ApiError(400, "Refresh interval must be between 1 and 168 hours.")
            value = str(v)
        if key == "commission_window_months":
            try:
                v = int(float(value))
            except (TypeError, ValueError):
                raise ApiError(400, "Window must be a whole number of months.")
            if not 1 <= v <= 120:
                raise ApiError(400, "Window must be between 1 and 120 months.")
            value = str(v)
        core.set_setting(ctx.conn, key, value)
        changed.append(key)
    if changed:
        core.audit(ctx.conn, ctx.user, "settings.updated", ", ".join(changed), ctx.ip)
    return {"ok": True, "changed": changed}


@route("PUT", "/api/admin/fx")
def put_fx(ctx):
    need_admin(ctx)
    updated = []
    for item in (ctx.body.get("rates") or []):
        code = str(item.get("code", "")).upper()[:8]
        try:
            rate = float(item.get("rate", 0))
        except (TypeError, ValueError):
            continue
        if not code or rate <= 0:
            continue
        if code == "USD":
            rate = 1.0
        exists = ctx.conn.one("SELECT code FROM fx WHERE code = ?", (code,))
        if exists:
            ctx.conn.execute("UPDATE fx SET rate = ?, updated_at = ? WHERE code = ?",
                             (rate, core.now_iso(), code))
        else:
            ctx.conn.execute("INSERT INTO fx (code, rate, label, updated_at) VALUES (?,?,?,?)",
                             (code, rate, str(item.get("label", ""))[:60], core.now_iso()))
        updated.append(code)
    if updated:
        core.audit(ctx.conn, ctx.user, "fx.updated", ", ".join(updated), ctx.ip)
    return {"ok": True, "updated": updated}


@route("POST", "/api/admin/fx/sync")
def fx_sync_now(ctx):
    need_admin(ctx)
    report = core.sync_fx(ctx.conn, ctx.user, ctx.ip)
    if not report["ok"]:
        raise ApiError(502, report["error"])
    return report


@route("GET", "/api/admin/fx/status")
def fx_status(ctx):
    need_admin(ctx)
    s = core.get_settings(ctx.conn)
    return {
        "key_present": bool(core.fx_api_key()),
        "auto": s.get("fx_auto") == "1",
        "interval_hours": int(float(s.get("fx_interval_hours", 12) or 12)),
        "last_sync": s.get("fx_last_sync", ""),
        "last_status": s.get("fx_last_status", "never run"),
        "provider": "ExchangeRate-API",
    }


@route("GET", "/api/fx")
def public_fx(ctx):
    need_auth(ctx)
    rates, rows = core.get_fx(ctx.conn)
    return {"fx": rows}


@route("GET", "/api/admin/audit")
def audit_log(ctx):
    need_admin(ctx)
    where, params = [], []

    sc = scope(ctx)
    if sc is not None:
        where.append("company_id = ?")
        params.append(sc)

    actor = ctx.q("actor")
    if actor:
        where.append("actor = ?")
        params.append(actor)

    action = ctx.q("action")
    if action:
        if action.endswith("*"):                      # e.g. "payment*"
            where.append("action LIKE ?")
            params.append(action[:-1] + "%")
        else:
            where.append("action = ?")
            params.append(action)

    date_from = ctx.q("from")
    if date_from:
        where.append("created_at >= ?")
        params.append(date_from[:10] + " 00:00:00")

    date_to = ctx.q("to")
    if date_to:
        where.append("created_at <= ?")
        params.append(date_to[:10] + " 23:59:59")

    search = ctx.q("q")
    if search:
        where.append("(LOWER(detail) LIKE ? OR LOWER(actor) LIKE ? OR LOWER(action) LIKE ?)")
        needle = "%" + search.lower() + "%"
        params.extend([needle, needle, needle])

    try:
        limit = max(1, min(1000, int(ctx.q("limit", "300"))))
    except (TypeError, ValueError):
        limit = 300

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = ctx.conn.query(
        f"SELECT * FROM audit{clause} ORDER BY id DESC LIMIT {limit}", tuple(params))
    total = ctx.conn.one(f"SELECT COUNT(*) AS n FROM audit{clause}", tuple(params))

    # Distinct values so the UI can offer real choices rather than free text.
    scope_clause = " WHERE company_id = ?" if sc is not None else ""
    scope_params = (sc,) if sc is not None else ()
    actors = [r["actor"] for r in ctx.conn.query(
        f"SELECT DISTINCT actor FROM audit{scope_clause or ' WHERE 1=1'} AND actor <> '' "
        f"ORDER BY actor" if sc is not None else
        "SELECT DISTINCT actor FROM audit WHERE actor <> '' ORDER BY actor", scope_params)]
    actions = [r["action"] for r in ctx.conn.query(
        f"SELECT DISTINCT action FROM audit{scope_clause or ' WHERE 1=1'} AND action <> '' "
        f"ORDER BY action" if sc is not None else
        "SELECT DISTINCT action FROM audit WHERE action <> '' ORDER BY action", scope_params)]

    return {"audit": rows, "matched": int(total["n"] or 0), "limit": limit,
            "actors": actors, "actions": actions, "can_drill": bool(ctx.user.get("is_super"))}


@route("GET", r"/api/admin/audit/(\d+)")
def audit_detail(ctx, entry_id):
    """Full detail behind one log line. Super users only — it can reveal
    activity across every company on the platform."""
    need_super(ctx)
    row = ctx.conn.one("SELECT * FROM audit WHERE id = ?", (int(entry_id),))
    if not row:
        raise ApiError(404, "Log entry not found.")

    meta = {}
    if row.get("meta"):
        try:
            meta = json.loads(row["meta"])
        except (ValueError, TypeError):
            meta = {"raw": row["meta"]}

    actor = ctx.conn.one("SELECT id, name, email, role, status, company_id, is_super "
                         "FROM users WHERE id = ?", (row.get("user_id") or 0,))
    company = None
    if row.get("company_id"):
        company = ctx.conn.one("SELECT id, name FROM companies WHERE id = ?", (row["company_id"],))

    # Surrounding activity gives the entry context — what led to it, what followed.
    nearby = ctx.conn.query(
        "SELECT id, created_at, actor, action, detail FROM audit "
        "WHERE actor = ? AND id <> ? AND id BETWEEN ? AND ? ORDER BY id DESC LIMIT 8",
        (row["actor"], row["id"], row["id"] - 25, row["id"] + 25))

    return {"entry": row, "meta": meta, "actor": actor, "company": company, "nearby": nearby}


# ==========================================================================
# REPORTS — CSV downloads, scoped to the caller's company
# ==========================================================================
REPORT_TYPES = {
    "commission": "Commission by agent",
    "payments": "Payments register",
    "pipeline": "Client pipeline",
    "payouts": "Payout history",
    "agents": "Agent performance",
    "activity": "Client activity trail",
}


def _csv(rows, headers):
    def cell(v):
        if v is None:
            return ""
        text = str(v)
        # A leading =, +, - or @ turns a CSV cell into a formula in Excel;
        # prefixing an apostrophe stops a client name being executed.
        if text[:1] in ("=", "+", "-", "@"):
            text = "'" + text
        return '"' + text.replace('"', '""') + '"'
    out = [",".join(cell(h) for h in headers)]
    for r in rows:
        out.append(",".join(cell(r.get(h)) for h in headers))
    return "\n".join(out)


@route("GET", "/api/reports")
def report_meta(ctx):
    need_auth(ctx)
    return {"types": [{"key": k, "label": v} for k, v in REPORT_TYPES.items()]}


@route("POST", "/api/reports/build")
def build_report(ctx):
    """Returns CSV text plus a filename; the browser saves it."""
    need_auth(ctx)
    kind = ctx.s("kind")
    if kind not in REPORT_TYPES:
        raise ApiError(400, "Unknown report.")
    frm, to = ctx.s("from")[:10], ctx.s("to")[:10]
    for label, value in (("From", frm), ("To", to)):
        if value and not core.parse_date(value):
            raise ApiError(400, f"{label} date must be YYYY-MM-DD.")

    conn, settings = ctx.conn, core.get_settings(ctx.conn)
    is_admin = ctx.user["role"] == "admin"
    sc = scope(ctx)

    if is_admin:
        agents = conn.query("SELECT * FROM users WHERE role = 'agent' ORDER BY name") \
            if sc is None else conn.query(
                "SELECT * FROM users WHERE role = 'agent' AND company_id = ? ORDER BY name", (sc,))
        client_rows = conn.query("SELECT * FROM clients") if sc is None else \
            conn.query("SELECT * FROM clients WHERE company_id = ?", (sc,))
    else:
        agents = [ctx.user]
        client_rows = conn.query("SELECT * FROM clients WHERE agent_id = ?", (ctx.user["id"],))

    names = {u["id"]: u["name"] for u in conn.query("SELECT id, name FROM users")}
    ids = [c["id"] for c in client_rows]
    payments = core.client_payment_rows(conn, ids, settings, newest_first=False)
    core.split_payments(payments, core.active_collaborations(conn, ids))
    if frm:
        payments = [p for p in payments if str(p["paid_date"])[:10] >= frm]
    if to:
        payments = [p for p in payments if str(p["paid_date"])[:10] <= to]
    client_names = {c["id"]: c["name"] for c in client_rows}

    if kind == "commission":
        headers = ["Agent", "Country", "Type", "Clients won", "Collected (USD)",
                   "First-payment (USD)", "Monthly (USD)", "Total earned (USD)",
                   "Paid out (USD)", "Outstanding (USD)", "First rate", "Monthly rate",
                   "Window (months)", "Rate source"]
        rows = []
        for a in agents:
            summ = core.agent_commission_summary(conn, a["id"], settings)
            r = core.effective_rules(conn, a["id"], settings)
            won = conn.one("SELECT COUNT(*) AS n FROM clients WHERE agent_id = ? "
                           "AND stage = 'Won'", (a["id"],))
            rows.append({"Agent": a["name"], "Country": a.get("country", ""),
                         "Type": a.get("agent_type", ""), "Clients won": int(won["n"] or 0),
                         "Collected (USD)": summ["collected_usd"],
                         "First-payment (USD)": summ["first_payment_usd"],
                         "Monthly (USD)": summ["recurring_usd"],
                         "Total earned (USD)": summ["earned_usd"],
                         "Paid out (USD)": summ["paid_out_usd"],
                         "Outstanding (USD)": summ["outstanding_usd"],
                         "First rate": r[0], "Monthly rate": r[1],
                         "Window (months)": r[2], "Rate source": r[3]})

    elif kind == "payments":
        headers = ["Date", "Client", "Lead agent", "Amount", "Currency", "FX rate",
                   "Amount (USD)", "Commission type", "Rate", "Agent total (USD)",
                   "Lead share (USD)", "Partner", "Partner share (USD)",
                   "StatsPack (USD)", "Reference", "Voided"]
        rows = [{"Date": str(p["paid_date"])[:10], "Client": p.get("client_name", ""),
                 "Lead agent": names.get(p.get("agent_id"), ""),
                 "Amount": p["amount"], "Currency": p["currency"], "FX rate": p["fx_rate"],
                 "Amount (USD)": p["amount_usd"], "Commission type": p["commission_kind"],
                 "Rate": p["commission_rate"], "Agent total (USD)": p["agent_commission_usd"],
                 "Lead share (USD)": p.get("owner_commission_usd"),
                 "Partner": names.get(p.get("partner_id"), ""),
                 "Partner share (USD)": p.get("partner_commission_usd"),
                 "StatsPack (USD)": p["statspack_share_usd"],
                 "Reference": p.get("reference", ""),
                 "Voided": "Yes" if p.get("voided") else "No"} for p in payments]

    elif kind == "pipeline":
        headers = ["Client", "Agent", "Stage", "Industry", "Country", "Product",
                   "Monthly value", "Currency", "Monthly value (USD)", "Payments",
                   "Collected (USD)", "Won on", "Created"]
        rows = []
        for c in client_rows:
            paid = conn.one("SELECT COUNT(*) AS n, COALESCE(SUM(amount_usd),0) AS t "
                            "FROM payments WHERE client_id = ? AND voided = 0", (c["id"],))
            rows.append({"Client": c["name"], "Agent": names.get(c["agent_id"], ""),
                         "Stage": c["stage"], "Industry": c.get("industry", ""),
                         "Country": c.get("country", ""), "Product": c.get("product", ""),
                         "Monthly value": c["monthly_value"], "Currency": c["currency"],
                         "Monthly value (USD)": core.to_usd(
                             c["monthly_value"], core.fx_rate(conn, c["currency"])),
                         "Payments": int(paid["n"] or 0),
                         "Collected (USD)": round(float(paid["t"] or 0), 2),
                         "Won on": c.get("won_date", ""), "Created": str(c["created_at"])[:10]})

    elif kind == "payouts":
        agent_ids = [a["id"] for a in agents] or [0]
        marks = ",".join("?" for _ in agent_ids)
        pay = conn.query(f"SELECT * FROM payouts WHERE agent_id IN ({marks}) "
                         f"ORDER BY created_at", tuple(agent_ids))
        if frm:
            pay = [p for p in pay if str(p["created_at"])[:10] >= frm]
        if to:
            pay = [p for p in pay if str(p["created_at"])[:10] <= to]
        headers = ["Created", "Agent", "Period", "Amount (USD)", "Status", "Paid on",
                   "Reference", "Note"]
        rows = [{"Created": str(p["created_at"])[:10], "Agent": names.get(p["agent_id"], ""),
                 "Period": p.get("period_label", ""), "Amount (USD)": p["amount_usd"],
                 "Status": p["status"], "Paid on": p.get("paid_date", ""),
                 "Reference": p.get("reference", ""), "Note": p.get("note", "")} for p in pay]

    elif kind == "agents":
        headers = ["Agent", "Email", "Type", "Country", "Status", "Started", "Quota (USD)",
                   "Clients", "Won", "Lost", "Win rate", "Collected (USD)",
                   "Attainment", "Partnerships"]
        rows = []
        for a in agents:
            cl = conn.query("SELECT stage FROM clients WHERE agent_id = ?", (a["id"],))
            won = len([c for c in cl if c["stage"] == "Won"])
            lost = len([c for c in cl if c["stage"] in ("Lost", "Churned")])
            summ = core.agent_commission_summary(conn, a["id"], settings)
            quota = float(a.get("quota_usd") or 0)
            helped = conn.one("SELECT COUNT(*) AS n FROM collaborations WHERE "
                              "(owner_id = ? OR partner_id = ?) AND status = 'Accepted'",
                              (a["id"], a["id"]))
            rows.append({"Agent": a["name"], "Email": a["email"],
                         "Type": a.get("agent_type", ""), "Country": a.get("country", ""),
                         "Status": a["status"], "Started": a.get("start_date", ""),
                         "Quota (USD)": quota, "Clients": len(cl), "Won": won, "Lost": lost,
                         "Win rate": round(won / (won + lost), 4) if (won + lost) else "",
                         "Collected (USD)": summ["collected_usd"],
                         "Attainment": round(summ["collected_usd"] / quota, 4) if quota else "",
                         "Partnerships": int(helped["n"] or 0)})

    else:  # activity
        headers = ["When", "Client", "Agent", "Type", "From", "To", "Notes",
                   "Next step", "Due"]
        rows = []
        for cid in ids:
            for e in core.client_timeline(conn, cid):
                when = str(e["created_at"])[:10]
                if (frm and when < frm) or (to and when > to):
                    continue
                rows.append({"When": e["created_at"], "Client": client_names.get(cid, ""),
                             "Agent": e.get("author_name", ""), "Type": e["kind"],
                             "From": e.get("from_stage", ""), "To": e.get("to_stage", ""),
                             "Notes": e.get("body", ""), "Next step": e.get("next_step", ""),
                             "Due": e.get("due_date", "")})
        rows.sort(key=lambda r: r["When"], reverse=True)

    span = f"_{frm or 'start'}_to_{to or 'today'}" if (frm or to) else ""
    filename = f"statspack_{kind}{span}_{core.today()}.csv"
    core.audit(ctx.conn, ctx.user, "report.downloaded",
               f"{REPORT_TYPES[kind]} ({len(rows)} rows)", ctx.ip,
               meta={"kind": kind, "rows": len(rows), "from": frm, "to": to})
    return {"filename": filename, "csv": _csv(rows, headers), "rows": len(rows),
            "label": REPORT_TYPES[kind]}


@route("GET", "/api/admin/backup")
def backup(ctx):
    need_admin(ctx)
    out = {"exported_at": core.now_iso(), "version": VERSION, "engine": db.engine_name()}
    for table in ("companies", "users", "clients", "client_events", "payments",
                  "payouts", "reviews", "collaborations", "incidents", "fx", "settings"):
        rows = ctx.conn.query(f"SELECT * FROM {table}")
        if table == "users":
            for r in rows:
                r.pop("password_hash", None)
        out[table] = rows
    core.audit(ctx.conn, ctx.user, "backup.exported", "", ctx.ip)
    return out


# ==========================================================================
# SYSTEM HEALTH — storage, standby mirror, incidents
# ==========================================================================
@route("GET", "/api/admin/system")
def system_health(ctx):
    need_super(ctx)
    primary = db.storage_stats(ctx.conn)
    standby = mirror.check_mirror_health()
    st = mirror.state()

    # Warn before the free tier fills up, rather than when writes start failing.
    for label, stats in (("primary", primary), ("standby", standby)):
        pct = stats.get("pct")
        if pct is not None and pct >= 80:
            mirror.record_incident(
                ctx.conn, f"storage.{label}",
                "error" if pct >= 92 else "warning",
                f"{label.title()} database is {pct:.0f}% full",
                f"{stats.get('used_mb')} MB of {stats.get('quota_mb')} MB used.")
        elif pct is not None:
            mirror.resolve_incident(ctx.conn, f"storage.{label}")

    if standby.get("configured") and st.get("last_success"):
        try:
            last = datetime.strptime(st["last_success"], "%Y-%m-%d %H:%M:%S")
            age_min = (datetime.utcnow() - last).total_seconds() / 60
            st["age_minutes"] = round(age_min, 1)
            limit = max(30, st["interval_minutes"] * 3)
            if age_min > limit:
                mirror.record_incident(
                    ctx.conn, "mirror.stale", "warning",
                    "Standby copy is out of date",
                    f"Last successful copy was {int(age_min)} minutes ago; it should run "
                    f"every {st['interval_minutes']} minutes. If the primary failed now you "
                    f"would lose everything recorded since then.")
            else:
                mirror.resolve_incident(ctx.conn, "mirror.stale")
        except ValueError:
            pass
    elif standby.get("configured"):
        mirror.record_incident(ctx.conn, "mirror.stale", "warning",
                               "Standby has never been copied to",
                               "Press 'Copy to standby now' to take the first copy.")

    if standby.get("configured") and not standby.get("reachable"):
        mirror.record_incident(ctx.conn, "standby.unreachable", "error",
                               "Standby database cannot be reached",
                               standby.get("error") or "No response.")
    elif standby.get("configured"):
        mirror.resolve_incident(ctx.conn, "standby.unreachable")

    drift = None
    if standby.get("reachable"):
        try:
            live = {t: ctx.conn.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
                    for t in ("users", "clients", "payments")}
            drift = {t: {"primary": int(live[t] or 0), "standby": standby["rows"].get(t)}
                     for t in live}
        except Exception:
            drift = None

    ka = db.keepalive_state()
    if ka["enabled"] and not ka["within_quota"]:
        mirror.record_incident(
            ctx.conn, "keepalive.quota", "warning",
            "Keep-alive window may exhaust the free compute allowance",
            f"The current window works out at about {ka['cu_hours_per_month']} CU-hours a "
            f"month against a quota of {ka['quota_cu_hours']}. If the quota runs out the "
            f"database is suspended until the next billing period. Narrow the hours.")
    else:
        mirror.resolve_incident(ctx.conn, "keepalive.quota")

    return {"primary": primary, "standby": standby, "mirror": st, "drift": drift,
            "keepalive": ka,
            "incidents": mirror.open_incidents(ctx.conn),
            "history": mirror.all_incidents(ctx.conn, 40)}


@route("POST", "/api/admin/system/mirror")
def run_mirror_now(ctx):
    need_super(ctx)
    report = mirror.run_once("manual", force=bool(ctx.body.get("force")))
    core.audit(ctx.conn, ctx.user, "mirror.run",
               "copied %s rows" % report.get("rows") if report.get("ok")
               else "failed: %s" % report.get("error"), ctx.ip, meta=report)
    if not report.get("ok"):
        raise ApiError(502, report.get("error") or "Mirror failed.")
    return report


@route("POST", r"/api/admin/incidents/(\d+)/resolve")
def resolve_incident_ep(ctx, incident_id):
    need_super(ctx)
    row = ctx.conn.one("SELECT * FROM incidents WHERE id = ?", (int(incident_id),))
    if not row:
        raise ApiError(404, "Incident not found.")
    ctx.conn.execute("UPDATE incidents SET resolved = 1, resolved_at = ? WHERE id = ?",
                     (core.now_iso(), row["id"]))
    core.audit(ctx.conn, ctx.user, "incident.resolved", row["title"], ctx.ip)
    return {"ok": True}


@route("GET", "/api/admin/alerts")
def alerts(ctx):
    """Small, cheap poll so the banner can appear without loading a whole page."""
    need_admin(ctx)
    if not ctx.user.get("is_super"):
        return {"incidents": [], "count": 0}
    rows = mirror.open_incidents(ctx.conn)
    return {"incidents": rows, "count": len(rows)}


@route("GET", "/api/health")
def health(ctx):
    st = mirror.state()
    return {"ok": True, "version": VERSION, "engine": db.engine_name(),
            "time": core.now_iso(),
            "mirror": {"enabled": st["enabled"], "last_success": st["last_success"],
                       "failures": st["failures"]}}


# ==========================================================================
# HTTP plumbing
# ==========================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "StatsPackPortal/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers
    def client_ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()[:60]
        return self.client_address[0] if self.client_address else ""

    def send_json(self, status, payload):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type=None):
        if not os.path.isfile(path):
            self.send_json(404, {"error": "Not found"})
            return
        with open(path, "rb") as fh:
            body = fh.read()
        ctype = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if path.endswith((".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp")):
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > 2_000_000:
            raise ApiError(413, "Request too large.")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Request body must be valid JSON.")

    def bearer(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return ""

    # ---- dispatch
    def dispatch(self, method):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path.startswith("/api/"):
            return self.handle_api(method, path, query)
        if method != "GET":
            return self.send_json(405, {"error": "Method not allowed"})
        return self.handle_static(path)

    def handle_static(self, path):
        if path in ("/", "/index.html", "/dashboard"):
            return self.send_file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
        if path in ("/login", "/login.html"):
            return self.send_file(os.path.join(STATIC, "login.html"), "text/html; charset=utf-8")
        # brand images live at the project root, like your other StatsPack apps
        if path in ("/logo.png", "/login.png", "/favicon.ico", "/icon-192.png",
                    "/apple-touch-icon.png"):
            candidate = os.path.join(ROOT, path.lstrip("/"))
            if os.path.isfile(candidate):
                return self.send_file(candidate)
            return self.send_json(404, {"error": "Not found"})
        if path == "/manifest.json":
            return self.send_file(os.path.join(STATIC, "manifest.json"), "application/json")

        safe = os.path.normpath(path).lstrip("/\\")
        candidate = os.path.normpath(os.path.join(STATIC, safe))
        if not candidate.startswith(STATIC):
            return self.send_json(403, {"error": "Forbidden"})
        if os.path.isfile(candidate):
            return self.send_file(candidate)
        # unknown route -> the app shell decides what to show
        return self.send_file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")

    def run_route(self, fn, match, method, path, body, query):
        """Execute one route inside a transaction.

        Serverless Postgres suspends when idle and drops its sockets, so the
        first request after a quiet spell can arrive on a dead connection.
        That failure happens before anything commits, so retrying is safe and
        cannot double-write. Without this the user sees a server error and
        succeeds on their second click, which is exactly what was reported.
        """
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                with db.connection() as conn:
                    user = core.user_for_token(conn, self.bearer()) if self.bearer() else None
                    ctx = Ctx(conn, user, body, query, self.client_ip())
                    if user and user.get("must_change_pw") and path not in (
                            "/api/me", "/api/change-password", "/api/logout", "/api/health"):
                        raise ApiError(428, "You must set a new password before continuing.")
                    if path == "/api/logout" and self.bearer():
                        core.destroy_session(conn, self.bearer())
                    return fn(ctx, *match.groups())
            except db.ConnectionLost as e:
                if "checked moments ago" in str(e):
                    raise ApiError(503,
                        "The database is not responding. If it is only waking up this will "
                        "clear in a few seconds — otherwise contact your StatsPack "
                        "administrator.")
                if attempt == attempts:
                    print(f"  database unreachable after {attempts} attempts: {e}")
                    raise ApiError(503,
                        "The database is not responding. If it is only waking up this will "
                        "clear in a few seconds — otherwise contact your StatsPack "
                        "administrator.")
                time.sleep(0.35 * attempt)          # brief backoff, then retry
                continue
        raise ApiError(503, "The database is unavailable.")

    def handle_api(self, method, path, query):
        try:
            body = self.read_body() if method in ("POST", "PUT", "PATCH", "DELETE") else {}

            if path == "/api/health" and method == "GET":
                alive, why = db.reachable()
                st = mirror.state()
                payload = {"ok": alive, "version": VERSION, "engine": db.engine_name(),
                           "time": core.now_iso(),
                           "database": "reachable" if alive else "UNREACHABLE",
                           "mirror": {"enabled": st["enabled"],
                                      "last_success": st["last_success"],
                                      "failures": st["failures"]}}
                if not alive:
                    payload["detail"] = why
                    payload["advice"] = ("The primary database is down. The standby copy is "
                                         "not promoted automatically — repoint DATABASE_URL "
                                         "to it and redeploy if this persists.")
                return self.send_json(200 if alive else 503, payload)

            for m, rx, fn in ROUTES:
                if m != method:
                    continue
                match = rx.match(path)
                if not match:
                    continue
                result = self.run_route(fn, match, method, path, body, query)
                return self.send_json(200, result)

            return self.send_json(404, {"error": "Unknown endpoint"})

        except ApiError as e:
            return self.send_json(e.status, {"error": e.message})
        except Exception:
            traceback.print_exc()
            return self.send_json(500, {"error": "Something went wrong on the server."})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")


def main():
    # The server must start even when the database is unreachable. If it exits
    # here instead, Render crash-loops, /api/health never answers, and nobody
    # gets told what is wrong.
    created = None
    db_ready = False
    try:
        db.init_db()
        with db.connection() as conn:
            created = core.bootstrap(conn)
        db_ready = True
    except Exception as e:
        print("!" * 66)
        print(" DATABASE UNREACHABLE AT STARTUP")
        print(f"   {str(e)[:300]}")
        print("   Serving 503 on every page. /api/health will report the outage.")
        print("   Check DATABASE_URL, or repoint it at the standby copy.")
        print("!" * 66)

    print("=" * 66)
    print(f" StatsPack Tech Sales Agent Portal {VERSION}")
    print(f" storage : {db.engine_name()}{'' if db_ready else '  [UNREACHABLE]'}")
    print(f" listening on http://0.0.0.0:{PORT}")
    if created:
        print("-" * 66)
        print(f" FIRST ADMIN CREATED")
        print(f"   email    : {created['email']}")
        print(f"   password : {created['password']}")
        if created["generated"]:
            print("   (generated because ADMIN_PASSWORD was not set — copy it now,")
            print("    it will not be shown again. You must change it at first sign-in.)")
    print("=" * 66)

    db.start_keepalive()
    _ka = db.keepalive_state()
    if _ka["enabled"]:
        print(f" keepalive: {_ka['window']} days {_ka['days']} (UTC+{_ka['tz_offset']:g}) "
              f"~{_ka['cu_hours_per_month']} of {_ka['quota_cu_hours']} CU-hours/month "
              f"({_ka['quota_pct']:.0f}%)")
        if not _ka["within_quota"]:
            print("           WARNING: this window may exhaust the free allowance, which")
            print("           suspends the database until the next billing period.")
    else:
        print(" keepalive: off")

    if mirror.SELF_MIRROR:
        print(" mirror  : DISABLED — MIRROR_DATABASE_URL is the same database as "
              "DATABASE_URL.")
        print("           Point it at the other database, or the backup would be "
              "overwritten.")
    elif mirror.MIRROR_ENABLED:
        mirror.start_worker()
        print(f" mirror  : {mirror._safe_host()} every {mirror.MIRROR_INTERVAL_MIN} min")
    else:
        print(" mirror  : off (set MIRROR_DATABASE_URL to enable)")

    if core.fx_api_key():
        core.start_fx_worker()
        print(" fx sync : ExchangeRate-API key detected, auto-refresh on")
    else:
        print(" fx sync : off (set EXCHANGERATE_API_KEY to enable)")

    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()


if __name__ == "__main__":
    main()
