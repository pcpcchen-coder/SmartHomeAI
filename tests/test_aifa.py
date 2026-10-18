import asyncio,json,time
import httpx,pytest
from backend.aifa import AifaAccount,account_view
from backend.store import Store
from backend.adapters import AdapterError

def test_account_login_read_only_and_never_persists_password(tmp_path,monkeypatch):
 s=Store(tmp_path);s.put('devices','existing',{'id':'existing','mac':'aa:bb:cc:dd:ee:ff','placement':{'confirmed':True}});calls=[]
 def handler(r):
  calls.append((r.method,r.url.path))
  assert r.url.host=='api.aifaremote.com'
  if r.url.path=='/oauth2/token':
   assert json.loads(r.content)['password']=='fixture-password'
   return httpx.Response(200,json={'access_token':'fixture-token','refresh_token':'fixture-refresh','expires_in':3600})
  assert r.method=='GET' and r.headers['Authorization']=='Bearer fixture-token'
  return httpx.Response(200,json=[{'id':12,'deviceName':'示例控制器','mac':'AABBCCDDEEFF','subDevices':[{'id':3,'name':'示例冷氣','type':'AC'}],'private_field':'never-return'}])
 orig=httpx.AsyncClient;monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:orig(**kw,transport=httpx.MockTransport(handler)))
 a=AifaAccount();view=asyncio.run(a.login(s,'fixture@example.test','fixture-password'))
 assert calls==[('POST','/oauth2/token'),('GET','/devices')]
 assert view['items'][0]['matched_device_id']=='existing' and view['read_only']
 assert s.one('devices','existing')['placement']=={'confirmed':True}
 assert 'password' not in json.dumps(s.secret('aifa')) and 'fixture-token' not in json.dumps(view) and 'private_field' not in json.dumps(view)
 assert b'fixture-password' not in (tmp_path/'home.sqlite3').read_bytes()
 s.db.close()

def test_auth_failure_is_sanitized_and_keeps_prior_tokens(tmp_path,monkeypatch):
 s=Store(tmp_path);s.set_secret('aifa',{'access_token':'old-token'});orig=httpx.AsyncClient
 monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:orig(**kw,transport=httpx.MockTransport(lambda r:httpx.Response(401,json={'password':'do-not-echo','token':'do-not-echo'}))))
 with pytest.raises(AdapterError,match='AIFA 未接受') as e:asyncio.run(AifaAccount().login(s,'x','fixture-password'))
 assert 'do-not-echo' not in str(e.value) and s.secret('aifa')['access_token']=='old-token'
 s.db.close()

def test_failed_discovery_preserves_previous_inventory_and_can_disconnect(tmp_path):
 s=Store(tmp_path);a=AifaAccount();s.set_secret('aifa',{'access_token':'fixture-token','expires_at':time.time()+3600});s.put('settings','aifa-discovery',{'items':[{'id':'existing'}],'queried_at':1})
 async def fail(*args,**kwargs):raise AdapterError('aifa_connection','連線未完成')
 a.request=fail
 async def run():
  with pytest.raises(AdapterError):await a.discover(s)
  assert account_view(s)['items']==[{'id':'existing'}]
  await a.disconnect(s);assert not account_view(s)['connected']
  assert not s.secret('aifa') and not account_view(s)['items']
 asyncio.run(run());s.db.close()
