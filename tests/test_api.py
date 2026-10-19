import os, tempfile, time, json, struct, asyncio
from pathlib import Path
os.environ['WULING_POLL']='0'
os.environ['WULING_DATA_DIR']=tempfile.mkdtemp(prefix='wuling-import-')
import pytest
from fastapi.testclient import TestClient
from backend import app as module
from backend.store import Store
from backend.adapters import Adapters, observation, AdapterError, lifx_packet

class FakeAdapters:
 def __init__(self,store):self.store=store;self.casts={};self.sent=[];self.fail_read=False
 def configured(self,d):return d['adapter'] in ['lifx','cast'] or bool(self.store.secret(d['id']))
 async def close(self):pass
 async def read(self,d):
  if self.fail_read:raise AdapterError('timeout','回讀逾時')
  return observation('test-device',{'power':True,'brightness':50},['power','brightness'])
 async def send(self,d,c,v):self.sent.append((d['id'],c,v))

@pytest.fixture
def ctx(tmp_path,monkeypatch):
 fixture_base=tmp_path/'example';(fixture_base/'assets').mkdir(parents=True)
 (fixture_base/'assets/example-seed.json').write_bytes((module.BASE/'assets/example-seed.json').read_bytes())
 monkeypatch.setattr(module,'BASE',fixture_base)
 store=Store(tmp_path/'data');adapters=FakeAdapters(store)
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'adapters',adapters);monkeypatch.setattr(module,'locks',{})
 with TestClient(module.app,base_url='http://127.0.0.1:8788',client=('127.0.0.1',60000)) as client:
  client.headers['Authorization']='Bearer '+store.master_token
  d=next(d for d in store.devices() if d['adapter']=='lifx')
  store.put('state',d['id'],observation('test-device',{'power':True,'brightness':50},['power','brightness']))
  yield client,store,adapters,d
 store.db.close()

def test_all_inventory_and_sources(ctx):
 c,s,a,d=ctx;h=c.get('/api/v1/home').json()
 assert h['summary']['total']==3
 assert len(h['layout']['rooms'])==2
 assert h['layout']['model_url'] is None
 assert h['layout']['image'].endswith('example-floorplan.svg')
 assert all(not x.get('placement',{}).get('confirmed') if x.get('placement') else True for x in h['devices'])

def test_unauthenticated_cannot_read_devices_or_plans(ctx):
 c,*_=ctx;c.headers.pop('Authorization')
 for path in ['/api/v1/home','/api/v1/export','/assets/home/floorplan.png']:
  assert c.get(path).status_code==401

def test_local_login_checks_origin_and_peer(ctx):
 c,*_=ctx
 assert c.post('/api/v1/auth/local',headers={'Origin':'https://evil.example'}).status_code==403
 assert c.post('/api/v1/auth/local',headers={'Origin':'http://127.0.0.1:8788'}).status_code==200
 with TestClient(module.app,base_url='http://127.0.0.1:8788',client=('192.168.50.60',60000)) as remote:
  assert remote.post('/api/v1/auth/local',headers={'Origin':'http://127.0.0.1:8788'}).status_code==403

def test_csrf_origin_rejected(ctx):
 c,s,a,d=ctx
 assert c.patch('/api/v1/devices/'+d['id'],json={'name':'惡意修改'},headers={'Origin':'http://other.home'}).status_code==403
 assert s.one('devices',d['id'])['name']==d['name']

def test_command_confirmation_and_idempotency(ctx):
 c,s,a,d=ctx;p='/api/v1/devices/'+d['id']+'/commands';headers={'Idempotency-Key':'test-exactly-once'}
 first=c.post(p,json={'command':'power','value':True},headers=headers)
 second=c.post(p,json={'command':'power','value':True},headers=headers)
 assert first.status_code==200 and first.json()['status']=='confirmed'
 assert second.json()['id']==first.json()['id'] and len(a.sent)==1
 assert c.post(p,json={'command':'power','value':False},headers=headers).status_code==409

def test_mismatch_is_unconfirmed(ctx):
 c,s,a,d=ctx;r=c.post('/api/v1/devices/'+d['id']+'/commands',json={'command':'power','value':False})
 assert r.json()['status']=='unconfirmed'

def test_readback_timeout_not_claimed_success(ctx):
 c,s,a,d=ctx;a.fail_read=True
 assert c.post('/api/v1/devices/'+d['id']+'/commands',json={'command':'power','value':True}).json()['status']=='unconfirmed'

