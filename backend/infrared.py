"""Tuya IR discovery and one-way commands. Never infer appliance power from an ACK."""
import asyncio,hashlib,hmac,json,re,time,uuid
from urllib.parse import quote
import httpx
from .adapters import AdapterError

HOSTS={'us':'openapi.tuyaus.com','az':'openapi.tuyaus.com','us-e':'openapi-ueaz.tuyaus.com','eu':'openapi.tuyaeu.com','eu-w':'openapi-weaz.tuyaeu.com','cn':'openapi.tuyacn.com','in':'openapi.tuyain.com','sg':'openapi-sg.iotbing.com'}
class TuyaIRCloud:
 def __init__(self):self.lock=asyncio.Lock();self.tokens={}
 async def request(self,store,method,path,body=None):
  if method not in ['GET','POST'] or not re.fullmatch(r'/v2\.0/infrareds/[A-Za-z0-9_-]+/remotes(?:/[A-Za-z0-9_-]+/(?:keys|raw/command))?',path):raise AdapterError('invalid_ir_path','紅外線介接路徑無效')
  c=store.secret('tuya-cloud');host=HOSTS.get(c.get('region'))
  if not host or not c.get('api_key') or not c.get('api_secret'):raise AdapterError('cloud_not_linked','尚未設定 Tuya 雲端認證')
  async with self.lock:
   identity=hashlib.sha256((c['api_key']+c['api_secret']+host).encode()).hexdigest()
   async with httpx.AsyncClient(timeout=8,follow_redirects=False) as client:
    async def call(method,path,body=None,token=''):
     raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode() if body is not None else b''
     stamp=str(int(time.time()*1000));canonical=method+'\n'+hashlib.sha256(raw).hexdigest()+'\n\n'+path
     sign=hmac.new(c['api_secret'].encode(),(c['api_key']+token+stamp+canonical).encode(),hashlib.sha256).hexdigest().upper()
     headers={'client_id':c['api_key'],'sign':sign,'t':stamp,'sign_method':'HMAC-SHA256'}
     if token:headers['access_token']=token
     if body is not None:headers['Content-Type']='application/json'
     response=await client.request(method,'https://'+host+path,headers=headers,content=raw);response.raise_for_status();reply=response.json()
     if not reply.get('success'):
      code=str(reply.get('code'))
      if code in ['1010','1011','1012','1013']:self.tokens.pop(identity,None)
      if code in ['28841101','1106']:raise AdapterError('ir_service_required','Tuya 尚未授權 IR Control Hub Open Service；目前不能同步按鍵或送出紅外線指令')
      raise AdapterError('ir_cloud_rejected','Tuya 未接受紅外線請求，請核對服務、額度與控制器連線')
     return reply.get('result')
    token,expiry=self.tokens.get(identity,('',0))
    if time.time()>expiry:
     result=await call('GET','/v1.0/token?grant_type=1');token=result['access_token'];self.tokens[identity]=(token,time.time()+max(0,result.get('expire_time',3600)-60))
    # POST is deliberately never retried: a timed-out toggle may already have been emitted.
    return await call(method,path,body,token)

def controller_type(device):
 c=device.get('config',{})
 if c.get('cloud_category')=='wnykq':return 'tuya-ir'
 if device['kind']=='gateway':return 'gateway'
 if c.get('external_provider')=='aifa':return 'aifa'
 return None

def ir_view(store,device):
 if not device.get('config',{}).get('infrared_virtual'):return None
 cache=store.one('settings','ir-keys:'+device['id'],{})
 parent=next((x for x in store.devices() if x.get('config',{}).get('device_id')==device['config'].get('gateway_id')),None)
 error=store.one('settings','ir-discovery:'+(parent or {}).get('id',''),{}).get('error')
 discovery=store.one('settings','ir-discovery:'+(parent or {}).get('id',''),{})
 fresh=bool(cache.get('queried_at') and time.time()-cache['queried_at']<86400 and cache.get('remote_id') in discovery.get('remote_ids',[]))
 return {'keys':cache.get('keys',[]),'ready':bool(fresh and cache.get('keys') and not error),'queried_at':cache.get('queried_at'),'controller_id':parent['id'] if parent else None,'controller_name':parent['name'] if parent else None,'error':error or ({'code':'ir_keys_failed','message':cache['error']} if cache.get('error') else None),'one_way':True}

