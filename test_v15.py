#!/usr/bin/env python3
"""v1.5: agent search, client deletion, keep-alive."""
import json, sys, urllib.request, urllib.error
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
def onboard(email,name,tok,**kw):
    st,a=call('POST','/api/admin/agents',dict(kw,email=email,name=name),tok)
    if st!=200: return None,None
    _,s=call('POST','/api/login',{'email':email,'password':a['temp_password']})
    call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'Passw0rd!2026'},s['token'])
    return a['id'], s['token']
from urllib.parse import quote

print("="*72); print("v1.5 — agent search, client deletion, keep-alive"); print("="*72)
_,d=call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
SUP=d['token']
A1,T1=onboard('thabo.mokoena@statspack.co.ls','Thabo Mokoena',SUP,country='Lesotho',agent_type='Tech Sales Agent')
A2,T2=onboard('naledi@kalahari.co.bw','Naledi Dlamini',SUP,country='Botswana',agent_type='Channel Partner')
A3,T3=onboard('chipo@statspack.co.ls','Chipo Banda',SUP,country='Zambia',agent_type='Reseller')

print("\n[ AGENT SEARCH ]")
st,d=call('GET','/api/admin/agents',None,SUP)
ok("unfiltered lists everyone", d['matched']==3, d['matched'])
for q,expect,label in [('thabo',['Thabo Mokoena'],'first name'),
                       ('MOKOENA',['Thabo Mokoena'],'surname, case-insensitive'),
                       ('kalahari',['Naledi Dlamini'],'email domain'),
                       ('botswana',['Naledi Dlamini'],'country'),
                       ('reseller',['Chipo Banda'],'agent type'),
                       ('statspack.co.ls',['Chipo Banda','Thabo Mokoena'],'partial email')]:
    st,d=call('GET','/api/admin/agents?q='+quote(q),None,SUP)
    got=sorted(a['name'] for a in d['agents'])
    ok(f"search by {label} ('{q}')", got==sorted(expect), got)
st,d=call('GET','/api/admin/agents?q=zzzznothing',None,SUP)
ok("no matches returns empty, not an error", st==200 and d['matched']==0, d.get('matched'))
st,d=call('GET','/api/admin/agents?agent_type=Reseller',None,SUP)
ok("filter by type", [a['name'] for a in d['agents']]==['Chipo Banda'], [a['name'] for a in d['agents']])
call('PATCH','/api/admin/agents/%s'%A3,{'status':'Suspended'},SUP)
st,d=call('GET','/api/admin/agents?status=Suspended',None,SUP)
ok("filter by status", [a['name'] for a in d['agents']]==['Chipo Banda'], [a['name'] for a in d['agents']])
st,d=call('GET','/api/admin/agents?q=chipo&status=Active',None,SUP)
ok("search and filter combine", d['matched']==0, d['matched'])
call('PATCH','/api/admin/agents/%s'%A3,{'status':'Active'},SUP)
st,d=call('GET','/api/admin/agents?q=%27+OR+1%3D1+--',None,SUP)
ok("injection through search is harmless", st==200 and d['matched']==0, d.get('matched'))

print("\n[ SEARCH RESPECTS COMPANY BOUNDARIES ]")
_,co=call('POST','/api/admin/companies',{'name':'Rival Ltd','admin_name':'RA','admin_email':'ra@rival.com'},SUP)
_,s=call('POST','/api/login',{'email':'ra@rival.com','password':co['temp_password']})
RT=s['token']; call('POST','/api/change-password',{'current_password':co['temp_password'],'new_password':'Rival!2026'},RT)
onboard('local@rival.com','Local Person',RT)
st,d=call('GET','/api/admin/agents?q=thabo',None,RT)
ok("company admin cannot find another company's agent", d['matched']==0, [a['name'] for a in d['agents']])
st,d=call('GET','/api/admin/agents?q=local',None,RT)
ok("company admin finds their own", d['matched']==1, d['matched'])

print("\n[ DELETING A CLIENT WITH NO PAYMENTS ]")
_,c1=call('POST','/api/clients',{'name':'Never Paid Ltd','currency':'USD','monthly_value':500},T1)
call('POST','/api/clients/%s/timeline'%c1['id'],{'kind':'call','body':'Spoke to them'},T1)
st,d=call('GET','/api/clients/%s/footprint'%c1['id'],None,SUP)
ok("footprint says it can be deleted simply", d['can_delete_simply'] is True, d)
ok("footprint counts activity entries", d['footprint']['timeline_entries']>=2, d['footprint'])
st,d=call('DELETE','/api/clients/%s'%c1['id'],{},SUP)
ok("admin deletes a client that never paid", st==200, d)
st,d=call('GET','/api/clients/%s'%c1['id'],None,SUP)
ok("it is gone", st==404, st)

