"""Persistent device polling, independent of the browser and without catch-up bursts."""
import asyncio, math, time
from .adapters import AdapterError

DEFAULT={'enabled':True,'source':'local','interval_seconds':35}

def schedule_view(store,device):
 config=store.one('settings','sensor-refresh:'+device['id'],DEFAULT)
 runtime=store.one('settings','sensor-refresh-runtime:'+device['id'],{})
 return {'config':config,'last_attempt_at':runtime.get('last_attempt_at'),'last_success_at':runtime.get('last_success_at'),'last_error':runtime.get('last_error'),'failures':runtime.get('failures',0),'next_run_at':runtime.get('next_run_at') if config['enabled'] else None,'estimated_status_queries_per_day':math.ceil(86400/config['interval_seconds']) if config['enabled'] and config['source']=='tuya-cloud' else 0}

class TelemetryScheduler:
 def __init__(self,store,cloud,read_local):
  self.store=store;self.cloud=cloud;self.read_local=read_local;self.active={};self.transports={};self.semaphore=asyncio.Semaphore(10);self.running=False
 async def tick(self):
  now=time.time()
  for device in self.store.devices():
   view=schedule_view(self.store,device)
   if not view['config']['enabled'] or device['id'] in self.active or (view['next_run_at'] or 0)>now:continue
   task=asyncio.create_task(self.update(device));self.active[device['id']]=task
   task.add_done_callback(lambda task,id=device['id']:self.active.pop(id,None))
 async def update(self,device):
  # Recheck after queued transport work: a user may have disabled or edited the schedule.
  transport=device.get('config',{}).get('gateway_id') or device.get('config',{}).get('device_id') or device['id']
  async with self.transports.setdefault(transport,asyncio.Lock()):
   async with self.semaphore:
    view=schedule_view(self.store,device);config=view['config']
    if not config['enabled']:return
    if config['source']=='tuya-cloud':
     last=self.store.one('settings','sensor-cloud-attempt:'+device['id'],{}).get('at',0)
     if time.time()-last<60:
      self.save_runtime(device,{'next_run_at':last+60});return
    attempted=time.time();self.save_runtime(device,{'last_attempt_at':attempted})
    error=None
    try:
     if config['source']=='tuya-cloud':await self.cloud.fetch(self.store,device)
     else:
      result=await self.read_local(device);state=result['state']
      if not state.get('fresh') or not state.get('available'):
       detail=state.get('error') or {};raise AdapterError(detail.get('code','read_failed'),detail.get('message','設備未回應'))
    except asyncio.CancelledError:raise
    except Exception as e:
     if isinstance(e,AdapterError) and e.code=='cloud_cooldown':
      if schedule_view(self.store,device)['config']==config:
       last=self.store.one('settings','sensor-cloud-attempt:'+device['id'],{}).get('at',time.time())
       self.save_runtime(device,{'next_run_at':last+60})
      return
     error={'code':e.code,'message':e.message} if isinstance(e,AdapterError) else {'code':'read_failed','message':'背景讀取未完成；保留最後讀值並稍後重試'}
    # Configuration changes during a request take effect when it completes; no overlapping reads.
    latest=schedule_view(self.store,device)
    if latest['config']!=config:return
    config=latest['config'];failures=latest['failures']+1 if error else 0
    interval=config['interval_seconds'];delay=max(interval,min(3600,interval*2**min(failures,6))) if error and config['source']=='tuya-cloud' else interval
    changes={'last_error':error,'failures':failures,'next_run_at':time.time()+delay if config['enabled'] else None}
    if not error:changes['last_success_at']=time.time()
    self.save_runtime(device,changes)
 def save_runtime(self,device,changes):
  key='sensor-refresh-runtime:'+device['id'];self.store.put('settings',key,{**self.store.one('settings',key,{}),**changes})
 async def run(self):
  self.running=True
  try:
   while True:await self.tick();await asyncio.sleep(1)
  finally:
   self.running=False
   for task in list(self.active.values()):task.cancel()
   await asyncio.gather(*list(self.active.values()),return_exceptions=True)
