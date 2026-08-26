#!/usr/bin/env python3
"""Adversarial audit: cross-tenant holes, orphaned data, escalation, integrity."""
import json, sys, urllib.request, urllib.error
B = sys.argv[1] if len(sys.argv)>1 else "http://localhost:8470"
PASS=FAIL=0; ISSUES=[]
def ok(l,c,x=""):
    global PASS,FAIL
    if c: PASS+=1; print(f"  PASS  {l}")
    else:
        FAIL+=1; ISSUES.append(l); print(f"  FAIL  {l}   <-- {x}")
def call(m,p,b=None,t=None):
    r=urllib.request.Request(B+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=25) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or '{}')

def onboard(email,name,tok,pw='Passw0rd!2026',**kw):
    st,a=call('POST','/api/admin/agents',dict(kw,email=email,name=name),tok)
    if st!=200: return None,None
    _,s=call('POST','/api/login',{'email':email,'password':a['temp_password']})
    call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':pw},s['token'])
    return a['id'], s['token']

print("="*72); print("ADVERSARIAL AUDIT"); print("="*72)
_,d=call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
SUPER=d['token']

# --- two tenants ---
A1,T1 = onboard('a1@statspack.co.ls','Host Agent',SUPER,country='Lesotho')
_,co = call('POST','/api/admin/companies',{'name':'Rival Co','admin_name':'Rival Admin',
    'admin_email':'rival@rival.com','first_rate':0.5,'recurring_rate':0.08},SUPER)
_,s = call('POST','/api/login',{'email':'rival@rival.com','password':co['temp_password']})
RT=s['token']; call('POST','/api/change-password',{'current_password':co['temp_password'],'new_password':'Rival!2026'},RT)
A2,T2 = onboard('a2@rival.com','Rival Agent',RT)

_,hc = call('POST','/api/clients',{'name':'Host Client','currency':'USD','monthly_value':1000},T1)
_,rc = call('POST','/api/clients',{'name':'Rival Client','currency':'USD','monthly_value':1000},T2)
call('POST','/api/payments',{'client_id':hc['id'],'amount':1000,'currency':'USD','paid_date':'2026-01-15'},SUPER)
_,hp = call('GET','/api/payments',None,SUPER)
HOST_PAY = [p for p in hp['payments'] if p['client_id']==hc['id']][0]['id']
_,po = call('POST','/api/payouts',{'agent_id':A1,'amount_usd':100},SUPER)
HOST_PAYOUT = po['id']
call('POST','/api/admin/reviews',{'agent_id':A1,'rating':3,'body':'note'},SUPER)
_,rv = call('GET',f'/api/admin/agents/{A1}/progress',None,SUPER)
HOST_REVIEW = rv['reviews'][0]['id']

print("\n[ CROSS-TENANT WRITE ATTEMPTS by a rival company admin ]")
st,d = call('POST','/api/payments',{'client_id':hc['id'],'amount':999,'currency':'USD','paid_date':'2026-05-01'},RT)
ok("cannot record a payment against another company's client", st==403, f"got {st}")
st,d = call('POST',f'/api/payments/{HOST_PAY}/void',{},RT)
ok("cannot void another company's payment", st==403, f"got {st}")
st,d = call('PATCH',f'/api/payouts/{HOST_PAYOUT}',{'status':'Paid'},RT)
ok("cannot approve another company's payout", st==403, f"got {st}")
st,d = call('DELETE',f'/api/payouts/{HOST_PAYOUT}',{},RT)
ok("cannot delete another company's payout", st==403, f"got {st}")
st,d = call('DELETE',f'/api/admin/reviews/{HOST_REVIEW}',{},RT)
ok("cannot delete another company's progress note", st==403, f"got {st}")
st,d = call('POST',f'/api/admin/agents/{A1}/reset-password',{},RT)
ok("cannot reset another company's agent password", st==403, f"got {st}")
st,d = call('GET',f'/api/admin/agents/{A1}/export',None,RT)
ok("cannot export another company's agent records", st==403, f"got {st}")
st,d = call('POST',f'/api/clients/{hc["id"]}/timeline',{'kind':'note','body':'x'},RT)
ok("cannot write to another company's client timeline", st==403, f"got {st}")
st,d = call('DELETE',f'/api/clients/{hc["id"]}',{},RT)
ok("cannot delete another company's client", st in (400,403), f"got {st}")

print("\n[ CLIENT REASSIGNMENT ACROSS COMPANIES ]")
st,d = call('PATCH',f'/api/clients/{rc["id"]}',{'agent_id':A1},RT)
ok("rival admin cannot hand their client to a host agent", st==403, f"got {st}")
st,d = call('PATCH',f'/api/clients/{hc["id"]}',{'agent_id':A2},SUPER)
if st==200:
    _,chk = call('GET',f'/api/clients/{hc["id"]}',None,SUPER)
    _,rivalview = call('GET','/api/clients',None,RT)
    moved = any(c['id']==hc['id'] for c in rivalview['clients'])
    ok("super moving a client across companies also moves its company_id",
       moved, "client reassigned but company_id stale — invisible to its new owner")
    call('PATCH',f'/api/clients/{hc["id"]}',{'agent_id':A1},SUPER)
else:
    ok("cross-company reassignment handled", st in (400,403), f"got {st}")

