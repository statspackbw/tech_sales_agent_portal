#!/usr/bin/env python3
"""v1.2: companies, per-agent commission, timeline, industries, avatars, log drill-down."""
import json, sys, urllib.request, urllib.error, base64
B = sys.argv[1] if len(sys.argv)>1 else "http://localhost:8470"
PASS=FAIL=0
def ok(l,c,x=""):
    global PASS,FAIL
    if c: PASS+=1; print(f"  PASS  {l}")
    else: FAIL+=1; print(f"  FAIL  {l} {x}")
def call(m,p,b=None,t=None):
    r=urllib.request.Request(B+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=25) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or '{}')
def onboard(email, pw_new, tok_admin, **kw):
    st,a = call('POST','/api/admin/agents',dict(kw,email=email),tok_admin)
    if st!=200: return None,a
    _,s = call('POST','/api/login',{'email':email,'password':a['temp_password']})
    call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':pw_new},s['token'])
    return a['id'], s['token']

print("="*70); print("v1.2 — multi-company, per-agent commission, timeline, log detail"); print("="*70)
_,d = call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
SUPER = d['token']

print("\n[ WORLDWIDE CURRENCIES ]")
st,d = call('GET','/api/fx',None,SUPER)
codes = {f['code'] for f in d['fx']}
ok("160 currencies seeded", len(codes)>=160, len(codes))
for c in ('JPY','BRL','INR','NGN','KES','XOF','LSL','ZAR'):
    if c not in codes: ok(f"{c} present", False, "missing"); break
else: ok("major world + SADC currencies all present", True)

print("\n[ TAXONOMIES ]")
st,d = call('GET','/api/me',None,SUPER)
ok("agent types exposed", len(d['agent_types'])>=6, d.get('agent_types'))
ok("industries exposed", len(d['industries'])>=20, len(d.get('industries',[])))
ok("host company identified", d['company']['is_host'] is True, d.get('company'))

print("\n[ AGENT TYPE + PER-AGENT COMMISSION ]")
A1,T1 = onboard('thabo@statspack.co.ls','ThaboSells!2026',SUPER,
                name='Thabo Mokoena',country='Lesotho',quota_usd=60000,
                agent_type='Tech Sales Agent')
ok("agent created with a type", A1 is not None, T1)
st,d = call('GET',f'/api/admin/agents/{A1}/progress',None,SUPER)
ok("agent_type stored", d['agent']['agent_type']=='Tech Sales Agent', d['agent'].get('agent_type'))
ok("inherits portal rates by default", d['rules']['source']=='portal default' and d['rules']['first_rate']==0.6, d['rules'])

st,d = call('POST','/api/admin/agents',{'name':'Bad','email':'bad@x.com','agent_type':'Wizard'},SUPER)
ok("unknown agent type rejected", st==400, st)

A2,T2 = onboard('naledi@statspack.co.ls','NalediSells!2026',SUPER,
                name='Naledi Dlamini',country='South Africa',agent_type='Channel Partner',
                first_rate=0.45, recurring_rate=0.05, window_months=24)
st,d = call('GET',f'/api/admin/agents/{A2}/progress',None,SUPER)
ok("per-agent override stored", d['rules']['first_rate']==0.45 and d['rules']['window_months']==24, d['rules'])
ok("override is labelled as such", d['rules']['source']=='agent override', d['rules'])

st,d = call('PATCH',f'/api/admin/agents/{A2}',{'first_rate':1.5},SUPER)
ok("out-of-range rate rejected", st==400, st)

print("\n[ SETTINGS ARE THE SINGLE SOURCE OF TRUTH FOR THE HOST COMPANY ]")
call('PUT','/api/admin/settings',{'settings':{'commission_first_rate':0.7}},SUPER)
st,d = call('GET',f'/api/admin/agents/{A1}/progress',None,SUPER)
ok("editing Settings actually moves an agent's rate", d['rules']['first_rate']==0.7, d['rules'])
st,co = call('GET','/api/admin/companies',None,SUPER)
host = [c for c in co['companies'] if c['is_host']][0]
ok("host company row stays in step with Settings", abs(host['first_rate']-0.7)<1e-6, host['first_rate'])
call('PUT','/api/admin/settings',{'settings':{'commission_first_rate':0.6}},SUPER)
st,d = call('GET',f'/api/admin/agents/{A1}/progress',None,SUPER)
ok("other companies are unaffected by portal settings", d['rules']['first_rate']==0.6, d['rules'])

print("\n[ COMMISSION USES THE AGENT'S OWN RATES ]")
_,c1 = call('POST','/api/clients',{'name':'LRA','currency':'LSL','monthly_value':18000,
    'industry':'Government & Public Sector','stage':'Proposal'},T1)
_,c2 = call('POST','/api/clients',{'name':'Cape Retail','currency':'LSL','monthly_value':18000,
    'industry':'Retail & Wholesale','stage':'Proposal'},T2)
