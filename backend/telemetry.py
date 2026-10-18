"""Normalized sensor readings. Receipt/query time is not a device measurement time."""
import asyncio, hashlib, hmac, math, time
from urllib.parse import quote
import httpx
from .adapters import AdapterError

# code: stable key, label, default unit, decimal places, group
SPECS={
 'temp_current':('temperature','溫度','°C',1,'environment'),
 'humidity_value':('humidity','濕度','%',0,'environment'),
 'co2_value':('co2','二氧化碳','ppm',0,'air'),
 'ch2o_value':('formaldehyde','甲醛','mg/m³',3,'air'),
 'voc_value':('voc','VOC','mg/m³',3,'air'),
 'pm25_value':('pm25','PM2.5','μg/m³',0,'air'),
 'pm10':('pm10','PM10','μg/m³',0,'air'),
 'illuminance_value':('illuminance','照度','lx',0,'environment'),
 'battery_percentage':('battery','電池','%',0,'device'),
 'air_quality_index':('air_quality','裝置空氣等級','',0,'air'),
 'presence_state':('presence','人體存在','',0,'security'),
 'pir':('motion','人體感應','',0,'security'),
 'watersensor_state':('leak','漏水狀態','',0,'security'),
 'doorcontact_state':('contact','門窗狀態','',0,'security'),
 'cur_power':('power_usage','功率','W',1,'energy'),
 'cur_current':('current','電流','mA',0,'energy'),
 'cur_voltage':('voltage','電壓','V',1,'energy'),
 'add_ele':('energy','設備累積用電','kWh',3,'energy'),
}
UNITS={'PPM':'ppm','ug/m3':'μg/m³','mg/m3':'mg/m³','lux':'lx','kwh':'kWh','w':'W'}
STATES={'presence':'有人','none':'無人','pir':'偵測到移動','alarm':'警報','normal':'正常'}

def normalize(mapping, dps=None, codes=None):
 rows=[];dps=dps or {};codes=codes or {}
 for dp,spec in mapping.items():
  code=spec.get('code')
  if code not in SPECS:continue
  key,label,unit,precision,group=SPECS[code];meta=spec.get('values') if isinstance(spec.get('values'),dict) else {}
  value=codes.get(code) if code in codes else dps.get(str(dp))
  raw=value
  if value is not None:
   if spec.get('type','').lower()=='integer':
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):value=None
    else:value=value/(10**max(0,min(9,int(meta.get('scale',0)))))
   elif code=='doorcontact_state':value='開啟' if value is True else '關閉' if value is False else None
   elif code=='air_quality_index':value='等級 '+value.split('_')[-1] if isinstance(value,str) and value.startswith('level_') else str(value)
   else:value=STATES.get(str(value),value)
  rows.append({'key':key,'code':code,'label':label,'unit':UNITS.get(meta.get('unit'),meta.get('unit') or unit),'precision':precision,'group':group,'value':value,'alarm':code=='watersensor_state' and raw=='alarm'})
 return rows

def metrics_for(device, state):
 rows=normalize(device.get('config',{}).get('mapping',{}),state.get('values',{}).get('dps',{}))
 values=state.get('values',{})
 if device['adapter']=='lg' and device['kind']=='dehumidifier':
  rows=[{'key':'humidity','code':'humidity','label':'濕度','unit':'%','precision':0,'group':'environment','value':values.get('humidity'),'alarm':False}]
 if device['adapter']=='homeassistant':
  attrs=values.get('attributes',{});dc=attrs.get('device_class');code={'temperature':'temp_current','humidity':'humidity_value','carbon_dioxide':'co2_value','illuminance':'illuminance_value','pm25':'pm25_value','pm10':'pm10','volatile_organic_compounds':'voc_value','battery':'battery_percentage','power':'cur_power','voltage':'cur_voltage','current':'cur_current','energy':'add_ele'}.get(dc)
  if code:
   rows=normalize({'value':{'code':code,'type':'Integer','values':{'scale':0,'unit':attrs.get('unit_of_measurement')}}})
   try:v=float(values.get('entity_state'));rows[0]['value']=v if math.isfinite(v) else None
   except (ValueError,TypeError):pass
 return rows

