import asyncio, hashlib, hmac, ipaddress, json, os, re, secrets, time, uuid
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Any
from urllib.parse import urlparse

from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, ConfigDict, field_validator
from .store import Store
from .adapters import Adapters, AdapterError
from .telemetry import metrics_for, sensor_view, CloudSnapshots
from .telemetry_scheduler import TelemetryScheduler, schedule_view
from .infrared import Infrared, controller_type, ir_view
from .aifa import AifaAccount, account_view
from .native_ir import NativeIR
from .aifa_native import AifaNative
from .ewelink import EWeLink,view as ewelink_view
from .ac_timers import ACTimers, timer_view

BASE=Path(__file__).resolve().parent.parent
store=Store(os.environ.get('WULING_DATA_DIR',Path.home()/'Library/Application Support/WulingHomeControl' if os.sys.platform=='darwin' else Path.home()/'.local/share/wuling-home-control'))
adapters=Adapters(store);locks={};refresh_lock=asyncio.Lock()
cloud_snapshots=CloudSnapshots();telemetry_scheduler=None;infrared=Infrared();aifa_account=AifaAccount();native_ir=NativeIR(aifa_account,infrared);aifa_native=AifaNative(aifa_account);ewelink_account=EWeLink();ac_timers=None
SESSION='wuling_session';API='/api/v1'

def initialize():
 with store.lock:
  for r in store.db.execute('SELECT id,body FROM commands').fetchall():
   result=json.loads(r['body'])
   if result.get('status')=='accepted':
    result.update(status='unconfirmed',message='中控在指令完成前重新啟動，結果待重新觀測')
    store.db.execute('UPDATE commands SET body=? WHERE id=?',(json.dumps(result,ensure_ascii=False),r['id']))
  store.db.commit()
 seed=BASE/'assets/seed.json'
 if not seed.exists():seed=BASE/'assets/example-seed.json'
 if not store.devices() and seed.exists():
  data=json.loads(seed.read_text())
  for d in data['devices']:store.put('devices',d['id'],d)
  store.put('settings','layout',data['layout'])
  store.event('system.ready',{'message':'已載入初始設備與空間配置'})

def authorized(token):
 if not token:return False
 if hmac.compare_digest(token,store.master_token):return True
 digest=hashlib.sha256(token.encode()).hexdigest()
 with store.lock:return bool(store.db.execute('SELECT 1 FROM tokens WHERE hash=? AND revoked=0',(digest,)).fetchone())

bearer_scheme=HTTPBearer(auto_error=False)

async def auth(request:Request,credential:HTTPAuthorizationCredentials|None=Depends(bearer_scheme)):
 bearer=request.headers.get('authorization','');token=bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get(SESSION,'')
 if not authorized(token):raise HTTPException(401,detail={'code':'authentication_required','message':'請先登入武陵居中控'})
 if request.method not in ['GET','HEAD','OPTIONS']:
  origin=request.headers.get('origin')
  if origin and urlparse(origin).netloc!=request.headers.get('host'):raise HTTPException(403,detail={'code':'origin_rejected','message':'拒絕跨網站控制指令'})
 return True

def find(id):
 d=store.one('devices',id)
 if not d:raise HTTPException(404,detail={'code':'device_not_found','message':'找不到這台設備'})
 return d

def public(d):
 state=store.one('state',d['id'],{'available':None,'observed_at':None,'values':{},'commands':[],'fresh':False,'source':None,'error':None})
 age=time.time()-state['observed_at'] if state.get('observed_at') else None
 state={**state,'fresh':bool(state.get('fresh') and age is not None and age<90),'age_seconds':round(age,1) if age is not None else None}
 ready=adapters.configured(d)
 gid=d.get('config',{}).get('gateway_id');gateway=next((x for x in store.devices() if x.get('config',{}).get('device_id')==gid),None) if gid else None
 via={k:gateway[k] for k in ['id','name','ips']} if gateway else None
 return {**d,'infrared':ir_view(store,d),'gateway':via,'telemetry':sensor_view(store,d),'configured':ready,'state':state,'can_control':bool(ready and state['fresh'] and state.get('available') and state.get('commands')),'credential_configured':bool(store.secret(d['id']))}

async def refresh_one(d):
 async with locks.setdefault(d['id'],asyncio.Lock()):
  try:
   if d['adapter'] in ['tuya','lg','homeassistant'] and not adapters.configured(d):raise AdapterError('credentials_required','需要完成帳號或金鑰設定')
   result=await asyncio.wait_for(adapters.read(d),timeout=14)
  except Exception as e:
   prev=store.one('state',d['id'],{})
   result={**prev,'fresh':False,'available':False,'commands':[], 'error':{'code':e.code if isinstance(e,AdapterError) else 'read_failed','message':e.message if isinstance(e,AdapterError) else '設備未回應，或認證尚未完成'},'attempted_at':time.time(),'diagnostic':{'type':type(e).__name__,'errno':getattr(e,'errno',None)}}
  store.put('state',d['id'],result)
  if result.get('fresh') and result.get('available'):store.record_readings(d['id'],result.get('observed_at'),result.get('source'),metrics_for(d,result))
  return public(d)

async def refresh_all():
 if refresh_lock.locked():return
 async with refresh_lock:
  semaphore=asyncio.Semaphore(10);transports={}
  async def worker(d):
   transport=d.get('config',{}).get('gateway_id') or d.get('config',{}).get('device_id') or d['id']
   async with transports.setdefault(transport,asyncio.Lock()):
    async with semaphore:await refresh_one(d)
  await asyncio.gather(*(worker(d) for d in store.devices()))
  store.event('devices.refreshed',{'message':'已更新設備觀測狀態'})

@asynccontextmanager
async def lifespan(app):
 global telemetry_scheduler,ac_timers
 initialize();ac_timers=ACTimers(store,aifa_account,native_ir);telemetry_scheduler=TelemetryScheduler(store,cloud_snapshots,refresh_one)
 task=asyncio.create_task(telemetry_scheduler.run()) if os.environ.get('WULING_POLL','1')=='1' else None
 ac_task=asyncio.create_task(ac_timers.run()) if os.environ.get('WULING_POLL','1')=='1' else None
 yield
 if ac_task:
  ac_task.cancel()
  try:await ac_task
  except asyncio.CancelledError:pass
 if task:
  task.cancel()
  try:await task
  except asyncio.CancelledError:pass
 await adapters.close()