@pytest.mark.parametrize('body',[{'command':'power','value':'true'},{'command':'brightness','value':101},{'command':'volume','value':True},{'command':'temperature','value':1},{'command':'color','value':'green'}])
def test_invalid_values_do_not_send(ctx,body):
 c,s,a,d=ctx
 assert c.post('/api/v1/devices/'+d['id']+'/commands',json=body).status_code==422
 assert not a.sent

def test_old_state_blocks_commands(ctx):
 c,s,a,d=ctx;st=s.one('state',d['id']);st['observed_at']=time.time()-91;s.put('state',d['id'],st)
 assert c.post('/api/v1/devices/'+d['id']+'/commands',json={'command':'power','value':True}).status_code==409
 assert not a.sent

def test_unsupported_device_does_not_send(ctx):
 c,s,a,d=ctx
 assert c.post('/api/v1/devices/'+d['id']+'/commands',json={'command':'volume','value':20}).status_code==422
 assert not a.sent

def test_position_validation_and_persistence(ctx):
 c,s,a,d=ctx;p='/api/v1/devices/'+d['id']
 assert c.patch(p,json={'placement':{'x':1.1,'y':.5}}).status_code==422
 assert c.patch(p,json={'placement':{'x':.2,'y':.3,'room_id':'invented'}}).status_code==422
 placement={'x':.2,'y':.3,'z':1.5,'room_id':'living','confirmed':True}
 assert c.patch(p,json={'placement':placement}).status_code==200
 second=Store(s.path);assert second.one('devices',d['id'])['placement']['x']==.2;second.db.close()
 assert c.patch(p,json={'placement':None}).json()['placement'] is None

def test_ip_and_null_validation(ctx):
 c,s,a,d=ctx;p='/api/v1/devices/'+d['id']
 for b in [{'ips':['8.8.8.8']},{'ips':['localhost']},{'ips':[]},{'name':None},{'kind':None}]:assert c.patch(p,json=b).status_code==422
 assert c.patch(p,json={'ips':['192.168.50.240']}).status_code==200
 assert not c.get(p).json()['can_control']

def test_layout_conflict_and_assigned_rooms(ctx):
 c,s,a,d=ctx;l=c.get('/api/v1/home').json()['layout'];keys=['width_m','depth_m','wall_height_m','rooms','model_url','model_scale']
 b={k:l[k] for k in keys};b['expected_version']=l['version']
 assert c.put('/api/v1/layout',json=b).status_code==200
 assert c.put('/api/v1/layout',json=b).status_code==409
 b['expected_version']+=1;b['rooms']=[r for r in b['rooms'] if r['id']!='living']
 assert c.put('/api/v1/layout',json=b).status_code==409

def test_tokens_revoke_and_hashed_storage(ctx):
 c,s,a,d=ctx;r=c.post('/api/v1/access-tokens',json={'name':'測試系統'}).json()
 token=r['token'];assert s.db.execute('SELECT hash FROM tokens WHERE id=?',(r['id'],)).fetchone()[0]!=token
 assert c.get('/api/v1/home',headers={'Authorization':'Bearer '+token}).status_code==200
 assert token not in c.get('/api/v1/access-tokens').text
 c.delete('/api/v1/access-tokens/'+r['id'])
 assert c.get('/api/v1/home',headers={'Authorization':'Bearer '+token}).status_code==401

def test_secrets_encrypted_and_excluded(ctx):
 c,s,a,d=ctx;secret='SENSITIVE_TEST_LOCAL_KEY';s.set_secret(d['id'],{'local_key':secret});s.set_secret('lg',{'pat':secret})
 assert secret not in s.db.execute('SELECT body FROM secrets WHERE id=?',(d['id'],)).fetchone()[0]
 assert secret not in c.get('/api/v1/export').text
 assert secret not in c.get('/api/v1/home').text
 assert secret not in c.get('/api/v1/integrations').text

def test_external_glb_resources_rejected(ctx):
 c,*_=ctx;doc=json.dumps({'asset':{'version':'2.0'},'buffers':[{'uri':'https://external.test/x','byteLength':1}]}).encode();doc+=b' '*((-len(doc))%4)
 glb=b'glTF'+struct.pack('<II',2,20+len(doc))+struct.pack('<II',len(doc),0x4e4f534a)+doc
 assert c.post('/api/v1/layout/model',files={'file':('bad.glb',glb)}).status_code==422

