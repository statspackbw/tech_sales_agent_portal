#!/usr/bin/env python3
"""End-to-end tests. Run against a live server: python3 test_portal.py [base_url]"""
import json
import sys
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8470"
ADMIN_EMAIL = "admin@statspack.co.ls"
ADMIN_PW = "TestAdmin!2026"

PASS = FAIL = 0


def ok(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label} {extra}")


def call(method, path, body=None, token=None, expect=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=25) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


print("=" * 68)
print("StatsPack Tech Sales Agent Portal — end-to-end tests")
print("=" * 68)

# ---------------------------------------------------------------- auth
print("\n[ AUTHENTICATION ]")
st, d = call("POST", "/api/login", {"email": ADMIN_EMAIL, "password": "wrong-password"})
ok("bad password rejected", st == 401, st)

st, d = call("POST", "/api/login", {"email": ADMIN_EMAIL, "password": ADMIN_PW})
ok("admin signs in", st == 200 and "token" in d, d)
ADMIN = d.get("token")

st, d = call("GET", "/api/admin/overview")
ok("no token is refused", st == 401, st)

st, d = call("GET", "/api/admin/overview", token="garbage-token")
ok("invalid token is refused", st == 401, st)

st, d = call("GET", "/api/me", token=ADMIN)
ok("admin identity correct", d.get("user", {}).get("role") == "admin", d)

# ---------------------------------------------------------------- agents
print("\n[ AGENTS ]")
st, d = call("POST", "/api/admin/agents",
             {"name": "Thabo Mokoena", "email": "thabo@statspack.co.ls",
              "country": "Lesotho", "quota_usd": 60000}, ADMIN)
ok("create agent", st == 200 and d.get("id"), d)
AGENT_ID = d.get("id")
AGENT_TEMP_PW = d.get("temp_password")
ok("temporary password issued", bool(AGENT_TEMP_PW))

st, d = call("POST", "/api/admin/agents",
             {"name": "Duplicate", "email": "thabo@statspack.co.ls"}, ADMIN)
ok("duplicate email rejected", st == 409, st)

st, d = call("POST", "/api/admin/agents", {"name": "Bad", "email": "not-an-email"}, ADMIN)
ok("invalid email rejected", st == 400, st)

st, d = call("POST", "/api/admin/agents",
             {"name": "Naledi Dlamini", "email": "naledi@statspack.co.ls",
              "country": "South Africa", "quota_usd": 120000}, ADMIN)
AGENT2_ID = d.get("id")
AGENT2_TEMP_PW = d.get("temp_password")
ok("create second agent", st == 200)

# agent must change password before doing anything
st, d = call("POST", "/api/login", {"email": "thabo@statspack.co.ls", "password": AGENT_TEMP_PW})
ok("agent signs in with temp password", st == 200, d)
AGENT = d.get("token")

st, d = call("GET", "/api/clients", token=AGENT)
ok("forced password change blocks other endpoints", st == 428, st)

st, d = call("POST", "/api/change-password",
             {"current_password": AGENT_TEMP_PW, "new_password": "short"}, AGENT)
ok("short password rejected", st == 400, st)

st, d = call("POST", "/api/change-password",
             {"current_password": "wrong", "new_password": "ThaboSells!2026"}, AGENT)
ok("wrong current password rejected", st == 400, st)

st, d = call("POST", "/api/change-password",
             {"current_password": AGENT_TEMP_PW, "new_password": "ThaboSells!2026"}, AGENT)
ok("agent sets own password", st == 200, d)

st, d = call("GET", "/api/clients", token=AGENT)
ok("agent unblocked after password change", st == 200, st)

st, d = call("POST", "/api/login", {"email": "naledi@statspack.co.ls", "password": AGENT2_TEMP_PW})
AGENT2 = d.get("token")
call("POST", "/api/change-password",
     {"current_password": AGENT2_TEMP_PW, "new_password": "NalediSells!2026"}, AGENT2)

# ---------------------------------------------------------------- permissions
print("\n[ PERMISSIONS ]")
st, d = call("GET", "/api/admin/overview", token=AGENT)
ok("agent cannot see admin overview", st == 403, st)

st, d = call("GET", "/api/admin/agents", token=AGENT)
ok("agent cannot list agents", st == 403, st)

st, d = call("POST", "/api/admin/agents", {"name": "Sneaky", "email": "s@x.com"}, AGENT)
ok("agent cannot create agents", st == 403, st)