call('POST','/api/payments',{'client_id':c1['id'],'amount':18000,'currency':'LSL','paid_date':'2026-01-15'},SUPER)
st,p2 = call('POST','/api/payments',{'client_id':c2['id'],'amount':18000,'currency':'LSL','paid_date':'2026-01-15'},SUPER)
ok("standard agent earns 60% of $1000", abs(call('GET',f'/api/admin/agents/{A1}/progress',None,SUPER)[1]['metrics']['earned_usd']-600)<0.01)
ok("override agent earns 45% of $1000", abs(call('GET',f'/api/admin/agents/{A2}/progress',None,SUPER)[1]['metrics']['earned_usd']-450)<0.01,
   call('GET',f'/api/admin/agents/{A2}/progress',None,SUPER)[1]['metrics']['earned_usd'])
call('POST','/api/payments',{'client_id':c2['id'],'amount':18000,'currency':'LSL','paid_date':'2027-06-15'},SUPER)
st,d = call('GET',f'/api/clients/{c2["id"]}',None,SUPER)
late = [p for p in d['payments'] if p['paid_date'][:10]=='2027-06-15'][0]
ok("24-month window keeps a month-17 payment earning", late['commission_kind']=='Monthly (year 1)' and abs(late['agent_commission_usd']-50)<0.01, late)

print("\n[ INDUSTRY ]")
st,d = call('GET',f'/api/clients/{c1["id"]}',None,T1)
ok("industry stored on client", d['client']['industry']=='Government & Public Sector', d['client'].get('industry'))
st,d = call('POST','/api/clients',{'name':'X','industry':'Space Mining'},T1)
ok("unknown industry rejected", st==400, st)

print("\n[ CLIENT TIMELINE / FLOWCHART ]")
st,d = call('GET',f'/api/clients/{c1["id"]}/timeline',None,T1)
kinds = [e['kind'] for e in d['timeline']]
ok("creation logged a stage event", 'stage' in kinds, kinds)
ok("payment logged onto the timeline", len(d['timeline'])>=3, len(d['timeline']))
ok("progress reports position on pipeline", d['progress']['index']==4 and d['progress']['closed'], d['progress'])

st,d = call('POST',f'/api/clients/{c1["id"]}/timeline',
    {'kind':'meeting','body':'Met the CFO. Wants a security review.','next_step':'Send SOC2 pack','due_date':'2026-09-01'},T1)
ok("agent can log an activity", st==200, d)
st,d = call('POST',f'/api/clients/{c1["id"]}/timeline',{'kind':'telepathy','body':'x'},T1)
ok("unknown activity kind rejected", st==400, st)
st,d = call('POST',f'/api/clients/{c1["id"]}/timeline',{'kind':'note','body':''},T1)
ok("empty note rejected", st==400, st)
st,d = call('GET',f'/api/clients/{c2["id"]}/timeline',None,T1)
ok("agent cannot read another agent's timeline", st==403, st)

_,before = call('GET',f'/api/clients/{c1["id"]}/timeline',None,T1)
call('PATCH',f'/api/clients/{c1["id"]}',{'stage':'Churned','stage_note':'Budget cut.'},T1)
_,after = call('GET',f'/api/clients/{c1["id"]}/timeline',None,T1)
moved = [e for e in after['timeline'] if e['kind']=='stage' and e['to_stage']=='Churned']
ok("stage change recorded with from/to", moved and moved[0]['from_stage']=='Won', moved[:1])
ok("stage note captured", moved and 'Budget cut' in moved[0]['body'], moved[:1])
ok("lost/churned shown as closed-negative", after['progress']['index']==-1, after['progress'])
st,d = call('DELETE',f'/api/clients/{c1["id"]}/timeline/{moved[0]["id"]}',{},SUPER)
ok("stage events cannot be deleted", st==400, st)
call('PATCH',f'/api/clients/{c1["id"]}',{'stage':'Won'},T1)

print("\n[ AVATAR ]")
png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n"+b"x"*200).decode()
st,d = call('PUT','/api/me/avatar',{'avatar':png},T1)
ok("agent uploads a profile picture", st==200, d)
st,d = call('GET','/api/me',None,T1)
ok("avatar returned with profile", d['user']['avatar'].startswith('data:image/png'), d['user'].get('avatar','')[:30])
st,d = call('PUT','/api/me/avatar',{'avatar':'data:text/html;base64,PHNjcmlwdD4='},T1)
ok("non-image rejected", st==400, st)
st,d = call('PUT','/api/me/avatar',{'avatar':'data:image/png;base64,'+('A'*500000)},T1)
ok("oversized image rejected", st==413, st)
st,d = call('PUT','/api/me/avatar',{'avatar':''},T1)
ok("avatar can be removed", st==200 and d['avatar']=='', d)

print("\n[ COMPANIES — a client managing their own agents ]")
st,d = call('POST','/api/admin/companies',{'name':'Kalahari Systems','country':'Botswana',
    'admin_name':'Kagiso Seretse','admin_email':'kagiso@kalahari.co.bw',
    'first_rate':0.5,'recurring_rate':0.08,'window_months':18},SUPER)