def test_lifx_packet_target_and_length():
 p,source=lifx_packet({'mac':'02:00:00:00:00:01'},117,struct.pack('<HI',65535,200),source=123,seq=7)
 assert len(p)==42 and struct.unpack_from('<H',p)[0]==42
 assert p[8:14]==bytes.fromhex('020000000001') and p[23]==7
 assert struct.unpack_from('<H',p,32)[0]==117

@pytest.mark.asyncio
async def test_ha_unavailable_never_controllable(tmp_path):
 a=Adapters(Store(tmp_path/'ha'));a.store.set_secret('homeassistant',{'token':'fixture','url':'http://fixture'})
 async def unavailable(*args,**kwargs):return {'entity_id':'light.fixture','state':'unavailable','attributes':{}}
 a.ha=unavailable
 with pytest.raises(AdapterError,match='尚未取得'):await a.read({'id':'fixture','adapter':'homeassistant','config':{'entity_id':'light.fixture'}})

def test_cast_observation_never_launches_receiver(tmp_path):
 from types import SimpleNamespace
 sent=[]
 class Media:
  status=SimpleNamespace(media_session_id=123,title='old',artist='old',player_state='PLAYING')
  def update_status(self):raise AssertionError('This method can launch an app')
  def send_message_nocheck(self,data,callback_function=None):
   sent.append(data)
   if callback_function:callback_function(True,{})
 cast=SimpleNamespace(status=SimpleNamespace(volume_level=.4,volume_muted=False,display_name=None),media_controller=Media(),socket_client=SimpleNamespace(is_connected=True,app_namespaces=[],receiver_controller=SimpleNamespace(update_status=lambda callback_function=None:callback_function(True,{}))))
 a=Adapters(Store(tmp_path/'cast'));a.cast=lambda d:cast
 r=a.read_cast({'id':'test'})
 assert not sent and r['commands']==['volume','mute'] and r['values']['media_session'] is None
 cast.socket_client.app_namespaces=['urn:x-cast:com.google.cast.media'];r=a.read_cast({'id':'test'})
 assert sent==[{'type':'GET_STATUS'}] and r['values']['title']=='old'


def test_interrupted_command_is_never_resent(ctx):
 c,s,a,d=ctx
 body={'command':'power','value':True,'channel':None}
 s.save_command('interrupted',d['id'],'crash-key',body,{'id':'interrupted','status':'accepted','device_id':d['id']})
 module.initialize()
 r=c.post('/api/v1/devices/'+d['id']+'/commands',json=body,headers={'Idempotency-Key':'crash-key'})
 assert r.json()['status']=='unconfirmed' and not a.sent
 assert c.get('/api/v1/commands/interrupted').json()['status']=='unconfirmed'

def test_tuya_channel_requires_observed_boolean(ctx):
 c,s,a,d=ctx;d={**d,'adapter':'tuya','config':{'power_dps':['1','2']}};s.put('devices',d['id'],d);s.set_secret(d['id'],{'local_key':'fixture'})
 s.put('state',d['id'],observation('tuya-lan',{'power':True,'channels':[{'id':'1','on':True}],'dps':{'1':True,'2':42}},['power']))
 r=c.post('/api/v1/devices/'+d['id']+'/commands',json={'command':'power','value':False,'channel':'2'})
 assert r.status_code==422 and not a.sent

@pytest.mark.parametrize('second_on',[True,False])
def test_tuya_channel_command_keeps_all_channels_and_verifies_selected(ctx,monkeypatch,second_on):
 from contextlib import contextmanager
 from types import SimpleNamespace
 c,s,_,d=ctx
 d={**d,'ips':['192.0.2.1'],'adapter':'tuya','config':{'device_id':'three-gang','power_dps':['1','2','3']}}
 s.put('devices',d['id'],d);s.set_secret(d['id'],{'local_key':'fixture'})
 a=Adapters(s);sent=[]
 @contextmanager
 def fake_tuya(device):yield SimpleNamespace(status=lambda:{'dps':{'1':not second_on,'2':second_on,'3':False}})
 async def send(device,command,value):sent.append((list(device['config']['power_dps']),command,value))
 a.tuya=fake_tuya;a.send=send;monkeypatch.setattr(module,'adapters',a)
 s.put('state',d['id'],observation('tuya-lan',{'power':False,'channels':[{'id':str(i),'on':False} for i in [1,2,3]]},['power']))
 r=c.post('/api/v1/devices/'+d['id']+'/commands',json={'command':'power','value':True,'channel':'2'})
 assert r.status_code==200
 result=r.json();assert result['status']==('confirmed' if second_on else 'unconfirmed')
 assert sent==[(['2'],'power',True)]
 for state in [result['state'],c.get('/api/v1/devices/'+d['id']).json()['state']]:
  assert state['values']['channels']==[{'id':'1','on':not second_on},{'id':'2','on':second_on},{'id':'3','on':False}]
 assert s.one('devices',d['id'])['config']['power_dps']==['1','2','3']


