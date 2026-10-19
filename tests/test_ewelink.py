import asyncio,json,time
from urllib.parse import urlparse,parse_qs
import httpx,pytest
from backend.store import Store
from backend.adapters import AdapterError
from backend.ewelink import EWeLink,view,CALLBACK,HOSTS,sign,usage_key

@pytest.fixture
def ew(tmp_path):
 s=Store(tmp_path);e=EWeLink();e.configure(s,'fixture-app-id','fixture-app-secret')
 yield s,e
 s.db.close()

def test_oauth_uses_official_host_signature_single_use_and_no_public_credentials(ew):
 s,e=ew;begin=e.begin(s);p=urlparse(begin['authorization_url']);q=parse_qs(p.query)
 assert p.scheme=='https' and p.netloc=='c2ccdn.coolkit.cc'
 assert q['redirectUrl']==[CALLBACK] and q['showQRCode']==['true']
 assert q['authorization']==[sign('fixture-app-secret','fixture-app-id_'+q['seq'][0])]
 assert 'state' not in s.secret('ewelink-oauth')
 calls=[]
 async def request(store,region,method,path,body=None,token=None):
  calls.append((method,path))
  if path.endswith('/token'):return {'accessToken':'fixture-access','refreshToken':'fixture-refresh','atExpiredTime':int((time.time()+3600)*1000)}
  return {'thingList':[{'itemType':1,'itemData':{'deviceid':'example-id','name':'示例插座','online':True,'devicekey':'private-key','apikey':'private-api','params':{'switch':'off'}}}],'total':1}
 e.request=request
 async def run():
  for code,state,region in [('code','wrong','as'),('code',q['state'][0],'evil')]:
   with pytest.raises(AdapterError):await e.finish(s,code,state,region)
  result=await e.finish(s,'code',q['state'][0],'as')
  assert result['connected'] and result['items'][0]['power'] is False
  assert not result['items'][0]['control_ready']
  public=json.dumps(result)
  for v in ['fixture-access','fixture-refresh','private-key','private-api','fixture-app-secret']:assert v not in public
  with pytest.raises(AdapterError):await e.finish(s,'code',q['state'][0],'as')
  assert calls==[('POST','/v2/user/oauth/token'),('GET','/v2/device/thing')]
  assert 'private-key' in json.dumps(s.secret('ewelink-devices'))
 asyncio.run(run())
 assert b'fixture-app-secret' not in (s.path/'home.sqlite3').read_bytes()

def test_expired_state_and_no_replay_after_exchange_error(ew):
 s,e=ew;q=parse_qs(urlparse(e.begin(s)['authorization_url']).query);pending=s.secret('ewelink-oauth');pending['expires_at']=1;s.set_secret('ewelink-oauth',pending)
 with pytest.raises(AdapterError):asyncio.run(e.finish(s,'code',q['state'][0],'as'))
 q=parse_qs(urlparse(e.begin(s)['authorization_url']).query);calls=[]
 async def fail(*args,**kwargs):calls.append(1);raise AdapterError('ewelink_auth','未完成')
 e.request=fail
 with pytest.raises(AdapterError):asyncio.run(e.finish(s,'code',q['state'][0],'as'))
 with pytest.raises(AdapterError):asyncio.run(e.finish(s,'code',q['state'][0],'as'))
 assert calls==[1]

def test_fixed_hosts_signature_quota_error_sanitization_and_usage(ew,monkeypatch):
 s,e=ew;orig=httpx.AsyncClient;requests=[]
 def handler(r):
  requests.append(r)
  assert r.url.host=='as-apia.coolkit.cc'
  assert r.headers['Authorization']=='Sign '+sign('fixture-app-secret',r.content.decode())
  return httpx.Response(200,json={'error':412,'msg':'private credential do not echo','data':{}})
 monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:orig(**kw,transport=httpx.MockTransport(handler)))
 with pytest.raises(AdapterError) as exc:asyncio.run(e.request(s,'as','POST','/v2/user/oauth/token',{'code':'fixture-code'}))
 assert exc.value.code=='ewelink_quota' and 'private credential' not in str(exc.value)
 assert view(s)['usage']['attempted_calls_by_region']['as']==1
 s.put('settings',usage_key(),{'as':50000});e.last_request=0
 with pytest.raises(AdapterError):asyncio.run(e.request(s,'as','GET','/v2/device/thing',token='fixture-token'))
 assert len(requests)==1
 with pytest.raises(AdapterError):asyncio.run(e.request(s,'https://evil.example','GET','/v2/device/thing'))
 with pytest.raises(AdapterError):asyncio.run(e.request(s,'as','POST','/v2/device/thing/status'))

def test_refresh_and_restricted_inventory_preserve_auth_and_previous_devices(ew):
 s,e=ew;s.set_secret('ewelink',{'region':'as','access_token':'old','refresh_token':'fixture-refresh','expires_at':1});seen=[]
 async def request(store,region,method,path,body=None,token=None):
  seen.append(path)
  if path.endswith('/refresh'):return {'at':'new-access','rt':'new-refresh'}
  raise AdapterError('ewelink_permission','品牌未授權')
 e.request=request
 async def run():
  token=await e.ensure_token(s);assert token['access_token']=='new-access'
  s.put('settings','ewelink-integration',{'items':[{'id':'existing'}]})
  with pytest.raises(AdapterError):await e.discover(s)
  assert view(s)['items']==[{'id':'existing'}] and view(s)['connected']
  await e.disconnect(s);assert not view(s)['connected'] and view(s)['configured']
 asyncio.run(run());assert seen==['/v2/user/refresh','/v2/device/thing']
