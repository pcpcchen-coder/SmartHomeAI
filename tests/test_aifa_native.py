import asyncio,time
import pytest
from backend.aifa import AifaAccount
from backend.aifa_native import AifaNative,native_key
from backend.store import Store
from backend.adapters import AdapterError

@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path);a=AifaAccount();n=AifaNative(a);sent=[]
 s.put('settings','aifa-discovery',{'queried_at':time.time(),'items':[{'id':'hub','name':'example','children':[{'id':'ac','type':'0','sub_type':'0','device_code':'19968'}]}]})
 async def exchange(store,device,packet=None,on_send=None):
  if on_send:on_send()
  sent.append(packet)
  return {'sent':True,'status':None}
 a.ac_exchange=exchange
 yield s,a,n,sent
 s.db.close()

def test_native_is_aifa_button_not_plus_configuration_or_tuya(env):
 s,a,n,sent=env;cancelled=[]
 assert n.view(s,'hub','ac')['available'] and not n.view(s,'hub','ac')['verified']
 with pytest.raises(AdapterError):n.verify(s,'hub','ac','unissued',True)
 async def run():
  r=await n.send(s,'hub','ac','one',on_accept=lambda:cancelled.append(True))
  assert r['status']=='unconfirmed' and r['sender']=='aifa' and sent==['ffa026f0']
  assert not n.view(s,'hub','ac')['verified']
  assert await n.send(s,'hub','ac','one',on_accept=lambda:cancelled.append(True))==r
  assert cancelled==[True] and sent==['ffa026f0']
  with pytest.raises(AdapterError,match='30 秒'):await n.send(s,'hub','ac','two')
  assert n.verify(s,'hub','ac',r['id'],True)['verified']
 asyncio.run(run())

def test_rejects_unsupported_profile_other_subtype_and_stale_inventory(env):
 s,a,n,sent=env
 cache=s.one('settings','aifa-discovery');p=cache['items'][0]['children'][0]
 p['device_code']='173';s.put('settings','aifa-discovery',cache)
 assert not n.view(s,'hub','ac')['available']
 with pytest.raises(AdapterError):asyncio.run(n.send(s,'hub','ac','bad'))
 p['device_code']='19968';p['sub_type']='1';s.put('settings','aifa-discovery',cache)
 with pytest.raises(AdapterError):asyncio.run(n.send(s,'hub','ac','bad2'))
 p['sub_type']='0';cache['queried_at']=1;s.put('settings','aifa-discovery',cache)
 with pytest.raises(AdapterError):asyncio.run(n.send(s,'hub','ac','bad3'))
 assert not sent

def test_unknown_send_is_not_retried_and_profile_change_invalidates_verification(env):
 s,a,n,sent=env
 async def timeout(store,device,packet=None,on_send=None):
  on_send();sent.append(packet);raise TimeoutError()
 a.ac_exchange=timeout
 async def run():
  r=await n.send(s,'hub','ac','once')
  assert r['status']=='unconfirmed'
  assert await n.send(s,'hub','ac','once')==r and len(sent)==1
  n.verify(s,'hub','ac',r['id'],True)
  cache=s.one('settings','aifa-discovery');cache['items'][0]['children'][0]['device_code']='173';s.put('settings','aifa-discovery',cache)
  assert not n.view(s,'hub','ac')['verified']
  with pytest.raises(AdapterError):await n.send(s,'hub','ac','once')
 asyncio.run(run())

def test_failure_before_send_cannot_be_verified_or_retried(env):
 s,a,n,sent=env
 async def fail(*args,**kwargs):raise AdapterError('aifa_auth','授權未接受')
 a.ac_exchange=fail
 async def run():
  r=await n.send(s,'hub','ac','fail');assert r['status']=='failed'
  with pytest.raises(AdapterError):n.verify(s,'hub','ac',r['id'],True)
  assert await n.send(s,'hub','ac','fail')==r
 asyncio.run(run())