def test_telemetry_is_authenticated_read_only_and_history_validated(ctx,monkeypatch):
 c,s,a,d=ctx
 async def forbidden(*args):raise AssertionError('GET must never query cloud')
 monkeypatch.setattr(module.cloud_snapshots,'fetch',forbidden)
 r=c.get('/api/v1/telemetry');assert r.status_code==200 and not r.json()['cloud_polling']
 sensor=next(x for x in r.json()['items'] if x['kind']=='sensor');assert sensor['status']=='missing'
 p='/api/v1/devices/'+sensor['id']+'/readings'
 assert c.get(p,params={'metric':'temperature','hours':24}).json()['points']==[]
 assert c.get(p,params={'metric':'temperature','hours':2}).status_code==422
 assert c.get(p,params={'metric':'imaginary','hours':24}).status_code==404
 c.headers.pop('Authorization')
 assert c.get('/api/v1/telemetry').status_code==401
 assert c.get(p,params={'metric':'temperature'}).status_code==401
 assert c.post('/api/v1/devices/'+sensor['id']+'/sensor-cloud-snapshot').status_code==401


def test_telemetry_schedule_validation_persistence_and_no_upstream_call(ctx,monkeypatch):
 c,s,a,d=ctx
 sensor=next(x for x in c.get('/api/v1/telemetry').json()['items'] if x['kind']=='sensor');p='/api/v1/devices/'+sensor['id']+'/telemetry-schedule'
 body={'enabled':True,'source':'local','interval_seconds':15}
 before=len(a.sent);r=c.put(p,json=body);assert r.status_code==200 and r.json()['config']==body
 second=Store(s.path);assert second.one('settings','sensor-refresh:'+sensor['id'])==body;second.db.close()
 assert len(a.sent)==before
 for patch in [{'interval_seconds':14},{'interval_seconds':86401},{'interval_seconds':True},{'interval_seconds':'60'},{'enabled':'false'},{'source':'unsupported'}]:
  assert c.put(p,json={**body,**patch}).status_code==422
 assert c.put(p,json={**body,'source':'tuya-cloud','interval_seconds':59}).status_code==422
 assert c.put(p,json={**body,'source':'tuya-cloud','interval_seconds':60}).status_code==409
 c.headers.pop('Authorization');assert c.put(p,json=body).status_code==401


def test_cloud_schedule_can_be_paused_without_valid_credentials(ctx):
 c,s,a,d=ctx;sensor=next(x for x in c.get('/api/v1/telemetry').json()['items'] if x['kind']=='sensor')
 r=c.put('/api/v1/devices/'+sensor['id']+'/telemetry-schedule',json={'enabled':False,'source':'tuya-cloud','interval_seconds':300})
 assert r.status_code==200 and r.json()['next_run_at'] is None


def test_controller_endpoints_require_auth_and_reject_non_ir(ctx):
 c,s,a,d=ctx
 assert c.get('/api/v1/controllers').status_code==200
 assert c.post('/api/v1/controllers/'+d['id']+'/discover').status_code==422
 assert c.post('/api/v1/devices/'+d['id']+'/infrared-commands',json={'key':'Power','key_id':1}).status_code==422
 c.headers.pop('Authorization')
 assert c.get('/api/v1/controllers').status_code==401
 assert c.post('/api/v1/devices/'+d['id']+'/infrared-commands',json={'key':'Power','key_id':1}).status_code==401


