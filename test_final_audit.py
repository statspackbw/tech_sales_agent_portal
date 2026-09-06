#!/usr/bin/env python3
"""Pre-deploy audit of the newest code: guards, breaker, collaboration orphans."""
import json, sys, urllib.request, urllib.error, threading, time
B = sys.argv[1] if len(sys.argv)>1 else "http://localhost:8470"
P=F=0; ISSUES=[]
def ok(l,c,x=""):
    global P,F
    if c: P+=1; print(f"  PASS  {l}")
    else: F+=1; ISSUES.append(l); print(f"  FAIL  {l}   <-- {x}")
def call(m,p,b=None,t=None):
    r=urllib.request.Request(B+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=40) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or '{}')
    except Exception as e: return 0, {'error':str(e)[:60]}
def onboard(email,name,tok,**kw):
    st,a=call('POST','/api/admin/agents',dict(kw,email=email,name=name),tok)
    if st!=200: return None,None
    _,s=call('POST','/api/login',{'email':email,'password':a['temp_password']})
    call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'Passw0rd!2026'},s['token'])
    return a['id'], s['token']

print("="*72); print("FINAL PRE-DEPLOY AUDIT"); print("="*72)
_,d=call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
SUP=d['token']
A1,T1 = onboard('lead@x.com','Lead',SUP,country='Lesotho')
A2,T2 = onboard('helper@x.com','Helper',SUP,country='Botswana')
_,c1 = call('POST','/api/clients',{'name':'Shared Client','currency':'USD','monthly_value':1000},T1)
call('POST','/api/payments',{'client_id':c1['id'],'amount':1000,'currency':'USD','paid_date':'2026-01-15'},SUP)
_,k = call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':A2,'split_pct':0.3},T1)
call('PATCH','/api/collaborations/%s'%k['id'],{'status':'Accepted'},T2)

print("\n[ COLLABORATION ORPHANS ]")
st,d = call('GET','/api/admin/agents/%s/footprint'%A2,None,SUP)
ok("footprint mentions partnerships before deleting a partner",
   'collaborations' in json.dumps(d).lower() or d['footprint'].get('collaborations') is not None,
   d.get('footprint'))
st,d = call('DELETE','/api/admin/agents/%s/purge'%A2,{'confirm_name':'Helper'},SUP)
ok("partner agent can be purged", st==200, d)
st,d = call('GET','/api/admin/backup',None,SUP)
live_users = {u['id'] for u in d['users']}
ok("backup includes collaborations at all", 'collaborations' in d, "TABLE MISSING FROM BACKUP")
orphan_collab = [c for c in d.get('collaborations',[])
                 if c['partner_id'] not in live_users or c['owner_id'] not in live_users]
ok("no collaborations left pointing at a deleted agent", not orphan_collab, orphan_collab[:2])
st,d = call('GET','/api/admin/agents/%s/progress'%A1,None,SUP)
ok("lead's commission returns to 100% after the partner is deleted",
   abs(d['metrics']['earned_usd']-600)<0.01, d['metrics']['earned_usd'])
st,d = call('GET','/api/clients/%s'%c1['id'],None,T1)
ok("the shared client still opens normally", st==200, st)

print("\n[ CLIENT DELETION WITH A PARTNERSHIP ]")
A3,T3 = onboard('p2@x.com','Partner Two',SUP)
_,c2 = call('POST','/api/clients',{'name':'Doomed','currency':'USD','monthly_value':5},T1)
_,k2 = call('POST','/api/collaborations',{'client_id':c2['id'],'partner_id':A3,'split_pct':0.2},T1)
call('PATCH','/api/collaborations/%s'%k2['id'],{'status':'Accepted'},T3)
st,d = call('DELETE','/api/clients/%s'%c2['id'],{},SUP)
ok("client with a partnership can be deleted", st==200, d)
st,d = call('GET','/api/admin/backup',None,SUP)
live_clients = {c['id'] for c in d['clients']}
ok("backup still includes collaborations", 'collaborations' in d, "MISSING")
orphan2 = [c for c in d.get('collaborations',[]) if c['client_id'] not in live_clients]
ok("no collaborations left pointing at a deleted client", not orphan2, orphan2[:2])
st,d = call('GET','/api/collaborations',None,T3)
ok("partner's list does not break after the client is gone", st==200, st)
st,d = call('GET','/api/agent/overview',None,T3)
ok("partner's dashboard still loads", st==200, st)

print("\n[ CIRCUIT BREAKER DOES NOT MISFIRE ]")
res=[]
def hit():
    st,_=call('GET','/api/admin/overview',None,SUP); res.append(st)
th=[threading.Thread(target=hit) for _ in range(30)]
[t.start() for t in th]; [t.join() for t in th]
ok(f"30 concurrent requests all succeed ({res.count(200)}/30)", res.count(200)==30,
   [r for r in res if r!=200][:5])
t0=time.time(); st,_=call('GET','/api/admin/overview',None,SUP); el=time.time()-t0
ok(f"normal request stays fast ({el:.2f}s)", st==200 and el<5, el)

print("\n[ REPORTS AFTER DELETIONS ]")
for kind in ('commission','payments','pipeline','agents','activity','payouts'):
    st,d = call('POST','/api/reports/build',{'kind':kind},SUP)
    ok(f"{kind} report still builds", st==200, f"{st} {str(d)[:50]}")

print("\n[ TEAMWORK EDGE CASES ]")
st,d = call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':99999,'split_pct':0.3},T1)
ok("cannot invite a non-existent agent", st==400, st)
st,d = call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':A3,'split_pct':1.5},T1)
ok("split above 90% rejected", st==400, st)
st,d = call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':A3,'split_pct':0},T1)
ok("zero split rejected", st==400, st)
call('PATCH','/api/admin/agents/%s'%A3,{'status':'Suspended'},SUP)
st,d = call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':A3,'split_pct':0.3},T1)
ok("cannot invite a suspended agent", st==400, st)
call('PATCH','/api/admin/agents/%s'%A3,{'status':'Active'},SUP)
st,d = call('GET','/api/agents/directory',None,SUP)
ok("directory works for an admin too", st==200, st)

print("\n[ SYSTEM HEALTH WITHOUT A STANDBY ]")
st,d = call('GET','/api/admin/system',None,SUP)
ok("system page loads with no mirror configured", st==200, st)
ok("primary size still reported", d['primary']['used_mb'] is not None, d.get('primary'))
ok("no false incident when no standby is configured",
   not any(i['kind'].startswith('mirror') for i in d['incidents']), [i['kind'] for i in d['incidents']])

print("\n" + "="*72); print(f"  {P} passed, {F} failed")
if ISSUES:
    print("\n  ISSUES:"); [print("   -",i) for i in ISSUES]
print("="*72)
sys.exit(1 if F else 0)