st, d = call("GET", "/api/admin/settings", token=AGENT)
ok("agent cannot read settings", st == 403, st)

# ---------------------------------------------------------------- clients
print("\n[ CLIENTS ]")
st, d = call("POST", "/api/clients",
             {"name": "Lesotho Revenue Authority", "country": "Lesotho",
              "currency": "LSL", "monthly_value": 18000, "stage": "Proposal",
              "product": "SmartRegister"}, AGENT)
ok("agent creates own client", st == 200, d)
CLIENT_A = d.get("id")

st, d = call("POST", "/api/clients",
             {"name": "Cape Retail Group", "country": "South Africa",
              "currency": "ZAR", "monthly_value": 36000, "stage": "Demo"}, AGENT2)
CLIENT_B = d.get("id")
ok("second agent creates own client", st == 200)

st, d = call("GET", "/api/clients/%s" % CLIENT_B, token=AGENT)
ok("agent cannot read another agent's client", st == 403, st)

st, d = call("PATCH", "/api/clients/%s" % CLIENT_B, {"name": "Hijacked"}, AGENT)
ok("agent cannot edit another agent's client", st == 403, st)

st, d = call("GET", "/api/clients", token=AGENT)
names = [c["name"] for c in d.get("clients", [])]
ok("agent sees only own clients", names == ["Lesotho Revenue Authority"], names)

st, d = call("GET", "/api/clients", token=ADMIN)
ok("admin sees all clients", len(d.get("clients", [])) == 2, len(d.get("clients", [])))

st, d = call("POST", "/api/clients", {"name": "Bad currency", "currency": "XXX"}, AGENT)
ok("unknown currency rejected", st == 400, st)

st, d = call("POST", "/api/clients", {"name": "Bad stage", "stage": "Nonsense"}, AGENT)
ok("unknown stage rejected", st == 400, st)

# ---------------------------------------------------------------- payments
print("\n[ PAYMENTS — agents may not record them ]")
st, d = call("POST", "/api/payments",
             {"client_id": CLIENT_A, "amount": 18000, "paid_date": "2026-01-15"}, AGENT)
ok("agent cannot record a payment", st == 403, st)

st, d = call("POST", "/api/payments",
             {"client_id": CLIENT_A, "amount": -500, "paid_date": "2026-01-15"}, ADMIN)
ok("negative amount rejected", st == 400, st)

st, d = call("POST", "/api/payments",
             {"client_id": CLIENT_A, "amount": 100, "paid_date": "15/01/2026"}, ADMIN)
ok("malformed date rejected", st == 400, st)

# ------------------------------------------------- THE COMMISSION ENGINE
print("\n[ COMMISSION ENGINE — 60% first, 10% for 12 months, then nil ]")
# LSL rate is 18.0, so 18,000 LSL = 1,000.00 USD exactly.
schedule = [
    ("2026-01-15", 18000, "First payment",    600.00, 400.00),   # 60% of 1000
    ("2026-02-15", 18000, "Monthly (year 1)", 100.00, 900.00),   # 10% of 1000
    ("2026-06-15", 18000, "Monthly (year 1)", 100.00, 900.00),
    ("2027-01-15", 18000, "Monthly (year 1)", 100.00, 900.00),   # exactly 12 months — still inside
    ("2027-01-16", 18000, "After month 12",     0.00, 1000.00),  # one day past — nil
    ("2027-06-15", 18000, "After month 12",     0.00, 1000.00),
]
pay_ids = []
for date, amount, expect_kind, expect_agent, expect_sp in schedule:
    st, d = call("POST", "/api/payments",
                 {"client_id": CLIENT_A, "amount": amount, "currency": "LSL",
                  "paid_date": date, "reference": "TEST-" + date}, ADMIN)
    p = d.get("payment", {})
    pay_ids.append(d.get("id"))
    ok(f"{date}  ->  {expect_kind}", p.get("commission_kind") == expect_kind,
       p.get("commission_kind"))
    ok(f"{date}  ->  agent ${expect_agent:,.2f}",
       abs(p.get("agent_commission_usd", -1) - expect_agent) < 0.01, p.get("agent_commission_usd"))
    ok(f"{date}  ->  StatsPack ${expect_sp:,.2f}",
       abs(p.get("statspack_share_usd", -1) - expect_sp) < 0.01, p.get("statspack_share_usd"))

st, d = call("GET", "/api/clients/%s" % CLIENT_A, token=ADMIN)
ok("window end is 12 months after first payment",
   d["payments"][0]["window_end"] == "2027-01-15", d["payments"][0]["window_end"])
