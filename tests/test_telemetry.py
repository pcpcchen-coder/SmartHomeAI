import asyncio, hashlib, hmac, time
import httpx
import pytest
from backend.telemetry import normalize, sensor_view, CloudSnapshots
from backend.store import Store
from backend.adapters import AdapterError

MAPPING={str(i):{'code':code,'type':'Integer','values':{'scale':scale,'unit':unit}} for i,code,scale,unit in [(2,'temp_current',1,'°C'),(5,'ch2o_value',3,'mg/m3'),(6,'voc_value',3,'mg/m3'),(7,'pm25_value',0,'ug/m3')]}
def device():return {'id':'sensor','name':'示例感測器','adapter':'tuya','kind':'sensor','ips':[],'config':{'device_id':'authorized-sensor','mapping':MAPPING}}

def test_scaling_units_missing_and_boolean_contact():
 rows=normalize(MAPPING,dps={'2':276,'5':202,'6':2435})
 assert [r['value'] for r in rows]==[27.6,.202,2.435,None]
 assert [r['unit'] for r in rows]==['°C','mg/m³','mg/m³','μg/m³']
 assert normalize(MAPPING,dps={'2':True})[0]['value'] is None
 assert normalize(MAPPING,dps={'2':float('inf')})[0]['value'] is None
 contact=normalize({'1':{'code':'doorcontact_state','type':'Boolean'}},dps={'1':False})
 assert contact[0]['value']=='關閉' and contact[0]['key']=='contact'


def test_source_freshness_and_no_measurement_timestamp(tmp_path):
 s=Store(tmp_path);d=device()
 s.put('state',d['id'],{'observed_at':None,'values':{},'fresh':False})
 assert sensor_view(s,d)['status']=='missing'
 now=time.time();rows=normalize(MAPPING,codes={'temp_current':276})
 s.put('settings','sensor-cloud:'+d['id'],{'queried_at':now,'metrics':rows})
 v=sensor_view(s,d);assert v['status']=='snapshot' and v['measurement_at'] is None and v['source']=='tuya-cloud-snapshot'
 s.put('state',d['id'],{'observed_at':now+1,'values':{'dps':{'2':280}},'fresh':True,'available':True,'source':'tuya-lan'})
 assert sensor_view(s,d)['status']=='live' and sensor_view(s,d)['metrics'][0]['value']==28
 s.put('state',d['id'],{'observed_at':now-1000,'values':{},'fresh':False})
 s.put('settings','sensor-cloud:'+d['id'],{'queried_at':now-999,'metrics':rows})
 assert sensor_view(s,d)['status']=='stale'
 s.db.close()


def test_history_deduplicates_preserves_real_points_and_prunes(tmp_path):
 s=Store(tmp_path);now=time.time()
 s.record_readings('sensor',now-31*86400,'tuya-lan',[{'key':'temperature','value':99,'unit':'°C'}])
 assert not s.readings('sensor','temperature',0)['points']
 for i in range(400):
  rows=[{'key':'temperature','value':i,'unit':'°C'},{'key':'absent','value':None,'unit':'%'}]
  s.record_readings('sensor',now-400+i,'tuya-lan',rows)
  s.record_readings('sensor',now-400+i,'tuya-lan',rows)
 r=s.readings('sensor','temperature',now-1000)
 assert r['total_samples']==400 and len(r['points'])==360
 assert r['points'][0]['value']==0 and r['points'][-1]['value']==399
 assert all(p['value']==round(p['at']-(now-400)) for p in r['points'])
 assert not s.readings('sensor','absent',0)['points']
 s.db.close()


def test_cloud_is_read_only_signed_authorized_and_throttled(tmp_path,monkeypatch):
 s=Store(tmp_path);d=device();s.set_secret('tuya-cloud',{'api_key':'test-client','api_secret':'test-secret','region':'us'})
 s.set_secret('tuya-cloud-devices',{'devices':[{'id':'authorized-sensor'}]})
 requests=[]
 def reply(request):
  requests.append(request)
  assert request.method=='GET' and request.url.host=='openapi.tuyaus.com'
  token=request.headers.get('access_token','');stamp=request.headers['t'];path=request.url.raw_path.decode()
  canonical='GET\n'+hashlib.sha256(b'').hexdigest()+'\n\n'+path
  signature=hmac.new(b'test-secret',('test-client'+token+stamp+canonical).encode(),hashlib.sha256).hexdigest().upper()
  assert request.headers['sign']==signature and 'test-secret' not in str(request.headers)
  result={'access_token':'test-token','expire_time':3600} if '/token?' in path else [{'code':'temp_current','value':276}]
  return httpx.Response(200,json={'success':True,'result':result})
 original=httpx.AsyncClient
 monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(reply)))
 async def run():
  cloud=CloudSnapshots();r=await cloud.fetch(s,d)
  assert r['metrics'][0]['value']==27.6 and len(requests)==2
  with pytest.raises(AdapterError,match='每台設備'):await cloud.fetch(s,d)
  assert len(requests)==2
  s.put('settings','sensor-cloud-attempt:sensor',{'at':time.time()-61})
  await cloud.fetch(s,d);assert len(requests)==3 # cached token
  other={**d,'id':'other','config':{**d['config'],'device_id':'not-authorized'}}
  with pytest.raises(AdapterError,match='尚未由'):await cloud.fetch(s,other)
  assert len(requests)==3
 asyncio.run(run());assert s.readings(d['id'],'temperature',0)['total_samples']==2
 assert 'test-secret' not in str(sensor_view(s,d));s.db.close()
