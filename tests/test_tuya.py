from contextlib import contextmanager
from types import SimpleNamespace
from backend.adapters import Adapters
from backend.store import Store
from backend.tuya_import import import_devices


def test_sensor_booleans_never_become_power_and_import_is_stable(tmp_path):
 s=Store(tmp_path);rows=[{'id':'door','name':'門磁','category':'mcs','key':'fixture','mapping':{'1':{'code':'doorcontact_state','type':'Boolean'}}}]
 first=import_devices(s,rows)[0];d=s.one('devices',first['id']);d['placement']={'x':.2,'y':.4,'confirmed':True};s.put('devices',d['id'],d)
 import_devices(s,rows);d=s.one('devices',d['id']);assert len(s.devices())==1 and d['placement']['confirmed'] and d['config']['power_dps']==[]
 a=Adapters(s)
 @contextmanager
 def fake(device):yield SimpleNamespace(status=lambda:{'dps':{'1':True,'40':True}})
 a.tuya=fake;r=a.read_tuya(d)
 assert r['commands']==[] and r['values']['channels']==[] and r['values']['power'] is None
 assert r['values']['metrics'][0]['value']=='開啟'
 assert 'fixture' not in str(s.devices())


def test_gateway_child_uses_parent_key_and_closes_socket(tmp_path,monkeypatch):
 import tinytuya
 s=Store(tmp_path);import_devices(s,[{'id':'gateway','name':'閘道','category':'wg2','key':'parent-secret','mapping':{}},{'id':'child','name':'三切','category':'kg','key':'child-secret','gateway_id':'gateway','node_id':'node','mapping':{'1':{'code':'switch_1','type':'Boolean'},'40':{'code':'child_lock','type':'Boolean'}}}])
 parent,child=s.devices();parent['ips']=['192.0.2.130'];s.put('devices',parent['id'],parent)
 created=[]
 class Device:
  def __init__(self,*args,**kwargs):self.args=args;self.kwargs=kwargs;self.closed=False;created.append(self)
  def set_socketTimeout(self,*a):pass
  def set_socketRetryLimit(self,*a):pass
  def close(self):self.closed=True
  def status(self):return {'dps':{'1':False,'40':True}}
 monkeypatch.setattr(tinytuya,'Device',Device);a=Adapters(s);r=a.read_tuya(child)
 assert created[0].args==('gateway','192.0.2.130','parent-secret')
 assert created[1].kwargs['parent'] is created[0] and created[1].kwargs['cid']=='node'
 assert created[0].closed and r['values']['channels']==[{'id':'1','on':False}]
 assert r['commands']==['power'] and not child['ips']
