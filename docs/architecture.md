# 架構與控制合約 v1

瀏覽器（React / Three.js）→ 本機 FastAPI → 設備轉接器。SQLite 為配置與指令持久化來源，觀測狀態另存，金鑰不進前端或配置匯出。前端不直接存取設備網路。外部系統使用相同 API，避免依畫面或廠商差異控制。

## 身分與空間

設備 ID 由盤點 MAC（缺失時 IP）雜湊建立；別名、分類與 IP 可變更，ID 保持不變。目前不會自動重新掃描或追蹤 DHCP；變更設備 IP 後請在接入設定更新。已有 MAC 的 LIFX 回覆及 Google Cast UUID 會核對，防止僅依過期 IP 控制錯誤設備。不同網卡可能屬於同一實體，洗乾衣塔兩個端點仍保留未確認映射。

空間資料包含 rooms（多邊形）、dimensions、device placement 與 model transform。平面圖左上角為 (0,0)、右下角為 (1,1)；3D 世界座標 X=(x-.5)*width、Z=(y-.5)*depth、Y=離地高度。GLB 為公尺、Y-up。原始 SketchUp 檢視副本已平移，載入時地板厚度 -.15m；另可調整模型縮放、水平旋轉與 X/Z 平移。原始家具圖與建築模型版本不一定相同，不宣稱自動完成精準對齊。

## HTTP 介面

API base `/api/v1`，JSON；OpenAPI `/api/openapi.json`（含 HTTPBearer scheme），簡明文件 `/api/docs`。除了 health 與登入，資料與私人資產皆需驗證。`Authorization: Bearer TOKEN` 適合其他系統；UI 使用 HttpOnly / SameSite=Strict cookie，同來源寫入檢查。未設定跨來源瀏覽器 CORS；外部系統建議從其後端呼叫。權杖目前皆為完整管理權限，可建立與撤銷；主權杖存於資料目錄 `.api-token`。

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| GET | /home | 家庭、設備、狀態、配置與最近事件 |
| GET | /devices；/devices/{id} | 設備能力與最近觀測 |
| PATCH | /devices/{id} | 名稱、分類、IP、favorite、notes、placement（null 清除） |
| PUT | /devices/{id}/connection | 廠商對應與加密認證，接著實際測試查詢 |
| POST | /devices/{id}/refresh；/refresh | 個別查詢／背景刷新全部 |
| POST | /devices/{id}/commands | 送出可用控制，`Idempotency-Key` 表頭 |
| GET | /commands/{command_id} | 查詢指令結果 |
| PUT | /layout | 以 expected_version 避免覆蓋他人編輯 |
| POST | /layout/model | GLB 上傳，25MB 內，不允許外部資源 URL |
| PUT | /integrations/lg；/integrations/homeassistant | 儲存加密的全域認證 |
| POST | /integrations/{name}/probe | 讀取官方帳號／實體清單 |
| GET/POST/DELETE | /access-tokens[/{id}] | 建立、列出、撤銷外部權杖 |
| GET | /events | SSE，支援 Last-Event-ID 或 since 重播 |
| GET | /export | 不含憑證的配置匯出 |

範例：

```sh
curl http://MAC_IP:8788/api/v1/devices \
  -H 'Authorization: Bearer YOUR_TOKEN'
curl -X POST http://MAC_IP:8788/api/v1/devices/DEVICE_ID/commands \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: caller-unique-command-id' \
  -d '{"command":"power","value":true}'
```

## 狀態與指令

`state` 含 source、observed_at（Unix UTC 秒）、fresh、available、values、commands、error。90 秒以上或讀取失敗視為不新鮮；`can_control` 同時取決於憑證、狀態、連線與實際能力。ICMP 觀測只說明「有／未回應」，不等同實體離線或可控制。

指令只允許 `power / brightness / temperature / color / volume / mute / play / pause / stop`。布林開關、0..100 亮度／音量、2500..9000K、#RRGGBB 色碼經型別與範圍驗證。Tuya 的 channel 必須已列在設定的 power_dps，並由解密狀態證實為布林通道。相同設備與相同 Idempotency-Key、相同內容回傳原結果；同鍵不同內容 409。呼叫方應使用唯一業務指令 ID，網路重試保留同鍵。

控制前先持久化 accepted，再交給轉接器；同一設備序列化。送出後再回讀，不直接以 HTTP 200 當作家電已完成動作。結果 `confirmed / unconfirmed / failed`，另含 command id 與 message。數值型亮度／音量容忍 ±2；LIFX 顏色依 hue/saturation 比較。進程若在指令中斷，重啟時將 accepted 改為 unconfirmed，保留原鍵，不會自動重送實體指令。回讀逾時會停用舊狀態的控制能力。指令曾被設備接收但連線中斷仍可能造成不確定結果，應重新觀測。

