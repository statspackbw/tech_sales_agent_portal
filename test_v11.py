#!/usr/bin/env python3
"""Tests for v1.1: audit filters, FX sync, permanent deletion."""
import json, sys, urllib.request, urllib.error
B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8470"
PASS = FAIL = 0
def ok(l, c, x=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  PASS  {l}")
    else: FAIL += 1; print(f"  FAIL  {l} {x}")
def call(m, p, b=None, t=None):
    r = urllib.request.Request(B+p, method=m, headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None, timeout=25) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or '{}')

print("="*68); print("v1.1 — audit filters, FX sync, permanent deletion"); print("="*68)

_, d = call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
ADMIN = d['token']

print("\n[ SUPER USER ]")
st, d = call('GET','/api/me',None,ADMIN)
ok("bootstrap admin is a super user", d['user'].get('is_super') is True, d['user'])

_, a1 = call('POST','/api/admin/agents',{'name':'Thabo Mokoena','email':'thabo@statspack.co.ls','country':'Lesotho','quota_usd':60000},ADMIN)
AG1 = a1['id']
_, s1 = call('POST','/api/login',{'email':'thabo@statspack.co.ls','password':a1['temp_password']})
T1 = s1['token']
call('POST','/api/change-password',{'current_password':a1['temp_password'],'new_password':'ThaboSells!2026'},T1)

_, a2 = call('POST','/api/admin/agents',{'name':'Second Admin','email':'admin2@statspack.co.ls','role':'admin'},ADMIN)
AG2 = a2['id']
_, s2 = call('POST','/api/login',{'email':'admin2@statspack.co.ls','password':a2['temp_password']})
T2 = s2['token']
call('POST','/api/change-password',{'current_password':a2['temp_password'],'new_password':'Admin2Pass!2026'},T2)

st, d = call('GET','/api/me',None,T2)
ok("a second admin is NOT super by default", d['user'].get('is_super') is False, d['user'])
st, d = call('GET',f'/api/admin/agents/{AG1}/footprint',None,T2)
ok("non-super admin cannot see the purge footprint", st==403, st)
st, d = call('DELETE',f'/api/admin/agents/{AG1}/purge',{'confirm_name':'Thabo Mokoena'},T2)
ok("non-super admin cannot purge", st==403, st)
st, d = call('DELETE',f'/api/admin/agents/{AG1}/purge',{'confirm_name':'Thabo Mokoena'},T1)
ok("an agent cannot purge anyone", st==403, st)

print("\n[ BUILD DATA TO DESTROY ]")
_, c1 = call('POST','/api/clients',{'name':'Lesotho Revenue Authority','currency':'LSL','monthly_value':18000,'stage':'Proposal'},T1)
CID = c1['id']
for date, amt in [('2026-01-15',18000),('2026-02-15',18000),('2026-03-15',18000)]:
    call('POST','/api/payments',{'client_id':CID,'amount':amt,'currency':'LSL','paid_date':date},ADMIN)
call('POST','/api/payouts',{'agent_id':AG1,'amount_usd':300,'period_label':'Jan'},ADMIN)
call('POST','/api/admin/reviews',{'agent_id':AG1,'rating':4,'body':'Strong quarter.'},ADMIN)
st, d = call('GET',f'/api/admin/agents/{AG1}/footprint',None,ADMIN)
f = d['footprint']
ok("footprint counts clients", f['clients']==1, f)
ok("footprint counts payments", f['payments']==3, f)
ok("footprint totals collections", abs(f['collected_usd']-3000)<0.01, f['collected_usd'])
ok("footprint counts notes", f['reviews']==1, f)

st, d = call('GET',f'/api/admin/agents/{AG1}/export',None,ADMIN)
ok("pre-delete export includes payments", len(d['payments'])==3, len(d.get('payments',[])))
ok("export omits the password hash", 'password_hash' not in d['agent'], 'LEAK')
ok("export carries the commission summary", abs(d['commission_summary']['earned_usd']-800)<0.01, d['commission_summary']['earned_usd'])

print("\n[ DELETION GUARDS ]")
st, d = call('DELETE',f'/api/admin/agents/{AG1}/purge',{'confirm_name':'Wrong Name'},ADMIN)
ok("wrong confirmation name is rejected", st==400, st)
st, d = call('DELETE',f'/api/admin/agents/{AG1}/purge',{},ADMIN)
ok("missing confirmation is rejected", st==400, st)
_, me = call('GET','/api/me',None,ADMIN)
st, d = call('DELETE',f"/api/admin/agents/{me['user']['id']}/purge",{'confirm_name':me['user']['name']},ADMIN)
ok("super user cannot delete themselves", st==400, st)
st, d = call('GET',f'/api/admin/agents/{AG1}/progress',None,ADMIN)
ok("agent still intact after failed attempts", st==200 and d['metrics']['clients_total']==1, st)

