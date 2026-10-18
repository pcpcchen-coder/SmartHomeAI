"""Import an authorized cloud snapshot. Device keys remain in encrypted storage."""
import hashlib, re

def import_devices(store, records):
 result=[]
 for row in records:
  cid=row['id'];mac=(row.get('mac') or '').lower();category=row.get('category','');mapping=row.get('mapping') or {}
  d=next((x for x in store.devices() if x.get('config',{}).get('device_id')==cid or (mac and x.get('mac','').lower()==mac)),None)
  if d is None:
   d={'id':'tuya-'+hashlib.sha256(cid.encode()).hexdigest()[:12],'ips':[],'mac':mac,'placement':None,'favorite':False,'vendor':'Tuya','notes':''}
  kind='sensor' if category in ['sj','pir','hps','hjjcy','mcs'] else 'cover' if category=='cl' else 'gateway' if category.startswith('wg') else 'remote' if category=='wxkg' or category=='wnykq' or category.startswith('infrared_') else 'switch'
  power=[str(dp) for dp,spec in mapping.items() if str(dp).isdigit() and spec.get('type')=='Boolean' and re.fullmatch(r'switch(?:_[1-9][0-9]*)?',spec.get('code',''))] if kind=='switch' else []
  config={**d.get('config',{}),'device_id':cid,'version':d.get('config',{}).get('version','3.4'),'power_dps':power,'mapping':mapping,'cloud_category':category,'infrared_virtual':category.startswith('infrared_')}
  if row.get('gateway_id') and row.get('node_id'):config.update(gateway_id=row['gateway_id'],node_id=row['node_id'])
  for dp,spec in mapping.items():
   if kind=='cover' and spec.get('code')=='control' and isinstance(spec.get('values'),dict) and set(['open','close','stop'])<=set(spec['values'].get('range',[])):config['cover_dp']=str(dp)
  d.update(name=row['name'],adapter='tuya',kind=kind,category={'sensor':'感應器','cover':'窗簾','gateway':'智慧閘道','remote':'遙控器／按鈕','switch':'開關／插座'}[kind],config=config)
  store.put('devices',d['id'],d)
  if row.get('key'):store.set_secret(d['id'],{**store.secret(d['id']),'local_key':row['key']})
  old=store.one('state',d['id'],{});store.put('state',d['id'],{**old,'fresh':False,'commands':[]})
  result.append({'id':d['id'],'name':d['name'],'ips':d['ips'],'kind':kind,'gateway':config.get('gateway_id')})
 store.event('tuya.imported',{'message':f'已匯入 {len(result)} 筆授權設備，金鑰已加密保存'})
 return result
