import json, sqlite3, time, secrets, os, threading
from pathlib import Path
from cryptography.fernet import Fernet

class Store:
 def __init__(self,path):
  self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True);os.chmod(self.path,0o700)
  self.lock=threading.RLock();self.db=sqlite3.connect(self.path/'home.sqlite3',check_same_thread=False)
  self.db.row_factory=sqlite3.Row
  self.db.executescript('''PRAGMA journal_mode=WAL;
   CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,body TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS state(id TEXT PRIMARY KEY,body TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS settings(id TEXT PRIMARY KEY,body TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS secrets(id TEXT PRIMARY KEY,body TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL,kind TEXT,body TEXT);
   CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,device TEXT,idem TEXT,request TEXT,body TEXT,UNIQUE(device,idem));
   CREATE TABLE IF NOT EXISTS tokens(id TEXT PRIMARY KEY,hash TEXT,name TEXT,created REAL,revoked INTEGER DEFAULT 0);
   CREATE TABLE IF NOT EXISTS readings(device TEXT,metric TEXT,at REAL,source TEXT,value TEXT,unit TEXT,PRIMARY KEY(device,metric,at,source));
   CREATE INDEX IF NOT EXISTS readings_at ON readings(at);
   ''');self.db.commit()
  for filename,contents in [('.encryption-key',Fernet.generate_key().decode()),('.api-token',secrets.token_urlsafe(32))]:
   f=self.path/filename
   if not f.exists():
    fd=os.open(f,os.O_CREAT|os.O_WRONLY|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(contents)
  self.fernet=Fernet((self.path/'.encryption-key').read_bytes());self.master_token=(self.path/'.api-token').read_text()
  self.last_readings_prune=0
 def one(self,table,id,default=None):
  with self.lock:
   row=self.db.execute(f'SELECT body FROM {table} WHERE id=?',(id,)).fetchone()
   return json.loads(row['body']) if row else default
 def put(self,table,id,body):
  assert table in ['devices','state','settings']
  with self.lock:self.db.execute(f'INSERT OR REPLACE INTO {table}(id,body) VALUES (?,?)',(id,json.dumps(body,ensure_ascii=False)));self.db.commit()
 def devices(self):
  with self.lock:return [json.loads(r['body']) for r in self.db.execute('SELECT body FROM devices')]
 def record_readings(self,device,at,source,metrics):
  if not at or not source:return
  with self.lock:
   for m in metrics:
    if m.get('value') is not None:self.db.execute('INSERT OR IGNORE INTO readings VALUES (?,?,?,?,?,?)',(device,m['key'],at,source,json.dumps(m['value'],ensure_ascii=False),m['unit']))
   if time.time()-self.last_readings_prune>3600:
    self.db.execute('DELETE FROM readings WHERE at<?',(time.time()-30*86400,));self.last_readings_prune=time.time()
   self.db.commit()
 def readings(self,device,metric,since,limit=360):
  with self.lock:rows=self.db.execute('SELECT at,source,value,unit FROM readings WHERE device=? AND metric=? AND at>=? ORDER BY at',(device,metric,since)).fetchall()
  count=len(rows)
  if count>limit:rows=[rows[round(i*(count-1)/(limit-1))] for i in range(limit)]
  return {'total_samples':count,'points':[{'at':r['at'],'source':r['source'],'value':json.loads(r['value']),'unit':r['unit']} for r in rows]}
 def secret(self,id):
  with self.lock:
   r=self.db.execute('SELECT body FROM secrets WHERE id=?',(id,)).fetchone()
   return json.loads(self.fernet.decrypt(r['body'].encode())) if r else {}
 def set_secret(self,id,value):
  with self.lock:self.db.execute('INSERT OR REPLACE INTO secrets VALUES (?,?)',(id,self.fernet.encrypt(json.dumps(value).encode()).decode()));self.db.commit()
 def event(self,kind,body):
  with self.lock:
   cur=self.db.execute('INSERT INTO events(ts,kind,body) VALUES (?,?,?)',(time.time(),kind,json.dumps(body,ensure_ascii=False)));self.db.commit();return cur.lastrowid
 def events(self,since=0,limit=50):
  with self.lock:return [{'seq':r['seq'],'at':r['ts'],'type':r['kind'],'data':json.loads(r['body'])} for r in self.db.execute('SELECT * FROM events WHERE seq>? ORDER BY seq ASC LIMIT ?',(since,limit))]
 def latest_events(self,limit=30):
  with self.lock:return [{'seq':r['seq'],'at':r['ts'],'type':r['kind'],'data':json.loads(r['body'])} for r in self.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT ?',(limit,))]
 def command(self,device,idem):
  with self.lock:
   r=self.db.execute('SELECT body,request FROM commands WHERE device=? AND idem=?',(device,idem)).fetchone()
   return {'result':json.loads(r['body']),'request':json.loads(r['request'])} if r else None
 def save_command(self,id,device,idem,req,result):
  with self.lock:self.db.execute('INSERT INTO commands VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(id,device,idem,json.dumps(req,sort_keys=True),json.dumps(result,ensure_ascii=False)));self.db.commit()
