"""Start the local service once; the installed Mac launcher opens its browser."""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

def healthy():
 try:
  with urllib.request.urlopen('http://127.0.0.1:8788/api/v1/health',timeout=1) as r:return json.load(r).get('service')=='wuling-home-control'
 except Exception:return False

def start():
 base=Path(__file__).resolve().parent.parent
 data=Path(os.environ.get('WULING_DATA_DIR',Path.home()/'Library/Application Support/WulingHomeControl'))
 data.mkdir(parents=True,exist_ok=True);os.chmod(data,0o700)
 if healthy():return
 lock=data/'starting.lock'
 try:lock.mkdir()
 except FileExistsError:
  if time.time()-lock.stat().st_mtime>45:lock.rmdir();lock.mkdir()
  else:
   for _ in range(35):
    if healthy():return
    time.sleep(.3)
   raise RuntimeError('另一個中控啟動程序仍在執行')
 try:
  env={**os.environ,'WULING_DATA_DIR':str(data),'PYTHONPATH':os.environ.get('PYTHONPATH',str(base))}
  with (data/'service.log').open('ab') as log:
   p=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app:app','--host','0.0.0.0','--port','8788','--no-access-log'],cwd=base,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  (data/'service.pid').write_text(str(p.pid))
  for _ in range(40):
   if healthy():return
   if p.poll() is not None:break
   time.sleep(.25)
  raise RuntimeError('中控無法啟動，請檢查服務紀錄或 8788 連接埠')
 finally:
  try:lock.rmdir()
  except OSError:pass

if __name__=='__main__':start()