ok("super registers a company + its admin", st==200 and d.get('temp_password'), d)
CO = d['id']; CO_PW = d['temp_password']
_,s = call('POST','/api/login',{'email':'kagiso@kalahari.co.bw','password':CO_PW})
CT = s['token']
call('POST','/api/change-password',{'current_password':CO_PW,'new_password':'Kalahari!2026'},CT)

st,d = call('GET','/api/me',None,CT)
ok("company admin is not a super user", d['user']['is_super'] is False, d['user'])
ok("company admin belongs to their company", d['user']['company_id']==CO, d['user'])
st,d = call('GET','/api/admin/companies',None,CT)
ok("company admin cannot list companies", st==403, st)
st,d = call('POST','/api/admin/companies',{'name':'Sneaky','admin_name':'A','admin_email':'a@b.com'},CT)
ok("company admin cannot create companies", st==403, st)

CA1, CAT = onboard('boitumelo@kalahari.co.bw','Boitumelo!2026',CT,
                   name='Boitumelo Rex',agent_type='Reseller')
ok("company admin creates their own agent", CA1 is not None, CAT)
st,d = call('GET',f'/api/admin/agents/{CA1}/progress',None,CT)
ok("their agent inherits the COMPANY rate, not the portal's",
   d['rules']['first_rate']==0.5 and d['rules']['window_months']==18 and d['rules']['source']=='company rate', d['rules'])

print("\n[ TENANT ISOLATION ]")
st,d = call('GET','/api/admin/agents',None,CT)
names = [a['name'] for a in d['agents']]
ok("company admin sees only their own agents", names==['Boitumelo Rex'], names)
st,d = call('GET','/api/admin/agents',None,SUPER)
ok("super sees agents from every company", len(d['agents'])>=3, len(d['agents']))
st,d = call('GET',f'/api/admin/agents/{A1}/progress',None,CT)
ok("company admin cannot open another company's agent", st==403, st)
st,d = call('PATCH',f'/api/admin/agents/{A1}',{'name':'Hacked'},CT)
ok("company admin cannot edit another company's agent", st==403, st)
st,d = call('POST','/api/payouts',{'agent_id':A1,'amount_usd':10},CT)
ok("company admin cannot pay another company's agent", st==403, st)
st,d = call('GET','/api/clients',None,CT)
ok("company admin sees no other company's clients", len(d['clients'])==0, len(d['clients']))
st,d = call('GET',f'/api/clients/{c1["id"]}',None,CT)
ok("company admin cannot open another company's client", st==403, st)
st,d = call('GET','/api/admin/overview',None,CT)
ok("their dashboard totals exclude other companies", d['totals']['collected_usd']==0, d['totals'])
st,d = call('POST','/api/admin/reviews',{'agent_id':A1,'rating':1,'body':'x'},CT)
ok("company admin cannot review another company's agent", st==403, st)

_,cc = call('POST','/api/clients',{'name':'Gaborone Freight','currency':'BWP',
    'monthly_value':9000,'industry':'Logistics & Transport'},CAT)
call('POST','/api/payments',{'client_id':cc['id'],'amount':9000,'currency':'BWP','paid_date':'2026-02-01'},CT)
st,d = call('GET','/api/admin/overview',None,CT)
ok("their own data does appear", d['totals']['clients']==1 and d['totals']['collected_usd']>0, d['totals'])
st,d = call('GET','/api/commissions',None,CT)
ok("company rate applied to their agent's commission",
   abs(d['commissions'][0]['first_payment_usd'] - round(9000/13.5*0.5,2))<0.02, d['commissions'][0])

print("\n[ AUDIT SCOPING + DRILL-DOWN ]")
st,d = call('GET','/api/admin/audit',None,CT)
ok("company admin's log excludes other companies",
   all('kalahari' in a['actor'] or a['actor']=='' for a in d['audit']), [a['actor'] for a in d['audit']][:4])
ok("company admin cannot drill into entries", d['can_drill'] is False, d.get('can_drill'))
st,d = call('GET','/api/admin/audit?action=payment.recorded',None,SUPER)
ok("super can drill", d['can_drill'] is True, d.get('can_drill'))
EID = d['audit'][0]['id']
st,e = call('GET',f'/api/admin/audit/{EID}',None,SUPER)
ok("detail returns structured meta", st==200 and 'amount_usd' in e['meta'], e.get('meta'))
ok("detail identifies the actor", e['actor'] and e['actor']['email'], e.get('actor'))
ok("detail shows nearby activity", isinstance(e['nearby'], list), type(e.get('nearby')))
st,d2 = call('GET',f'/api/admin/audit/{EID}',None,CT)
ok("company admin blocked from drill-down", d2 and st==403, st)
st,d = call('GET','/api/admin/audit/999999',None,SUPER)
ok("missing entry 404s", st==404, st)

print("\n" + "="*70); print(f"  {PASS} passed, {FAIL} failed"); print("="*70)
sys.exit(1 if FAIL else 0)
