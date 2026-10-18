"""Device adapters return observations; a successful send is not verified state."""
import asyncio, colorsys, random, socket, struct, time, uuid, subprocess, threading
import httpx
from contextlib import contextmanager

class AdapterError(Exception):
 def __init__(self,code,message):self.code,self.message=code,message;super().__init__(message)

def observation(source,values,commands=None):
 return {'source':source,'observed_at':time.time(),'fresh':True,'values':values,'commands':commands or [],'available':True,'error':None}

def lifx_packet(device,kind,payload=b'',source=None,seq=0):
 target=bytes.fromhex(device['mac'].replace(':',''))+b'\0\0'
 source=source or random.randint(1,2**32-1)
 return struct.pack('<HHI8s6sBB8sHH',36+len(payload),1024|0x1000,source,target,b'\0'*6,1,seq,b'\0'*8,kind,0)+payload,source

def lifx_exchange(device,kind,payload=b'',expect=107):
 packet,source=lifx_packet(device,kind,payload)
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
  sock.settimeout(1.8);sock.sendto(packet,(device['ips'][0],56700));deadline=time.monotonic()+1.8
  while time.monotonic()<deadline:
   response,_=sock.recvfrom(4096)
   if len(response)>=36 and response[8:14]==bytes.fromhex(device['mac'].replace(':','')) and struct.unpack_from('<I',response,4)[0]==source and struct.unpack_from('<H',response,32)[0]==expect:return response[36:]
 raise AdapterError('timeout','設備未回覆區網查詢')

def read_lifx(device):
 p=lifx_exchange(device,101)
 if len(p)<44:raise AdapterError('protocol_error','燈具回覆格式無法辨識')
 h,s,b,k=struct.unpack_from('<HHHH',p);power=struct.unpack_from('<H',p,10)[0]
 return observation('lifx-lan',{'power':power>0,'brightness':round(b/65535*100),'temperature':k,'hue':h,'saturation':s,'label':p[12:44].rstrip(b'\0').decode(errors='replace')},['power','brightness','temperature','color'])

def send_lifx(device,command,value):
 if command=='power':payload=struct.pack('<HI',65535 if value else 0,200);kind=117
 else:
  st=read_lifx(device)['values'];h,s,b,k=st['hue'],st['saturation'],round(st['brightness']/100*65535),st['temperature']
  if command=='brightness':b=round(value/100*65535)
  elif command=='temperature':k=int(value);s=0
  elif command=='color':
   rgb=tuple(int(value[i:i+2],16)/255 for i in [1,3,5]);hh,ss,_=colorsys.rgb_to_hsv(*rgb);h,s=round(hh*65535),round(ss*65535)
  else:raise AdapterError('unsupported','這個燈具不支援此指令')
  kind=102;payload=struct.pack('<BHHHHI',0,h,s,b,k,200)
 packet,_=lifx_packet(device,kind,payload)
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:sock.sendto(packet,(device['ips'][0],56700))

