#!/usr/bin/env python3
"""Role swap: Supabase becomes primary, Neon becomes standby. No new project."""
import json, sys, urllib.request, urllib.error, subprocess, time
P=F=0
def ok(l,c,x=""):
    global P,F
    if c: P+=1; print(f"  PASS  {l}")
    else: F+=1; print(f"  FAIL  {l}   <-- {x}")
def call(base,m,p,b=None,t=None):
    r=urllib.request.Request(base+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=60) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or '{}')
    except Exception as e: return 0, {'error':str(e)[:70]}
def sql(dbn,q):
    r=subprocess.run(["su","postgres","-c",
      "/usr/lib/postgresql/16/bin/psql -h /tmp -p 5433 -U postgres -d %s -tAc %r" % (dbn,q)],
      capture_output=True,text=True)
    return r.stdout.strip()
A=sys.argv[1]  # portal running on NEON primary
print("="*72); print("ROLE SWAP — no new project needed"); print("="*72)
T=call(A,'POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})[1]['token']
a=call(A,'POST','/api/admin/agents',{'name':'Swap Agent','email':'sw@x.com','country':'Lesotho'},T)[1]
s=call(A,'POST','/api/login',{'email':'sw@x.com','password':a['temp_password']})[1]
call(A,'POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'Passw0rd!2026'},s['token'])
c=call(A,'POST','/api/clients',{'name':'Swap Client','currency':'USD','monthly_value':1000},s['token'])[1]
call(A,'POST','/api/payments',{'client_id':c['id'],'amount':1000,'currency':'USD','paid_date':'2026-01-15'},T)
call(A,'POST','/api/admin/system/mirror',{},T)
ok("Supabase holds the data before the swap", sql('supadb',"SELECT COUNT(*) FROM payments")=='1',
   sql('supadb',"SELECT COUNT(*) FROM payments"))
print("\n  (portal restarted with the two URLs swapped — see runner)")