def sensor_view(store, device):
 state=store.one('state',device['id'],{});lan=metrics_for(device,state)
 snapshot=store.one('settings','sensor-cloud:'+device['id'],{})
 use_cloud=bool((snapshot.get('queried_at') or 0)>(state.get('observed_at') or 0))
 rows=snapshot.get('metrics',[]) if use_cloud else lan
 if not rows:rows=lan
 if not rows:return None
 at=snapshot.get('queried_at') if use_cloud else state.get('observed_at');age=time.time()-at if at else None
 has_values=any(m['value'] is not None for m in rows)
 live=not use_cloud and state.get('fresh') and state.get('available') and age is not None and age<90
 status='live' if live and has_values else 'snapshot' if use_cloud and has_values and age<900 else 'stale' if has_values else 'missing'
 return {'id':device['id'],'name':device['name'],'kind':device['kind'],'room_id':(device.get('placement') or {}).get('room_id'),'ips':device.get('ips',[]),'metrics':rows,'status':status,'received_at':at,'measurement_at':None,'source':'tuya-cloud-snapshot' if use_cloud else state.get('source'),'source_label':'Tuya 雲端快照' if use_cloud else {'tuya-lan':'Tuya 區網','tuya-gateway-lan':'Tuya 閘道','lg-thinq-cloud':'LG ThinQ','lg-thinq':'LG ThinQ','home-assistant':'Home Assistant'}.get(state.get('source'),'尚無讀值' if not at else state.get('source','本機觀測')),'error':None if live else state.get('error'),'cloud_supported':device['adapter']=='tuya' and bool(device.get('config',{}).get('mapping')),'last_cloud_attempt':store.one('settings','sensor-cloud-attempt:'+device['id'],{}).get('at')}

class CloudSnapshots:
 """Read-only, explicit requests; no scheduled cloud polling or cloud commands."""
 def __init__(self):self.lock=asyncio.Lock();self.tokens={}
 async def fetch(self, store, device):
  c=store.secret('tuya-cloud');did=device.get('config',{}).get('device_id')
  allowed={d['id'] for d in store.secret('tuya-cloud-devices').get('devices',[])}
  if not c.get('api_key') or not c.get('api_secret') or did not in allowed:raise AdapterError('cloud_not_linked','此設備尚未由 Tuya 帳號授權')
  hosts={'us':'openapi.tuyaus.com','az':'openapi.tuyaus.com','us-e':'openapi-ueaz.tuyaus.com','eu':'openapi.tuyaeu.com','eu-w':'openapi-weaz.tuyaeu.com','cn':'openapi.tuyacn.com','in':'openapi.tuyain.com','sg':'openapi-sg.iotbing.com'}
  host=hosts.get(c.get('region'))
  if not host:raise AdapterError('region_required','Tuya 資料中心尚未設定')
  async with self.lock:
   last=store.one('settings','sensor-cloud-attempt:'+device['id'],{}).get('at',0)
   if time.time()-last<60:raise AdapterError('cloud_cooldown','每台設備的雲端查詢間隔至少 60 秒，請稍後再試')
   store.put('settings','sensor-cloud-attempt:'+device['id'],{'at':time.time()})
   identity=hashlib.sha256((c['api_key']+c['api_secret']+host).encode()).hexdigest()
   async with httpx.AsyncClient(timeout=8,follow_redirects=False) as client:
    async def get(path,token=''):
     stamp=str(int(time.time()*1000));canonical='GET\n'+hashlib.sha256(b'').hexdigest()+'\n\n'+path
     signature=hmac.new(c['api_secret'].encode(),(c['api_key']+token+stamp+canonical).encode(),hashlib.sha256).hexdigest().upper()
     headers={'client_id':c['api_key'],'sign':signature,'sign_method':'HMAC-SHA256','t':stamp}
     if token:headers['access_token']=token
     reply=await client.get('https://'+host+path,headers=headers);reply.raise_for_status();data=reply.json()
     if not data.get('success'):
      if token and str(data.get('code')) in ['1010','1011','1012','1013']:self.tokens.pop(identity,None)
      raise AdapterError('cloud_rejected','Tuya 雲端未允許查詢，請檢查服務效期、額度與帳號授權')
     return data['result']
    token,expiry=self.tokens.get(identity,('',0))
    if time.time()>expiry:
     result=await get('/v1.0/token?grant_type=1');token=result['access_token'];self.tokens[identity]=(token,time.time()+max(0,result.get('expire_time',3600)-60))
    raw=await get('/v1.0/devices/'+quote(did,safe='')+'/status',token)
   rows=normalize(device['config']['mapping'],codes={x['code']:x.get('value') for x in raw if isinstance(x,dict) and 'code' in x})
   snapshot={'queried_at':time.time(),'metrics':rows,'source':'tuya-cloud-snapshot'}
   store.put('settings','sensor-cloud:'+device['id'],snapshot)
   store.record_readings(device['id'],snapshot['queried_at'],snapshot['source'],rows)
   return snapshot