print("\n[ THE DELETION ]")
st, d = call('DELETE',f'/api/admin/agents/{AG1}/purge',{'confirm_name':'thabo mokoena'},ADMIN)
ok("correct name (case-insensitive) deletes", st==200, d)
ok("reports clients removed", d['deleted']['clients']==1, d.get('deleted'))
ok("reports payments removed", d['deleted']['payments']==3, d.get('deleted'))
ok("reports sessions killed", d['deleted']['sessions']>=1, d.get('deleted'))

st, d = call('GET',f'/api/admin/agents/{AG1}/progress',None,ADMIN)
ok("agent is gone", st==404, st)
st, d = call('GET','/api/clients',None,ADMIN)
ok("their clients are gone", len(d['clients'])==0, len(d['clients']))
st, d = call('GET','/api/payments',None,ADMIN)
ok("their payments are gone", len(d['payments'])==0, len(d['payments']))
st, d = call('GET','/api/payouts',None,ADMIN)
ok("their payouts are gone", len(d['payouts'])==0, len(d['payouts']))
st, d = call('GET','/api/agent/overview',None,T1)
ok("their session no longer works", st==401, st)
st, d = call('POST','/api/login',{'email':'thabo@statspack.co.ls','password':'ThaboSells!2026'})
ok("they cannot sign back in", st==401, st)
st, d = call('GET','/api/admin/overview',None,ADMIN)
ok("dashboard totals recalculate to zero", d['totals']['collected_usd']==0, d['totals'])

print("\n[ AUDIT TRAIL SURVIVES ]")
st, d = call('GET','/api/admin/audit?action=agent.PURGED',None,ADMIN)
ok("the deletion is recorded", len(d['audit'])==1, len(d['audit']))
ok("record names the agent", 'Thabo Mokoena' in d['audit'][0]['detail'], d['audit'][0]['detail'])
ok("record states what was destroyed", '3 payment(s)' in d['audit'][0]['detail'], d['audit'][0]['detail'])

print("\n[ AUDIT FILTERS ]")
st, d = call('GET','/api/admin/audit',None,ADMIN)
total = d['matched']
ok("unfiltered returns everything", total > 10, total)
ok("filter options are offered", len(d['actors'])>=1 and len(d['actions'])>5, (len(d['actors']),len(d['actions'])))
st, d = call('GET','/api/admin/audit?action=login.success',None,ADMIN)
ok("filter by action", all(a['action']=='login.success' for a in d['audit']) and d['matched']>0, d['matched'])
st, d = call('GET','/api/admin/audit?action=payment*',None,ADMIN)
ok("wildcard action prefix", all(a['action'].startswith('payment') for a in d['audit']) and d['matched']==3, d['matched'])
st, d = call('GET','/api/admin/audit?actor=admin@statspack.co.ls',None,ADMIN)
ok("filter by actor", all(a['actor']=='admin@statspack.co.ls' for a in d['audit']), d['matched'])
st, d = call('GET','/api/admin/audit?q=lesotho',None,ADMIN)
ok("free-text search on detail", d['matched']>0 and all('lesotho' in (a['detail']+a['actor']+a['action']).lower() for a in d['audit']), d['matched'])
st, d = call('GET','/api/admin/audit?from=2020-01-01&to=2020-01-02',None,ADMIN)
ok("date range excludes everything out of range", d['matched']==0, d['matched'])
st, d = call('GET','/api/admin/audit?limit=5',None,ADMIN)
ok("limit caps rows but reports true total", len(d['audit'])==5 and d['matched']>5, (len(d['audit']),d['matched']))
st, d = call('GET','/api/admin/audit?action=login.success&actor=nobody@nowhere.com',None,ADMIN)
ok("filters combine with AND", d['matched']==0, d['matched'])
st, d = call('GET','/api/admin/audit',None,T2)
ok("non-super admin can still read the log", st==200, st)

print("\n[ FX STATUS ]")
st, d = call('GET','/api/admin/fx/status',None,ADMIN)
ok("status reports no key configured", st==200 and d['key_present'] is False, d)
ok("provider named", d['provider']=='ExchangeRate-API', d)
st, d = call('POST','/api/admin/fx/sync',{},ADMIN)
ok("sync without a key fails cleanly, not with a crash", st==502 and 'EXCHANGERATE_API_KEY' in d['error'], (st,d))
st, d = call('POST','/api/admin/fx/sync',{},T1 if False else T2)
ok("any admin may trigger a sync", st in (200,502), st)

print("\n" + "="*68); print(f"  {PASS} passed, {FAIL} failed"); print("="*68)
sys.exit(1 if FAIL else 0)
