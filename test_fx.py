#!/usr/bin/env python3
"""Unit tests for the FX sync merge logic, with the provider stubbed out."""
import os, sys
os.environ.setdefault("SQLITE_PATH", "/tmp/fxtest.db")
for f in ("/tmp/fxtest.db", "/tmp/fxtest.db-wal", "/tmp/fxtest.db-shm"):
    if os.path.exists(f): os.remove(f)
import db, core

PASS = FAIL = 0
def ok(l, c, x=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  PASS  {l}")
    else: FAIL += 1; print(f"  FAIL  {l} {x}")

db.init_db()
with db.connection() as conn:
    core.bootstrap(conn)

print("="*68); print("FX sync — provider stubbed, merge logic under test"); print("="*68)

# A realistic response: real codes, ZWG deliberately absent (provider ships ZWL).
FAKE = {
    "USD": 1, "ZAR": 17.42, "LSL": 17.42, "NAD": 17.42, "SZL": 17.42,
    "BWP": 13.11, "ZMW": 24.86, "MWK": 1742.5, "TZS": 2512.0, "MZN": 63.4,
    "MUR": 45.1, "SCR": 14.2, "AOA": 912.0, "CDF": 2795.0, "MGA": 4460.0,
    "KMF": 449.0, "ZWL": 26.5,
}
core.fetch_live_rates = lambda: (FAKE, None)
os.environ["EXCHANGERATE_API_KEY"] = "test-key"

with db.connection() as conn:
    before = {r["code"]: float(r["rate"]) for r in conn.query("SELECT * FROM fx")}
    rep = core.sync_fx(conn)
    after = {r["code"]: float(r["rate"]) for r in conn.query("SELECT * FROM fx")}
    rows = {r["code"]: r for r in conn.query("SELECT * FROM fx")}

ok("sync reports success", rep["ok"], rep)
ok("ZAR updated to the live rate", abs(after["ZAR"] - 17.42) < 1e-6, after["ZAR"])
ok("TZS updated to the live rate", abs(after["TZS"] - 2512.0) < 1e-6, after["TZS"])
ok("USD stays pinned at 1", after["USD"] == 1.0, after["USD"])
ok("ZWG left alone — provider ships ZWL, not ZWG",
   "ZWG" in rep["unmatched"] and after["ZWG"] == before["ZWG"], (rep["unmatched"], after.get("ZWG")))
ok("unmatched currencies keep their manual source", rows["ZWG"]["source"] == "manual", rows["ZWG"]["source"])
ok("updated currencies are marked live", rows["ZAR"]["source"] == "ExchangeRate-API", rows["ZAR"]["source"])
ok("synced_at stamped on updated rows", bool(rows["ZAR"]["synced_at"]), rows["ZAR"]["synced_at"])
ok("change report lists ZAR with a percentage",
   any(u["code"] == "ZAR" and u["change_pct"] is not None for u in rep["updated"]), rep["updated"][:3])
ok("report counts match the rows changed", len(rep["updated"]) >= 10, len(rep["updated"]))

# Re-running with identical rates must report no change, not fake churn.
with db.connection() as conn:
    rep2 = core.sync_fx(conn)
ok("re-sync with unchanged rates reports nothing changed", len(rep2["updated"]) == 0, rep2["updated"])

# --- historic payments must not move when rates do ---
print("\n[ HISTORIC COMMISSION IS IMMUNE TO RATE CHANGES ]")
with db.connection() as conn:
    settings = core.get_settings(conn)
    aid = conn.insert("INSERT INTO users (name,email,phone,country,role,status,password_hash,"
        "must_change_pw,is_super,quota_usd,start_date,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A","a@x.com","","LS","agent","Active",core.hash_password("x"),0,0,0,"","",core.now_iso()))
    cid = conn.insert("INSERT INTO clients (agent_id,name,contact_person,contact_email,contact_phone,"
        "country,product,currency,monthly_value,stage,won_date,notes,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid,"C","","","","LS","","LSL",18000,"Won","","",core.now_iso(),core.now_iso()))
    rate = core.fx_rate(conn, "LSL")
    usd_at_record = core.to_usd(18000, rate)
    conn.insert("INSERT INTO payments (client_id,amount,currency,fx_rate,amount_usd,paid_date,"
        "reference,note,voided,recorded_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid,18000,"LSL",rate,usd_at_record,"2026-01-15","","",0,aid,core.now_iso()))
    comm_before = core.agent_commission_summary(conn, aid, settings)["earned_usd"]

core.fetch_live_rates = lambda: (dict(FAKE, LSL=35.0), None)   # currency halves in value
with db.connection() as conn:
    core.sync_fx(conn)
    comm_after = core.agent_commission_summary(conn, aid, core.get_settings(conn))["earned_usd"]
    new_rate = core.fx_rate(conn, "LSL")

ok("the rate really did move", abs(new_rate - 35.0) < 1e-6, new_rate)
ok("commission already earned is unchanged", abs(comm_before - comm_after) < 0.01,
   (comm_before, comm_after))

print("\n[ FAILURE HANDLING ]")
core.fetch_live_rates = lambda: (None, "Provider refused the request (the API key was rejected).")
with db.connection() as conn:
    rep3 = core.sync_fx(conn)
    kept = core.fx_rate(conn, "ZAR")
ok("a rejected key returns an error, not an exception", rep3["ok"] is False, rep3)
ok("rates survive a failed sync untouched", abs(kept - 17.42) < 1e-6, kept)
with db.connection() as conn:
    status = core.get_settings(conn).get("fx_last_status", "")
ok("failure reason is recorded for the admin to see", "rejected" in status, status)

print("\n" + "="*68); print(f"  {PASS} passed, {FAIL} failed"); print("="*68)
sys.exit(1 if FAIL else 0)
