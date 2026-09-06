#!/usr/bin/env python3
"""Clicks through the real interface. Catches wiring bugs the API tests miss."""
import json, sys, urllib.request
from playwright.sync_api import sync_playwright
B = sys.argv[1] if len(sys.argv)>1 else "http://localhost:8470"
PASS=FAIL=0; ISSUES=[]
def ok(l,c,x=""):
    global PASS,FAIL
    if c: PASS+=1; print(f"  PASS  {l}")
    else: FAIL+=1; ISSUES.append(l); print(f"  FAIL  {l}   <-- {x}")
def api(m,p,b=None,t=None):
    r=urllib.request.Request(B+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None) as x:
        return json.loads(x.read())

print("="*72); print("UI CLICK-THROUGH"); print("="*72)
SUP = api('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})['token']
a = api('POST','/api/admin/agents',{'name':'UI Agent','email':'ui@statspack.co.ls','country':'Lesotho','quota_usd':50000},SUP)
s = api('POST','/api/login',{'email':'ui@statspack.co.ls','password':a['temp_password']})
api('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'UiAgent!2026'},s['token'])
AT = api('POST','/api/login',{'email':'ui@statspack.co.ls','password':'UiAgent!2026'})['token']
c = api('POST','/api/clients',{'name':'UI Client','currency':'USD','monthly_value':1000,'industry':'Technology & Software'},AT)
api('POST','/api/payments',{'client_id':c['id'],'amount':1000,'currency':'USD','paid_date':'2026-01-15'},SUP)

errs=[]; failed_calls=[]
with sync_playwright() as p:
    br=p.chromium.launch()
    def newpage(tok, w=1440, h=1000):
        pg=br.new_page(viewport={"width":w,"height":h})
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("response", lambda r: failed_calls.append((r.status, r.url.split('/api/')[-1]))
              if '/api/' in r.url and r.status>=400 and r.status not in (401,) else None)
        pg.goto(B+"/login"); pg.evaluate("t=>localStorage.setItem('sp_token',t)",tok)
        return pg

    print("\n[ EVERY PAGE LOADS — super admin ]")
    pg = newpage(SUP)
    for name,h in [("dashboard","#/dashboard"),("companies","#/companies"),("agents","#/agents"),
                   ("clients","#/clients"),("payments","#/payments"),("commissions","#/commissions"),
                   ("payouts","#/payouts"),("settings","#/settings"),("audit","#/audit"),
                   ("reports","#/reports"),("system health","#/system"),
                   ("profile","#/profile"),("agent detail","#/agent/2"),("client detail","#/client/1")]:
        pg.goto(B+"/"+h, wait_until="networkidle"); pg.wait_for_timeout(700)
        body = pg.inner_text("#view")
        broken = "Could not load" in body
        ok(f"{name} renders", not broken, body[:80])

    print("\n[ EVERY 'ADD' DIALOG OPENS WITH ITS FIELDS ]")
    checks = [("#/companies","#addCo","#coName,#coAdminName,#coAdminEmail","Register company"),
              ("#/agents","#addAgent","#aName,#aEmail,#aType","Add agent"),
              ("#/clients","#addClient","#cName,#cIndustry,#cCur","Add client"),
              ("#/payments","#newPay","#pAmount,#pDate","Record payment"),
              ("#/client/1","#addEvent","#evKind,#evBody,#evNext","Log activity")]
    for route,btn,fields,label in checks:
        pg.goto(B+"/"+route, wait_until="networkidle"); pg.wait_for_timeout(900)
        if not pg.query_selector(btn):
            ok(f"{label} button present", False, f"{btn} missing"); continue
        pg.click(btn); pg.wait_for_timeout(700)
        missing=[f for f in fields.split(',') if not pg.query_selector(f)]
        ok(f"{label} dialog has all its fields", not missing, f"missing {missing}")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

    print("\n[ REGISTER A COMPANY END-TO-END ]")
    pg.goto(B+"/#/companies", wait_until="networkidle"); pg.wait_for_timeout(900)
    pg.click("#addCo"); pg.wait_for_timeout(600)
    pg.fill("#coName","Zambezi Digital"); pg.fill("#coCountry","Zambia")
    pg.fill("#coAdminName","Chipo Banda"); pg.fill("#coAdminEmail","chipo@zambezi.co.zm")
    pg.fill("#coFirst","55"); pg.fill("#coRecur","9"); pg.fill("#coWindow","18")
    pg.click("#coSave"); pg.wait_for_timeout(2500)
    head = pg.query_selector(".modal-head h3")
    ok("company registered (success dialog shown)",
       head and "registered" in head.inner_text().lower(),
       (head.inner_text() if head else "no dialog") + " | " + (pg.query_selector(".toast").inner_text() if pg.query_selector(".toast") else ""))
    box = pg.query_selector(".copybox")
    ok("one-time password shown", box and "@" in box.inner_text(), box.inner_text() if box else "none")
    pg.click(".modal-foot .btn"); pg.wait_for_timeout(1500)
    rows = pg.inner_text("#view")
    ok("new company appears in the list", "Zambezi Digital" in rows, rows[:120])
    ok("its commission rules are shown", "55.0% / 9.0%" in rows, rows[:200])

    print("\n[ EDIT AN EXISTING COMPANY ]")
    pg.goto(B+"/#/companies", wait_until="networkidle"); pg.wait_for_timeout(900)
    btns = pg.query_selector_all("[data-co]")
    btns[-1].click(); pg.wait_for_timeout(700)
    head = pg.query_selector(".modal-head h3")
    ok("edit dialog says Edit, not Register", head and head.inner_text().startswith("Edit"), head.inner_text() if head else "none")
    ok("edit dialog hides the administrator fields", not pg.query_selector("#coAdminName"), "admin fields shown when editing")
    ok("edit dialog pre-fills the name", pg.input_value("#coName") != "", "empty")
    pg.fill("#coCountry","Zambia (updated)"); pg.click("#coSave"); pg.wait_for_timeout(1800)
    ok("edit saves without error", "Zambia (updated)" in pg.inner_text("#view"), pg.inner_text("#view")[:120])

    print("\n[ AGENT'S OWN VIEW ]")
    ap = newpage(AT)
    for name,h in [("dashboard","#/dashboard"),("my clients","#/clients"),("payments","#/payments"),
                   ("commission","#/commissions"),("payouts","#/payouts"),("profile","#/profile"),
                   ("teamwork","#/teamwork"),("reports","#/reports"),
                   ("client detail","#/client/1")]:
        ap.goto(B+"/"+h, wait_until="networkidle"); ap.wait_for_timeout(700)
        ok(f"agent {name} renders", "Could not load" not in ap.inner_text("#view"), ap.inner_text("#view")[:70])
    ap.goto(B+"/#/dashboard", wait_until="networkidle"); ap.wait_for_timeout(600)
    nav = ap.inner_text("#nav")
    ok("agent nav hides Companies", "Companies" not in nav, nav.replace("\n"," | "))
    ok("agent nav hides Settings", "Settings" not in nav, nav.replace("\n"," | "))

    print("\n[ LOG AN ACTIVITY END-TO-END ]")
    ap.goto(B+"/#/client/1", wait_until="networkidle"); ap.wait_for_timeout(900)
    ap.click("#addEvent"); ap.wait_for_timeout(600)
    ap.select_option("#evKind","meeting"); ap.fill("#evBody","Met the ops lead on site.")
    ap.fill("#evNext","Send pricing"); ap.click("#evSave"); ap.wait_for_timeout(1800)
    ok("activity appears on the timeline", "Met the ops lead on site." in ap.inner_text("#view"), "not shown")
    ok("next step rendered", "Send pricing" in ap.inner_text("#view"), "not shown")

    print("\n[ AGENT SEARCH UI ]")
    pg.goto(B+"/#/agents", wait_until="networkidle"); pg.wait_for_timeout(900)
    ok("search box present", pg.query_selector("#agentSearch") is not None, "missing")
    ok("type filter present", pg.query_selector("#agFilterType") is not None, "missing")
    pg.fill("#agentSearch", "zzznomatch"); pg.wait_for_timeout(1400)
    ok("no-match state shown", "No agents match" in pg.inner_text("#view"), pg.inner_text("#view")[:80])
    pg.fill("#agentSearch", ""); pg.wait_for_timeout(1400)
    ok("clearing restores the list", pg.query_selector("table") is not None, "no table")

    print("\n[ DELETE CLIENT UI ]")
    pg.goto(B+"/#/client/1", wait_until="networkidle"); pg.wait_for_timeout(1000)
    ok("delete button shown to admin", pg.query_selector("#delClient") is not None, "missing")
    pg.click("#delClient"); pg.wait_for_timeout(1600)
    head = pg.query_selector(".modal-head h3")
    ok("delete dialog opens", head is not None, "no dialog")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

    print("\n[ REPORTS PAGE ]")
    pg.goto(B+"/#/reports", wait_until="networkidle"); pg.wait_for_timeout(900)
    ok("report options render", len(pg.query_selector_all("[data-rep]"))>=6, len(pg.query_selector_all("[data-rep]")))
    pg.click("#prevRep"); pg.wait_for_timeout(2000)
    ok("preview renders a table", pg.query_selector("#repPreview table") is not None, pg.inner_text("#repPreview")[:80])

    print("\n[ TEAMWORK PAGE — agent ]")
    tp = newpage(AT)
    tp.goto(B+"/#/teamwork", wait_until="networkidle"); tp.wait_for_timeout(1200)
    body = tp.inner_text("#view")
    ok("teamwork page renders", "Could not load" not in body, body[:90])
    ok("colleague directory or empty state shown",
       tp.query_selector(".dir-card") is not None or "Nobody matches" in body or "No partnerships" in body, body[:90])

    print("\n[ LOG DRILL-DOWN ]")
    pg.goto(B+"/#/audit", wait_until="networkidle"); pg.wait_for_timeout(1000)
    row = pg.query_selector("tr[data-log]")
    ok("audit rows are clickable for super users", row is not None, "no clickable rows")
    if row:
        row.click(); pg.wait_for_timeout(1500)
        h = pg.query_selector(".modal-head h3")
        ok("log detail dialog opens", h and "Log entry" in h.inner_text(), h.inner_text() if h else "none")
        pg.keyboard.press("Escape")

    br.close()

print("\n[ NO ERRORS ANYWHERE ]")
ok("no JavaScript errors", not errs, errs[:3])
ok("no failed API calls during click-through", not failed_calls, failed_calls[:5])
print("\n" + "="*72); print(f"  {PASS} passed, {FAIL} failed")
if ISSUES:
    print("\n  ISSUES:")
    for i in ISSUES: print("   -", i)
print("="*72)
sys.exit(1 if FAIL else 0)
