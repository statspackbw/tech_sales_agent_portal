#!/usr/bin/env python3
"""Second pass: validation, integrity, concurrency, edge cases."""
import json, sys, urllib.request, urllib.error, threading
B = sys.argv[1] if len(sys.argv)>1 else "http://localhost:8470"
PASS=FAIL=0; ISSUES=[]
def ok(l,c,x=""):
    global PASS,FAIL
    if c: PASS+=1; print(f"  PASS  {l}")
    else: FAIL+=1; ISSUES.append(l); print(f"  FAIL  {l}   <-- {x}")
def call(m,p,b=None,t=None,raw=None):
    r=urllib.request.Request(B+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    data = raw if raw is not None else (json.dumps(b).encode() if b is not None else None)
    try:
        with urllib.request.urlopen(r, data, timeout=25) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or '{}')
        except Exception: return e.code, {}
    except Exception as e:
        return 0, {'error':str(e)}

print("="*72); print("EDGE CASES & INTEGRITY"); print("="*72)
_,d=call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
T=d['token']
_,a=call('POST','/api/admin/agents',{'name':'Edge Agent','email':'edge@statspack.co.ls'},T)
_,s=call('POST','/api/login',{'email':'edge@statspack.co.ls','password':a['temp_password']})
AT=s['token']; call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'Edge!2026xyz'},AT)
AID=a['id']

print("\n[ MALFORMED INPUT ]")
st,d = call('POST','/api/login',None,raw=b'{not json')
ok("malformed JSON returns 400 not 500", st==400, f"got {st}")
st,d = call('POST','/api/clients',{'name':'X','monthly_value':'abc'},AT)
ok("non-numeric money handled", st in (200,400), f"got {st}")
st,d = call('POST','/api/clients',{'name':'A'*5000},AT)
ok("very long name truncated not crashed", st==200, f"got {st}")
st,d = call('POST','/api/clients',{},AT)
ok("missing required field rejected", st==400, f"got {st}")
st,d = call('GET','/api/clients/999999',None,T)
ok("missing client 404s", st==404, f"got {st}")
st,d = call('GET','/api/clients/abc',None,T)
ok("non-numeric id does not crash", st in (404,400), f"got {st}")
st,d = call('POST','/api/payments',{'client_id':0,'amount':1,'paid_date':'2026-01-01'},T)
ok("payment against a non-existent client rejected", st==404, f"got {st}")

print("\n[ XSS / INJECTION ]")
xss = "<script>alert(1)</script>"
st,c = call('POST','/api/clients',{'name':xss,'currency':'USD','monthly_value':1},AT)
st,d = call('GET',f'/api/clients/{c["id"]}',None,AT)
ok("script tag stored verbatim (escaped at render)", d['client']['name']==xss, d['client']['name'])
sqli = "'; DROP TABLE users; --"
st,c2 = call('POST','/api/clients',{'name':sqli,'currency':'USD','monthly_value':1},AT)
st,d = call('GET','/api/me',None,T)
ok("SQL injection attempt harmless — users table intact", st==200, "TABLE GONE")
st,d = call('GET','/api/admin/audit?q=' + urllib.request.quote("' OR 1=1 --"),None,T)
ok("injection through a filter param is parameterised", st==200, f"got {st}")

print("\n[ PATH TRAVERSAL ]")
for p in ('/../server.py','/static/../../etc/passwd','/..%2fserver.py'):
    try:
        r=urllib.request.Request(B+p)
        body=urllib.request.urlopen(r,timeout=10).read()[:200]
        leaked = b'DATABASE_URL' in body or b'root:' in body or b'def main' in body
    except Exception:
        leaked=False
    ok(f"cannot read files via {p}", not leaked, "FILE LEAKED")

