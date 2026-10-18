import asyncio,json,time
from types import SimpleNamespace
import pytest
from backend.store import Store
from backend.aifa import AifaAccount
from backend.infrared import Infrared
from backend.native_ir import NativeIR,target_key
from backend.ac_timers import ACTimers,KEY
from backend.adapters import AdapterError

class Cloud:
 def __init__(self):self.calls=[];self.captured=False;self.fail_save=False;self.timeout=False
 async def request(self,s,method,path,body=None):
  self.calls.append((method,path,body))
  if method=='GET':return {'success':self.captured,'code':'DemoCapturedCode0123456789=='}
  if path.endswith('/learning-codes') and '/remotes/' not in path:
   if self.fail_save:raise TimeoutError()
   return 'dedicated-remote'
  if self.timeout and method=='POST':raise TimeoutError()
  return True

@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path);a=AifaAccount();cloud=Cloud();ir=Infrared(cloud);native=NativeIR(a,ir)
 s.put('settings','aifa-discovery',{'queried_at':time.time(),'items':[{'id':'hub','name':'示例控制器','mac':'aabbccddeeff','children':[{'id':'ac','type':'0','sub_type':'0'}]}]})
 s.put('devices','ir',{'id':'ir','name':'示例紅外線','kind':'remote','ips':[],'config':{'device_id':'owned','cloud_category':'wnykq'}})
 s.set_secret('tuya-cloud-devices',{'devices':[{'id':'owned'}]})
 yield s,a,cloud,native,ACTimers(s,a,native)
 s.db.close()

async def learn(s,c,n):
 v=await n.begin(s,'hub','ac','ir','anti_mold_off');ident=v['session']['id'];c.captured=True
 v=await n.capture(s,'hub','ac',ident);assert v['session']['status']=='captured'
 return await n.save(s,'hub','ac',ident)

def test_learning_only_new_signal_no_emission_and_private_storage(env):
 s,a,c,n,t=env
 async def run():
  v=await n.begin(s,'hub','ac','ir','anti_mold_off');ident=v['session']['id']
  v=await n.capture(s,'hub','ac',ident);assert v['session']['status']=='waiting'
  assert not any(method=='POST' for method,_,_ in c.calls)
  assert 'learning_time=' in c.calls[-1][1]
  ss=s.one('settings','native-session:'+target_key('hub','ac'));ss['last_poll']=0;n.write_session(s,ss);c.captured=True
  await n.capture(s,'hub','ac',ident);v=await n.save(s,'hub','ac',ident)
  assert not v['keys'][0]['verified'] and 'code' not in json.dumps(v)
  assert not any('/remotes/' in path and method=='POST' for method,path,_ in c.calls)
  assert b'DemoCapturedCode0123456789' not in (s.path/'home.sqlite3').read_bytes()
 asyncio.run(run())

def test_learning_ownership_exclusive_and_expiry_cleanup(env):
 s,a,c,n,t=env
 async def run():
  with pytest.raises(AdapterError):await n.begin(s,'hub','bad','ir','anti_mold_off')
  v=await n.begin(s,'hub','ac','ir','anti_mold_off')
  with pytest.raises(AdapterError):await n.begin(s,'hub','ac','ir','quiet')
  ss=s.one('settings','native-session:'+target_key('hub','ac'));ss['expires_at']=0;n.write_session(s,ss)
  await n.cleanup(s)
  assert n.view(s,'hub','ac')['session']['status']=='expired' and c.calls[-1][1].endswith('state=false')
  assert not n.profile(s,target_key('hub','ac'))
 asyncio.run(run())

def test_ambiguous_profile_save_never_retried(env):
 s,a,c,n,t=env
 async def run():
  v=await n.begin(s,'hub','ac','ir','anti_mold_off');ident=v['session']['id'];c.captured=True
  await n.capture(s,'hub','ac',ident);c.fail_save=True
  with pytest.raises(TimeoutError):await n.save(s,'hub','ac',ident)
  with pytest.raises(AdapterError):await n.save(s,'hub','ac',ident)
  with pytest.raises(AdapterError):await n.begin(s,'hub','ac','ir','quiet')
  assert len([x for x in c.calls if x[0]=='POST'])==1
 asyncio.run(run())

def test_native_replay_idempotency_and_human_verification(env):
 s,a,c,n,t=env
 async def run():
  await learn(s,c,n)
  with pytest.raises(AdapterError):await n.verify(s,'hub','ac','anti_mold_off',True)
  c.timeout=True;r=await n.send(s,'hub','ac','anti_mold_off','once')
  assert r['status']=='unconfirmed'
  assert await n.send(s,'hub','ac','anti_mold_off','once')==r
  assert len([x for x in c.calls if x[0]=='POST' and '/remotes/' in x[1]])==1
  assert not n.view(s,'hub','ac')['keys'][0]['verified']
  v=await n.verify(s,'hub','ac','anti_mold_off',True);assert v['keys'][0]['verified']
 asyncio.run(run())

def make_due(s,t,action='off',idem='job'):
 j=t.schedule('hub','ac',action,60,idem);jobs=s.one('settings',KEY);jobs[-1]['due_at']=time.time()-1;t.save(jobs);return j

