# 架構與控制合約 v1

## AIFA 與 eWeLink 接入進度

AIFA Smart 相容接入支援登入、清單讀取，以及下方所述的基本冷氣開關。協定參考 [社群實作](https://github.com/PinLin/aifa-smart-homeassistant)，屬非公開介面，廠商更新可能使其失效。沒有安裝或執行社群程式。

`POST /integrations/aifa/login` 僅允許本機 peer，仍需中控認證與來源檢查。email/password 僅經 HTTPS 傳到固定 api.aifaremote.com/oauth2/token，不允許重新導向，密碼不保存。access/refresh token 加密保存，期限到達可刷新。錯誤不回傳上游原文或輸入值。`GET /integrations/aifa` 回傳白名單清單快取，沒有 token。`POST /integrations/aifa/discover` 讀 /devices，最少相隔 60 秒，失敗保留清單；完整 MAC 才可對應既有設備，不推測 IP、不改位置。`DELETE /integrations/aifa` 只移除本機 token 與快取，不刪除手機 App 設備，也不宣稱向廠商撤銷 token。

`GET /integrations/ewelink` 顯示接入階段。介面呈現個人開發者認證、建立應用、帳號授權三步。已提供 eWeLink OAuth callback 與設備清單同步；目前只讀取與核對，不提供控制。專屬憑證尚待控制台應用清單問題解除。詳見 [接入說明](ewelink.md)。

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

## 背景讀取排程

`PUT /devices/{id}/telemetry-schedule` 設定 `{enabled, source, interval_seconds}`，source 為 local 或 tuya-cloud。local 使用既有轉接器（含已接入的 LG／HA 服務），限制 15～86,400 秒；Tuya 雲端至少 60 秒，僅允許已授權設備。停用不受雲端憑證是否仍有效限制；已送出的讀取可完成，但不再安排下一次。

GET /telemetry 的各 item.auto_update 含設定、last_attempt_at、last_success_at、next_run_at、last_error、failures、estimated_status_queries_per_day；頂層 background_running 表示服務是否正在排程。

排程在 FastAPI lifespan 執行，不由瀏覽器計時器觸發。每台僅一個待執行／執行中工作，最多十台並行，共用閘道的工作序列化。沒有自訂設定時保持既有 35 秒區網讀取。設定可按台暫停，手動讀取仍可用。設定及下次執行時間保存在 SQLite，重啟或喚醒不補送積欠查詢。完成後開始下一段間隔；雲端失敗依 2 倍、4 倍等退避，最多一小時（原設定更長時維持原間隔）。失敗保留舊值與歷史。

每日雲端查詢估算為 ceil(86400 / interval_seconds)，僅估計此設備狀態查詢，不含認證、手動查詢、服務中斷或退避。手動與排程共用至少 60 秒的限制，沒有自動付費升級或新增服務。

## 紅外線與控制器合約

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| GET | /controllers | 快取的控制器、實際關聯家電、同步結果與待對應外部名稱；不查雲端 |
| POST | /controllers/{id}/discover | 讀取已授權 Tuya 紅外線控制器的家電及實際按鍵；最少相隔 60 秒 |
| POST | /devices/{id}/infrared-commands | `{key, key_id}`，搭配 Idempotency-Key；僅接受同步出的按鍵 |
| PUT | /external-observations/{id}/match | `{device_id}`；人工加入名稱別名，不改 IP、位置或執行開關 |
| DELETE | /external-observations/{id}/match | 解除這筆名稱對應，保留實體設備 |

紅外線使用固定資料中心 HTTPS 與 HMAC-SHA256，僅允許 token、remotes、keys 及 raw/command 官方路徑。控制器 ID 必須出現在已加密儲存的授權設備清單，remote ID 必須出現在最新成功同步結果。既有家電依控制器及 remote/node ID 對應，保留 ID、使用者名稱與位置；新家電沒有獨立 IP，也不從紅外線名稱猜測房間。同步不刪除原有清單；已移除、同步失敗或超過 24 小時的按鍵停用。

各設備 public.infrared 含 keys、ready、controller_id、controller_name、queried_at、error、one_way。ready 只表示按鍵可用，不表示家電在線或目前開關狀態。一般 can_control 仍是雙向控制能力，不涵蓋紅外線。

雲端 raw/command 收到 true 後，指令結果為 unconfirmed，transport_status=accepted_by_cloud；逾時為 unconfirmed/unknown，明確拒絕為 failed。沒有紅外線實體狀態回讀，不寫入 power，也不自動重送。相同 Idempotency-Key 及相同內容回傳原結果；不同內容拒絕。每台控制器指令序列化且至少相隔一秒。

外部名稱觀察與實體設備分開存放；只允許人工對應 switch/light/unknown-smart。別名不改變既有識別與控制路徑，解除時只移除此對應新增的別名。這不是 Google Home 控制 API。i-Ctrl 的 API 接入仍須另外取得 AIFA 支援與授權，不使用 Tuya 認證代替。

官方文件：[服務與 API 清單](https://developer.tuya.com/en/docs/cloud/ir-control-hub-open-service?id=Kb3oe2mk8ya72)、[家電清單](https://developer.tuya.com/en/docs/cloud/791872dd27?id=Kb3oe4qw98wmm)、[實際按鍵](https://developer.tuya.com/en/docs/cloud/ff7882b8a5?id=Kb3oe37sqdjx5)、[送出按鍵](https://developer.tuya.com/en/docs/cloud/d59591e9f6?id=Kb3oe4hspk8bi)。

AIFA 已擴充 type=0/subType=0 冷氣的手動開機與關機，以及控制器狀態查詢。使用驗證憑證的 TLS aifaremote.com:8751，MAC 與 token 握手必須成功。`POST /integrations/aifa/devices/{hub}/children/{child}/status` 只查詢；同路徑 `/power` 接收嚴格布林 power 與必填 Idempotency-Key。僅接受 24 小時內帳號清單中的家電，控制序列化、兩秒間隔，不自動重送。指令結果始終為 unconfirmed，控制器狀態另列，不能宣稱冷氣實際動作完成。溫度／模式／風速尚未接入。

冷氣完整設定：POST /integrations/aifa/devices/{hub}/children/{child}/climate，必填 Idempotency-Key。僅接受清單中的設定碼 173；mode=auto/cool/dry，temperature=17..30 整數，fan=auto/low/medium/high，swing=auto/fixed_1..fixed_5，feature=normal/sleep/turbo/energy_saving。完整驗證後才編碼傳送，不自動重送；定時、防霉尚未確認。控制器回報解析出模式、溫度、風速、方向與特殊模式，不宣稱是實體冷氣感測回饋。