print("\n[ DELETING A CLIENT THAT HAS PAID ]")
_,c2=call('POST','/api/clients',{'name':'Paying Client','currency':'USD','monthly_value':1000},T1)
call('POST','/api/payments',{'client_id':c2['id'],'amount':1000,'currency':'USD','paid_date':'2026-01-15'},SUP)
call('POST','/api/payments',{'client_id':c2['id'],'amount':1000,'currency':'USD','paid_date':'2026-02-15'},SUP)
st,d=call('GET','/api/clients/%s/footprint'%c2['id'],None,SUP)
ok("footprint flags that a super user is needed", d['requires_super'] is True, d)
ok("footprint reports the money at stake", abs(d['footprint']['collected_usd']-2000)<0.01
   and abs(d['footprint']['commission_usd']-700)<0.01, d['footprint'])
st,d=call('DELETE','/api/clients/%s'%c2['id'],{},SUP)
ok("plain delete still refuses a paying client", st==400, st)
st,d=call('POST','/api/admin/agents',{'name':'Plain','email':'plain@statspack.co.ls','role':'admin'},SUP)
_,s2=call('POST','/api/login',{'email':'plain@statspack.co.ls','password':d['temp_password']})
PT=s2['token']; call('POST','/api/change-password',{'current_password':d['temp_password'],'new_password':'Plain!2026x'},PT)
st,d=call('DELETE','/api/clients/%s/purge'%c2['id'],{'confirm_name':'Paying Client'},PT)
ok("non-super admin cannot purge a paying client", st==403, st)
st,d=call('DELETE','/api/clients/%s/purge'%c2['id'],{'confirm_name':'Paying Client'},T1)
ok("agent cannot purge at all", st==403, st)
st,d=call('DELETE','/api/clients/%s/purge'%c2['id'],{'confirm_name':'Wrong'},SUP)
ok("wrong confirmation name refused", st==400, st)
st,d=call('GET','/api/clients/%s/export'%c2['id'],None,SUP)
ok("pre-delete export includes payments", len(d['payments'])==2, len(d.get('payments',[])))
st,d=call('DELETE','/api/clients/%s/purge'%c2['id'],{'confirm_name':'paying client'},SUP)
ok("super user purges it (case-insensitive name)", st==200, d)
ok("reports what was erased", d['deleted']['payments']==2 and abs(d['collected_usd']-2000)<0.01, d)
st,d=call('GET','/api/admin/overview',None,SUP)
ok("company totals drop to zero", d['totals']['collected_usd']==0, d['totals'])
st,d=call('GET','/api/admin/agents/%s/progress'%A1,None,SUP)
ok("agent's commission recalculates", d['metrics']['earned_usd']==0, d['metrics']['earned_usd'])
st,d=call('GET','/api/admin/backup',None,SUP)
live={c['id'] for c in d['clients']}
ok("no orphaned payments", not [p for p in d['payments'] if p['client_id'] not in live], 'orphans')
ok("no orphaned timeline entries", not [e for e in d['client_events'] if e['client_id'] not in live], 'orphans')
ok("no orphaned partnerships", not [k for k in d['collaborations'] if k['client_id'] not in live], 'orphans')
st,d=call('GET','/api/admin/audit?action=client.PURGED',None,SUP)
ok("deletion recorded in the log", d['matched']==1, d['matched'])
ok("log names the money erased", '2,000' in d['audit'][0]['detail'], d['audit'][0]['detail'])

print("\n[ KEEP-ALIVE ]")
st,d=call('GET','/api/admin/system',None,SUP)
ka=d.get('keepalive')
ok("system health reports keep-alive", ka is not None, d.keys())
ok("it states the window", 'window' in ka and ka['window'], ka)
ok("it projects compute usage", ka['cu_hours_per_month']>0, ka)
ok("default window stays inside the free quota", ka['within_quota'] is True,
   f"{ka['cu_hours_per_month']} of {ka['quota_cu_hours']}")
ok("no quota incident raised at the default", not any(i['kind']=='keepalive.quota' for i in d['incidents']),
   [i['kind'] for i in d['incidents']])
print(f"\n  {P} passed, {F} failed")
if ISSUES:
    print("\n  ISSUES:"); [print("   -",i) for i in ISSUES]
sys.exit(1 if F else 0)
