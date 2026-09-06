#!/usr/bin/env python3
"""Is the standby a true copy, and could you actually run on it?"""
import json, sys, urllib.request, urllib.error, subprocess, base64
import psycopg2, psycopg2.extras
B='http://localhost:8470'
PRIMARY='postgresql://postgres@127.0.0.1:5433/neondb'
STANDBY='postgresql://postgres@127.0.0.1:5433/supadb'
P=F=0; ISSUES=[]
def ok(l,c,x=""):
    global P,F
    if c: P+=1; print(f"  PASS  {l}")
    else: F+=1; ISSUES.append(l); print(f"  FAIL  {l}   <-- {x}")
def call(m,p,b=None,t=None,base=B):
    r=urllib.request.Request(base+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=90) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or '{}')

print("="*72); print("MIRROR FIDELITY — is Supabase a true copy of Neon?"); print("="*72)
_,d=call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
T=d['token']

# --- awkward data on purpose: unicode, quotes, NULLs, decimals, long text ---
print("\n[ SEEDING AWKWARD DATA ]")
def onboard(email,name,**kw):
    a=call('POST','/api/admin/agents',dict(kw,email=email,name=name),T)[1]
    s=call('POST','/api/login',{'email':email,'password':a['temp_password']})[1]
    call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'Passw0rd!2026'},s['token'])
    return a['id'], s['token']
A1,T1 = onboard('thabo@statspack.co.ls','Thabo Mokoena — Lesotho',country='Lesotho',
                agent_type='Tech Sales Agent',quota_usd=63500.75)
A2,T2 = onboard('naledi@statspack.co.ls','Naledi "Nali" Dlamini',country='Botswana',
                agent_type='Channel Partner',first_rate=0.455,recurring_rate=0.075,window_months=18)
_,c1 = call('POST','/api/clients',{'name':"O'Brien & Sons, Lesotho (Pty) Ltd",'currency':'LSL',
    'monthly_value':18450.55,'country':'Lesotho','industry':'Mining & Resources',
    'notes':'Unicode: Sesotho — Lumela ntate. Emoji 🇱🇸. Tab\tand "quotes" and, commas.'},T1)
_,c2 = call('POST','/api/clients',{'name':'Мосэнерго 日本語 Ltd','currency':'ZAR',
    'monthly_value':99999.99,'country':'Botswana','industry':'Technology & Software'},T2)
for dte,amt in [('2026-01-15',18450.55),('2026-02-15',18450.55),('2027-06-15',18450.55)]:
    call('POST','/api/payments',{'client_id':c1['id'],'amount':amt,'currency':'LSL',
        'paid_date':dte,'reference':'INV/2026-"A",1'},T)
call('POST','/api/payments',{'client_id':c2['id'],'amount':99999.99,'currency':'ZAR','paid_date':'2026-03-01'},T)
call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':A2,'split_pct':0.275,
    'reason':'Needs Botswana presence'},T1)
_,kl = call('GET','/api/collaborations',None,T2)
call('PATCH','/api/collaborations/%s'%kl['incoming'][0]['id'],{'status':'Accepted'},T2)
call('POST','/api/clients/%s/timeline'%c1['id'],{'kind':'meeting','body':'Notes with\nnewlines and "quotes".','next_step':'Send pack','due_date':'2026-09-01'},T1)
call('POST','/api/admin/reviews',{'agent_id':A1,'rating':5,'body':'Excellent — 100% attainment.'},T)
call('POST','/api/payouts',{'agent_id':A1,'amount_usd':512.34,'period_label':'Q1 “2026”'},T)
png='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'z'*300).decode()
call('PUT','/api/me/avatar',{'avatar':png,'avatar_thumb':png},T1)
print("  seeded")

print("\n[ MIRROR RUN ]")
st,rep = call('POST','/api/admin/system/mirror',{},T)
ok("mirror completes", st==200 and rep.get('ok'), rep)

# ---------- column-by-column comparison ----------
print("\n[ EVERY TABLE, EVERY COLUMN, EVERY ROW ]")
def rows(dsn, table):
    c=psycopg2.connect(dsn); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"SELECT * FROM {table} ORDER BY 1")
    out=[dict(r) for r in cur.fetchall()]; cur.close(); c.close(); return out
def norm(v):
    from decimal import Decimal
    if isinstance(v,Decimal): return round(float(v),6)
    return v

import mirror as M
total_rows=0; mismatches=[]
for table in M.TABLES:
    a=rows(PRIMARY,table); b=rows(STANDBY,table)
    if table=='audit' and len(a)>len(b):
        # The mirror logs its own run after the snapshot; trim those.
        extra=[r for r in a[len(b):]]
        if all('mirror' in (r.get('action') or '') for r in extra):
            a=a[:len(b)]
        else:
            mismatches.append(f"audit: unexpected extra rows {[r.get('action') for r in extra]}")
            continue
    if len(a)!=len(b):
        mismatches.append(f"{table}: {len(a)} vs {len(b)} rows"); continue
    for ra,rb in zip(a,b):
        if set(ra.keys())!=set(rb.keys()):
            mismatches.append(f"{table}: column mismatch"); break
        for k in ra:
            if norm(ra[k])!=norm(rb[k]):
                mismatches.append(f"{table}.{k}: {ra[k]!r} vs {rb[k]!r}"); break
    total_rows+=len(a)
