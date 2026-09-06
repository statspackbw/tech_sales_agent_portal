#!/usr/bin/env python3
"""Disaster drill: can you actually run the portal on the standby copy?"""
import json, sys, urllib.request, urllib.error
P=F=0
def ok(l,c,x=""):
    global P,F
    if c: P+=1; print(f"  PASS  {l}")
    else: F+=1; print(f"  FAIL  {l}   <-- {x}")
def call(base,m,p,b=None,t=None):
    r=urllib.request.Request(base+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=40) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or '{}')
PRI, STB = sys.argv[1], sys.argv[2]
snap = json.load(open('/tmp/primary_snapshot.json'))

print("="*72); print("PROMOTION DRILL — portal running on the Supabase copy"); print("="*72)
st,d = call(STB,'POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})
ok("admin can sign in against the standby", st==200, d); T=d.get('token')
st,d = call(STB,'GET','/api/admin/overview',None,T)
ok(f"collections match the primary (${d['totals']['collected_usd']:,.2f})",
   abs(d['totals']['collected_usd']-snap['collected'])<0.01, (d['totals']['collected_usd'],snap['collected']))
ok(f"agent commission matches (${d['totals']['agent_commission_usd']:,.2f})",
   abs(d['totals']['agent_commission_usd']-snap['commission'])<0.01,
   (d['totals']['agent_commission_usd'],snap['commission']))
ok("client count matches", d['totals']['clients']==snap['clients'], (d['totals']['clients'],snap['clients']))
st,d = call(STB,'GET','/api/admin/agents',None,T)
ok("agents present with their rates", len(d['agents'])==snap['agents'], len(d['agents']))
ok("per-agent override preserved",
   any(abs((a['rules']['first_rate'])-0.125)<1e-6 for a in d['agents']), [a['rules'] for a in d['agents']])
st,d = call(STB,'GET','/api/collaborations',None,T)
ok("partnerships preserved", len(d['collaborations'])==snap['collabs'], len(d['collaborations']))

print("\n[ THE STANDBY ACCEPTS NEW WRITES ]")
st,a = call(STB,'POST','/api/admin/agents',{'name':'Post-Failover Agent','email':'pf@statspack.co.ls'},T)
ok("can create an agent (id sequence not clashing)", st==200 and a.get('id'), a)
st,c = call(STB,'POST','/api/clients',{'name':'Post-Failover Client','currency':'USD','monthly_value':900},T)
ok("can create a client", st==200, c)
st,p = call(STB,'POST','/api/payments',{'client_id':c['id'],'amount':900,'currency':'USD','paid_date':'2026-07-01'},T)
ok("can record a payment", st==200, p)
ok("commission computes on the standby", abs(p['payment']['agent_commission_usd']-540)<0.01,
   p['payment'].get('agent_commission_usd'))
st,d = call(STB,'GET','/api/reports/../reports',None,T)
st,d = call(STB,'POST','/api/reports/build',{'kind':'commission'},T)
ok("reports build on the standby", st==200 and d['rows']>=1, d.get('rows'))

print("\n[ AGENT LOGINS STILL WORK ]")
st,d = call(STB,'POST','/api/login',{'email':'thabo@statspack.co.ls','password':'Passw0rd!2026'})
ok("agent password still valid after failover", st==200, d)
AT=d.get('token')
st,d = call(STB,'GET','/api/agent/overview',None,AT)
ok("agent sees their own commission", st==200 and d['metrics']['earned_usd']>0, d.get('metrics',{}).get('earned_usd'))
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