class Infrared:
 def __init__(self,cloud=None):self.cloud=cloud or TuyaIRCloud();self.locks={}
 def owned(self,store,controller):
  cid=controller.get('config',{}).get('device_id')
  allowed={r['id'] for r in store.secret('tuya-cloud-devices').get('devices',[])}
  if controller_type(controller)!='tuya-ir' or cid not in allowed:raise AdapterError('ir_not_authorized','此控制器尚未由 Tuya 帳號授權')
  return cid
 async def discover(self,store,controller):
  cid=self.owned(store,controller)
  async with self.locks.setdefault(controller['id'],asyncio.Lock()):
   statekey='ir-discovery:'+controller['id'];previous=store.one('settings',statekey,{})
   if time.time()-previous.get('attempted_at',0)<60:raise AdapterError('ir_cooldown','同步家電與按鍵至少相隔 60 秒')
   store.put('settings',statekey,{**previous,'attempted_at':time.time()})
   try:
    rows=await self.cloud.request(store,'GET','/v2.0/infrareds/'+quote(cid,safe='')+'/remotes')
    if not isinstance(rows,list):raise AdapterError('ir_bad_response','Tuya 紅外線家電清單格式無法辨識')
    imported=[];errors=[];remote_ids=[]
    for row in rows:
     rid=row.get('remote_id')
     if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',rid):continue
     remote_ids.append(rid)
     d=next((d for d in store.devices() if d.get('config',{}).get('gateway_id')==cid and rid in [d['config'].get('infrared_remote_id'),d['config'].get('node_id'),d['config'].get('device_id')]),None)
     if d is None:
      d={'id':'ir-'+hashlib.sha256((cid+':'+rid).encode()).hexdigest()[:12],'name':str(row.get('remote_name') or '紅外線家電')[:80],'kind':'remote','adapter':'tuya','category':'紅外線家電','ips':[],'mac':'','vendor':'Tuya','placement':None,'notes':'由紅外線控制器讀取的家電項目；沒有獨立 IP。','favorite':False,'config':{}}
     d['config'].update(infrared_virtual=True,gateway_id=cid,infrared_remote_id=rid)
     store.put('devices',d['id'],d);imported.append(d['id'])
     try:
      data=await self.cloud.request(store,'GET','/v2.0/infrareds/'+quote(cid,safe='')+'/remotes/'+quote(rid,safe='')+'/keys')
      keys=[]
      for k in data.get('key_list',[]):
       if isinstance(k.get('key'),str) and 0<len(k['key'])<=128 and isinstance(k.get('key_id',0),int) and not isinstance(k.get('key_id'),bool) and k.get('key_id',0)>=0:keys.append({'key':k['key'],'key_id':k.get('key_id',0),'label':str(k.get('key_name') or k['key'])[:100]})
      category=data.get('category_id',row.get('category_id'))
      if not isinstance(category,int) or isinstance(category,bool) or category<1:raise AdapterError('ir_bad_response','Tuya 尚未提供有效的紅外線家電類別')
      store.put('settings','ir-keys:'+d['id'],{'queried_at':time.time(),'keys':keys,'category_id':category,'remote_id':rid,'controller_device_id':cid,'duplicate_power':bool(data.get('duplicate_power'))})
     except (AdapterError,httpx.HTTPError,ValueError,KeyError,TypeError) as e:
      # Invalidate an old key set if this remote could not be refreshed.
      store.put('settings','ir-keys:'+d['id'],{'keys':[],'error':'按鍵同步未完成'})
      errors.append({'device_id':d['id'],'message':e.message if isinstance(e,AdapterError) else '按鍵同步未完成'})
    store.put('settings',statekey,{'attempted_at':time.time(),'synced_at':time.time(),'remote_ids':remote_ids,'error':None,'key_errors':errors})
    return {'device_ids':imported,'key_errors':errors}
   except Exception as e:
    message=e.message if isinstance(e,AdapterError) else '紅外線清單同步未完成，請稍後再試'
    store.put('settings',statekey,{**previous,'attempted_at':time.time(),'error':{'code':e.code if isinstance(e,AdapterError) else 'ir_read_failed','message':message}})
    raise
 async def send(self,store,device,key,key_id,idem):
  c=device.get('config',{})
  if not c.get('infrared_virtual'):raise AdapterError('ir_unsupported','此設備不是紅外線家電')
  controller=next((d for d in store.devices() if d.get('config',{}).get('device_id')==c.get('gateway_id')),None)
  if not controller:raise AdapterError('ir_controller_missing','尚未對應紅外線控制器')
  cid=self.owned(store,controller);request={'command':'infrared','key':key,'key_id':key_id}
  async with self.locks.setdefault(controller['id'],asyncio.Lock()):
   previous=store.command(device['id'],idem)
   if previous:
    if previous['request']!=request:raise AdapterError('idempotency_conflict','相同指令識別碼不可用於另一個按鍵')
    return previous['result']
   cache=store.one('settings','ir-keys:'+device['id'],{});discovery=store.one('settings','ir-discovery:'+controller['id'],{})
   if not ir_view(store,device)['ready'] or cache.get('controller_device_id')!=cid or cache.get('remote_id') not in discovery.get('remote_ids',[]):raise AdapterError('ir_keys_required','請先同步控制器的家電與按鍵')
   if not any(k['key']==key and k['key_id']==key_id for k in cache.get('keys',[])):raise AdapterError('ir_key_unsupported','此家電尚未提供這個按鍵')
   last=store.one('settings','ir-last-send:'+controller['id'],{}).get('at',0)
   if time.time()-last<1:raise AdapterError('ir_cooldown','請稍候一秒再按下一個按鍵')
   ident=str(uuid.uuid4());result={'id':ident,'device_id':device['id'],'command':'infrared','requested_at':time.time(),'status':'accepted','message':'紅外線指令處理中'}
   store.save_command(ident,device['id'],idem,request,result);store.put('settings','ir-last-send:'+controller['id'],{'at':time.time()})
   try:
    reply=await self.cloud.request(store,'POST','/v2.0/infrareds/'+quote(cid,safe='')+'/remotes/'+quote(cache['remote_id'],safe='')+'/raw/command',{'category_id':cache['category_id'],'key':key,'key_id':key_id})
    if reply is not True:raise AdapterError('ir_send_rejected','Tuya 未接受紅外線指令')
    result.update(status='unconfirmed',transport_status='accepted_by_cloud',message='Tuya 已接受紅外線指令；家電不回傳狀態，請確認實際反應')
   except AdapterError as e:result.update(status='failed',message=e.message)
   except Exception:result.update(status='unconfirmed',transport_status='unknown',message='紅外線送出結果不明，請確認家電反應；不會自動重送')
   store.save_command(ident,device['id'],idem,request,result);store.event('command.'+result['status'],{'device_id':device['id'],'name':device['name'],'command':'infrared','command_id':ident,'message':result['message']})
   return result
