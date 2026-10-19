"""Official eWeLink OAuth. Credentials and raw account/device replies stay encrypted."""
import asyncio,base64,hashlib,hmac,json,secrets,time,math
from urllib.parse import urlencode
import httpx
from .adapters import AdapterError

CALLBACK='http://127.0.0.1:8788/api/v1/integrations/ewelink/callback'
HOSTS={'as':'https://as-apia.coolkit.cc','us':'https://us-apia.coolkit.cc','eu':'https://eu-apia.coolkit.cc','cn':'https://cn-apia.coolkit.cn'}
AUTH_URL='https://c2ccdn.coolkit.cc/oauth/index.html'
def usage_key():return 'ewelink-usage:'+time.strftime('%Y-%m',time.gmtime())

def sign(secret,message):return base64.b64encode(hmac.new(secret.encode(),message.encode(),hashlib.sha256).digest()).decode()
def view(store):
 config=store.secret('ewelink-app');token=store.secret('ewelink');cache=store.one('settings','ewelink-integration',{})
 items=[]
 for row in cache.get('items') or []:
  device=next((d for d in store.devices() if d.get('adapter')=='ewelink' and d.get('config',{}).get('ewelink_device_id')==row.get('id')),None)
  state=store.one('state',device['id'],{}) if device else {}
  items.append({**row,'imported_device_id':device['id'] if device else None,'power':state.get('values',{}).get('power',row.get('power')),'control_ready':bool(device and device.get('config',{}).get('single_switch_confirmed'))})
 return {**{k:cache.get(k) for k in ['status','error','queried_at','total','limited','certification','platform_issue']},'items':items,'configured':bool(config.get('app_id') and config.get('app_secret')),'connected':bool(token.get('access_token')),'region':token.get('region'),'redirect_url':CALLBACK,'monthly_free_calls':50000,'manual_refresh_only':not any(d.get('adapter')=='ewelink' and store.one('settings','sensor-refresh:'+d['id'],{}).get('enabled') for d in store.devices()),'usage':{'month_utc':usage_key().split(':')[-1],'attempted_calls_by_region':store.one('settings',usage_key(),{}),'local_count_only':True}}