ok(f"all {len(M.TABLES)} tables identical across {total_rows} rows", not mismatches, mismatches[:4])

pc=psycopg2.connect(PRIMARY); pcur=pc.cursor()
pcur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
ptabs={r[0] for r in pcur.fetchall()}
sc=psycopg2.connect(STANDBY); scur=sc.cursor()
scur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
stabs={r[0] for r in scur.fetchall()}
ok("standby has every table the primary has", ptabs<=stabs, ptabs-stabs)

for t in ('users','clients','payments'):
    pcur.execute(f"SELECT column_name,data_type FROM information_schema.columns WHERE table_name='{t}' ORDER BY 1")
    pcols=dict(pcur.fetchall())
    scur.execute(f"SELECT column_name,data_type FROM information_schema.columns WHERE table_name='{t}' ORDER BY 1")
    scols=dict(scur.fetchall())
    ok(f"{t} columns and types match", pcols==scols,
       {k:(pcols.get(k),scols.get(k)) for k in set(pcols)|set(scols) if pcols.get(k)!=scols.get(k)})

print("\n[ AWKWARD VALUES SURVIVED ]")
sb_clients={r['name'] for r in rows(STANDBY,'clients')}
ok("unicode client name copied", 'Мосэнерго 日本語 Ltd' in sb_clients, sb_clients)
ok("apostrophes and commas copied", "O'Brien & Sons, Lesotho (Pty) Ltd" in sb_clients, sb_clients)
sb_users={r['name']:r for r in rows(STANDBY,'users')}
ok("embedded quotes copied", 'Naledi "Nali" Dlamini' in sb_users, list(sb_users))
ok("NULL commission overrides stayed NULL",
   sb_users['Thabo Mokoena — Lesotho']['first_rate'] is None, sb_users['Thabo Mokoena — Lesotho']['first_rate'])
ok("commission rate stored at full precision (0.455, not rounded to 0.46)",
   abs(float(sb_users['Naledi "Nali" Dlamini']['first_rate'])-0.455)<1e-6,
   sb_users['Naledi "Nali" Dlamini']['first_rate'])
ok("avatar data URL copied intact", sb_users['Thabo Mokoena — Lesotho']['avatar'].startswith('data:image/png'), 'no')
sb_events=rows(STANDBY,'client_events')
ok("newlines inside notes preserved", any('\n' in (e['body'] or '') for e in sb_events), 'lost')
sb_pay=rows(STANDBY,'payments')
ok("decimal amounts exact", any(abs(float(p['amount'])-18450.55)<0.001 for p in sb_pay), [str(p['amount']) for p in sb_pay])
sb_col=rows(STANDBY,'collaborations')
ok("collaboration split at full precision (0.275, not 0.28)",
   sb_col and abs(float(sb_col[0]['split_pct'])-0.275)<1e-6,
   sb_col[0]['split_pct'] if sb_col else None)

# Rates that only work with real precision
print("\n[ PRECISION THAT 2 DECIMAL PLACES WOULD DESTROY ]")
call('PUT','/api/admin/fx',{'rates':[{'code':'BHD','rate':0.376},{'code':'KWD','rate':0.307}]},T)
call('PATCH','/api/admin/agents/%s'%A1,{'first_rate':0.125},T)
st,rep2 = call('POST','/api/admin/system/mirror',{},T)
sb_fx={r['code']:float(r['rate']) for r in rows(STANDBY,'fx')}
ok("BHD 0.376 survives (2dp would give 0.38)", abs(sb_fx['BHD']-0.376)<1e-6, sb_fx.get('BHD'))
ok("KWD 0.307 survives (2dp would give 0.31)", abs(sb_fx['KWD']-0.307)<1e-6, sb_fx.get('KWD'))
sb_u2={r['id']:r for r in rows(STANDBY,'users')}
ok("12.5% commission survives (2dp would give 13%)",
   abs(float(sb_u2[A1]['first_rate'])-0.125)<1e-6, sb_u2[A1]['first_rate'])
st,d = call('GET','/api/admin/agents/%s/progress'%A1,None,T)
ok("portal reports 12.5%, not 13%", abs(d['rules']['first_rate']-0.125)<1e-6, d['rules'])
pc.close(); sc.close()
print("\n" + "="*72); print(f"  {P} passed, {F} failed")
if ISSUES:
    print("\n  ISSUES:"); [print("   -",i) for i in ISSUES]
print("="*72)
sys.exit(1 if F else 0)