ok("recording a payment marks the client Won", d["client"]["stage"] == "Won", d["client"]["stage"])

st, d = call("GET", "/api/commissions", token=ADMIN)
row = [c for c in d["commissions"] if c["agent_id"] == AGENT_ID][0]
ok("total earned = 600 + 100*3", abs(row["earned_usd"] - 900.00) < 0.01, row["earned_usd"])
ok("first-payment portion = 600", abs(row["first_payment_usd"] - 600.00) < 0.01, row["first_payment_usd"])
ok("recurring portion = 300", abs(row["recurring_usd"] - 300.00) < 0.01, row["recurring_usd"])
ok("collected = 6 x 1000", abs(row["collected_usd"] - 6000.00) < 0.01, row["collected_usd"])

# ---- voiding the first payment must promote the next one to 60%
print("\n[ VOIDING — the next live payment becomes the 'first' ]")
st, d = call("POST", "/api/payments/%s/void" % pay_ids[0], {}, ADMIN)
ok("first payment voided", st == 200 and d.get("voided"), d)

st, d = call("GET", "/api/clients/%s" % CLIENT_A, token=ADMIN)
live = [p for p in d["payments"] if not p["voided"]]
ok("voided payment earns nothing",
   [p for p in d["payments"] if p["voided"]][0]["agent_commission_usd"] == 0)
ok("next payment promoted to First payment", live[0]["commission_kind"] == "First payment",
   live[0]["commission_kind"])
ok("promoted payment pays 60%", abs(live[0]["agent_commission_usd"] - 600.00) < 0.01,
   live[0]["agent_commission_usd"])
ok("window recalculated from the new first payment",
   live[0]["window_end"] == "2027-02-15", live[0]["window_end"])

st, d = call("POST", "/api/payments/%s/void" % pay_ids[0], {}, ADMIN)
ok("void is reversible", st == 200 and not d.get("voided"), d)

st, d = call("GET", "/api/clients/%s" % CLIENT_A, token=ADMIN)
ok("restoring puts the original first payment back",
   d["payments"][0]["commission_kind"] == "First payment"
   and d["payments"][0]["paid_date"][:10] == "2026-01-15", d["payments"][0])

# ---- FX is locked at the time of recording
print("\n[ EXCHANGE RATES ARE LOCKED PER PAYMENT ]")
st, d = call("PUT", "/api/admin/fx", {"rates": [{"code": "LSL", "rate": 36.0}]}, ADMIN)
ok("admin updates LSL rate to 36", st == 200, d)
st, d = call("GET", "/api/clients/%s" % CLIENT_A, token=ADMIN)
ok("historic payment keeps its original USD value",
   abs(d["payments"][0]["amount_usd"] - 1000.00) < 0.01, d["payments"][0]["amount_usd"])
st, d = call("POST", "/api/payments",
             {"client_id": CLIENT_A, "amount": 18000, "currency": "LSL",
              "paid_date": "2026-03-15"}, ADMIN)
ok("new payment uses the new rate (18000/36 = 500)",
   abs(d["payment"]["amount_usd"] - 500.00) < 0.01, d["payment"]["amount_usd"])
call("PUT", "/api/admin/fx", {"rates": [{"code": "LSL", "rate": 18.0}]}, ADMIN)
call("POST", "/api/payments/%s/void" % d["id"], {}, ADMIN)

# ---------------------------------------------------------------- payouts
print("\n[ PAYOUTS ]")
st, d = call("GET", "/api/commissions", token=ADMIN)
outstanding = [c for c in d["commissions"] if c["agent_id"] == AGENT_ID][0]["outstanding_usd"]

st, d = call("POST", "/api/payouts",
             {"agent_id": AGENT_ID, "amount_usd": outstanding + 5000}, ADMIN)
ok("overpayment is blocked", st == 400, st)

st, d = call("POST", "/api/payouts",
             {"agent_id": AGENT_ID, "amount_usd": 400, "period_label": "Jan 2026"}, ADMIN)
ok("payout created", st == 200, d)
PAYOUT_ID = d.get("id")

st, d = call("GET", "/api/commissions", token=ADMIN)
row = [c for c in d["commissions"] if c["agent_id"] == AGENT_ID][0]
ok("pending payout reduces outstanding",
   abs(row["outstanding_usd"] - (outstanding - 400)) < 0.01, row["outstanding_usd"])
ok("pending payout is not counted as paid", row["paid_out_usd"] == 0, row["paid_out_usd"])