print("\n[ COMMISSION INTEGRITY ]")
_,cc = call('POST','/api/clients',{'name':'Money Client','currency':'USD','monthly_value':1000},AT)
call('POST','/api/payments',{'client_id':cc['id'],'amount':1000,'currency':'USD','paid_date':'2026-01-15'},T)
call('POST','/api/payments',{'client_id':cc['id'],'amount':1000,'currency':'USD','paid_date':'2026-02-15'},T)
st,d = call('GET','/api/commissions',None,T)
row=[c for c in d['commissions'] if c['agent_id']==AID][0]
ok("earned = 600 + 100", abs(row['earned_usd']-700)<0.01, row['earned_usd'])
st,d = call('POST','/api/payouts',{'agent_id':AID,'amount_usd':700},T)
PO=d.get('id')
st,d = call('POST','/api/payouts',{'agent_id':AID,'amount_usd':700},T)
ok("cannot double-pay the same outstanding balance", st==400, f"got {st} — overpayment allowed")
call('PATCH',f'/api/payouts/{PO}',{'status':'Cancelled'},T)
st,d = call('GET','/api/commissions',None,T)
row=[c for c in d['commissions'] if c['agent_id']==AID][0]
ok("cancelling a payout restores the outstanding balance", abs(row['outstanding_usd']-700)<0.01, row['outstanding_usd'])

print("\n[ SAME-DAY PAYMENT ORDERING ]")
_,c3 = call('POST','/api/clients',{'name':'SameDay','currency':'USD','monthly_value':500},AT)
ids=[]
for i in range(3):
    _,p = call('POST','/api/payments',{'client_id':c3['id'],'amount':500,'currency':'USD','paid_date':'2026-03-01'},T)
    ids.append(p['id'])
st,d = call('GET',f'/api/clients/{c3["id"]}',None,T)
firsts=[p for p in d['payments'] if p['commission_kind']=='First payment']
ok("exactly one payment counts as first even on the same date", len(firsts)==1, len(firsts))
ok("the earliest id is the first payment", firsts and firsts[0]['id']==ids[0], firsts[:1])

print("\n[ CONCURRENCY ]")
res=[]
def add(i):
    st,_=call('POST','/api/clients',{'name':f'Race {i}','currency':'USD','monthly_value':1},AT)
    res.append(st)
ths=[threading.Thread(target=add,args=(i,)) for i in range(12)]
[t.start() for t in ths]; [t.join() for t in ths]
ok(f"12 concurrent creates all succeeded", res.count(200)==12, f"{res.count(200)}/12")
_,d = call('GET','/api/clients',None,AT)
names=[c['name'] for c in d['clients'] if c['name'].startswith('Race ')]
ok("no duplicates or lost writes under concurrency", len(names)==len(set(names))==12, len(names))

print("\n[ SESSION HYGIENE ]")
st,d = call('GET','/api/me',None,'Bearer-nonsense')
ok("garbage token rejected", st==401, f"got {st}")
st,d = call('PATCH',f'/api/admin/agents/{AID}',{'status':'Suspended'},T)
st,d = call('GET','/api/agent/overview',None,AT)
ok("suspending an agent kills their live session", st==401, f"got {st}")
call('PATCH',f'/api/admin/agents/{AID}',{'status':'Active'},T)

print("\n[ FX EDGE CASES ]")
st,d = call('PUT','/api/admin/fx',{'rates':[{'code':'USD','rate':50}]},T)
_,fx = call('GET','/api/fx',None,T)
usd=[f for f in fx['fx'] if f['code']=='USD'][0]
ok("USD cannot be moved off 1.0", float(usd['rate'])==1.0, usd['rate'])
st,d = call('PUT','/api/admin/fx',{'rates':[{'code':'ZAR','rate':0}]},T)
_,fx = call('GET','/api/fx',None,T)
zar=[f for f in fx['fx'] if f['code']=='ZAR'][0]
ok("zero/negative rate ignored (would divide by zero)", float(zar['rate'])>0, zar['rate'])

print("\n[ HEALTH ]")
st,d = call('GET','/api/health')
ok("health endpoint needs no auth (for the cron ping)", st==200, f"got {st}")
ok("health does not leak the connection string", 'postgres://' not in json.dumps(d) and '@' not in json.dumps(d), d)

print("\n" + "="*72); print(f"  {PASS} passed, {FAIL} failed")
if ISSUES:
    print("\n  ISSUES:")
    for i in ISSUES: print("   -", i)
print("="*72)
