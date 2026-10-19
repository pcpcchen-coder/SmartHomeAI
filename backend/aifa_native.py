"""Native AC buttons emitted by the AIFA controller, without Tuya or timers.

The AIFA Smart 1.2.9 AcMold button passes integer 38 to DevicePacketCommand;
its serializer emits FF, subtype code, command, F0. See docs/aifa-native.md.
This is a button press, not a readable or idempotent on/off state.
"""
import time,uuid
from .adapters import AdapterError

def native_key(hub,child):return f'aifa-native-mold:{hub}:{child}'

class AifaNative:
 def __init__(self,aifa):self.aifa=aifa
 def profile(self,store,hub,child):
  device=self.aifa.owned_child(store,hub,child)
  profile=next(x for x in device['children'] if x['id']==child)
  return device,profile
 def view(self,store,hub,child):
  _,profile=self.profile(store,hub,child);code=profile.get('device_code');saved=store.one('settings',native_key(hub,child),{})
  if saved.get('profile')!=code:saved={}
  return {'sender':'aifa','profile':code,'available':code=='19968','verified':bool(saved.get('verified')),'last_command_id':saved.get('last_command_id'),'last_sent_at':saved.get('last_sent_at'),'result':saved.get('result'),'one_way':True}
 async def send(self,store,hub,child,idem,on_accept=None):
  async with self.aifa.lock:
   device,profile=self.profile(store,hub,child);key=native_key(hub,child);code=profile.get('device_code')
   request={'action':'anti_mold','profile':code};previous=store.command(key,idem)
   if previous:
    if previous['request']!=request:raise AdapterError('idempotency_conflict','相同識別碼不可用於另一個冷氣設定碼')
    return previous['result']
   if code!='19968':raise AdapterError('aifa_native_profile','此 AIFA 設定碼的原廠防霉指令尚未確認')
   saved=store.one('settings',key,{})
   if time.time()-max(saved.get('last_sent_at',0),saved.get('attempted_at',0))<30:raise AdapterError('aifa_cooldown','防霉按鍵至少相隔 30 秒，避免重複按下中斷行程')
   if time.time()-store.one('settings','aifa-last-send',{}).get('at',0)<2:raise AdapterError('aifa_cooldown','請稍候兩秒再操作 AIFA')
   ident=str(uuid.uuid4());sent=False
   result={'id':ident,'status':'accepted','sender':'aifa','action':'anti_mold','profile':code,'message':'AIFA 防霉指令處理中'}
   store.save_command(ident,key,idem,request,result)
   saved={**(saved if saved.get('profile')==code else {}),'profile':code,'attempted_at':time.time(),'last_command_id':ident,'result':'accepted'}
   store.put('settings',key,saved)
   def mark_send():
    nonlocal sent
    sent=True;saved['last_sent_at']=time.time();store.put('settings',key,saved)
    store.put('settings','aifa-last-send',{'at':time.time()})
   try:
    if on_accept:on_accept()
    reply=await self.aifa.ac_exchange(store,device,packet='ffa026f0',on_send=mark_send)
    result.update(status='unconfirmed',controller_status=reply.get('status'),message='已透過 AIFA i-Ctrl 按下原廠防霉；請觀察冷氣是否開始乾燥，系統不自動重送')
   except AdapterError as e:result.update(status='unconfirmed' if sent else 'failed',message=e.message)
   except Exception:result.update(status='unconfirmed' if sent else 'failed',message='AIFA 指令結果待確認；請觀察冷氣，系統不自動重送' if sent else 'AIFA 連線未完成，沒有送出防霉指令')
   saved['result']=result['status'];store.put('settings',key,saved);store.save_command(ident,key,idem,request,result)
   store.event('command.'+result['status'],{'command_id':ident,'sender':'aifa','action':'anti_mold','message':result['message']})
   return result
 def verify(self,store,hub,child,command_id,verified):
  _,profile=self.profile(store,hub,child);key=native_key(hub,child);saved=store.one('settings',key,{})
  if saved.get('profile')!=profile.get('device_code') or saved.get('last_command_id')!=command_id or saved.get('result')!='unconfirmed' or not saved.get('last_sent_at'):raise AdapterError('aifa_native_verify','請先由中控透過 AIFA 實測，再確認這次冷氣反應')
  saved.update(verified=verified,verified_at=time.time());store.put('settings',key,saved)
  return self.view(store,hub,child)