class EWeLink:
 def __init__(self):self.lock=asyncio.Lock();self.last_request=0
 def configure(self,store,app_id,app_secret):
  if not all(isinstance(x,str) and 8<=len(x)<=128 and x.isascii() and not any(c.isspace() for c in x) for x in [app_id,app_secret]):raise AdapterError('ewelink_config','請填入官方控制台的 App ID 與 App Secret')
  store.set_secret('ewelink-app',{'app_id':app_id,'app_secret':app_secret,'redirect_url':CALLBACK})
  store.set_secret('ewelink',{});store.set_secret('ewelink-oauth',{});store.set_secret('ewelink-devices',{})
  old=store.one('settings','ewelink-integration',{})
  store.put('settings','ewelink-integration',{'certification':old.get('certification'),'status':'awaiting_authorization','items':[]})
  return view(store)
 def begin(self,store):
  config=store.secret('ewelink-app')
  if not config.get('app_id') or not config.get('app_secret'):raise AdapterError('ewelink_config','請先設定專屬應用憑證')
  state=secrets.token_urlsafe(32);seq=str(int(time.time()*1000))
  store.set_secret('ewelink-oauth',{'state_hash':hashlib.sha256(state.encode()).hexdigest(),'expires_at':time.time()+600})
  return {'authorization_url':AUTH_URL+'?'+urlencode({'clientId':config['app_id'],'seq':seq,'authorization':sign(config['app_secret'],config['app_id']+'_'+seq),'redirectUrl':CALLBACK,'grantType':'authorization_code','state':state,'nonce':secrets.token_hex(4),'showQRCode':'true'}),'expires_in':600}
 async def request(self,store,region,method,path,body=None,token=None):
  if region not in HOSTS or (method,path) not in [('POST','/v2/user/oauth/token'),('POST','/v2/user/refresh'),('GET','/v2/device/thing'),('GET','/v2/device/thing/status'),('POST','/v2/device/thing/status')]:raise AdapterError('ewelink_path','不支援此易微聯介面')
  config=store.secret('ewelink-app')
  if not config.get('app_id') or not config.get('app_secret'):raise AdapterError('ewelink_config','請先設定應用憑證')
  delay=1-(time.monotonic()-self.last_request)
  if delay>0:await asyncio.sleep(delay)
  data=json.dumps(body,separators=(',',':'),ensure_ascii=False) if method=='POST' else ''
  headers={'X-CK-Appid':config['app_id'],'X-CK-Nonce':secrets.token_hex(4),'Content-Type':'application/json','Authorization':'Bearer '+token if token else 'Sign '+sign(config['app_secret'],data)}
  self.last_request=time.monotonic()
  usage=store.one('settings',usage_key(),{})
  if usage.get(region,0)>=50000:raise AdapterError('ewelink_quota','本機已達這個資料區的每月免費呼叫預算，暫停呼叫；不自動付費')
  usage[region]=usage.get(region,0)+1;store.put('settings',usage_key(),usage)
  try:
   async with httpx.AsyncClient(timeout=12,follow_redirects=False) as client:
    r=await client.request(method,HOSTS[region]+path,headers=headers,content=data.encode() if method=='POST' else None,params=body if method=='GET' else None)
   if r.status_code==403:raise AdapterError('ewelink_quota','易微聯拒絕此呼叫，可能已達免費額度或應用權限不足；不會自動升級')
   r.raise_for_status();reply=r.json()
   if not isinstance(reply,dict):raise ValueError()
   error=reply.get('error')
   if error!=0:
    code='ewelink_quota' if error==412 else 'ewelink_permission' if error in [406,407] else 'ewelink_auth' if error in [401,402,405] else 'ewelink_rejected'
    messages={'ewelink_quota':'易微聯呼叫額度已用完；等待重置或自行評估方案，不自動付費','ewelink_permission':'此免費應用尚未獲准讀取這類設備','ewelink_auth':'易微聯授權未完成或已過期，請重新授權','ewelink_rejected':'易微聯未接受此次操作，既有設備資料保留'}
    raise AdapterError(code,messages[code])
   if not isinstance(reply.get('data'),dict):raise ValueError()
   return reply['data']
  except AdapterError:raise
  except (httpx.HTTPError,ValueError):raise AdapterError('ewelink_connection','易微聯連線或回覆未完成，請稍後再試') from None
 def save_tokens(self,store,data,region,previous=None):
  at=data.get('accessToken',data.get('at'));rt=data.get('refreshToken',data.get('rt')) or (previous or {}).get('refresh_token')
  if not isinstance(at,str) or not at or not isinstance(rt,str) or not rt:raise AdapterError('ewelink_format','易微聯沒有回傳完整授權')
  try:expires=min(float(data.get('atExpiredTime',0))/1000,time.time()+35*86400)
  except (TypeError,ValueError):expires=0
  if expires<=0:expires=time.time()+30*86400
  store.set_secret('ewelink',{'access_token':at,'refresh_token':rt,'expires_at':expires,'region':region})
 async def finish(self,store,code,state,region):
  async with self.lock:
   pending=store.secret('ewelink-oauth')
   if not isinstance(state,str) or len(state)>256 or not hmac.compare_digest(hashlib.sha256(state.encode()).hexdigest(),pending.get('state_hash','')) or time.time()>pending.get('expires_at',0):raise AdapterError('ewelink_state','授權連結已失效或來源不符，請回中控重新開始')
   if region not in HOSTS or not isinstance(code,str) or not 1<=len(code)<=512:raise AdapterError('ewelink_callback','授權回傳內容不完整，請重新開始')
   store.set_secret('ewelink-oauth',{}) # Consume once, before any network operation.
   data=await self.request(store,region,'POST','/v2/user/oauth/token',{'code':code,'redirectUrl':CALLBACK,'grantType':'authorization_code'})
   self.save_tokens(store,data,region)
   store.put('settings','ewelink-integration',{'certification':'approved','status':'connected','items':[]})
   store.event('ewelink.linked',{'message':'已完成易微聯官方 OAuth 授權；沒有保存手機帳密'})
  try:return await self.discover(store)
  except AdapterError:return view(store) # OAuth succeeded even if inventory is restricted.
 async def ensure_token(self,store):
  token=store.secret('ewelink')
  if not token.get('access_token') or token.get('region') not in HOSTS:raise AdapterError('ewelink_auth','請先完成易微聯官方帳號授權')
  if time.time()>=token.get('expires_at',0)-60:
   data=await self.request(store,token['region'],'POST','/v2/user/refresh',{'rt':token.get('refresh_token')})
   self.save_tokens(store,data,token['region'],token);token=store.secret('ewelink')
  return token
 async def discover(self,store):
  async with self.lock:
   old=store.one('settings','ewelink-integration',{})
   if time.time()-old.get('attempted_at',0)<60:raise AdapterError('ewelink_cooldown','易微聯清單同步至少相隔 60 秒')
   store.put('settings','ewelink-integration',{**old,'attempted_at':time.time()})
   try:
    token=await self.ensure_token(store);things=[];cursor=None;total=0
    for _ in range(10):
     query={'num':30}
     if cursor is not None:query['beginIndex']=cursor
     reply=await self.request(store,token['region'],'GET','/v2/device/thing',query,token=token['access_token'])
     page=reply.get('thingList');total=reply.get('total',0)
     if not isinstance(page,list) or not isinstance(total,int):raise AdapterError('ewelink_format','易微聯設備清單格式不相容')
     seen={str(x.get('itemData',{}).get('deviceid')) for x in things}
     fresh=[x for x in page if isinstance(x,dict) and x.get('itemType') in [1,2] and isinstance(x.get('itemData'),dict) and str(x['itemData'].get('deviceid')) not in seen]
     things+=fresh
     if len(page)<30 or len(things)>=total:break
     indexes=[x.get('index') for x in page if isinstance(x,dict) and isinstance(x.get('index'),int)]
     if not fresh or not indexes or indexes[-1]==cursor:break
     cursor=indexes[-1]
    items=[]
    for thing in things:
     d=thing['itemData'];ident=d.get('deviceid')
     if not isinstance(ident,str) or not ident:continue
     extra=d.get('extra') if isinstance(d.get('extra'),dict) else {};params=d.get('params') if isinstance(d.get('params'),dict) else {}
     items.append({'id':ident,'name':str(d.get('name') or '未命名設備')[:80],'brand':str(d.get('brandName') or '')[:80],'model':str(d.get('productModel') or '')[:80],'uiid':extra.get('uiid'),'online':d.get('online') is True,'power':params.get('switch')=='on' if params.get('switch') in ['on','off'] else None,'control_ready':False})
    store.set_secret('ewelink-devices',{'things':things,'queried_at':time.time()})
    store.put('settings','ewelink-integration',{**old,'status':'connected','attempted_at':time.time(),'queried_at':time.time(),'items':items,'total':total,'limited':len(items)<total,'error':None,'platform_issue':None})
   except AdapterError as e:
    store.put('settings','ewelink-integration',{**old,'attempted_at':time.time(),'error':e.message});raise
   return view(store)
 async def disconnect(self,store):
  async with self.lock:
   store.set_secret('ewelink',{});store.set_secret('ewelink-oauth',{});store.set_secret('ewelink-devices',{})
   store.put('settings','ewelink-integration',{'certification':'approved','status':'awaiting_authorization','items':[]})
   return view(store)

 def owned(self,store,device):
  cache=store.secret('ewelink-devices');ident=device.get('config',{}).get('ewelink_device_id')
  if time.time()-cache.get('queried_at',0)>86400:raise AdapterError('ewelink_stale','請先重新同步易微聯設備清單')
  row=next((t['itemData'] for t in cache.get('things',[]) if t.get('itemType') in [1,2] and t.get('itemData',{}).get('deviceid')==ident),None)
  if not row or row.get('extra',{}).get('uiid')!=182 or device.get('config',{}).get('power_outlet')!=0:raise AdapterError('ewelink_owned','此設備尚未確認易微聯單一開關的對應')
  return row
 async def read_device(self,store,device):
  from .adapters import observation
  async with self.lock:
   row=self.owned(store,device);token=await self.ensure_token(store)
   reply=await self.request(store,token['region'],'GET','/v2/device/thing/status',{'type':1,'id':row['deviceid'],'params':'switches|power|voltage|current'},token=token['access_token'])
   params=reply.get('params')
   if not isinstance(params,dict):raise AdapterError('ewelink_format','易微聯沒有回傳設備狀態')
   switches=params.get('switches') or []
   primary=next((v.get('switch') for v in switches if isinstance(v,dict) and type(v.get('outlet')) is int and v['outlet']==0),None)
   power=primary=='on' if primary in ['on','off'] else None
   def reading(key):
    value=params.get(key)
    if isinstance(value,bool):return None
    try:value=float(value)
    except (TypeError,ValueError):return None
    return value if math.isfinite(value) and value>=0 else None
   result=observation('ewelink-cloud',{'power':power,'power_usage':reading('power'),'voltage':reading('voltage'),'current':reading('current')},['power'] if power is not None else [])
   result.update(available=row.get('online') is True,physical_feedback=False,measurement_at=None)
   return result
 async def send_power(self,store,device,power):
  if type(power) is not bool:raise AdapterError('ewelink_value','開關必須為布林值')
  async with self.lock:
   row=self.owned(store,device);token=await self.ensure_token(store)
   try:await self.request(store,token['region'],'POST','/v2/device/thing/status',{'type':1,'id':row['deviceid'],'params':{'switches':[{'outlet':0,'switch':'on' if power else 'off'}]}},token=token['access_token'])
   except AdapterError as e:
    if e.code=='ewelink_connection':raise AdapterError('ewelink_unknown','易微聯送出結果不明，請確認實際開關；系統不自動重送') from None
    raise

 def import_single_switch(self,store,cloud_id):
  cache=store.secret('ewelink-devices')
  if time.time()-cache.get('queried_at',0)>86400:raise AdapterError('ewelink_stale','請先同步易微聯設備清單')
  row=next((t['itemData'] for t in cache.get('things',[]) if t.get('itemType') in [1,2] and t.get('itemData',{}).get('deviceid')==cloud_id),None)
  if not row or row.get('extra',{}).get('uiid')!=182:raise AdapterError('ewelink_profile','目前只開放已核對的 UIID 182 單一插座')
  old=next((d for d in store.devices() if d.get('adapter')=='ewelink' and d.get('config',{}).get('ewelink_device_id')==cloud_id),None)
  ident=old['id'] if old else 'ewelink-'+hashlib.sha256(cloud_id.encode()).hexdigest()[:12]
  mac=row.get('params',{}).get('staMac') or row.get('extra',{}).get('staMac') or row.get('extra',{}).get('mac')
  device=old or {'id':ident,'name':str(row.get('name') or '易微聯插座')[:80],'category':'智慧插座','kind':'switch','ips':[],'mac':mac,'vendor':str(row.get('brandName') or 'eWeLink'),'adapter':'ewelink','config':{},'placement':None,'notes':'由易微聯帳號接入；內網 IP 與位置待核對。','favorite':False,'aliases':[]}
  device['config']={**device.get('config',{}),'ewelink_device_id':cloud_id,'uiid':182,'power_outlet':0,'single_switch_confirmed':True}
  store.put('devices',ident,device)
  if not old:store.put('settings','sensor-refresh:'+ident,{'enabled':True,'source':'local','interval_seconds':300})
  store.event('device.imported',{'device_id':ident,'message':'已接入經確認的易微聯單一智慧插座，沒有發送開關指令'})
  return device
