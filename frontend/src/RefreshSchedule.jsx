import React,{useEffect,useState} from 'react';
import {Clock,RefreshCw,Save,Timer} from 'lucide-react';
const date=t=>t?new Date(t*1000).toLocaleString('zh-TW',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'尚無紀錄';
const intervals=[15,30,35,60,300,900,1800,3600];
const label=n=>n<60?`${n} 秒`:n<3600?`${n/60} 分鐘`:`${n/3600} 小時`;
export default function RefreshSchedule({sensor,backgroundRunning,cloudQueriesPerDay,now,api,notify,onSaved}){
 const config=sensor.auto_update.config;
 const[draft,setDraft]=useState(config),[saving,setSaving]=useState(false),[custom,setCustom]=useState(!intervals.includes(config.interval_seconds));
 useEffect(()=>{setDraft(config);setCustom(!intervals.includes(config.interval_seconds))},[sensor.id,config.enabled,config.source,config.interval_seconds]);
 const minimum=draft.source==='tuya-cloud'?60:15,valid=Number.isInteger(Number(draft.interval_seconds))&&Number(draft.interval_seconds)>=minimum&&Number(draft.interval_seconds)<=86400;
 const dirty=draft.enabled!==config.enabled||draft.source!==config.source||Number(draft.interval_seconds)!==config.interval_seconds;
 const update=patch=>setDraft(d=>({...d,...patch}));
 const save=async()=>{setSaving(true);try{await api(`/devices/${sensor.id}/telemetry-schedule`,{method:'PUT',body:JSON.stringify({...draft,interval_seconds:Number(draft.interval_seconds)})});notify(draft.enabled?'背景自動更新設定已儲存':'已暫停這台設備的背景自動更新');await onSaved()}catch(e){notify(e.message)}finally{setSaving(false)}};
 const auto=sensor.auto_update,seconds=auto.next_run_at?Math.max(0,Math.ceil(auto.next_run_at-now)):0;
 const status=!config.enabled?'已暫停':!backgroundRunning?'背景服務未運行':auto.last_error?'等待重試':'背景執行中';
 return <section className="refresh-schedule" aria-label="背景自動更新設定"><div className="refresh-schedule-heading"><div><Timer size={18}/><div><h3>背景自動更新</h3><span className={!config.enabled?'schedule-paused':auto.last_error?'schedule-warning':''}>{status}{config.enabled&&` · 每 ${label(config.interval_seconds)}`}</span></div></div><label className="schedule-toggle"><input type="checkbox" aria-label="啟用背景自動更新" checked={draft.enabled} onChange={e=>update({enabled:e.target.checked})}/><span>啟用</span></label></div>
  <div className="schedule-fields"><label>讀取來源<select aria-label="自動更新讀取來源" value={draft.source} onChange={e=>{const source=e.target.value;update({source,interval_seconds:source==='tuya-cloud'&&draft.interval_seconds<60?300:draft.interval_seconds})}}><option value="local">區網／已接入服務</option>{(sensor.cloud_supported||config.source==='tuya-cloud')&&<option value="tuya-cloud">Tuya 雲端</option>}</select></label><label>更新間隔<select aria-label="自動更新時間間隔" value={custom?'custom':draft.interval_seconds} onChange={e=>{setCustom(e.target.value==='custom');if(e.target.value!=='custom')update({interval_seconds:Number(e.target.value)})}}>{intervals.filter(n=>n>=minimum).map(n=><option key={n} value={n}>每 {label(n)}</option>)}<option value="custom">自訂秒數</option></select></label>{custom&&<label>自訂秒數<input aria-label="自訂更新秒數" type="number" min={minimum} max={86400} step="1" value={draft.interval_seconds} onChange={e=>update({interval_seconds:e.target.value})}/></label>}<button className="primary" disabled={saving||!valid||!dirty} onClick={save}><Save size={14}/>{saving?'儲存中…':'儲存設定'}</button></div>
  <div className="schedule-runtime"><span><RefreshCw size={12}/>上次成功：{date(auto.last_success_at)}</span><span><Clock size={12}/>{!config.enabled?'自動讀取已暫停':!backgroundRunning?'請啟動中控背景服務':seconds?`${seconds} 秒後${auto.last_error?'重試':'讀取'}`:'等待背景讀取'}</span></div>
  {auto.last_error&&<p className="schedule-error" role="status">上次未完成：{auto.last_error.message}{config.source==='tuya-cloud'?'。已延長重試間隔，保留最後讀值。':''}</p>}
  <p className="schedule-help">關閉頁面或 App 視窗後仍會更新；Mac 睡眠時暫停，喚醒後繼續。重新開機後請先開啟中控 App。間隔從每次讀取結束後起算。</p>
  {cloudQueriesPerDay>0&&<p className="schedule-help">全屋已設定的 Tuya 雲端排程：約 {cloudQueriesPerDay.toLocaleString('zh-TW')} 次狀態查詢／日。</p>}
  {draft.source==='tuya-cloud'&&<p className="schedule-cloud-note">{draft.enabled&&valid?`此設備約 ${Math.ceil(86400/Number(draft.interval_seconds)).toLocaleString('zh-TW')} 次狀態查詢／日，另有認證查詢。`:''}自動與手動查詢都會使用 Tuya API 額度，至少間隔 60 秒。{!valid?'請輸入 60～86,400 秒的整數。':''}</p>}
  {draft.source==='local'&&!valid&&<p className="schedule-error">請輸入 15～86,400 秒的整數。</p>}
 </section>
}