def test_timer_cancel_replace_idempotency_and_no_early_send(env):
 s,a,c,n,t=env
 one=t.schedule('hub','ac','off',60,'one');assert t.schedule('hub','ac','off',60,'one')==one
 with pytest.raises(AdapterError):t.schedule('hub','ac','on',60,'one')
 t.schedule('hub','ac','on',120,'two');assert s.one('settings',KEY)[0]['status']=='replaced'
 t.cancel('hub','ac');asyncio.run(t.tick());assert not c.calls
 assert s.one('settings',KEY)[-1]['status']=='cancelled'

def test_timer_one_shot_restart_missed_and_no_retry(env):
 s,a,c,n,t=env;sent=[]
 async def power(*args):sent.append(args);return {'id':'result','status':'unconfirmed','message':'待確認'}
 a.ac_power=power;make_due(s,t);asyncio.run(t.tick());asyncio.run(t.tick());assert len(sent)==1
 make_due(s,t,idem='late');jobs=s.one('settings',KEY);jobs[-1]['due_at']=time.time()-121;t.save(jobs);asyncio.run(t.tick());assert jobs and s.one('settings',KEY)[-1]['status']=='missed' and len(sent)==1
 j=make_due(s,t,idem='crash');t.set_result(j['id'],{'status':'dispatching'});ACTimers(s,a,n).initialize();asyncio.run(t.tick());assert len(sent)==1 and s.one('settings',KEY)[-1]['status']=='unconfirmed'

def test_native_timer_requires_verified_signal_and_sends_no_second_off(env):
 s,a,c,n,t=env
 with pytest.raises(AdapterError):t.schedule('hub','ac','native_off',60,'no')
 async def run():
  await learn(s,c,n);await n.send(s,'hub','ac','anti_mold_off','test');await n.verify(s,'hub','ac','anti_mold_off',True)
  p=n.profile(s,target_key('hub','ac'));p['last_send_at']=0;s.set_secret('native-ir:'+target_key('hub','ac'),p)
  make_due(s,t,'native_off');await t.tick();await t.tick()
  assert len([x for x in c.calls if x[0]=='POST' and '/remotes/' in x[1]])==2
  assert s.one('settings',KEY)[-1]['status']=='unconfirmed'
 asyncio.run(run())

def test_cancel_while_other_timer_awaits_cannot_dispatch_stale_snapshot(env):
 s,a,c,n,t=env
 # A second target cancellation can happen while awaiting the first network request.
 first=make_due(s,t);jobs=s.one('settings',KEY);second={**jobs[0],'id':'second','hub':'other','idempotency_key':'second'};t.save([*jobs,second]);sent=[]
 async def power(*args):sent.append(args);t.cancel('other','ac');return {'id':'result','status':'unconfirmed','message':'待確認'}
 a.ac_power=power;asyncio.run(t.tick());assert len(sent)==1 and s.one('settings',KEY)[-1]['status']=='cancelled'

def test_expired_aifa_token_refresh_before_background_exchange(env):
 s,a,c,n,t=env;s.set_secret('aifa',{'access_token':'expired','refresh_token':'refresh','expires_at':0});calls=[]
 async def request(method,path,body=None,token=None):
  assert body['grant_type']=='refresh_token' and body['refresh_token']=='refresh'
  calls.append((method,path));return {'access_token':'fresh','refresh_token':'next','expires_in':3600}
 a.request=request
 async def run():
  async with a.lock:await a.ensure_token(s)
  async with a.lock:await a.ensure_token(s)
 asyncio.run(run());assert len(calls)==1 and s.secret('aifa')['access_token']=='fresh'


def test_manual_command_retry_does_not_cancel_new_timer(env):
 s,a,c,n,t=env
 async def exchange(*args,**kwargs):return {'status':{'power':False}}
 a.ac_exchange=exchange
 async def run():
  make_due(s,t)
  await a.ac_power(s,'hub','ac',False,'manual',on_accept=lambda:t.cancel('hub','ac'))
  assert s.one('settings',KEY)[-1]['status']=='cancelled'
  t.schedule('hub','ac','on',120,'new')
  await a.ac_power(s,'hub','ac',False,'manual',on_accept=lambda:t.cancel('hub','ac'))
  assert s.one('settings',KEY)[-1]['status']=='pending'
 asyncio.run(run())

def test_multiple_named_custom_keys_preserved(env):
 s,a,c,n,t=env
 async def run():
  for kind,label in [('custom_000000000001','示例 A'),('custom_000000000002','示例 B')]:
   v=await n.begin(s,'hub','ac','ir',kind,label);c.captured=True
   await n.capture(s,'hub','ac',v['session']['id']);await n.save(s,'hub','ac',v['session']['id'])
  assert len(n.view(s,'hub','ac')['keys'])==2
 asyncio.run(run())


def test_old_timer_idempotency_survives_history_pruning(env):
 s,a,c,n,t=env
 t.schedule('hub','ac','off',60,'old');t.save([])
 assert t.schedule('hub','ac','off',60,'old')['status']=='archived'
 assert s.one('settings',KEY)==[]
