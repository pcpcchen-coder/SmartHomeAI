import asyncio,hashlib,hmac,json,time
from types import SimpleNamespace
import httpx,pytest
from backend.infrared import Infrared,TuyaIRCloud,ir_view
from backend.adapters import AdapterError
from backend.store import Store

def setup(s):
 parent={'id':'controller','name':'示例紅外線控制器','kind':'remote','adapter':'tuya','ips':[],'config':{'device_id':'owned-controller','cloud_category':'wnykq'}}
 child={'id':'existing-tv','name':'保留自訂電視名稱','kind':'remote','adapter':'tuya','ips':[],'placement':{'x':.2,'y':.3,'confirmed':True},'config':{'infrared_virtual':True,'gateway_id':'owned-controller','node_id':'remote-tv'}}
 for d in [parent,child]:s.put('devices',d['id'],d)
 s.set_secret('tuya-cloud-devices',{'devices':[{'id':'owned-controller'}]});return parent,child

class Cloud:
 def __init__(self):self.calls=[];self.timeout=False;self.fail=False
 async def request(self,store,method,path,body=None):
  self.calls.append((method,path,body))
  if self.fail:raise AdapterError('ir_service_required','需要紅外線服務授權')
  if method=='POST':
   if self.timeout:raise httpx.ReadTimeout('timeout')
   return True
  if path.endswith('/remotes'):return [{'remote_id':'remote-tv','remote_name':'TV','category_id':2},{'remote_id':'remote-fan','remote_name':'Fan','category_id':3}]
  return {'category_id':2,'key_list':[{'key':'Power','key_id':1,'key_name':'Power'}]}

def test_ir_discovery_keeps_ids_names_positions_and_no_physical_commands(tmp_path):
 s=Store(tmp_path);p,d=setup(s);cloud=Cloud();ir=Infrared(cloud)
 asyncio.run(ir.discover(s,p));assert len(s.devices())==3
 saved=s.one('devices',d['id']);assert saved['name']==d['name'] and saved['placement']==d['placement']
 assert ir_view(s,saved)['ready'] and all(method=='GET' for method,_,_ in cloud.calls)
 assert not s.one('state',d['id'],{}).get('values',{}).get('power')
 s.db.close()

def test_ir_key_allowlist_idempotency_and_ack_not_physical_state(tmp_path):
 s=Store(tmp_path);p,d=setup(s);cloud=Cloud();ir=Infrared(cloud)
 async def run():
  await ir.discover(s,p);dev=s.one('devices',d['id'])
  with pytest.raises(AdapterError,match='尚未提供'):await ir.send(s,dev,'not-a-key',1,'bad')
  first=await ir.send(s,dev,'Power',1,'exactly-once');again=await ir.send(s,dev,'Power',1,'exactly-once')
  assert first==again and first['status']=='unconfirmed' and first['transport_status']=='accepted_by_cloud'
  assert len([c for c in cloud.calls if c[0]=='POST'])==1
  with pytest.raises(AdapterError,match='另一個按鍵'):await ir.send(s,dev,'different',1,'exactly-once')
  with pytest.raises(AdapterError,match='一秒'):await ir.send(s,dev,'Power',1,'new-id')
 asyncio.run(run());assert s.one('state',d['id']) is None;s.db.close()

def test_ir_permission_denied_keeps_inventory_and_timeout_never_retries(tmp_path):
 s=Store(tmp_path);p,d=setup(s);cloud=Cloud();ir=Infrared(cloud)
 async def run():
  cloud.fail=True
  with pytest.raises(AdapterError):await ir.discover(s,p)
  assert len(s.devices())==2 and not ir_view(s,d)['ready']
  s.put('settings','ir-discovery:'+p['id'],{});cloud.fail=False;await ir.discover(s,p);cloud.timeout=True
  result=await ir.send(s,s.one('devices',d['id']),'Power',1,'timeout')
  assert result['status']=='unconfirmed' and result['transport_status']=='unknown'
  again=await ir.send(s,s.one('devices',d['id']),'Power',1,'timeout');assert again==result
  assert len([c for c in cloud.calls if c[0]=='POST'])==1
 asyncio.run(run());s.db.close()

def test_ir_cloud_signs_exact_post_bytes_and_only_approved_paths(tmp_path,monkeypatch):
 s=Store(tmp_path);s.set_secret('tuya-cloud',{'region':'us','api_key':'fixture-client','api_secret':'fixture-secret'});calls=[]
 def handler(r):
  calls.append(r);token=r.headers.get('access_token','');canonical=r.method+'\n'+hashlib.sha256(r.content).hexdigest()+'\n\n'+r.url.raw_path.decode()
  expected=hmac.new(b'fixture-secret',('fixture-client'+token+r.headers['t']+canonical).encode(),hashlib.sha256).hexdigest().upper()
  assert r.headers['sign']==expected and 'fixture-secret' not in str(r.headers)
  return httpx.Response(200,json={'success':True,'result':{'access_token':'fixture-token','expire_time':3600} if '/token?' in str(r.url) else True})
 original=httpx.AsyncClient;monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(**kw,transport=httpx.MockTransport(handler)))
 async def run():
  cloud=TuyaIRCloud();assert await cloud.request(s,'POST','/v2.0/infrareds/owned/remotes/remote/raw/command',{'category_id':2,'key':'Power','key_id':1}) is True
  with pytest.raises(AdapterError):await cloud.request(s,'POST','/v1.0/arbitrary',{})
 asyncio.run(run());assert len(calls)==2;s.db.close()

def test_removed_or_stale_remote_keys_cannot_send(tmp_path):
 s=Store(tmp_path);p,d=setup(s);cloud=Cloud();ir=Infrared(cloud)
 async def run():
  await ir.discover(s,p);dev=s.one('devices',d['id']);cache=s.one('settings','ir-keys:'+d['id'])
  s.put('settings','ir-keys:'+d['id'],{**cache,'queried_at':time.time()-86401})
  with pytest.raises(AdapterError,match='先同步'):await ir.send(s,dev,'Power',1,'stale')
  s.put('settings','ir-keys:'+d['id'],cache)
  s.put('settings','ir-discovery:'+p['id'],{'synced_at':time.time(),'remote_ids':[]})
  with pytest.raises(AdapterError,match='先同步'):await ir.send(s,dev,'Power',1,'removed')
  assert not any(c[0]=='POST' for c in cloud.calls)
 asyncio.run(run());s.db.close()
