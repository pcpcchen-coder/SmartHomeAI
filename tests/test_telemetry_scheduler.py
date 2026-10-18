import asyncio,time
from types import SimpleNamespace
from backend.store import Store
from backend.telemetry_scheduler import TelemetryScheduler,schedule_view
from backend.adapters import AdapterError

def device(s):
 d={'id':'fixture-sensor','adapter':'tuya','config':{'device_id':'fixture-upstream'},'name':'示例感測器'};s.put('devices',d['id'],d);return d

def configure(s,d,source='tuya-cloud',interval=60,enabled=True):
 s.put('settings','sensor-refresh:'+d['id'],{'enabled':enabled,'source':source,'interval_seconds':interval})

async def drain(scheduler):
 await scheduler.tick();await asyncio.gather(*list(scheduler.active.values()))


def test_background_queries_without_browser_and_survives_restart(tmp_path,monkeypatch):
 s=Store(tmp_path);d=device(s);configure(s,d);now=[time.time()];monkeypatch.setattr(time,'time',lambda:now[0]);calls=[]
 async def cloud(store,dev):calls.append((dev['id'],now[0]))
 async def local(dev):raise AssertionError('cloud source must not also poll local')
 async def run():
  scheduler=TelemetryScheduler(s,SimpleNamespace(fetch=cloud),local)
  await drain(scheduler);assert len(calls)==1
  now[0]+=59;await drain(scheduler);assert len(calls)==1
  # A new service instance honors the saved next-run time, with no browser client.
  scheduler=TelemetryScheduler(s,SimpleNamespace(fetch=cloud),local)
  await drain(scheduler);assert len(calls)==1
  now[0]+=1;await drain(scheduler);assert len(calls)==2
  now[0]+=86400;await drain(scheduler);assert len(calls)==3 # one refresh, no catch-up burst
  assert schedule_view(s,d)['next_run_at']==now[0]+60
 asyncio.run(run());s.db.close()


def test_manual_cloud_cooldown_and_backoff(tmp_path,monkeypatch):
 s=Store(tmp_path);d=device(s);configure(s,d);now=[time.time()];monkeypatch.setattr(time,'time',lambda:now[0]);calls=[]
 s.put('settings','sensor-cloud-attempt:'+d['id'],{'at':now[0]-10})
 async def cloud(store,dev):calls.append(dev['id']);raise AdapterError('fixture_failure','雲端尚未可用')
 async def run():
  scheduler=TelemetryScheduler(s,SimpleNamespace(fetch=cloud),None)
  await drain(scheduler);assert not calls and schedule_view(s,d)['next_run_at']==now[0]+50
  now[0]+=50;await drain(scheduler);v=schedule_view(s,d)
  assert len(calls)==1 and v['last_success_at'] is None and v['failures']==1 and v['next_run_at']==now[0]+120
  now[0]+=120;await drain(scheduler);assert schedule_view(s,d)['next_run_at']==now[0]+240
 asyncio.run(run());s.db.close()


def test_disabling_during_read_prevents_next_run(tmp_path):
 s=Store(tmp_path);d=device(s);configure(s,d,source='local',interval=15)
 async def run():
  entered=asyncio.Event();release=asyncio.Event();calls=[]
  async def local(dev):
   calls.append(dev['id']);entered.set();await release.wait();return {'state':{'fresh':True,'available':True}}
  scheduler=TelemetryScheduler(s,None,local);await scheduler.tick();await entered.wait()
  configure(s,d,source='local',interval=15,enabled=False);release.set();await asyncio.gather(*list(scheduler.active.values()))
  await drain(scheduler);assert len(calls)==1 and schedule_view(s,d)['next_run_at'] is None
 asyncio.run(run());s.db.close()


def test_shutdown_cancels_pending_reads(tmp_path):
 s=Store(tmp_path);d=device(s);configure(s,d,source='local',interval=15)
 async def run():
  entered=asyncio.Event();cancelled=[]
  async def local(dev):
   entered.set()
   try:await asyncio.Event().wait()
   finally:cancelled.append(dev['id'])
  scheduler=TelemetryScheduler(s,None,local);service=asyncio.create_task(scheduler.run());await entered.wait()
  assert scheduler.running;service.cancel();await asyncio.gather(service,return_exceptions=True)
  assert not scheduler.running and cancelled==[d['id']] and not scheduler.active
 asyncio.run(run());s.db.close()


def test_source_edit_during_read_keeps_new_schedule_deadline(tmp_path):
 s=Store(tmp_path);d=device(s);configure(s,d,source='local',interval=35)
 async def run():
  entered=asyncio.Event();release=asyncio.Event()
  async def local(dev):
   entered.set();await release.wait();return {'state':{'fresh':False,'available':False,'error':{'message':'old local attempt failed'}}}
  scheduler=TelemetryScheduler(s,None,local);await scheduler.tick();await entered.wait()
  configure(s,d,source='tuya-cloud',interval=300)
  deadline=time.time();s.put('settings','sensor-refresh-runtime:'+d['id'],{'next_run_at':deadline,'failures':0,'last_error':None})
  release.set();await asyncio.gather(*list(scheduler.active.values()))
  result=schedule_view(s,d)
  assert result['next_run_at']==deadline and result['failures']==0 and result['last_error'] is None
 asyncio.run(run());s.db.close()


def test_manual_request_winning_cloud_lock_defers_without_false_failure(tmp_path):
 s=Store(tmp_path);d=device(s);configure(s,d)
 async def cloud(store,dev):
  store.put('settings','sensor-cloud-attempt:'+dev['id'],{'at':time.time()})
  raise AdapterError('cloud_cooldown','manual request just completed')
 async def run():
  scheduler=TelemetryScheduler(s,SimpleNamespace(fetch=cloud),None);await drain(scheduler)
  v=schedule_view(s,d);assert v['failures']==0 and v['last_error'] is None and v['next_run_at']>time.time()+59
 asyncio.run(run());s.db.close()
