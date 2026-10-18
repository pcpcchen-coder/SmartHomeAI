"""Capture actual vendor remote signals with an owned Tuya IR hub.
No guessed Panasonic clean code is installed or transmitted.
"""
import asyncio, hashlib, re, time, uuid
from urllib.parse import quote
from .adapters import AdapterError

KINDS={
 'anti_mold_enable':'啟用原廠乾燥防霉設定', 'anti_mold_off':'關機並保留乾燥防霉', 'anti_mold_start':'單獨啟動乾燥防霉',
 'immediate_off':'立即停止／關機', 'fan_only':'原廠送風', 'quiet':'原廠靜音',
 'powerful':'原廠強力', 'eco':'原廠節能', 'horizontal_swing':'左右擺風', 'vertical_swing':'上下擺風', 'nanoe':'原廠清淨', 'light':'機體燈光', 'other':'其他原廠按鍵',
}

def target_key(hub,child):return 'aifa:'+hub+':'+child

class NativeIR:
 def __init__(self,aifa,infrared):
  self.aifa=aifa;self.infrared=infrared;self.lock=asyncio.Lock()
 def profile(self,store,target):return store.secret('native-ir:'+target)
 def view(self,store,hub,child):
  target=target_key(hub,child);p=self.profile(store,target)
  controllers=[]
  allowed={x['id'] for x in store.secret('tuya-cloud-devices').get('devices',[])}
  for d in store.devices():
   if d.get('config',{}).get('cloud_category')=='wnykq' and d['config'].get('device_id') in allowed:
    controllers.append({'id':d['id'],'name':d['name'],'ips':d.get('ips',[])})
  session=store.one('settings','native-session:'+target,{})
  return {'controller_id':p.get('controller_id'),'keys':[{'kind':k,'label':v['label'],'captured_at':v['captured_at'],'verified':v.get('verified',False),'last_sent_at':v.get('last_sent_at'),'last_result':v.get('last_result')} for k,v in p.get('codes',{}).items()],
   'controllers':controllers,'kinds':KINDS,'session':{k:session.get(k) for k in ['id','kind','label','status','expires_at','cleanup_error','message']} if session else None}
 def owned(self,store,hub,child,controller_id):
  device=self.aifa.owned_child(store,hub,child)
  controller=store.one('devices',controller_id)
  if not controller:raise AdapterError('native_controller','找不到這台紅外線控制器')
  cid=self.infrared.owned(store,controller)
  return device,controller,cid
 def sessions(self,store):
  import json
  with store.lock:return [json.loads(r['body']) for r in store.db.execute("SELECT body FROM settings WHERE id LIKE 'native-session:%'")]
 def active(self,store,controller):
  return any(s.get('controller_id')==controller and (s.get('status') in ['starting','waiting'] and s.get('expires_at',0)>time.time() or s.get('cleanup_error')) for s in self.sessions(store))
 def write_session(self,store,s):store.put('settings','native-session:'+s['target'],s)
 async def stop_learning(self,store,s):
  s['cleanup_attempts']=s.get('cleanup_attempts',0)+1;s['last_cleanup_at']=time.time()
  try:
   await self.infrared.cloud.request(store,'PUT',f"/v2.0/infrareds/{quote(s['controller_device_id'],safe='')}/learning-state?state=false")
   s.pop('cleanup_error',None)
  except Exception:s['cleanup_error']='尚未確認控制器退出學習，最多會再嘗試兩次'
  self.write_session(store,s)
 async def begin(self,store,hub,child,controller_id,kind,label=None):
  if kind not in KINDS and not re.fullmatch(r'custom_[a-f0-9]{12}',kind):raise AdapterError('native_kind','不支援這個按鍵用途')
  if kind.startswith('custom_') and not label:raise AdapterError('native_label','請為自訂原廠按鍵命名')
  async with self.lock:
   _,_,cid=self.owned(store,hub,child,controller_id);target=target_key(hub,child);old=self.profile(store,target)
   if old.get('controller_id') not in [None,controller_id]:raise AdapterError('native_controller','請選擇已保存原廠按鍵的同一台控制器')
   prior=store.one('settings','native-session:'+target,{})
   if prior.get('status') in ['saving','save_unknown']:raise AdapterError('native_saving','先在 Smart Life 核對前次儲存結果，暫停建立新按鍵')
   if prior.get('status') in ['starting','waiting'] and prior.get('expires_at',0)>time.time():raise AdapterError('native_busy','這台冷氣已有進行中的學習，請先完成或取消')
   if self.active(store,controller_id):raise AdapterError('native_busy','這台控制器正在學習或尚未確認退出，請先完成或取消')
   session={'id':str(uuid.uuid4()),'target':target,'controller_id':controller_id,'controller_device_id':cid,'kind':kind,'label':(label or KINDS.get(kind,'原廠按鍵'))[:40],
    'status':'starting','learning_time':int(time.time()*1000),'expires_at':time.time()+60}
   self.write_session(store,session)
   try:
    reply=await self.infrared.cloud.request(store,'PUT',f'/v2.0/infrareds/{quote(cid,safe="")}/learning-state?state=true')
    if reply is not True:raise AdapterError('native_learning','控制器未接受學習模式')
    session.update(status='waiting',message='請將原廠遙控器對準這台控制器，按下要學習的最後確認／關機按鈕')
   except Exception:
    session.update(status='start_unknown',message='學習模式未確認；會嘗試讓控制器退出學習');await self.stop_learning(store,session);raise
   self.write_session(store,session);return self.view(store,hub,child)
 def session(self,store,hub,child,ident):
  s=store.one('settings','native-session:'+target_key(hub,child),{})
  if s.get('id')!=ident:raise AdapterError('native_session','找不到這次學習，請重新開始')
  return s
 async def capture(self,store,hub,child,ident):
  async with self.lock:
   s=self.session(store,hub,child,ident)
   if s['status']!='waiting':return self.view(store,hub,child)
   if time.time()>s['expires_at']:
    s.update(status='expired',message='本次學習逾時，原有按鍵保留');await self.stop_learning(store,s);return self.view(store,hub,child)
   if time.time()-s.get('last_poll',0)<3:raise AdapterError('native_cooldown','請稍候三秒再讀取學習結果')
   self.owned(store,hub,child,s['controller_id']);s['last_poll']=time.time();self.write_session(store,s)
   reply=await self.infrared.cloud.request(store,'GET',f"/v2.0/infrareds/{quote(s['controller_device_id'],safe='')}/learning-codes?learning_time={s['learning_time']}")
   code=reply.get('code') if isinstance(reply,dict) and reply.get('success') else None
   if code is not None:
    if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9+/=_-]{16,32768}',code):raise AdapterError('native_code','收到的紅外線資料格式不相容，沒有儲存或送出')
    store.set_secret('native-capture:'+ident,{'code':code});s.update(status='captured',message='已收到原廠訊號；儲存前請核對用途，這一步不會重播訊號')
    await self.stop_learning(store,s)
   return self.view(store,hub,child)
 async def cancel(self,store,hub,child,ident):
  async with self.lock:
   s=self.session(store,hub,child,ident)
   if s['status'] in ['saving','save_unknown']:raise AdapterError('native_saving','雲端儲存結果待核對，請勿重複建立')
   s.update(status='cancelled',message='已取消這次學習，原有按鍵保留');store.set_secret('native-capture:'+ident,{})
   await self.stop_learning(store,s);return self.view(store,hub,child)
 async def save(self,store,hub,child,ident):
  async with self.lock:
   s=self.session(store,hub,child,ident);target=s['target']
   if s['status']=='saved':return self.view(store,hub,child)
   if s['status']!='captured':raise AdapterError('native_capture','請先取得原廠訊號；不會重新送出結果不明的儲存要求')
   _,_,cid=self.owned(store,hub,child,s['controller_id']);capture=store.secret('native-capture:'+ident)
   if not capture.get('code'):raise AdapterError('native_capture','學習資料已失效，請重新學習')
   old=self.profile(store,target);codes={**old.get('codes',{}),s['kind']:{'code':capture['code'],'label':s['label'],'captured_at':time.time(),'verified':False}}
   body={'category_id':5,'remote_name':'中控・原廠專用功能','codes':[{'key':k,'key_name':v['label'],'code':v['code']} for k,v in codes.items()]}
   s.update(status='saving',message='正在保存到 Tuya 的專用遙控器設定');self.write_session(store,s)
   try:
    if old.get('remote_id'):
     reply=await self.infrared.cloud.request(store,'PUT',f"/v2.0/infrareds/{quote(cid,safe='')}/remotes/{quote(old['remote_id'],safe='')}/learning-codes",body)
     remote=old['remote_id']
     if not reply:raise AdapterError('native_save','平台沒有確認儲存')
    else:
     remote=await self.infrared.cloud.request(store,'POST',f"/v2.0/infrareds/{quote(cid,safe='')}/learning-codes",body)
     if not isinstance(remote,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',remote):raise AdapterError('native_save','平台未提供有效專用遙控器 ID')
    if old:
     history=store.secret('native-ir-history:'+target).get('items',[]);store.set_secret('native-ir-history:'+target,{'items':[*history[-19:],old]})
    store.set_secret('native-ir:'+target,{'controller_id':s['controller_id'],'remote_id':remote,'codes':codes})
    store.set_secret('native-capture:'+ident,{});s.update(status='saved',message='已保存。請先測試實際反應，再標記為已確認有效');self.write_session(store,s)
    store.event('native_ir.saved',{'message':'已保存原廠專用按鍵；尚未確認實體效果','kind':s['kind']})
   except Exception:
    s.update(status='save_unknown',message='雲端儲存結果不明，沒有自動重送；請先在 Smart Life 核對專用遙控器');self.write_session(store,s);raise
   return self.view(store,hub,child)
 async def send(self,store,hub,child,kind,idem,on_accept=None):
  async with self.aifa.lock,self.lock:
   target=target_key(hub,child);p=self.profile(store,target);code=p.get('codes',{}).get(kind)
   if not code:raise AdapterError('native_missing','尚未保存這個原廠按鍵')
   _,_,cid=self.owned(store,hub,child,p['controller_id'])
   if self.active(store,p['controller_id']):raise AdapterError('native_busy','請先完成或取消紅外線學習')
   key='native:'+target;req={'kind':kind,'fingerprint':hashlib.sha256(code['code'].encode()).hexdigest()};previous=store.command(key,idem)
   if previous:
    if previous['request']!=req:raise AdapterError('idempotency_conflict','相同指令識別碼不可用於另一個原廠按鍵')
    return previous['result']
   if time.time()-p.get('last_send_at',0)<2:raise AdapterError('native_cooldown','請稍候兩秒再按下一個按鍵')
   ident=str(uuid.uuid4());result={'id':ident,'status':'accepted','message':'原廠訊號送出中'};store.save_command(ident,key,idem,req,result)
   try:
    if on_accept:on_accept()
    p['last_send_at']=time.time();store.set_secret('native-ir:'+target,p)
    reply=await self.infrared.cloud.request(store,'POST',f"/v2.0/infrareds/{quote(cid,safe='')}/remotes/{quote(p['remote_id'],safe='')}/learning-codes",{'code':code['code']})
    if reply is not True:raise AdapterError('native_rejected','平台沒有接受原廠訊號')
    result.update(status='unconfirmed',message='已送出學習的原廠訊號，請確認冷氣實際反應；不會再補送關機訊號')
   except AdapterError as e:result.update(status='failed',message=e.message)
   except Exception:result.update(status='unconfirmed',message='送出結果不明，請確認冷氣反應；不會自動重送')
   code.update(last_sent_at=time.time(),last_result=result['status']);store.set_secret('native-ir:'+target,p)
   store.save_command(ident,key,idem,req,result);store.event('command.'+result['status'],{'message':result['message'],'command':'native-ir','kind':kind,'command_id':ident})
   return result
 async def verify(self,store,hub,child,kind,verified):
  async with self.lock:
   self.aifa.owned_child(store,hub,child);target=target_key(hub,child);p=self.profile(store,target);code=p.get('codes',{}).get(kind)
   if not code:raise AdapterError('native_missing','尚未保存這個按鍵')
   if verified and (code.get('last_result')!='unconfirmed' or code.get('last_sent_at',0)<code['captured_at']):raise AdapterError('native_verify','請先從中控測試這個按鍵並確認冷氣反應')
   code.update(verified=verified,verified_at=time.time() if verified else None);store.set_secret('native-ir:'+target,p)
   return self.view(store,hub,child)
 async def cleanup(self,store):
  async with self.lock:
   for s in self.sessions(store):
    if s.get('status') in ['starting','waiting'] and s.get('expires_at',0)<time.time():
     s.update(status='expired',message='本次學習已逾時，沒有保存或重播');await self.stop_learning(store,s)
    elif s.get('cleanup_error') and s.get('cleanup_attempts',0)<3 and time.time()-s.get('last_cleanup_at',0)>30:await self.stop_learning(store,s)
