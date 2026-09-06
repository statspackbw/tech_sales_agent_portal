#!/usr/bin/env python3
"""Both crash directions. Run with a live primary + standby.
   Usage: test_failure_modes.py <base_url> <primary_db> <standby_db>"""
import json, sys, urllib.request, urllib.error, subprocess, time
B=sys.argv[1] if len(sys.argv)>1 else 'http://localhost:8470'
PRI=sys.argv[2] if len(sys.argv)>2 else 'neondb'
STB=sys.argv[3] if len(sys.argv)>3 else 'supadb'
P=F=0
def ok(l,c,x=""):
    global P,F
    if c: P+=1; print(f"  PASS  {l}")
    else: F+=1; print(f"  FAIL  {l}   <-- {x}")
def call(m,p,b=None,t=None):
    r=urllib.request.Request(B+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=45) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or '{}')
    except Exception as e: return 0, {'error':str(e)[:80]}
def psql(c):
    cmd = "/usr/lib/postgresql/16/bin/psql -h /tmp -p 5433 -U postgres -c " + repr(c)
    subprocess.run(["su", "postgres", "-c", cmd], capture_output=True)

T=call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})[1]['token']
a=call('POST','/api/admin/agents',{'name':'A','email':'fm@x.com'},T)[1]
s=call('POST','/api/login',{'email':'fm@x.com','password':a['temp_password']})[1]
call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'Passw0rd!2026'},s['token'])
c=call('POST','/api/clients',{'name':'C','currency':'USD','monthly_value':1000},s['token'])[1]
call('POST','/api/payments',{'client_id':c['id'],'amount':1000,'currency':'USD','paid_date':'2026-01-15'},T)
call('POST','/api/admin/system/mirror',{},T)

print("[ STANDBY CRASHES — portal must be unaffected ]")
psql(f"DROP DATABASE {STB} WITH (FORCE);")
ok("portal keeps serving", call('GET','/api/admin/overview',None,T)[0]==200)
ok("writes still accepted", call('POST','/api/clients',{'name':'During','currency':'USD','monthly_value':5},s['token'])[0]==200)
ok("sign-in still works", call('POST','/api/login',{'email':'fm@x.com','password':'Passw0rd!2026'})[0]==200)
call('POST','/api/admin/system/mirror',{},T)
ok("warning raised", call('GET','/api/admin/alerts',None,T)[1].get('count',0)>=1)
ok("health still green", call('GET','/api/health')[1].get('ok') is True)
psql(f"CREATE DATABASE {STB};")
st,_ = call('POST','/api/admin/system/mirror',{},T)
ok("mirror recovers automatically once it returns", st==200)

print("\n[ PRIMARY CRASHES — portal must fail clearly, not silently ]")
psql(f"DROP DATABASE {PRI} WITH (FORCE);")
time.sleep(1)
st,d = call('GET','/api/health')
ok("health endpoint still answers", st==503, st)
ok("health names the problem", d.get('database')=='UNREACHABLE', d)
ok("health gives the recovery step", 'DATABASE_URL' in (d.get('advice') or ''), d.get('advice'))
st,d = call('GET','/api/admin/overview',None,T)
ok("clear 503 rather than a generic 500", st==503, st)
ok("readable message", 'not responding' in (d.get('error') or ''), d.get('error'))
ok("no writes accepted", call('POST','/api/clients',{'name':'X','currency':'USD','monthly_value':1},s['token'])[0]==503)
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
