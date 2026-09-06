#!/usr/bin/env python3
"""Commission is attributed by when a payment was recorded, not by whether the
partnership is still running today."""
import json, sys, urllib.request, urllib.error, time
B = sys.argv[1] if len(sys.argv)>1 else "http://localhost:8470"
P=F=0
def ok(l,c,x=""):
    global P,F
    if c: P+=1; print(f"  PASS  {l}")
    else: F+=1; print(f"  FAIL  {l}   <-- {x}")
def call(m,p,b=None,t=None):
    r=urllib.request.Request(B+p,method=m,headers={'Content-Type':'application/json'})
    if t: r.add_header('Authorization','Bearer '+t)
    try:
        with urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None,timeout=40) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or '{}')
T=call('POST','/api/login',{'email':'admin@statspack.co.ls','password':'TestAdmin!2026'})[1]['token']
def onboard(e,n):
    a=call('POST','/api/admin/agents',{'email':e,'name':n},T)[1]
    s=call('POST','/api/login',{'email':e,'password':a['temp_password']})[1]
    call('POST','/api/change-password',{'current_password':a['temp_password'],'new_password':'Passw0rd!2026'},s['token'])
    return a['id'], s['token']
def pos(aid):
    _,d=call('GET','/api/commissions',None,T)
    r=[x for x in d['commissions'] if x['agent_id']==aid][0]
    return r['earned_usd'], r['paid_out_usd'], r['outstanding_usd']

print("="*70); print("PARTNERSHIP HISTORY & COMMISSION"); print("="*70)
L,LT = onboard('h_lead@x.com','H Lead'); Pa,PT = onboard('h_part@x.com','H Partner')
c = call('POST','/api/clients',{'name':'H Joint','currency':'USD','monthly_value':1000},LT)[1]
k = call('POST','/api/collaborations',{'client_id':c['id'],'partner_id':Pa,'split_pct':0.3},LT)[1]
call('PATCH','/api/collaborations/%s'%k['id'],{'status':'Accepted'},PT)
time.sleep(1.1)
for d_ in ('2026-01-15','2026-02-15','2026-03-15'):
    call('POST','/api/payments',{'client_id':c['id'],'amount':1000,'currency':'USD','paid_date':d_},T)
le,_,_ = pos(L); pe,_,_ = pos(Pa)
ok("split applied while active (560/240)", abs(le-560)<0.01 and abs(pe-240)<0.01, (le,pe))
call('POST','/api/payouts',{'agent_id':Pa,'amount_usd':pe},T)
_,pl=call('GET','/api/payouts',None,T)
call('PATCH','/api/payouts/%s'%[x for x in pl['payouts'] if x['agent_id']==Pa][0]['id'],{'status':'Paid'},T)
time.sleep(1.1)
call('PATCH','/api/collaborations/%s'%k['id'],{'status':'Ended'},LT)
le2,_,_ = pos(L); pe2,_,po2 = pos(Pa)
ok("ending does not claw back the partner's earnings", abs(pe2-240)<0.01, pe2)
ok("partner's balance stays square", abs(po2)<0.01, po2)
ok("lead not retroactively credited", abs(le2-560)<0.01, le2)
time.sleep(1.1)
call('POST','/api/payments',{'client_id':c['id'],'amount':1000,'currency':'USD','paid_date':'2026-04-15'},T)
le3,_,_ = pos(L); pe3,_,_ = pos(Pa)
ok("later payments go wholly to the lead", abs(le3-660)<0.01 and abs(pe3-240)<0.01, (le3,pe3))
st,d = call('GET','/api/reports/build' if False else '/api/reports',None,T)
st,d = call('POST','/api/reports/build',{'kind':'payments'},T)
ok("payments report still itemises the split", st==200 and 'Partner share' in d['csv'], st)
_,ov = call('GET','/api/admin/overview',None,T)
ok("collections not double-counted", abs(ov['totals']['collected_usd']-4000)<0.01,
   ov['totals']['collected_usd'])
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
