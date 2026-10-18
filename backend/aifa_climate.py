"""AIFA classic AC profile, independently encoded from the community protocol reference."""
from .adapters import AdapterError
MODES={'auto':0x10,'cool':0x20,'dry':0x40}
FANS={'auto':1,'low':0x20,'medium':0x40,'high':0x80}
SWINGS={'auto':1,'fixed_1':4,'fixed_2':8,'fixed_3':0x80,'fixed_4':0x40,'fixed_5':0x20}
FEATURES={'normal':0,'sleep':8,'turbo':4,'energy_saving':2}
def capabilities(code):
 return {'modes':list(MODES),'fan_modes':list(FANS),'swing_modes':list(SWINGS),'features':list(FEATURES),'min_temperature':17,'max_temperature':30,'timer_supported':False} if str(code)=='173' else None

def encode(code,settings):
 if not capabilities(code):raise AdapterError('aifa_profile','此設定碼的完整功能尚未確認')
 if set(settings)!={'mode','temperature','fan','swing','feature'}:raise AdapterError('aifa_settings','請提供完整冷氣設定')
 t=settings['temperature']
 if isinstance(t,bool) or not isinstance(t,int) or not 17<=t<=30 or settings['mode'] not in MODES or settings['fan'] not in FANS or settings['swing'] not in SWINGS or settings['feature'] not in FEATURES:raise AdapterError('aifa_settings','冷氣設定超出已確認範圍')
 bcd=lambda x:int(str(x).zfill(2),16)
 return bytes([255,160,6 if settings['mode']=='cool' and t>=24 else 7 if settings['mode']=='cool' else 8,bcd(int(code)//100),bcd(int(code)%100),bcd(t),0,MODES[settings['mode']],FANS[settings['fan']],SWINGS[settings['swing']],FEATURES[settings['feature']],0,0,0,240]).hex()

def decode(p):
 if len(p)!=14 or p[:2]!=b'\xfe\xa0' or p[-1]!=240:return None
 bcd=lambda x:int(f'{x:02x}') if all(c.isdigit() for c in f'{x:02x}') else None
 off=p[4]==0 and p[6]==0
 return {'power':not off,'mode':None if off else next((k for k,v in MODES.items() if p[6]&v),None),'temperature':None if off else bcd(p[4]),'fan':next((k for k,v in FANS.items() if p[7]&v),None),'swing':next((k for k,v in SWINGS.items() if p[8]&v),None),'feature':next((k for k,v in FEATURES.items() if v and p[9]&v),'normal')}