app=FastAPI(title='武陵居 Home Control API',version='1.0.0',description='版本化家庭控制 API。支援 Bearer token、設備能力、狀態新鮮度、位置校準與具冪等性的控制指令。',docs_url=None,redoc_url=None,openapi_url='/api/openapi.json',lifespan=lifespan)

@app.middleware('http')
async def headers(request,call_next):
 response=await call_next(request)
 response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer' if request.url.path.endswith('/ewelink/callback') else 'same-origin'
 response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' blob:; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'"
 if request.url.path.startswith('/api/v1'):response.headers['Cache-Control']='no-store'
 return response

class Login(BaseModel):token:str
@app.post(API+'/auth/local',tags=['Authentication'])
async def local_session(request:Request,response:Response):
 origin=urlparse(request.headers.get('origin',''))
 if request.client.host not in ['127.0.0.1','::1'] or origin.hostname not in ['127.0.0.1','localhost'] or origin.netloc!=request.headers.get('host'):raise HTTPException(403,detail='請在這台 Mac 的 localhost 位址登入')
 response.set_cookie(SESSION,store.master_token,httponly=True,samesite='strict',max_age=86400*14)
 return {'authenticated':True}

@app.post(API+'/auth/login',tags=['Authentication'])
async def login(body:Login,request:Request,response:Response):
 if request.headers.get('origin') and urlparse(request.headers['origin']).netloc!=request.headers.get('host'):raise HTTPException(403,detail='來源不符')
 if not authorized(body.token):raise HTTPException(401,detail='存取權杖不正確')
 response.set_cookie(SESSION,body.token,httponly=True,samesite='strict',max_age=86400*14)
 return {'authenticated':True}

@app.get(API+'/health',tags=['System'])
async def health():return {'status':'ok','service':'wuling-home-control','version':'1.0.0','time':time.time()}

@app.get(API+'/home',dependencies=[Depends(auth)],tags=['Home'])
async def home():
 devices=[public(d) for d in store.devices()]
 return {'schema_version':1,'name':'武陵居','layout':store.one('settings','layout'),'devices':devices,'pending_external':store.one('settings','external-observations',[]),'events':store.latest_events(),'summary':{'total':len(devices),'available':sum(d['state'].get('available') is True and d['state']['fresh'] for d in devices),'controllable':sum(d['can_control'] for d in devices),'unplaced':sum(not d.get('placement') or not d['placement'].get('confirmed') for d in devices)},'server_time':time.time()}

@app.get(API+'/devices',dependencies=[Depends(auth)],tags=['Devices'])
async def devices():return {'items':[public(d) for d in store.devices()]}

@app.get(API+'/devices/{id}',dependencies=[Depends(auth)],tags=['Devices'])
async def device(id:str):return public(find(id))

@app.get(API+'/telemetry',dependencies=[Depends(auth)],tags=['Telemetry'])
async def telemetry():
 items=[x for d in store.devices() if (x:=sensor_view(store,d))]
 return {'items':items,'server_time':time.time(),'retention_days':30,'estimated_cloud_status_queries_per_day':sum(x['auto_update']['estimated_status_queries_per_day'] for x in items),'cloud_polling':bool(telemetry_scheduler and telemetry_scheduler.running) and any(x['auto_update']['config']['enabled'] and x['auto_update']['config']['source']=='tuya-cloud' for x in items),'background_running':bool(telemetry_scheduler and telemetry_scheduler.running),'cloud_cooldown_seconds':60}

@app.get(API+'/controllers',dependencies=[Depends(auth)],tags=['Controllers'])
async def controllers():
 devices=store.devices();items=[]
 for d in devices:
  kind=controller_type(d)
  if not kind:continue
  cid=d.get('config',{}).get('device_id')
  children=[public(x) for x in devices if cid and x.get('config',{}).get('gateway_id')==cid]
  items.append({'device':public(d),'controller_type':kind,'children':children,'discovery':store.one('settings','ir-discovery:'+d['id'],{})})
 return {'items':items,'external_observations':store.one('settings','external-observations',[])}

@app.post(API+'/controllers/{id}/discover',dependencies=[Depends(auth)],tags=['Controllers'])
async def discover_controller(id:str):
 d=find(id)
 if controller_type(d)!='tuya-ir':raise HTTPException(422,detail='此控制器尚未提供紅外線同步介面')
 try:return await infrared.discover(store,d)
 except AdapterError as e:raise HTTPException(429 if e.code=='ir_cooldown' else 409,detail={'code':e.code,'message':e.message})
 except (httpx.HTTPError,ValueError,KeyError,TypeError):raise HTTPException(502,detail='紅外線同步未完成，請稍後再試')

