#!/usr/bin/env python3
"""v1.3: collaboration + commission split, reports, storage stats, incidents."""
import json, sys, urllib.request, urllib.error
B = sys.argv[1] if len(sys.argv)>1 else "http://localhost:8470"
PASS=FAIL=0; ISSUES=[]
def ok(l,c,x=""):
    global PASS,FAIL
    if c: PASS+=1; print(f"  PASS  {l}")
    else: FAIL+=1; ISSUES.append(l); print(f"  FAIL  {l}   <-- {x}")
def call(m,p,b=None,t=None):
    r=urllib.request.Request(B+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=30) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or '{}')
def onboard(email,name,tok,**kw):
    st,a=call('POST','/api/admin/agents',dict(kw,email=email,name=name),tok)
    _,s=call('POST','/api/login',{'email':email,'password':a['temp_password']})
    call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'Passw0rd!2026'},s['token'])
    return a['id'], s['token']

print("="*72); print("v1.3 — collaboration, reports, storage, incidents"); print("="*72)
_,d=call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
SUP=d['token']
A1,T1 = onboard('lead@statspack.co.ls','Lead Agent',SUP,country='Lesotho',agent_type='Tech Sales Agent',quota_usd=50000)
A2,T2 = onboard('helper@statspack.co.ls','Helper Agent',SUP,country='Botswana',agent_type='Channel Partner')
A3,T3 = onboard('other@statspack.co.ls','Other Agent',SUP,country='Zambia')
_,c1 = call('POST','/api/clients',{'name':'Gaborone Mining','currency':'USD','monthly_value':1000,
    'country':'Botswana','industry':'Mining & Resources','stage':'Proposal'},T1)
_,c2 = call('POST','/api/clients',{'name':'Helper Own Client','currency':'USD','monthly_value':500},T2)
call('POST','/api/payments',{'client_id':c2['id'],'amount':500,'currency':'USD','paid_date':'2026-01-10'},SUP)

print("\n[ AGENT DIRECTORY ]")
st,d = call('GET','/api/agents/directory',None,T1)
ok("agent can browse colleagues", st==200 and len(d['agents'])==2, len(d.get('agents',[])))
ok("directory excludes themselves", all(a['id']!=A1 for a in d['agents']), "self listed")
ok("shows track record", 'clients_won' in d['agents'][0] and 'win_rate' in d['agents'][0], d['agents'][0].keys())
ok("shows the countries they've worked", 'countries' in d['agents'][0], d['agents'][0].keys())
ok("does NOT expose peers' earnings", not any(k in d['agents'][0] for k in
   ('earned_usd','collected_usd','outstanding_usd','quota_usd')), d['agents'][0].keys())
st,d = call('GET','/api/agents/directory?country=Botswana',None,T1)
ok("can filter by country", len(d['agents'])==1 and d['agents'][0]['name']=='Helper Agent', d.get('agents'))
st,d = call('GET','/api/agents/directory?country=Narnia',None,T1)
ok("unknown country returns nobody", len(d['agents'])==0, len(d['agents']))

print("\n[ REQUESTING HELP ]")
st,d = call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':A2,
    'split_pct':0.30,'reason':'Client is in Botswana; needs local presence.'},T1)
ok("lead agent requests a partner", st==200, d)
K = d.get('id')
st,d = call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':A3,'split_pct':0.2},T1)
ok("cannot open a second partnership on the same client", st==409, f"got {st}")
st,d = call('POST','/api/collaborations',{'client_id':c2['id'],'partner_id':A3,'split_pct':0.2},T1)
ok("cannot invite onto someone else's client", st==403, f"got {st}")
st,d = call('POST','/api/collaborations',{'client_id':c1['id'],'partner_id':A1,'split_pct':0.2},T1)
ok("cannot partner with yourself", st in (400,409), f"got {st}")
st,d = call('GET','/api/collaborations',None,T2)
ok("invited agent sees it as incoming", len(d['incoming'])==1, d.get('incoming'))
st,d = call('PATCH',f'/api/collaborations/{K}',{'status':'Accepted'},T3)
ok("an uninvolved agent cannot accept", st==403, f"got {st}")

print("\n[ BEFORE ACCEPTANCE, NOTHING IS SHARED ]")
st,d = call('GET',f'/api/clients/{c1["id"]}',None,T2)
ok("pending partner cannot open the client yet", st==403, f"got {st}")

print("\n[ AFTER ACCEPTANCE ]")
st,d = call('PATCH',f'/api/collaborations/{K}',{'status':'Accepted','response_note':'Happy to help.'},T2)
ok("invited agent accepts", st==200, d)
st,d = call('GET',f'/api/clients/{c1["id"]}',None,T2)
ok("partner can now open the client", st==200, f"got {st}")
ok("client is flagged as shared", d['client'].get('shared_with_me'), d['client'].get('shared_with_me'))
st,d = call('POST',f'/api/clients/{c1["id"]}/timeline',{'kind':'meeting','body':'Visited their Gaborone office.'},T2)
ok("partner can log activity", st==200, d)
st,d = call('PATCH',f'/api/clients/{c1["id"]}',{'stage':'Won'},T2)
ok("partner CANNOT change the deal itself", st==403, f"got {st}")
st,d = call('GET','/api/clients',None,T2)
ok("shared client appears in the partner's list", any(c['id']==c1['id'] for c in d['clients']), [c['name'] for c in d['clients']])

