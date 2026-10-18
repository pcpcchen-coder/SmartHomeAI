"""Opt-in, read-only AIFA Smart compatibility client.
Protocol reference: https://github.com/PinLin/aifa-smart-homeassistant (MIT).
No third-party package is executed; only the vendor HTTPS host receives credentials.
"""
import asyncio,time
import httpx
from .adapters import AdapterError
from .aifa_climate import capabilities,encode,decode

HOST='https://api.aifaremote.com'
CLIENT_ID='Ecp5TUQxtOjdQ24u' # Public AIFA application identifier; not a user credential.

def rows(data,*keys):
 if isinstance(data,list):return data
 if isinstance(data,dict):
  for key in keys:
   if isinstance(data.get(key),list):return data[key]
 raise AdapterError('aifa_format','AIFA 回覆格式不相容，既有資料保留')

def account_view(store):
 cache=store.one('settings','aifa-discovery',{})
 return {'connected':bool(store.secret('aifa').get('access_token')),'queried_at':cache.get('queried_at'),'items':cache.get('items',[]),'error':cache.get('error'),'read_only':False,'supports_ac_power':True,'compatibility_api':True}

class AifaAccount:
 def __init__(self):self.lock=asyncio.Lock()
 async def request(self,method,path,body=None,token=None,params=None):
  if (method,path) not in [('POST','/oauth2/token'),('GET','/devices'),('GET','/functions')]:raise AdapterError('aifa_path','不支援此 AIFA 介面')
  try:
   async with httpx.AsyncClient(timeout=10,follow_redirects=False) as c:
    r=await c.request(method,HOST+path,json=body,params=params,headers={'Authorization':'Bearer '+token} if token else {})
   if r.status_code in [400,401,403]:raise AdapterError('aifa_auth','AIFA 未接受登入或授權，請核對帳號或重新登入')
   r.raise_for_status();return r.json()
  except (httpx.HTTPError,ValueError):raise AdapterError('aifa_connection','AIFA 連線或回覆未完成，請稍後再試') from None
 def save_tokens(self,store,reply,previous=None):
  if not isinstance(reply,dict) or not isinstance(reply.get('access_token'),str) or not reply['access_token']:raise AdapterError('aifa_format','AIFA 沒有回傳有效授權')
  try:expires=max(0,float(reply.get('expires_in',3600)))
  except (ValueError,TypeError):expires=3600
  store.set_secret('aifa',{'access_token':reply['access_token'],'refresh_token':reply.get('refresh_token') or (previous or {}).get('refresh_token'),'expires_at':time.time()+expires})
 async def login(self,store,email,password):
  async with self.lock:
   attempt=store.one('settings','aifa-login-attempt',{}).get('at',0)
   if time.time()-attempt<10:raise AdapterError('aifa_cooldown','登入至少相隔 10 秒，請稍候')
   store.put('settings','aifa-login-attempt',{'at':time.time()})
   # Password is sent once, never persisted or included in errors/events.
   reply=await self.request('POST','/oauth2/token',{'client_id':CLIENT_ID,'grant_type':'password','email':email,'password':password})
   self.save_tokens(store,reply);store.put('settings','aifa-discovery',{})
   store.event('aifa.linked',{'message':'已取得 AIFA Smart 授權；密碼未保存'})
  return await self.discover(store)
 async def discover(self,store):
  async with self.lock:
   old=store.one('settings','aifa-discovery',{})
   if time.time()-old.get('attempted_at',0)<60:raise AdapterError('aifa_cooldown','AIFA 清單同步至少相隔 60 秒')
   store.put('settings','aifa-discovery',{**old,'attempted_at':time.time()})
   try:
    secret=store.secret('aifa')
    if not secret.get('access_token'):raise AdapterError('aifa_auth','請先連結 AIFA Smart 帳號')
    if time.time()>=secret.get('expires_at',0)-60:
     if not secret.get('refresh_token'):raise AdapterError('aifa_auth','AIFA 授權已到期，請重新登入')
     self.save_tokens(store,await self.request('POST','/oauth2/token',{'client_id':CLIENT_ID,'grant_type':'refresh_token','refresh_token':secret['refresh_token']}),secret);secret=store.secret('aifa')
    data=await self.request('GET','/devices',token=secret['access_token']);items=[]
    for row in rows(data,'devices','data','items'):
     if not isinstance(row,dict):continue
     ident=row.get('id',row.get('deviceId'))
     if ident is None:continue
     mac=str(row.get('mac') or '');compact=lambda x:''.join(c for c in x.lower() if c in '0123456789abcdef')
     matches=[d for d in store.devices() if len(compact(mac))==12 and compact(d.get('mac',''))==compact(mac)]
     children=[]
     for sub in row.get('subDevices',row.get('sub_devices',[])) or []:
      if not isinstance(sub,dict) or sub.get('id',sub.get('subDeviceId')) is None:continue
      children.append({'id':str(sub.get('id',sub.get('subDeviceId'))),'name':str(sub.get('name',sub.get('deviceName','未命名家電')))[:80],'type':str(sub.get('type',''))[:80],'sub_type':str(sub.get('subType',''))[:20],'device_code':str(sub.get('deviceCode',''))[:20],'capabilities':capabilities(sub.get('deviceCode'))})
     items.append({'id':str(ident),'name':str(row.get('deviceName',row.get('name','AIFA 控制器')))[:80],'mac':mac,'online':row.get('online') if isinstance(row.get('online'),bool) else None,'matched_device_id':matches[0]['id'] if len(matches)==1 else None,'children':children})
    store.put('settings','aifa-discovery',{'attempted_at':time.time(),'queried_at':time.time(),'items':items,'error':None})
    store.event('aifa.discovered',{'message':'已讀取 AIFA 控制器與家電清單'})
    return account_view(store)
   except AdapterError as e:
    store.put('settings','aifa-discovery',{**old,'attempted_at':time.time(),'error':e.message});raise
 async def disconnect(self,store):
  async with self.lock:
   store.set_secret('aifa',{});store.put('settings','aifa-discovery',{});store.event('aifa.disconnected',{'message':'已移除本機 AIFA 授權；不刪除手機 App 設備'})

 def owned_child(self,store,hub,child):
  cache=store.one('settings','aifa-discovery',{})
  if not cache.get('queried_at') or time.time()-cache['queried_at']>86400:raise AdapterError('aifa_stale','請先重新讀取 AIFA 家電清單')
  device=next((x for x in cache.get('items',[]) if x['id']==hub),None)
  if not device or not any(x['id']==child and x['type']=='0' for x in device['children']):raise AdapterError('aifa_owned','找不到已授權的 AIFA 冷氣')
  return device
 async def ac_exchange(self,store,device,power=None,on_send=None,packet=None):
  import ssl
  secret=store.secret('aifa')
  if not secret.get('access_token') or time.time()>secret.get('expires_at',0)-30:raise AdapterError('aifa_auth','請先重新讀取家電以更新 AIFA 授權')
  writer=None;sent=False
  try:
   reader,writer=await asyncio.wait_for(asyncio.open_connection('aifaremote.com',8751,ssl=ssl.create_default_context(),server_hostname='aifaremote.com'),5)
   writer.write(bytes.fromhex('a1fad7c0')+__import__('json').dumps({'mac':device['mac'],'token':secret['access_token']},separators=(',',':')).encode());await writer.drain()
   ack=await asyncio.wait_for(reader.read(32),3)
   if ack[:1]!=b'\x00':raise AdapterError('aifa_auth','AIFA 控制連線未接受授權')
   if power is not None or packet is not None:
    if on_send:on_send()
    sent=True;writer.write(bytes.fromhex(packet or ('ffa0a1f0' if power else 'ffa0a2f0')));await writer.drain()
    await asyncio.sleep(1)
   writer.write(bytes.fromhex('fff3f2a0f0'));await writer.drain();data=ack[1:];deadline=time.monotonic()+3
   while time.monotonic()<deadline:
    try:part=await asyncio.wait_for(reader.read(4096),.6)
    except asyncio.TimeoutError:continue
    if not part:break
    data+=part
   packets=[data[i:i+14] for i in range(max(0,len(data)-13)) if data[i:i+2]==b'\xfe\xa0' and data[i+13]==0xf0]
   status=None
   if packets:
    p=packets[-1];off=p[4]==0 and p[6]==0
    status={**decode(p),'received_at':time.time(),'source':'aifa-controller-reported','physical_feedback':False}
    store.put('settings','aifa-ac-status:'+device['id'],status)
   return {'sent':sent,'status':status}
  except AdapterError:raise
  except Exception:
   if sent:return {'sent':True,'status':None}
   raise AdapterError('aifa_connection','AIFA 控制連線未完成，沒有送出開關指令') from None
  finally:
   if writer:
    writer.close()
    try:await writer.wait_closed()
    except Exception:pass
 async def ac_status(self,store,hub,child):
  async with self.lock:
   d=self.owned_child(store,hub,child)
   return await self.ac_exchange(store,d)
 async def ac_power(self,store,hub,child,power,idem,settings=None):
  import uuid
  async with self.lock:
   d=self.owned_child(store,hub,child);key='aifa:'+hub+':'+child;req={'command':'climate','settings':settings} if settings is not None else {'command':'power','value':power};previous=store.command(key,idem)
   if previous:
    if previous['request']!=req:raise AdapterError('idempotency_conflict','相同指令識別碼不可用於不同內容')
    return previous['result']
   last=store.one('settings','aifa-last-send',{}).get('at',0)
   if time.time()-last<2:raise AdapterError('aifa_cooldown','請稍候兩秒再操作')
   ident=str(uuid.uuid4());result={'id':ident,'status':'accepted','message':'AIFA 指令處理中'};store.save_command(ident,key,idem,req,result)
   def mark_send():store.put('settings','aifa-last-send',{'at':time.time()})
   try:
    profile=next(x for x in d['children'] if x['id']==child)
    packet=encode(profile.get('device_code'),settings) if settings is not None else None
    reply=await self.ac_exchange(store,d,power,on_send=mark_send,**({'packet':packet} if packet else {}))
    result.update(status='unconfirmed',controller_status=reply['status'],message='已透過 AIFA 連線送出；控制器狀態不代表冷氣實際反應，請現場確認')
   except AdapterError as e:result.update(status='failed',message=e.message)
   store.save_command(ident,key,idem,req,result);store.event('command.'+result['status'],{'command_id':ident,'name':d['name'],'message':result['message'],'command':'aifa-power'})
   return result