class Adapters:
 def __init__(self,store):self.store=store;self.casts={};self.cast_locks={};self.lg_profiles={};self.tuya_locks={}
 def config(self,d):return {**d.get('config',{}),**self.store.secret(d['id'])}
 def configured(self,d):
  if d['adapter']=='tuya':
   c=self.config(d)
   return bool(c.get('local_key') and c.get('device_id') and not c.get('infrared_virtual') and (d.get('ips') or c.get('gateway_id')))
  if d['adapter']=='lg':return bool(self.store.secret('lg').get('pat') and self.config(d).get('lg_device_id'))
  if d['adapter']=='homeassistant':return bool(self.store.secret('homeassistant').get('token') and self.config(d).get('entity_id'))
  return d['adapter'] in ['lifx','cast']
 def cast(self,d):
  import pychromecast
  from pychromecast.models import CastInfo,HostServiceInfo
  key=d['id']
  if key not in self.casts:
   config=self.config(d)
   if not config.get('uuid'):raise AdapterError('configuration_required','缺少音箱識別碼，請先設定')
   actual=pychromecast.dial.get_device_info(d['ips'][0],timeout=5)
   if not actual:raise AdapterError('identity_unavailable','音箱識別查詢尚未回覆，請重新觀測並檢查區域網路權限')
   if str(actual.uuid)!=str(uuid.UUID(config['uuid'])):raise AdapterError('identity_mismatch','音箱識別與原始紀錄不同，請核對 IP 與設備')
   info=CastInfo(services={HostServiceInfo(d['ips'][0],8009)},uuid=uuid.UUID(config['uuid']),model_name=config.get('model','Google Cast'),friendly_name=d['name'],host=d['ips'][0],port=8009,cast_type=config.get('cast_type','audio'),manufacturer='Google Inc.')
   cast=pychromecast.Chromecast(cast_info=info,tries=1,timeout=4,retry_wait=1)
   cast.wait(timeout=5);self.casts[key]=cast
  return self.casts[key]
 def read_cast(self,d):
  cast=self.cast(d);reply=threading.Event();received=[]
  def receiver_reply(success,response):received.append(success);reply.set()
  cast.socket_client.receiver_controller.update_status(callback_function=receiver_reply)
  if not reply.wait(2) or not received[0]:raise AdapterError('timeout','音箱狀態查詢逾時')
  media_fresh=False
  if 'urn:x-cast:com.google.cast.media' in cast.socket_client.app_namespaces:
   media_reply=threading.Event();media_ok=[]
   def media_done(success,response):media_ok.append(success);media_reply.set()
   cast.media_controller.send_message_nocheck({'type':'GET_STATUS'},callback_function=media_done)
   media_fresh=media_reply.wait(1.5) and bool(media_ok[0])
  st=cast.status;media=cast.media_controller.status if media_fresh and 'urn:x-cast:com.google.cast.media' in cast.socket_client.app_namespaces else None
  if not st or not cast.socket_client.is_connected:raise AdapterError('timeout','音箱未回覆狀態')
  commands=['volume','mute']
  if media and media.media_session_id is not None:commands+=['play','pause','stop']
  return observation('google-cast',{'volume':round(st.volume_level*100),'mute':st.volume_muted,'app':st.display_name,'title':media.title if media else None,'artist':media.artist if media else None,'playback':media.player_state if media else 'UNKNOWN','media_session':media.media_session_id if media else None},commands)
 @contextmanager
 def tuya(self,d):
  import tinytuya
  c=self.config(d)
  if not self.configured(d):raise AdapterError('credentials_required','需要 Tuya local key，才能讀取與控制')
  gateway=c.get('gateway_id');parent=None
  with self.tuya_locks.setdefault(gateway or c['device_id'],threading.Lock()):
   if gateway:
    g=next((x for x in self.store.devices() if x.get('config',{}).get('device_id')==gateway),None)
    if not g or not g.get('ips'):raise AdapterError('gateway_required','尚未找到此設備所屬閘道的 IP')
    gc=self.config(g)
    parent=tinytuya.Device(gateway,g['ips'][0],gc['local_key'],version=float(gc.get('version','3.4')))
    parent.set_socketTimeout(3);parent.set_socketRetryLimit(1)
    dev=tinytuya.Device(c['device_id'],local_key=c['local_key'],parent=parent,cid=c['node_id'])
   else:dev=tinytuya.Device(c['device_id'],d['ips'][0],c['local_key'],version=float(c.get('version','3.4')))
   dev.set_socketTimeout(3);dev.set_socketRetryLimit(1)
   try:yield dev
   finally:(parent or dev).close()
 def read_tuya(self,d):
  with self.tuya(d) as dev:raw=dev.status()
  if not isinstance(raw,dict) or 'dps' not in raw:raise AdapterError('authentication_or_timeout','Tuya 未回覆可解密狀態，請核對金鑰、版本與連線')
  c=self.config(d);dps=raw['dps'];mapped=c.get('power_dps',[]);channels=[{'id':str(k),'on':v} for k,v in dps.items() if str(k) in mapped and isinstance(v,bool)]
  power=dps.get(str(mapped[0])) if mapped else None
  commands=['power'] if isinstance(power,bool) else []
  values={'power':power,'channels':channels,'dps':dps};metrics=[]
  labels={'temp_current':('溫度','°C'),'humidity_value':('濕度','%'),'co2_value':('二氧化碳','ppm'),'pm25_value':('PM2.5','μg/m³'),'pm10':('PM10','μg/m³'),'battery_percentage':('電池','%'),'presence_state':('人體存在',''),'pir':('人體感應',''),'watersensor_state':('漏水',''),'doorcontact_state':('門窗',''),'illuminance_value':('照度','lx')}
  states={'presence':'有人','none':'無人','pir':'偵測到移動','alarm':'警報','normal':'正常'}
  for dp,spec in c.get('mapping',{}).items():
   code=spec.get('code');v=dps.get(dp)
   if code in labels and v is not None:
    if spec.get('type')=='Integer' and isinstance(v,(int,float)):v=v/10**spec.get('values',{}).get('scale',0)
    if code=='doorcontact_state':v='開啟' if v else '關閉'
    label,unit=labels[code];metrics.append({'label':label,'value':states.get(str(v),v),'unit':unit})
  values['metrics']=metrics
  if c.get('cover_dp') and str(c['cover_dp']) in dps:commands.append('cover')
  return observation('tuya-gateway-lan' if c.get('gateway_id') else 'tuya-lan',values,commands)
 async def ha(self,path,method='GET',body=None):
  config=self.store.secret('homeassistant');url=config.get('url','').rstrip('/')
  if not url or not config.get('token'):raise AdapterError('credentials_required','請先連接 Home Assistant')
  async with httpx.AsyncClient(timeout=8,follow_redirects=False) as client:
   r=await client.request(method,url+'/api/'+path,headers={'Authorization':'Bearer '+config['token']},json=body);r.raise_for_status();return r.json()
 async def lg(self,method,*args):
  from aiohttp import ClientSession
  from thinqconnect.thinq_api import ThinQApi
  c=self.store.secret('lg')
  if not c.get('pat'):raise AdapterError('credentials_required','需要 LG ThinQ 個人存取權杖（PAT）')
  async with ClientSession() as session:
   api=ThinQApi(session,c['pat'],c.get('country','TW'),c.get('client_id',''))
   r=await getattr(api,method)(*args,timeout=10)
   if r is None:raise AdapterError('cloud_unavailable','LG ThinQ 未傳回資料，請檢查 PAT、帳號地區與網路')
   return r
 async def read(self,d):
  a=d['adapter']
  if a=='lifx':return await asyncio.to_thread(read_lifx,d)
  if a=='cast':return await asyncio.to_thread(self.read_cast,d)
  if a=='tuya':return await asyncio.to_thread(self.read_tuya,d)
  if a=='homeassistant':
   raw=await self.ha('states/'+self.config(d)['entity_id'])
   if raw.get('state') in ['unavailable','unknown']:raise AdapterError('upstream_unavailable','Home Assistant 尚未取得設備可用狀態')
   domain=raw['entity_id'].split('.')[0];attrs=raw.get('attributes',{})
   commands=['power'] if domain in ['light','switch','fan','humidifier','media_player'] else []
   if domain=='light' and 'brightness' in attrs:commands+=['brightness']
   return observation('home-assistant',{'power':raw['state']=='on','entity_state':raw['state'],'brightness':round(attrs.get('brightness',0)/255*100),'attributes':attrs,'upstream_updated_at':raw.get('last_updated')},commands)
  if a=='lg':
   c=self.config(d)
   if not self.configured(d):raise AdapterError('credentials_required','請接入 LG ThinQ 帳號並對應設備')
   cloud_id=c['lg_device_id'];raw=await self.lg('async_get_device_status',cloud_id)
   if cloud_id not in self.lg_profiles:self.lg_profiles[cloud_id]=await self.lg('async_get_device_profile',cloud_id)
   profile=self.lg_profiles[cloud_id];commands=[]
   prop=profile.get('property',profile.get('properties',{}))
   power_spec=prop.get('operation',{}).get('dehumidifierOperationMode',{})
   permitted=power_spec.get('value',{}).get('w',[]) if 'w' in power_spec.get('mode',[]) else []
   if d['kind']=='dehumidifier' and 'POWER_ON' in permitted and 'POWER_OFF' in permitted:commands=['power']
   hum=raw.get('humidity',{});op=raw.get('operation',{}).get('dehumidifierOperationMode')
   return observation('lg-thinq-cloud',{'power':op=='POWER_ON' if op else None,'humidity':hum.get('currentHumidity'),'target_humidity':hum.get('targetHumidity'),'details':raw},commands)
  return await self.read_network(d)
 async def read_network(self,d):
  def ping():return subprocess.run(['/sbin/ping','-n','-c','1','-W','600',d['ips'][0]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2).returncode==0
  online=await asyncio.to_thread(ping)
  r=observation('icmp',{'reachable':online});r['available']=online;return r
 async def send(self,d,command,value):
  a=d['adapter']
  if a=='lifx':await asyncio.to_thread(send_lifx,d,command,value)
  elif a=='cast':
   def act():
    cast=self.cast(d)
    if command=='volume':cast.set_volume(value/100)
    elif command=='mute':cast.set_volume_muted(value)
    elif command in ['play','pause','stop']:
     if 'urn:x-cast:com.google.cast.media' not in cast.socket_client.app_namespaces or cast.media_controller.status.media_session_id is None:raise AdapterError('session_ended','媒體工作階段已結束，請重新觀測')
     cast.media_controller.send_message_nocheck({'type':command.upper(),'mediaSessionId':cast.media_controller.status.media_session_id})
   await asyncio.to_thread(act)
  elif a=='tuya':
   c=self.config(d)
   def act_tuya():
    if command=='power' and c.get('power_dps'):dp=c['power_dps'][0]
    elif command=='cover' and c.get('cover_dp') and value in ['open','stop','close']:dp=c['cover_dp']
    else:raise AdapterError('unsupported','此設備未提供已對應的控制功能')
    with self.tuya(d) as dev:return dev.set_value(int(dp),value)
   result=await asyncio.to_thread(act_tuya)
   if isinstance(result,dict) and result.get('Error'):raise AdapterError('command_rejected','Tuya 裝置拒絕指令')
  elif a=='homeassistant':
   entity=self.config(d)['entity_id'];domain=entity.split('.')[0]
   if command=='power':service='turn_on' if value else 'turn_off';body={'entity_id':entity}
   elif command=='brightness':service='turn_on';body={'entity_id':entity,'brightness_pct':value}
   else:raise AdapterError('unsupported','這個 Home Assistant 實體不支援此指令')
   await self.ha('services/'+domain+'/'+service,'POST',body)
  elif a=='lg' and command=='power' and d['kind']=='dehumidifier':
   await self.lg('async_post_device_control',self.config(d)['lg_device_id'],{'operation':{'dehumidifierOperationMode':'POWER_ON' if value else 'POWER_OFF'}})
  else:raise AdapterError('unsupported','這台設備尚未設定可用的控制方式')
 async def close(self):
  for c in self.casts.values():
   try:await asyncio.to_thread(c.disconnect,2)
   except Exception:pass