def test_external_name_match_is_reversible_and_keeps_physical_device(ctx):
 c,s,a,d=ctx;position=d['placement'];s.put('settings','external-observations',[{'id':'observed-outlet','name':'示例別名','room_name':'示例房間'}])
 p='/api/v1/external-observations/observed-outlet/match'
 assert c.put(p,json={'device_id':d['id']}).status_code==200
 saved=s.one('devices',d['id']);assert saved['aliases']==['示例別名'] and saved['placement']==position
 assert c.get('/api/v1/home').json()['pending_external'][0]['matched_device_id']==d['id']
 assert c.delete(p).status_code==200
 assert s.one('devices',d['id'])['aliases']==[] and s.one('devices',d['id'])['placement']==position
 assert len(a.sent)==0

def test_aifa_login_auth_local_only_and_password_validation_redacted(ctx):
 c,s,a,d=ctx
 c.headers.pop('Authorization');assert c.get('/api/v1/integrations/aifa').status_code==401
 c.headers['Authorization']='Bearer '+s.master_token
 response=c.post('/api/v1/integrations/aifa/login',json={'email':False,'password':'secret-not-echoed'})
 assert response.status_code==422 and 'secret-not-echoed' not in response.text
 with TestClient(module.app,base_url='http://127.0.0.1:8788',client=('192.168.50.70',60000)) as remote:
  response=remote.post('/api/v1/integrations/aifa/login',headers={'Authorization':'Bearer '+s.master_token},json={'email':'fixture@example.test','password':'secret'})
  assert response.status_code==403
 assert c.get('/api/v1/integrations/ewelink').json()['connected'] is False


def test_ac_timer_and_learning_routes_validate_without_actuation(ctx):
 c,s,a,d=ctx
 s.put('settings','aifa-discovery',{'queried_at':time.time(),'items':[{'id':'hub','name':'示例控制器','mac':'aabbccddeeff','children':[{'id':'ac','type':'0','sub_type':'0'}]}]})
 base='/api/v1/integrations/aifa/devices/hub/children/ac'
 assert c.get(base+'/native-keys').status_code==200
 assert c.post(base+'/timers',json={'action':'off','delay_seconds':60}).status_code==422
 headers={'Idempotency-Key':'test-timer'}
 assert c.post(base+'/timers',json={'action':'off','delay_seconds':True},headers=headers).status_code==422
 r=c.post(base+'/timers',json={'action':'off','delay_seconds':60},headers=headers);assert r.status_code==200
 assert c.post(base+'/timers',json={'action':'off','delay_seconds':60},headers=headers).json()==r.json()
 result=c.delete(base+'/timers/'+r.json()['id']);assert result.json()['items'][0]['status']=='cancelled'
 assert c.post(base+'/native-keys/learn',json={'controller_id':'bad','kind':'invalid'}).status_code==409
 assert c.put(base+'/native-keys/anti_mold_off/verify',json={'verified':True}).status_code==409
 assert not a.sent

def test_aifa_native_route_requires_auth_owned_profile_and_idempotency(ctx,monkeypatch):
 from backend.aifa import AifaAccount
 from backend.aifa_native import AifaNative
 c,s,_,_=ctx;a=AifaAccount();n=AifaNative(a);sent=[]
 s.put('settings','aifa-discovery',{'queried_at':time.time(),'items':[{'id':'hub','name':'example','children':[{'id':'ac','type':'0','sub_type':'0','device_code':'19968'}]}]})
 async def exchange(store,device,packet=None,on_send=None):
  on_send();sent.append(packet);return {'sent':True,'status':None}
 a.ac_exchange=exchange;monkeypatch.setattr(module,'aifa_native',n)
 p='/api/v1/integrations/aifa/devices/hub/children/ac/native-mold'
 assert c.get(p).json()['sender']=='aifa'
 assert c.post(p).status_code==422
 assert c.post(p,json={'packet':'unknown'},headers={'Idempotency-Key':'bad'}).status_code==422
 assert c.put(p+'/verify',json={'command_id':'unknown','verified':True}).status_code==409
 r=c.post(p,headers={'Idempotency-Key':'one'});assert r.status_code==200 and r.json()['status']=='unconfirmed'
 assert c.post(p,headers={'Idempotency-Key':'one'}).json()['id']==r.json()['id']
 assert sent==['ffa026f0']
 assert c.put(p+'/verify',json={'command_id':r.json()['id'],'verified':True}).json()['verified']
 c.headers.pop('Authorization');assert c.get(p).status_code==401 and c.post(p,headers={'Idempotency-Key':'unauthorized'}).status_code==401