print("\n[ COMMISSION SPLIT ]")
call('POST','/api/payments',{'client_id':c1['id'],'amount':1000,'currency':'USD','paid_date':'2026-02-15'},SUP)
st,d = call('GET',f'/api/admin/agents/{A1}/progress',None,SUP)
lead = d['metrics']
st,d = call('GET',f'/api/admin/agents/{A2}/progress',None,SUP)
helper = d['metrics']
# $1000 first payment x 60% = $600 agent commission; 30% to partner = $180 / $420
ok("lead keeps 70% of the agent commission", abs(lead['earned_usd']-420)<0.01, lead['earned_usd'])
ok("partner earns 30% of it", abs(helper['earned_usd']-(300+180))<0.01,
   f"{helper['earned_usd']} (own $300 + share $180 expected)")
ok("lead's shared-away amount is visible", abs(lead['shared_away_usd']-180)<0.01, lead.get('shared_away_usd'))
ok("partner's share is itemised", abs(helper['partner_earnings_usd']-180)<0.01, helper.get('partner_earnings_usd'))
st,d = call('GET','/api/admin/overview',None,SUP)
ok("StatsPack's share is untouched by the split",
   abs(d['totals']['agent_commission_usd']-(600+300))<0.01, d['totals']['agent_commission_usd'])
ok("company revenue not double-counted", abs(d['totals']['collected_usd']-1500)<0.01, d['totals']['collected_usd'])

print("\n[ ENDING A PARTNERSHIP ]")
st,d = call('PATCH',f'/api/collaborations/{K}',{'status':'Ended','response_note':'Deal closed.'},T1)
ok("lead can end the partnership", st==200, d)
st,d = call('GET',f'/api/clients/{c1["id"]}',None,T2)
ok("partner loses access once ended", st==403, f"got {st}")
st,d = call('GET',f'/api/admin/agents/{A1}/progress',None,SUP)
# The partner keeps the share they earned while the partnership was live;
# only FUTURE payments go wholly to the lead. Clawing it back would take money
# off someone who had already been paid it.
ok("lead does NOT reclaim the partner's earned share", abs(d['metrics']['earned_usd']-420)<0.01,
   d['metrics']['earned_usd'])
st,d = call('GET',f'/api/admin/agents/{A2}/progress',None,SUP)
ok("partner keeps what they earned while active", d['metrics']['earned_usd'] >= 480-0.01,
   d['metrics']['earned_usd'])
ok("partner is not left with a negative balance", d['metrics']['outstanding_usd'] >= -0.01,
   d['metrics']['outstanding_usd'])

print("\n[ REPORTS ]")
call('POST','/api/payouts',{'agent_id':A1,'amount_usd':100,'period_label':'Feb 2026'},SUP)
st,d = call('GET','/api/reports',None,SUP)
ok("report types listed", st==200 and len(d['types'])>=6, d.get('types'))
for kind in ('commission','payments','pipeline','payouts','agents','activity'):
    st,d = call('POST','/api/reports/build',{'kind':kind},SUP)
    lines = d.get('csv','').split('\n')
    ok(f"{kind} report builds with data",
       st==200 and len(lines) >= 2 and lines[0].startswith('"') and d['rows'] >= 1,
       f"{st} rows={d.get('rows')} {str(d.get('csv',''))[:60]}")
st,d = call('POST','/api/reports/build',{'kind':'nonsense'},SUP)
ok("unknown report rejected", st==400, f"got {st}")
st,d = call('POST','/api/reports/build',{'kind':'payments','from':'not-a-date'},SUP)
ok("bad date rejected", st==400, f"got {st}")
st,d = call('POST','/api/reports/build',{'kind':'payments','from':'2026-02-01','to':'2026-02-28'},SUP)
ok("date range filters rows", d['rows']==1, d['rows'])
st,d = call('POST','/api/reports/build',{'kind':'payments'},T1)
ok("agent's report contains only their own clients", 'Helper Own Client' not in d['csv'], "LEAK")
st,d = call('POST','/api/reports/build',{'kind':'commission'},T1)
ok("agent commission report shows only themselves", d['rows']==1, d['rows'])

print("\n[ CSV INJECTION ]")
_,evil = call('POST','/api/clients',{'name':'=cmd|calc!A1','currency':'USD','monthly_value':1},T1)
st,d = call('POST','/api/reports/build',{'kind':'pipeline'},SUP)
ok("formula-like names are neutralised in CSV", '"\'=cmd' in d['csv'], d['csv'][:160])

print("\n[ STORAGE + SYSTEM HEALTH ]")
st,d = call('GET','/api/admin/system',None,SUP)
ok("system page available to super user", st==200, st)
ok("primary storage reports usage", d['primary']['used_mb'] is not None, d.get('primary'))
ok("primary storage reports remaining", d['primary']['free_mb'] is not None, d.get('primary'))
ok("storage percentage computed", d['primary']['pct'] is not None, d['primary'].get('pct'))
ok("per-table breakdown included", len(d['primary']['tables'])>0, d['primary'].get('tables'))
ok("standby reported as not configured", d['standby'].get('configured') is False, d.get('standby'))
ok("mirror state reported", 'enabled' in d['mirror'], d.get('mirror'))
st,d = call('GET','/api/admin/system',None,T1)
ok("agents cannot see system health", st==403, f"got {st}")
st,d = call('POST','/api/admin/system/mirror',{},SUP)
ok("mirror run without a standby fails cleanly", st==502, f"got {st}")
st,d = call('GET','/api/admin/alerts',None,SUP)
ok("alerts endpoint works", st==200 and 'count' in d, d)
st,d = call('GET','/api/health')
ok("health reports mirror status", 'mirror' in d, d)
ok("health does not leak the standby URL", 'password' not in json.dumps(d).lower(), d)

print("\n" + "="*72); print(f"  {PASS} passed, {FAIL} failed")
if ISSUES:
    print("\n  ISSUES:")
    for i in ISSUES: print("   -", i)
print("="*72)
sys.exit(1 if FAIL else 0)
