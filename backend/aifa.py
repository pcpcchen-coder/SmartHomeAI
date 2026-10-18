"""Opt-in, read-only AIFA Smart compatibility client.
Protocol reference: https://github.com/PinLin/aifa-smart-homeassistant (MIT).
No third-party package is executed; only the vendor HTTPS host receives credentials.
"""
import asyncio,time
import httpx
from .adapters import AdapterError

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
 return {'connected':bool(store.secret('aifa').get('access_token')),'queried_at':cache.get('queried_at'),'items':cache.get('items',[]),'error':cache.get('error'),'read_only':True,'compatibility_api':True}

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
      children.append({'id':str(sub.get('id',sub.get('subDeviceId'))),'name':str(sub.get('name',sub.get('deviceName','未命名家電')))[:80],'type':str(sub.get('type',''))[:80]})
     items.append({'id':str(ident),'name':str(row.get('deviceName',row.get('name','AIFA 控制器')))[:80],'mac':mac,'online':row.get('online') if isinstance(row.get('online'),bool) else None,'matched_device_id':matches[0]['id'] if len(matches)==1 else None,'children':children})
    store.put('settings','aifa-discovery',{'attempted_at':time.time(),'queried_at':time.time(),'items':items,'error':None})
    store.event('aifa.discovered',{'message':'已讀取 AIFA 控制器與家電清單'})
    return account_view(store)
   except AdapterError as e:
    store.put('settings','aifa-discovery',{**old,'attempted_at':time.time(),'error':e.message});raise
 async def disconnect(self,store):
  async with self.lock:
   store.set_secret('aifa',{});store.put('settings','aifa-discovery',{});store.event('aifa.disconnected',{'message':'已移除本機 AIFA 授權；不刪除手機 App 設備'})