class IRCommand(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 key:str=Field(min_length=1,max_length=128)
 key_id:int=Field(ge=0)

@app.post(API+'/devices/{id}/infrared-commands',dependencies=[Depends(auth)],tags=['Controllers'])
async def ir_command(id:str,body:IRCommand,request:Request):
 d=find(id)
 if not d.get('config',{}).get('infrared_virtual'):raise HTTPException(422,detail='此設備不是紅外線家電')
 idem=request.headers.get('idempotency-key') or str(uuid.uuid4())
 if not 1<=len(idem)<=128:raise HTTPException(422,detail='指令識別碼長度不正確')
 try:return await infrared.send(store,d,body.key,body.key_id,idem)
 except AdapterError as e:raise HTTPException(429 if e.code=='ir_cooldown' else 422 if e.code=='ir_key_unsupported' else 409,detail={'code':e.code,'message':e.message})

@app.get(API+'/integrations/ewelink',dependencies=[Depends(auth)],tags=['Integrations'])
async def ewelink_status():
 return ewelink_view(store)

class EWeLinkConfig(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 app_id:str=Field(min_length=8,max_length=128)
 app_secret:str=Field(min_length=8,max_length=128)

@app.put(API+'/integrations/ewelink/config',dependencies=[Depends(auth)],tags=['Integrations'])
async def ewelink_config(body:EWeLinkConfig,request:Request):
 if request.client.host not in ['127.0.0.1','::1']:raise HTTPException(403,detail='請在這台中控 Mac 設定應用憑證')
 try:
  async with ewelink_account.lock:return ewelink_account.configure(store,body.app_id,body.app_secret)
 except AdapterError as e:raise HTTPException(409,detail=e.message)

@app.post(API+'/integrations/ewelink/authorize',dependencies=[Depends(auth)],tags=['Integrations'])
async def ewelink_authorize(request:Request):
 if request.client.host not in ['127.0.0.1','::1']:raise HTTPException(403,detail='請在這台中控 Mac 完成帳號授權')
 try:
  async with ewelink_account.lock:return ewelink_account.begin(store)
 except AdapterError as e:raise HTTPException(409,detail=e.message)

@app.get(API+'/integrations/ewelink/callback',tags=['Integrations'])
async def ewelink_callback(request:Request):
 # Vendor redirects cannot carry the strict session cookie. A single-use, expiring
 # state plus loopback peer checks authorize this narrowly scoped callback instead.
 if request.client.host not in ['127.0.0.1','::1'] or request.url.hostname not in ['127.0.0.1','localhost']:raise HTTPException(403,detail='授權回呼只接受這台中控 Mac')
 try:await ewelink_account.finish(store,request.query_params.get('code'),request.query_params.get('state'),request.query_params.get('region'))
 except AdapterError as e:
  from html import escape
  return HTMLResponse('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>易微聯授權</title><h1>易微聯授權尚未完成</h1><p>'+escape(e.message)+'</p><a href="/">回到武陵居中控</a></html>',status_code=409)
 return Response(status_code=303,headers={'Location':'/?ewelink=connected'})

@app.post(API+'/integrations/ewelink/discover',dependencies=[Depends(auth)],tags=['Integrations'])
async def ewelink_discover():
 try:return await ewelink_account.discover(store)
 except AdapterError as e:raise HTTPException(429 if e.code=='ewelink_cooldown' else 409,detail=e.message)

@app.delete(API+'/integrations/ewelink',dependencies=[Depends(auth)],tags=['Integrations'])
async def ewelink_disconnect():return await ewelink_account.disconnect(store)

@app.get(API+'/integrations/aifa',dependencies=[Depends(auth)],tags=['Integrations'])
async def aifa_status():return account_view(store)

@app.post(API+'/integrations/aifa/login',dependencies=[Depends(auth)],tags=['Integrations'])
async def aifa_login(request:Request):
 # Credentials may only be entered on the controller Mac, never over LAN HTTP.
 if request.client.host not in ['127.0.0.1','::1']:raise HTTPException(403,detail='請在中控 Mac 上連結 AIFA 帳號')
 try:body=await request.json()
 except ValueError:raise HTTPException(422,detail='登入欄位格式無效')
 if not isinstance(body,dict) or set(body)!={'email','password'} or not all(isinstance(body.get(k),str) and 0<len(body[k])<=1024 for k in ['email','password']):raise HTTPException(422,detail='請輸入 AIFA Smart 帳號與密碼')
 try:return await aifa_account.login(store,body['email'].strip(),body['password'])
 except AdapterError as e:raise HTTPException(429 if e.code=='aifa_cooldown' else 409,detail=e.message)

@app.post(API+'/integrations/aifa/discover',dependencies=[Depends(auth)],tags=['Integrations'])
async def aifa_discover():
 try:return await aifa_account.discover(store)
 except AdapterError as e:raise HTTPException(429 if e.code=='aifa_cooldown' else 409,detail=e.message)

@app.delete(API+'/integrations/aifa',dependencies=[Depends(auth)],tags=['Integrations'])
async def aifa_disconnect():
 await aifa_account.disconnect(store);return account_view(store)

class AifaPower(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 power:bool

@app.post(API+'/integrations/aifa/devices/{hub}/children/{child}/status',dependencies=[Depends(auth)],tags=['Integrations'])
async def aifa_ac_status(hub:str,child:str):
 try:return await aifa_account.ac_status(store,hub,child)
 except AdapterError as e:raise HTTPException(409,detail=e.message)

@app.post(API+'/integrations/aifa/devices/{hub}/children/{child}/power',dependencies=[Depends(auth)],tags=['Integrations'])
async def aifa_ac_power(hub:str,child:str,body:AifaPower,request:Request):
 idem=request.headers.get('idempotency-key')
 if not idem or len(idem)>128:raise HTTPException(422,detail='請提供唯一指令識別碼')
 try:
  cancel=(lambda:ac_timers.cancel(hub,child,reason='已由手動開關取消舊定時，避免重複關機')) if ac_timers else None
  return await aifa_account.ac_power(store,hub,child,body.power,idem,on_accept=cancel)
 except AdapterError as e:raise HTTPException(429 if e.code=='aifa_cooldown' else 409,detail=e.message)

class AifaClimate(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 mode:Literal['auto','cool','dry']
 temperature:int=Field(ge=17,le=30)
 fan:Literal['auto','low','medium','high']
 swing:Literal['auto','fixed_1','fixed_2','fixed_3','fixed_4','fixed_5']
 feature:Literal['normal','sleep','turbo','energy_saving']='normal'

@app.post(API+'/integrations/aifa/devices/{hub}/children/{child}/climate',dependencies=[Depends(auth)],tags=['Integrations'])
async def aifa_climate(hub:str,child:str,body:AifaClimate,request:Request):
 idem=request.headers.get('idempotency-key')
 if not idem or len(idem)>128:raise HTTPException(422,detail='請提供唯一指令識別碼')
 try:return await aifa_account.ac_power(store,hub,child,None,idem,settings=body.model_dump())
 except AdapterError as e:raise HTTPException(429 if e.code=='aifa_cooldown' else 409,detail=e.message)

def ac_error(e):return HTTPException(429 if e.code.endswith('cooldown') else 409,detail=e.message)

class AifaNativeVerify(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 command_id:str=Field(min_length=1,max_length=128)
 verified:bool

AIFA_NATIVE_PATH=API+'/integrations/aifa/devices/{hub}/children/{child}/native-mold'
@app.get(AIFA_NATIVE_PATH,dependencies=[Depends(auth)],tags=['AIFA Native'])
async def aifa_native_view(hub:str,child:str):
 try:return aifa_native.view(store,hub,child)
 except AdapterError as e:raise ac_error(e)
@app.post(AIFA_NATIVE_PATH,dependencies=[Depends(auth)],tags=['AIFA Native'])
async def aifa_native_send(hub:str,child:str,request:Request):
 idem=request.headers.get('idempotency-key')
 if not idem or len(idem)>128:raise HTTPException(422,detail='請提供唯一指令識別碼')
 if await request.body():raise HTTPException(422,detail='防霉按鍵不接受其他控制參數')
 try:
  cancel=(lambda:ac_timers.cancel(hub,child,reason='已按下 AIFA 原廠防霉，取消舊定時以免中斷乾燥')) if ac_timers else None
  return await aifa_native.send(store,hub,child,idem,on_accept=cancel)
 except AdapterError as e:raise ac_error(e)
@app.put(AIFA_NATIVE_PATH+'/verify',dependencies=[Depends(auth)],tags=['AIFA Native'])
async def aifa_native_verify(hub:str,child:str,body:AifaNativeVerify):
 try:return aifa_native.verify(store,hub,child,body.command_id,body.verified)
 except AdapterError as e:raise ac_error(e)

class NativeBegin(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 controller_id:str=Field(min_length=1,max_length=128)
 kind:str=Field(min_length=1,max_length=32)
 label:str|None=Field(default=None,min_length=1,max_length=40)
class NativeVerify(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 verified:bool

NATIVE_PATH=API+'/integrations/aifa/devices/{hub}/children/{child}/native-keys'
@app.get(NATIVE_PATH,dependencies=[Depends(auth)],tags=['Infrared Learning'])
async def native_view(hub:str,child:str):return native_ir.view(store,hub,child)
@app.post(NATIVE_PATH+'/learn',dependencies=[Depends(auth)],tags=['Infrared Learning'])
async def native_begin(hub:str,child:str,body:NativeBegin):
 try:return await native_ir.begin(store,hub,child,body.controller_id,body.kind,body.label)
 except AdapterError as e:raise ac_error(e)
 except Exception:raise HTTPException(502,detail='學習模式未完成，既有原廠按鍵保留')
@app.post(NATIVE_PATH+'/sessions/{ident}/{action}',dependencies=[Depends(auth)],tags=['Infrared Learning'])
async def native_session_action(hub:str,child:str,ident:str,action:Literal['capture','save','cancel']):
 try:return await getattr(native_ir,action)(store,hub,child,ident)
 except AdapterError as e:raise ac_error(e)
 except Exception:raise HTTPException(502,detail='原廠按鍵操作未完成；既有按鍵保留，沒有發送紅外線')
@app.post(NATIVE_PATH+'/{kind}/send',dependencies=[Depends(auth)],tags=['Infrared Learning'])
async def native_send(hub:str,child:str,kind:str,request:Request):
 idem=request.headers.get('idempotency-key')
 if not idem or len(idem)>128:raise HTTPException(422,detail='請提供唯一指令識別碼')
 try:
  cancel=(lambda:ac_timers.cancel(hub,child,reason='已由原廠關機按鍵取消舊定時，避免中斷防霉行程')) if ac_timers and kind in ['anti_mold_off','immediate_off'] else None
  return await native_ir.send(store,hub,child,kind,idem,on_accept=cancel)
 except AdapterError as e:raise ac_error(e)
@app.put(NATIVE_PATH+'/{kind}/verify',dependencies=[Depends(auth)],tags=['Infrared Learning'])
async def native_verify(hub:str,child:str,kind:str,body:NativeVerify):
 try:return await native_ir.verify(store,hub,child,kind,body.verified)
 except AdapterError as e:raise ac_error(e)

class AcTimerBody(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 delay_seconds:int=Field(ge=60,le=86400)
 action:Literal['on','off','native_off']
TIMERS_PATH=API+'/integrations/aifa/devices/{hub}/children/{child}/timers'
@app.get(TIMERS_PATH,dependencies=[Depends(auth)],tags=['AC Timers'])
async def ac_timer_list(hub:str,child:str):return timer_view(store,hub,child)
@app.post(TIMERS_PATH,dependencies=[Depends(auth)],tags=['AC Timers'])
async def ac_timer_create(hub:str,child:str,body:AcTimerBody,request:Request):
 idem=request.headers.get('idempotency-key')
 if not idem or len(idem)>128:raise HTTPException(422,detail='請提供唯一指令識別碼')
 try:return ac_timers.schedule(hub,child,body.action,body.delay_seconds,idem)
 except AdapterError as e:raise ac_error(e)
@app.delete(TIMERS_PATH+'/{ident}',dependencies=[Depends(auth)],tags=['AC Timers'])
async def ac_timer_cancel(hub:str,child:str,ident:str):
 try:return ac_timers.cancel(hub,child,ident)
 except AdapterError as e:raise ac_error(e)

class ExternalMatch(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 device_id:str

@app.put(API+'/external-observations/{id}/match',dependencies=[Depends(auth)],tags=['Controllers'])
async def match_external(id:str,body:ExternalMatch):
 d=find(body.device_id);rows=store.one('settings','external-observations',[]);entry=next((x for x in rows if x['id']==id),None)
 if not entry:raise HTTPException(404,detail='找不到待對應的外部名稱')
 if d['kind'] not in ['switch','light','unknown-smart']:raise HTTPException(422,detail='請選擇插座、開關或待辨識的實體裝置')
 if entry.get('matched_device_id') and entry['matched_device_id']!=d['id']:raise HTTPException(409,detail='這個名稱已對應設備，請先核對原有設定')
 added=entry['name'] not in d.get('aliases',[])
 aliases=list(dict.fromkeys([*d.get('aliases',[]),entry['name']]))
 d['aliases']=aliases;store.put('devices',d['id'],d);entry.update(matched_device_id=d['id'],matched_at=time.time(),alias_added=entry.get('alias_added',added))
 store.put('settings','external-observations',rows);store.event('device.alias.matched',{'device_id':d['id'],'message':'已人工確認外部名稱與設備對應'})
 return public(d)

@app.delete(API+'/external-observations/{id}/match',dependencies=[Depends(auth)],tags=['Controllers'])
async def unmatch_external(id:str):
 rows=store.one('settings','external-observations',[]);entry=next((x for x in rows if x['id']==id),None)
 if not entry:raise HTTPException(404,detail='找不到外部名稱')
 d=store.one('devices',entry.get('matched_device_id'))
 if d and entry.get('alias_added') and not any(x['id']!=id and x.get('matched_device_id')==d['id'] and x['name']==entry['name'] for x in rows):
  d['aliases']=[x for x in d.get('aliases',[]) if x!=entry['name']];store.put('devices',d['id'],d)
 for key in ['matched_device_id','matched_at','alias_added']:entry.pop(key,None)
 store.put('settings','external-observations',rows);store.event('device.alias.unmatched',{'message':'已解除外部名稱對應，實體設備保留'})
 return {'status':'unmatched'}

class TelemetrySchedule(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 enabled:bool
 source:Literal['local','tuya-cloud']
 interval_seconds:int=Field(ge=15,le=86400)

@app.put(API+'/devices/{id}/telemetry-schedule',dependencies=[Depends(auth)],tags=['Telemetry'])
async def configure_telemetry_schedule(id:str,body:TelemetrySchedule):
 d=find(id)
 if not sensor_view(store,d):raise HTTPException(422,detail='此設備尚未提供感測數值')
 if body.source=='tuya-cloud':
  if body.interval_seconds<60:raise HTTPException(422,detail='Tuya 雲端更新間隔至少 60 秒')
  c=store.secret('tuya-cloud');allowed={x['id'] for x in store.secret('tuya-cloud-devices').get('devices',[])}
  if body.enabled and (d['adapter']!='tuya' or not c.get('api_key') or not c.get('api_secret') or d.get('config',{}).get('device_id') not in allowed):raise HTTPException(409,detail='此設備尚未完成 Tuya 雲端授權')
 store.put('settings','sensor-refresh:'+id,body.model_dump())
 runtime=store.one('settings','sensor-refresh-runtime:'+id,{})
 # Saving does not perform an upstream request. The background worker picks it up.
 store.put('settings','sensor-refresh-runtime:'+id,{**runtime,'next_run_at':time.time() if body.enabled else None,'failures':0,'last_error':None})
 store.event('telemetry.schedule.updated',{'device_id':id,'message':'已更新背景讀取設定'})
 return schedule_view(store,d)

@app.get(API+'/devices/{id}/readings',dependencies=[Depends(auth)],tags=['Telemetry'])
async def readings(id:str,metric:str,hours:int=24):
 d=find(id);view=sensor_view(store,d)
 if not view or metric not in [m['key'] for m in view['metrics']]:raise HTTPException(404,detail='此設備未提供這項讀值')
 if hours not in [1,24,168,720]:raise HTTPException(422,detail='時間範圍須為 1、24、168 或 720 小時')
 return {**store.readings(id,metric,time.time()-hours*3600),'device_id':id,'metric':metric,'hours':hours,'time_basis':'中控接收或查詢時間，非裝置量測時間'}

@app.post(API+'/devices/{id}/sensor-cloud-snapshot',dependencies=[Depends(auth)],tags=['Telemetry'])
async def cloud_snapshot(id:str):
 d=find(id)
 if d['adapter']!='tuya' or not metrics_for(d,{}):raise HTTPException(422,detail='此設備未提供可查詢的 Tuya 感測數值')
 try:await cloud_snapshots.fetch(store,d)
 except AdapterError as e:raise HTTPException(429 if e.code=='cloud_cooldown' else 409,detail={'code':e.code,'message':e.message})
 except (httpx.HTTPError,ValueError,KeyError):raise HTTPException(502,detail='雲端查詢未完成，既有讀值保留；請稍後再試')
 return sensor_view(store,d)

class Placement(BaseModel):
 model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
 room_id:str|None=None
 x:float=Field(ge=0,le=1)
 y:float=Field(ge=0,le=1)
 z:float=Field(default=1,ge=0,le=5)
 confirmed:bool=True
 source:str='人工設定'

class DeviceEdit(BaseModel):
 model_config=ConfigDict(extra='forbid')
 name:str|None=Field(default=None,min_length=1,max_length=80)
 kind:Literal['light','switch','speaker','camera','dehumidifier','laundry','computer','network','printer','gaming','mobile','other','unknown-smart','sensor','cover','gateway','remote']|None=None
 ips:list[str]|None=Field(default=None,min_length=1,max_length=4)
 @field_validator('ips')
 @classmethod
 def addresses(cls,v):
  if v is None:raise ValueError('IP 不可為空')
  for ip in v:
   a=ipaddress.ip_address(ip)
   if a.version!=4 or not a.is_private or a.is_loopback or a.is_unspecified or a.is_multicast:raise ValueError('請使用內網 IPv4 位址')
  return list(dict.fromkeys(v))
 favorite:bool|None=None
 notes:str|None=Field(default=None,max_length=1500)
 placement:Placement|None=None

@app.patch(API+'/devices/{id}',dependencies=[Depends(auth)],tags=['Devices'])
async def edit_device(id:str,body:DeviceEdit):
 d=find(id);patch=body.model_dump(exclude_unset=True)
 if any(patch.get(k,'') is None for k in ['name','kind','favorite','notes']):raise HTTPException(422,detail='設備欄位不可為空值')
 if patch.get('placement') and patch['placement']['room_id'] not in [None]+[r['id'] for r in store.one('settings','layout')['rooms']]:raise HTTPException(422,detail='房間不存在')
 if 'ips' in patch and patch['ips']!=d['ips']:
  cast=adapters.casts.pop(id,None)
  if cast:await asyncio.to_thread(cast.disconnect,2)
  old=store.one('state',id,{})
  store.put('state',id,{**old,'fresh':False,'commands':[]})
 d.update(patch);store.put('devices',id,d);store.event('device.updated',{'device_id':id,'name':d['name'],'message':'已儲存設備名稱或位置'})
 return public(d)

class Connection(BaseModel):
 model_config=ConfigDict(extra='forbid')
 adapter:Literal['lifx','cast','tuya','lg','homeassistant','network']
 device_id:str|None=Field(default=None,max_length=128)
 local_key:str|None=None
 version:Literal['3.1','3.3','3.4','3.5']='3.4'
 power_dps:list[str]=Field(default_factory=lambda:['1'],max_length=8)
 lg_device_id:str|None=Field(default=None,max_length=128)
 entity_id:str|None=Field(default=None,max_length=128)
 @field_validator('local_key')
 @classmethod
 def key_length(cls,v):
  if v and len(v.encode())!=16:raise ValueError('Tuya local key 必須是 16 個位元組')
  return v
 @field_validator('power_dps')
 @classmethod
 def dp_valid(cls,v):
  if any(not re.fullmatch(r'[1-9][0-9]{0,2}',x) for x in v):raise ValueError('DPS 必須為 1 至 999 的編號')
  return v
 @field_validator('entity_id')
 @classmethod
 def entity_valid(cls,v):
  if v and not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+',v):raise ValueError('實體 ID 格式錯誤')
  return v

@app.put(API+'/devices/{id}/connection',dependencies=[Depends(auth)],tags=['Integrations'])
async def connection(id:str,body:Connection):
 d=find(id);c=body.model_dump(exclude_none=True);key=c.pop('local_key',None);d['adapter']=c.pop('adapter');d['config']={**d.get('config',{}),**c}
 if key:store.set_secret(id,{**store.secret(id),'local_key':key})
 store.put('devices',id,d);store.event('device.connection',{'device_id':id,'message':'已更新設備接入方式'})
 return await refresh_one(d)

@app.post(API+'/devices/{id}/refresh',dependencies=[Depends(auth)],tags=['Devices'])
async def refresh_device(id:str):return await refresh_one(find(id))

@app.post(API+'/refresh',dependencies=[Depends(auth)],tags=['Devices'])
async def refresh():asyncio.create_task(refresh_all());return {'status':'started'}

class Command(BaseModel):
 model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
 command:Literal['power','brightness','temperature','color','volume','mute','play','pause','stop','cover']
 value:Any=None
 channel:str|None=None

def validate_command(body):
 c,v=body.command,body.value
 if c=='cover' and v not in ['open','close','stop']:raise HTTPException(422,detail='窗簾指令必須為 open、close 或 stop')
 if c in ['power','mute'] and not isinstance(v,bool):raise HTTPException(422,detail='開關值必須是布林值')
 if c in ['brightness','volume'] and (isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=100):raise HTTPException(422,detail='數值必須在 0 到 100 之間')
 if c=='temperature' and (not isinstance(v,int) or not 2500<=v<=9000):raise HTTPException(422,detail='色溫必須在 2500 到 9000 K 之間')
 if c=='color' and (not isinstance(v,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',v)):raise HTTPException(422,detail='顏色必須為 #RRGGBB')

async def execute(id,body,idem):
 validate_command(body);d=find(id);request=body.model_dump()
 async with locks.setdefault(id,asyncio.Lock()):
  cached=store.command(id,idem)
  if cached:
   if cached['request']!=request:raise HTTPException(409,detail='這個 Idempotency-Key 已用於不同指令')
   return cached['result']
  st=store.one('state',id,{})
  if not adapters.configured(d):raise HTTPException(409,detail={'code':'not_configured','message':'請先完成設備認證接入'})
  if not st.get('fresh') or not st.get('available') or time.time()-st.get('observed_at',0)>90:raise HTTPException(409,detail={'code':'state_stale','message':'狀態尚未確認，請先重新讀取設備'})
  if body.command not in st.get('commands',[]):raise HTTPException(422,detail={'code':'unsupported_command','message':'實際設備尚未提供這項控制能力'})
  command_device=d
  if body.channel:
   if d['adapter']!='tuya' or body.channel not in d['config'].get('power_dps',[]) or body.channel not in [c['id'] for c in st.get('values',{}).get('channels',[]) if isinstance(c.get('on'),bool)]:raise HTTPException(422,detail='此通道尚未完成對應')
   command_device={**d,'config':{**d['config'],'power_dps':[body.channel]}}
  cmdid=str(uuid.uuid4());result={'id':cmdid,'device_id':id,'requested_at':time.time(),'command':body.command,'status':'failed','message':''}
  result.update(status='accepted',message='指令已接受，正在執行與回讀')
  store.save_command(cmdid,id,idem,request,result)
  try:
   await adapters.send(command_device,body.command,body.value);result.update(status='sent',message='指令已送出，等待設備狀態')
   await asyncio.sleep(.4);readback=await asyncio.wait_for(adapters.read(d),timeout=14);store.put('state',id,readback)
   actual=readback['values'].get(body.command);expected=body.value
   if body.channel:actual=readback['values'].get('dps',{}).get(body.channel)
   verified=(actual==expected) if body.command in ['power','mute','temperature'] else (isinstance(actual,(int,float)) and abs(actual-expected)<=2) if body.command in ['volume','brightness'] else readback['values'].get('playback')=={'play':'PLAYING','pause':'PAUSED','stop':'IDLE'}.get(body.command) if body.command in ['play','pause','stop'] else False
   if body.command=='color':
    import colorsys
    rgb=tuple(int(expected[i:i+2],16)/255 for i in [1,3,5]);h,s,_=colorsys.rgb_to_hsv(*rgb);values=readback['values'];delta=abs(values.get('hue',-999999)-round(h*65535));verified=min(delta,65535-delta)<=100 and abs(values.get('saturation',-999999)-round(s*65535))<=100
   result.update(status='confirmed' if verified else 'unconfirmed',message='設備回讀已確認' if verified else '已送出，尚未從設備確認目標狀態',state=readback)
  except Exception as e:
   result['message']=e.message if isinstance(e,AdapterError) else ('已送出，但設備回讀逾時' if result['status']=='sent' else '控制未成功，請檢查設備連線與設定')
   if result['status']=='sent':
    result['status']='unconfirmed';previous=store.one('state',id,{})
    store.put('state',id,{**previous,'fresh':False,'commands':[]})
   else:result['status']='failed'
  store.save_command(cmdid,id,idem,request,result);store.event('command.'+result['status'],{'device_id':id,'name':d['name'],'command':body.command,'message':result['message'],'command_id':cmdid})
  return result

@app.get(API+'/commands/{command_id}',dependencies=[Depends(auth)],tags=['Commands'])
async def command_result(command_id:str):
 with store.lock:r=store.db.execute('SELECT body FROM commands WHERE id=?',(command_id,)).fetchone()
 if not r:raise HTTPException(404,detail='找不到這筆指令')
 return json.loads(r['body'])

@app.post(API+'/devices/{id}/commands',dependencies=[Depends(auth)],tags=['Commands'])
async def command(id:str,body:Command,request:Request):
 idem=request.headers.get('idempotency-key') or str(uuid.uuid4())
 if len(idem)>128:raise HTTPException(422,detail='Idempotency-Key 太長')
 return await execute(id,body,idem)

class Room(BaseModel):
 model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
 id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
 name:str=Field(min_length=1,max_length=40)
 color:str=Field(pattern=r'^#[0-9a-fA-F]{6}$')
 polygon:list[list[float]]=Field(min_length=3,max_length=100)
 @field_validator('polygon')
 @classmethod
 def points(cls,v):
  if any(len(p)!=2 or any(not 0<=x<=1 for x in p) for p in v):raise ValueError('房間座標需介於 0 和 1')
  return v

class LayoutEdit(BaseModel):
 model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
 expected_version:int
 width_m:float=Field(ge=1,le=100)
 depth_m:float=Field(ge=1,le=100)
 wall_height_m:float=Field(ge=.5,le=10)
 rooms:list[Room]=Field(max_length=100)
 model_scale:float=Field(default=1,ge=.01,le=100)
 model_rotation:float=Field(default=0,ge=-360,le=360)
 model_offset_x:float=Field(default=0,ge=-100,le=100)
 model_offset_z:float=Field(default=0,ge=-100,le=100)
 model_url:Literal['/assets/home/home-display.glb','/assets/home/model-upload.glb','/assets/home/home-original.glb']|None=None

@app.put(API+'/layout',dependencies=[Depends(auth)],tags=['Spatial layout'])
async def layout(body:LayoutEdit):
 with store.lock:
  current=store.one('settings','layout')
  if current['version']!=body.expected_version:raise HTTPException(409,detail='配置已由其他操作更新，請重新整理後再儲存')
  patch=body.model_dump();patch.pop('expected_version');patch['rooms']=[r.model_dump() for r in body.rooms]
  ids=[r['id'] for r in patch['rooms']]
  if len(ids)!=len(set(ids)):raise HTTPException(422,detail='房間 ID 不可重複')
  assigned={d.get('placement',{}).get('room_id') for d in store.devices() if d.get('placement')}
  if assigned-set(ids)-{None}:raise HTTPException(409,detail='有設備仍使用被移除的房間，請先重新定位')
  current.update(patch,version=current['version']+1);store.put('settings','layout',current)
 store.event('layout.updated',{'message':'已儲存空間配置','version':current['version']});return current

@app.post(API+'/layout/model',dependencies=[Depends(auth)],tags=['Spatial layout'])
async def upload_model(file:UploadFile=File(...)):
 blob=await file.read(25*1024*1024+1)
 if len(blob)>25*1024*1024 or len(blob)<20 or blob[:4]!=b'glTF':raise HTTPException(422,detail='請選擇小於 25 MB 的有效 GLB 模型')
 import struct
 if struct.unpack_from('<I',blob,4)[0]!=2 or struct.unpack_from('<I',blob,8)[0]!=len(blob):raise HTTPException(422,detail='GLB 檔案內容不完整')
 n=struct.unpack_from('<I',blob,12)[0]
 try:doc=json.loads(blob[20:20+n]);external=[x for group in ['buffers','images'] for x in doc.get(group,[]) if x.get('uri') and not x['uri'].startswith('data:')]
 except Exception:raise HTTPException(422,detail='GLB 模型描述無法讀取')
 if external:raise HTTPException(422,detail='模型需內嵌材質，不可包含外部資源網址')
 (BASE/'assets/model-upload.glb').write_bytes(blob);return {'url':'/assets/home/model-upload.glb','bytes':len(blob)}

class IntegrationConfig(BaseModel):
 model_config=ConfigDict(extra='forbid')
 token:str|None=Field(default=None,max_length=8192)
 url:str|None=Field(default=None,max_length=500)
 country:str='TW'

@app.get(API+'/integrations',dependencies=[Depends(auth)],tags=['Integrations'])
async def integrations():
 ha=store.secret('homeassistant');lg=store.secret('lg')
 return {'homeassistant':{'configured':bool(ha.get('token')),'url':ha.get('url','')},'lg':{'configured':bool(lg.get('pat')),'country':lg.get('country','TW')},'lifx':{'mode':'local','credentials_required':False},'cast':{'mode':'local','credentials_required':False},'tuya':{'configured_count':sum(bool(store.secret(d['id']).get('local_key')) for d in store.devices())}}

@app.put(API+'/integrations/{name}',dependencies=[Depends(auth)],tags=['Integrations'])
async def integration(name:Literal['homeassistant','lg'],body:IntegrationConfig):
 previous=store.secret(name)
 if name=='homeassistant':
  parsed=urlparse(body.url or previous.get('url',''))
  if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise HTTPException(422,detail='請填入完整的 Home Assistant http(s) 位址，不含帳密或參數')
  previous.update(url=(body.url or previous.get('url','')).rstrip('/'))
  if body.token:previous['token']=body.token
 else:
  if not re.fullmatch(r'[A-Z]{2}',body.country):raise HTTPException(422,detail='帳號地區需為兩位大寫代碼，例如 TW')
  if body.token:previous['pat']=body.token
  previous.update(country=body.country,client_id=previous.get('client_id') or str(uuid.uuid4()))
 store.set_secret(name,previous);return {'saved':True,'message':'憑證已加密儲存在本機'}

@app.post(API+'/integrations/{name}/probe',dependencies=[Depends(auth)],tags=['Integrations'])
async def integration_probe(name:Literal['homeassistant','lg']):
 try:
  raw=await adapters.ha('states') if name=='homeassistant' else await adapters.lg('async_get_device_list')
  return {'status':'connected','devices':raw}
 except Exception as e:raise HTTPException(502,detail={'code':'connection_failed','message':e.message if isinstance(e,AdapterError) else '無法完成認證查詢，請核對設定與網路'})

class TokenName(BaseModel):name:str=Field(min_length=1,max_length=80)
@app.post(API+'/access-tokens',dependencies=[Depends(auth)],tags=['Authentication'])
async def create_token(body:TokenName):
 token=secrets.token_urlsafe(32);id=str(uuid.uuid4())
 with store.lock:store.db.execute('INSERT INTO tokens(id,hash,name,created) VALUES (?,?,?,?)',(id,hashlib.sha256(token.encode()).hexdigest(),body.name,time.time()));store.db.commit()
 store.event('access.created',{'message':'已建立外部系統存取權杖','name':body.name});return {'id':id,'token':token,'name':body.name,'message':'只顯示一次，請妥善保存；此權杖可控制已接入設備'}

@app.get(API+'/access-tokens',dependencies=[Depends(auth)],tags=['Authentication'])
async def list_tokens():
 with store.lock:return {'items':[dict(r) for r in store.db.execute('SELECT id,name,created,revoked FROM tokens ORDER BY created DESC')]}

@app.delete(API+'/access-tokens/{id}',dependencies=[Depends(auth)],tags=['Authentication'])
async def revoke_token(id:str):
 with store.lock:store.db.execute('UPDATE tokens SET revoked=1 WHERE id=?',(id,));store.db.commit()
 return {'revoked':True}

@app.get(API+'/events',dependencies=[Depends(auth)],tags=['Events'])
async def events(request:Request,since:int=0):
 last=max(since,int(request.headers.get('last-event-id','0')) if request.headers.get('last-event-id','0').isdigit() else 0)
 async def stream():
  nonlocal last
  while not await request.is_disconnected():
   for e in store.events(last):
    last=e['seq'];yield f'id: {last}\nevent: {e["type"]}\ndata: {json.dumps(e,ensure_ascii=False)}\n\n'
   yield ': keepalive\n\n';await asyncio.sleep(3)
 return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.get(API+'/export',dependencies=[Depends(auth)],tags=['Home'])
async def export():
 return JSONResponse({'schema_version':1,'exported_at':time.time(),'layout':store.one('settings','layout'),'devices':store.devices()},headers={'Content-Disposition':'attachment; filename="wuling-home-config.json"'})

@app.get('/assets/home/{name}',dependencies=[Depends(auth)],include_in_schema=False)
async def asset(name:str):
 if name not in ['example-floorplan.svg','floorplan.png','floorplan-original.png','floorplan-20221122.pdf','home-original.skp','home-original.glb','home-display.glb','model-upload.glb']:raise HTTPException(404)
 f=BASE/'assets'/name
 if not f.is_file():raise HTTPException(404)
 return FileResponse(f)

@app.get('/api/docs',include_in_schema=False)
async def api_docs():
 return HTMLResponse('''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>武陵居 API</title><style>body{font:16px system-ui;max-width:920px;margin:60px auto;padding:24px;line-height:1.9;color:#25313b}pre{background:#f3f3f1;padding:20px;overflow:auto}a{color:#53734e}h1{font-weight:500}td,th{text-align:left;padding:10px 18px;border-bottom:1px solid #ddd}</style><h1>武陵居 Home Control API v1</h1><p>使用 Bearer token，從中控的「系統接入」建立與撤銷權杖。所有時間戳為 UTC Unix 秒；位置 x/y 為平面圖 0..1，z 為公尺。</p><p><a href="/api/openapi.json">下載 OpenAPI 規格</a> · <a href="/">回到中控</a></p><table><tr><th>操作</th><th>路徑</th></tr><tr><td>家庭與最新觀測</td><td>GET /api/v1/home</td></tr><tr><td>設備列表／設備資料</td><td>GET /api/v1/devices[/{id}]</td></tr><tr><td>更新名稱與位置</td><td>PATCH /api/v1/devices/{id}</td></tr><tr><td>真實設備控制</td><td>POST /api/v1/devices/{id}/commands</td></tr><tr><td>更新空間模型</td><td>PUT /api/v1/layout（expected_version）</td></tr><tr><td>事件串流</td><td>GET /api/v1/events（SSE）</td></tr><tr><td>匯出配置（不含憑證）</td><td>GET /api/v1/export</td></tr></table><h2>控制範例</h2><pre>curl -X POST http://MAC_IP:8788/api/v1/devices/DEVICE_ID/commands \\\n  -H "Authorization: Bearer YOUR_TOKEN" \\\n  -H "Content-Type: application/json" \\\n  -H "Idempotency-Key: unique-command-id" \\\n  -d '{"command":"power","value":true}'</pre><p>先讀取設備 state.commands 與 can_control。status=confirmed 表示已從設備回讀確認，unconfirmed 只表示送出，failed 表示失敗。同一設備、相同 Idempotency-Key 與相同內容會回傳原結果，避免重複操作；同鍵不同內容回傳 409。過期狀態拒絕控制，需先 refresh。</p><p>capabilities 以實機回覆為準。LG 雲端／Home Assistant 的上游狀態可能有延遲；IP 可回應不代表已完成控制認證。配置含房間、設備識別、位置與模型校準，不包含金鑰。</p></html>''')

if (BASE/'web').exists():
 app.mount('/static',StaticFiles(directory=BASE/'web'),name='static')
 @app.get('/{path:path}',include_in_schema=False)
 async def frontend(path:str):
  if path.startswith('api/') or path.startswith('assets/'):raise HTTPException(404)
  return FileResponse(BASE/'web/index.html')