`confirmed` 代表回讀的欄位符合目標；相同值測試能驗證送出與回讀路徑，不能證明每種功能均完成實際變更。LG / Home Assistant 受上游狀態更新延遲影響。

## 設備轉接器

- LIFX：UDP 56700，GetColor 101 / LightState 107；來源 ID、MAC 與型別核對；SetLightPower 117 / SetColor 102。
- Google Cast：真實 UUID、接收器 GET_STATUS；媒體命名空間存在時用不啟動 App 的 GET_STATUS。查詢等待回覆，未有新鮮媒體狀態就不顯示媒體控制。不主動啟動播放器、載入網址或改變內容。
- Tuya：tinytuya 本地認證與狀態；必須 local key / device ID / 協定版本；多路 DPS 人工對應。沒有金鑰時不嘗試破解。
- LG：官方 ThinQ Connect SDK，PAT / country / client id；先讀 profile / state。除濕機 POWER_ON/OFF 需同時出現在實機可寫 enum；洗乾衣塔先提供狀態，不套用通用電源作遠端啟動。
- Home Assistant：讀 /api/states/{entity}，控制只呼叫 /api/services/{domain}/{service}，再回讀實體。禁止用 POST /api/states 模擬家電控制；unknown/unavailable 不開放控制。
- Camera / network：目前只做連線觀測；沒有原生攝影機串流、PTZ、手機或電腦遠端電源控制。Home Assistant 映射能使用本版支援的 power / brightness 能力，不代表完整 camera 能力。

## 部署與限制

單一 Uvicorn worker、同一 SQLite 資料夾；勿以多 worker 並行控制。使用者要為其他系統建立獨立且可撤銷的權杖。本版沒有使用者角色、排程／情境引擎、語音助理、公共遠端存取、完整配置 UI 匯入或自動 DHCP 追蹤。Home Assistant 或日後轉接器可透過既有 API 擴充；新增能力須同時擴充 OpenAPI、驗證、回讀與 UI。

## 原始文件

- [LIFX 查詢](https://lan.developer.lifx.com/docs/querying-the-device-for-data)、[LIFX 控制](https://lan.developer.lifx.com/docs/changing-a-device)
- [pychromecast](https://github.com/home-assistant-libs/pychromecast)
- [TinyTuya](https://github.com/jasonacox/tinytuya)
- [LG ThinQ Connect](https://github.com/thinq-connect/pythinqconnect)
- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/)
- [Three.js GLTFLoader](https://threejs.org/docs/#examples/en/loaders/GLTFLoader)
- [openskp 本機轉換](https://github.com/iamahsanmehmood/openskp)

## 感測數值合約

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| GET | /telemetry | 已接入讀值的本機快取、設備能力、來源與新鮮度；不查雲端 |
| GET | /devices/{id}/readings?metric=temperature&hours=24 | 1／24／168／720 小時歷史，最多 360 個真實取樣點 |
| POST | /devices/{id}/sensor-cloud-snapshot | 僅查詢已授權 Tuya 設備，使用既有加密雲端認證；每台間隔至少 60 秒 |

每項 metric 含 key、code、label、unit、precision、group、value、alarm；數值依廠商 mapping 的 scale 換算，未取得的值為 null，不能視為 0。alarm 僅表示裝置明確回報的漏水警報，沒有推測空氣品質健康門檻。單位沿用設備定義並規範顯示字形。

每台 telemetry 含 source、received_at、measurement_at、status。本機新近讀值須 90 秒內且查詢成功；Tuya 雲端查詢 15 分鐘內標示 snapshot，並不保證裝置在此期間量測。超時保留資料改為 stale，無讀值為 missing。雲端與本機擇較新的一次完整回報，沒有用其他來源補造缺值。`measurement_at` 目前為 null，`received_at` 是中控成功接收／查詢時間。

SQLite readings 以設備／指標／時間／來源去重，僅保存非空值。成功接收時記錄，30 天以前資料在下一次寫入清除（最多每小時整理一次）。歷史多於 360 點時等距選取原始點，保留首尾，不做平均或插值；超過 3 分鐘的間隔與不同來源不連線。重啟後歷史保留，服務尚未取得的過去數值不回填。

Tuya 快照只使用 GET token/status，HTTPS 固定資料中心白名單，不允許重新導向；憑證與 token 不寫入 telemetry、歷史或前端。僅能查詢先前帳號授權匯入的設備 ID。失敗也記錄查詢間隔以避免連續消耗；不送控制指令、不自動開通服務、不處理付費升級。
