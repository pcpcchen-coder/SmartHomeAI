"""Persistent, one-shot AC actions. A crash or a missed deadline never causes replay."""
import asyncio, time, uuid
from .adapters import AdapterError

KEY='ac-timers'
ACTIVE={'pending','dispatching'}

def timer_view(store,hub,child):
 jobs=store.one('settings',KEY,[])
 return {'items':[j for j in jobs if j['hub']==hub and j['child']==child][-15:],
         'background_running':bool(store.one('settings','ac-timer-runtime',{}).get('running')), 'late_grace_seconds':120}

class ACTimers:
 def __init__(self,store,aifa,native):
  self.store=store;self.aifa=aifa;self.native=native;self.active=set()
 def save(self,jobs):self.store.put('settings',KEY,jobs)
 def initialize(self):
  jobs=self.store.one('settings',KEY,[])
  for j in jobs:
   if j['status']=='dispatching':j.update(status='unconfirmed',message='服務在執行中重新啟動，結果不明；不會自動重送')
  self.save(jobs)
 def schedule(self,hub,child,action,delay,idem):
  self.aifa.owned_child(self.store,hub,child)
  if action not in ['on','off','native_off'] or isinstance(delay,bool) or not isinstance(delay,int) or not 60<=delay<=86400:raise AdapterError('timer_invalid','定時範圍為 1 分鐘到 24 小時')
  jobs=self.store.one('settings',KEY,[]);req={'action':action,'delay_seconds':delay}
  ledger_key='ac-timer-schedule:'+hub+':'+child
  ledger=self.store.command(ledger_key,idem)
  previous=next((j for j in jobs if j['hub']==hub and j['child']==child and j.get('idempotency_key')==idem),None)
  if ledger and not previous:
   if ledger['request']!=req:raise AdapterError('idempotency_conflict','相同識別碼不可用於另一個定時')
   return {**ledger['result'],'status':'archived','message':'這個要求已處理；較早的定時紀錄已封存，不會重新建立'}
  if previous:
   if previous['request']!=req:raise AdapterError('idempotency_conflict','相同識別碼不可用於另一個定時')
   return previous
  if action=='native_off':
   code=self.native.profile(self.store,'aifa:'+hub+':'+child).get('codes',{}).get('anti_mold_off',{})
   if not code.get('verified'):raise AdapterError('native_unverified','請先學習並確認原廠防霉關機按鍵有效')
  if any(j['hub']==hub and j['child']==child and j['status']=='dispatching' for j in jobs):raise AdapterError('timer_dispatching','這台冷氣的定時正在送出，請稍候')
  now=time.time()
  for j in jobs:
   if j['hub']==hub and j['child']==child and j['status']=='pending':j.update(status='replaced',message='已被新的定時取代')
  job={'id':str(uuid.uuid4()),'hub':hub,'child':child,'action':action,'due_at':now+delay,'created_at':now,'status':'pending','idempotency_key':idem,'request':req,'message':'等待執行'}
  active=[j for j in jobs if j['status'] in ACTIVE];history=[j for j in jobs if j['status'] not in ACTIVE][-99:]
  self.store.save_command(job['id'],ledger_key,idem,req,job)
  self.save([*history,*active,job]);self.store.event('ac_timer.created',{'message':'已設定中控冷氣定時；尚未送出家電指令','timer_id':job['id'],'action':action,'due_at':job['due_at']});return job
 def cancel(self,hub,child,ident=None,reason='已取消定時'):
  jobs=self.store.one('settings',KEY,[]);found=False
  for j in jobs:
   if j['hub']!=hub or j['child']!=child or ident and j['id']!=ident:continue
   found=True
   if j['status']=='dispatching' and ident:raise AdapterError('timer_dispatching','指令已進入送出流程，無法保證取消；請確認冷氣反應')
   if j['status']=='pending':j.update(status='cancelled',message=reason,cancelled_at=time.time())
  if ident and not found:raise AdapterError('timer_missing','找不到這個定時')
  self.save(jobs);return timer_view(self.store,hub,child)
 def set_result(self,ident,changes):
  jobs=self.store.one('settings',KEY,[])
  for j in jobs:
   if j['id']==ident:j.update(changes)
  self.save(jobs)
 async def tick(self):
  now=time.time();jobs=self.store.one('settings',KEY,[])
  for j in jobs:
   current=next((v for v in self.store.one('settings',KEY,[]) if v['id']==j['id']),None)
   if not current or current['status']!='pending' or j['due_at']>now or j['id'] in self.active:continue
   now=time.time()
   if now-j['due_at']>120:
    self.set_result(j['id'],{'status':'missed','message':'中控睡眠、關機或停用而錯過時間；沒有補送指令'});continue
   # Persist dispatching BEFORE awaiting transport. Restart will not replay it.
   self.set_result(j['id'],{'status':'dispatching','started_at':now,'message':'定時指令處理中'});self.active.add(j['id'])
   try:
    if time.time()-self.store.one('settings','aifa-discovery',{}).get('queried_at',0)>23*3600:await self.aifa.discover(self.store)
    idem='ac-timer:'+j['id']
    if j['action']=='native_off':
     p=self.native.profile(self.store,'aifa:'+j['hub']+':'+j['child'])
     if not p.get('codes',{}).get('anti_mold_off',{}).get('verified'):raise AdapterError('native_unverified','原廠防霉關機按鍵已取消確認，沒有執行定時')
     result=await self.native.send(self.store,j['hub'],j['child'],'anti_mold_off',idem)
    else:result=await self.aifa.ac_power(self.store,j['hub'],j['child'],j['action']=='on',idem)
    self.set_result(j['id'],{'status':result['status'],'finished_at':time.time(),'message':result['message'],'command_id':result['id']})
   except asyncio.CancelledError:raise
   except Exception as e:
    self.set_result(j['id'],{'status':'failed','finished_at':time.time(),'message':e.message if isinstance(e,AdapterError) else '定時未完成；不會自動重送'})
   finally:self.active.discard(j['id'])
 async def run(self):
  self.initialize();self.store.put('settings','ac-timer-runtime',{'running':True})
  try:
   while True:
    try:
     await self.native.cleanup(self.store);await self.tick()
    except asyncio.CancelledError:raise
    except Exception:
     self.store.event('ac_timer.worker_error',{'message':'冷氣背景服務暫時無法完成檢查，稍後繼續；不重送已送出的指令'})
    await asyncio.sleep(1)
  finally:self.store.put('settings','ac-timer-runtime',{'running':False})