print("\n[ PRIVILEGE ESCALATION ]")
st,d = call('POST','/api/admin/agents',{'name':'Esc','email':'esc@rival.com','role':'admin','is_super':1},RT)
if st==200:
    _,s2 = call('POST','/api/login',{'email':'esc@rival.com','password':d['temp_password']})
    call('POST','/api/change-password',{'current_password':d['temp_password'],'new_password':'Esc!2026xyz'},s2['token'])
    st2,me = call('GET','/api/me',None,s2['token'])
    ok("is_super cannot be granted through agent creation", not me['user']['is_super'], "ESCALATION")
    st3,_ = call('GET','/api/admin/companies',None,s2['token'])
    ok("their created admin still cannot list companies", st3==403, f"got {st3}")
else:
    ok("company admin creating an admin handled", True)
st,d = call('POST','/api/admin/agents',{'name':'X','email':'x9@rival.com','company_id':1},RT)
if st==200:
    _,agl = call('GET','/api/admin/agents',None,RT)
    hijacked = [a for a in agl['agents'] if a['email']=='x9@rival.com']
    ok("company_id in the body cannot plant an agent in another company",
       hijacked and hijacked[0]['company_id']!=1, "planted into host company")
st,d = call('PATCH',f'/api/admin/agents/{A2}',{'is_super':1,'company_id':1},RT)
_,chk = call('GET',f'/api/admin/agents/{A2}/progress',None,RT)
ok("is_super/company_id ignored on update", not chk['agent']['is_super'] and chk['agent']['company_id']!=1, chk['agent'])

print("\n[ SUSPENDED COMPANY ]")
call('PATCH',f'/api/admin/companies/{co["id"]}',{'status':'Suspended'},SUPER)
st,d = call('GET','/api/admin/overview',None,RT)
ok("suspending a company kills live sessions", st==401, f"got {st}")
st,d = call('POST','/api/login',{'email':'rival@rival.com','password':'Rival!2026'})
ok("suspended company's admin cannot sign back in", st==403, f"got {st} — they can still log in")
st,d = call('POST','/api/login',{'email':'a2@rival.com','password':'Passw0rd!2026'})
ok("suspended company's agent cannot sign back in", st==403, f"got {st} — they can still log in")
call('PATCH',f'/api/admin/companies/{co["id"]}',{'status':'Active'},SUPER)

print("\n[ ORPHANED DATA AFTER DELETION ]")
_,s = call('POST','/api/login',{'email':'rival@rival.com','password':'Rival!2026'})
RT=s['token']
_,s = call('POST','/api/login',{'email':'a2@rival.com','password':'Passw0rd!2026'})
T2=s['token']
_,tmp = call('POST','/api/clients',{'name':'Doomed Client','currency':'USD','monthly_value':500},T2)
call('POST',f'/api/clients/{tmp["id"]}/timeline',{'kind':'call','body':'will be orphaned'},T2)
st,d = call('DELETE',f'/api/clients/{tmp["id"]}',{},RT)
ok("client with no payments can be deleted", st==200, d)
st,d = call('GET','/api/admin/orphans',None,SUPER)  # may not exist; checked below via purge

_,tmp2 = call('POST','/api/clients',{'name':'Purge Client','currency':'USD','monthly_value':500},T2)
call('POST',f'/api/clients/{tmp2["id"]}/timeline',{'kind':'demo','body':'purge me'},T2)
call('POST','/api/payments',{'client_id':tmp2['id'],'amount':500,'currency':'USD','paid_date':'2026-02-01'},RT)
st,d = call('DELETE',f'/api/admin/agents/{A2}/purge',{'confirm_name':'Rival Agent'},SUPER)
ok("super can purge any company's agent", st==200, d)

st,d = call('GET','/api/admin/backup',None,SUPER)
live_clients = {c['id'] for c in d['clients']}
orphan_events = [e for e in d.get('client_events',[]) if e['client_id'] not in live_clients]
orphan_pay = [p for p in d.get('payments',[]) if p['client_id'] not in live_clients]
ok("no orphaned timeline entries after deletions", not orphan_events, f"{len(orphan_events)} orphans")
ok("no orphaned payments after purge", not orphan_pay, f"{len(orphan_pay)} orphans")
live_users = {u['id'] for u in d['users']}
orphan_payouts = [p for p in d.get('payouts',[]) if p['agent_id'] not in live_users]
ok("no orphaned payouts after purge", not orphan_payouts, f"{len(orphan_payouts)} orphans")

print("\n[ BACKUP COMPLETENESS ]")
st,d = call('GET','/api/admin/backup',None,SUPER)
for table in ('companies','client_events'):
    ok(f"backup includes {table}", table in d, "MISSING — restoring would lose it")
ok("backup still excludes password hashes", all('password_hash' not in u for u in d.get('users',[])), "LEAK")

print("\n[ PAYLOAD SIZE ]")
import base64
big = 'data:image/png;base64,' + base64.b64encode(b'x'*250000).decode()[:290000]
small = 'data:image/jpeg;base64,' + base64.b64encode(b'y'*2000).decode()
call('PUT','/api/me/avatar',{'avatar':big[:390000],'avatar_thumb':small},T1)
r=urllib.request.Request(B+'/api/admin/agents',headers={'Authorization':'Bearer '+SUPER})
size=len(urllib.request.urlopen(r).read())
ok(f"agent list stays small ({size//1024}KB) — avatars not inlined", size < 120000,
   f"{size//1024}KB: avatars bloat every list response")

print("\n" + "="*72)
print(f"  {PASS} passed, {FAIL} failed")
if ISSUES:
    print("\n  ISSUES FOUND:")
    for i in ISSUES: print("   -", i)
print("="*72)