st, d = call("PATCH", "/api/payouts/%s" % PAYOUT_ID, {"status": "Paid"}, ADMIN)
ok("payout marked paid", st == 200, d)
st, d = call("GET", "/api/commissions", token=ADMIN)
row = [c for c in d["commissions"] if c["agent_id"] == AGENT_ID][0]
ok("paid payout moves into paid_out", abs(row["paid_out_usd"] - 400) < 0.01, row["paid_out_usd"])

st, d = call("DELETE", "/api/payouts/%s" % PAYOUT_ID, {}, ADMIN)
ok("a paid payout cannot be deleted", st == 400, st)

st, d = call("POST", "/api/payouts", {"agent_id": AGENT_ID, "amount_usd": 100}, AGENT)
ok("agent cannot create a payout for themselves", st == 403, st)

# ---------------------------------------------------------------- agent view
print("\n[ AGENT'S OWN VIEW ]")
st, d = call("GET", "/api/agent/overview", token=AGENT)
ok("agent sees own statement", st == 200, st)
ok("agent's earned figure matches admin's",
   abs(d["metrics"]["earned_usd"] - 900.00) < 0.01, d["metrics"]["earned_usd"])
ok("agent sees the rules that apply to them",
   d["rules"]["first_rate"] == 0.6 and d["rules"]["recurring_rate"] == 0.1, d["rules"])
ok("agent sees their own payout history", len(d["payouts"]) == 1, len(d["payouts"]))

st, d = call("GET", "/api/payments", token=AGENT2)
ok("second agent sees none of the first agent's payments",
   all(p["agent_id"] == AGENT2_ID for p in d["payments"]), len(d["payments"]))

# ---------------------------------------------------------------- settings
print("\n[ SETTINGS ]")
st, d = call("PUT", "/api/admin/settings", {"settings": {"commission_first_rate": 1.5}}, ADMIN)
ok("rate above 100% rejected", st == 400, st)
st, d = call("PUT", "/api/admin/settings", {"settings": {"commission_window_months": 0}}, ADMIN)
ok("zero-month window rejected", st == 400, st)
st, d = call("PUT", "/api/admin/settings", {"settings": {"commission_first_rate": 0.5}}, ADMIN)
ok("valid rate accepted", st == 200, d)
st, d = call("GET", "/api/clients/%s" % CLIENT_A, token=ADMIN)
ok("changing the rule recalculates history",
   abs(d["payments"][0]["agent_commission_usd"] - 500.00) < 0.01,
   d["payments"][0]["agent_commission_usd"])
call("PUT", "/api/admin/settings", {"settings": {"commission_first_rate": 0.6}}, ADMIN)

# ---------------------------------------------------------------- misc
print("\n[ SUSPENSION, AUDIT, BACKUP ]")
st, d = call("PATCH", "/api/admin/agents/%s" % AGENT2_ID, {"status": "Suspended"}, ADMIN)
ok("agent suspended", st == 200, d)
st, d = call("GET", "/api/agent/overview", token=AGENT2)
ok("suspended agent's session stops working", st == 401, st)
st, d = call("POST", "/api/login", {"email": "naledi@statspack.co.ls", "password": "NalediSells!2026"})
ok("suspended agent cannot sign back in", st == 403, st)
call("PATCH", "/api/admin/agents/%s" % AGENT2_ID, {"status": "Active"}, ADMIN)

st, d = call("GET", "/api/admin/audit", token=ADMIN)
actions = {a["action"] for a in d["audit"]}
ok("audit log records sign-ins", "login.success" in actions)
ok("audit log records failed sign-ins", "login.failed" in actions)
ok("audit log records payments", "payment.recorded" in actions)

st, d = call("GET", "/api/admin/backup", token=ADMIN)
ok("backup exports every table",
   all(k in d for k in ("users", "clients", "payments", "payouts", "fx", "settings")), list(d))
ok("backup never exports password hashes",
   all("password_hash" not in u for u in d["users"]), "LEAK")

st, d = call("DELETE", "/api/clients/%s" % CLIENT_A, {}, ADMIN)
ok("a client with payments cannot be deleted", st == 400, st)

st, d = call("POST", "/api/logout", {}, AGENT)
ok("logout succeeds", st == 200, st)
st, d = call("GET", "/api/agent/overview", token=AGENT)
ok("token dies on logout", st == 401, st)

print("\n" + "=" * 68)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 68)
sys.exit(1 if FAIL else 0)
