# 易微聯官方 OAuth 接入

中控的「控制器與遙控器 → 帳號接入 → 易微聯」提供專屬應用憑證設定、官方帳號授權與設備清單同步。提供帳號清單核對，以及已確認為單一開關之 UIID 182 插座的電源控制與用電快照。Google Home 同名項目仍需人工對應。

## 設定步驟

1. 在 eWeLink Developer Center 完成個人開發者認證。
2. 建立自己的 OAuth2.0 應用，回呼網址必須完整填為 `http://127.0.0.1:8788/api/v1/integrations/ewelink/callback`。
3. 在這台中控 Mac 的介面填入 App ID／App Secret。只加密儲存，不回傳到瀏覽器，也不放入配置匯出或 Git。
4. 按「前往易微聯官方授權」，在官方頁面登入或掃碼。中控不收取手機 App 密碼。官方授權會提供這個帳號下平台允許的設備，不能假定僅限兩個名稱。
5. 授權回來後同步設備，核對品牌、型號與 UIID，再實作該設備確定支援的控制參數。平台可能限制免費應用可見的品牌或設備類型，設備總數與回傳筆數會分開呈現。

開發者認證已通過，控制台已顯示自有應用，專屬憑證已加密設定。官方 QR 曾顯示過期且手機授權失敗；換成全新官方授權頁、選擇 Taiwan +886 後，由使用者自行登入成功。真實帳號已回傳兩台插座。未購買或升級方案。

## API 與授權邊界

路徑前綴 `/api/v1/integrations/ewelink`：

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| GET | / | 本機連結狀態、清單快取、本機呼叫計數，不呼叫雲端 |
| PUT | /config | `{app_id, app_secret}`；須登入中控且只能從 Mac loopback 設定 |
| POST | /authorize | 產生官方授權連結；須登入中控且只能從 Mac loopback 開始 |
| GET | /callback | OAuth 專用回呼，接受 loopback、有效 state 與固定資料區 |
| POST | /discover | 同步設備清單；至少相隔 60 秒 |
| DELETE | / | 清除本機帳號權杖、待授權狀態與原始設備快取，保留應用設定；不刪手機 App 設備 |

授權網址使用官方 HMAC-SHA256 簽章；state 為隨機值，僅將其雜湊加密存放，十分鐘到期，交換授權碼前即消耗，只能用一次。callback 不依賴跨站 cookie，不接受區網其他主機，不記錄 code/state/token 到事件或公開錯誤。回呼頁採 no-referrer/no-store，成功後 303 跳回不含授權碼的中控頁面。

所有雲端主機限制為 as/us/eu/cn 官方固定主機，不接受 callback 傳入 URL；目前只允許 token 交換、refresh 與讀取 thing list。連線使用 TLS 憑證驗證，不追蹤重新導向。raw 設備資料（含原廠 devicekey、apikey）加密保存；公開清單只保留名稱、設備 ID、品牌、型號、UIID、在線及單一開關快照。

帳號清單僅手動同步；確認並接入的插座預設每 300 秒由中控背景讀取，可在數值儀表板停用或延長，最低 300 秒。每兩台約 576 次狀態查詢／日，另計手動操作與認證。呼叫間隔至少一秒，每次網路嘗試計入本機 UTC 月計數，每區達 50,000 次即停止；這個計數不包含其他程式呼叫，不是官方剩餘額度。官方回報 412 或 HTTP 403 時顯示額度／權限錯誤，不自動付費升級。權杖、原始設備資料不會傳給第三方服務。

## 來源與驗證

[官方 OAuth 文件](https://github.com/CoolKit-Technologies/eWeLink-API/blob/main/en/OAuth2.0.md)，2026-10-19 核對。測試涵蓋簽章、一次性 state、到期／重播拒絕、固定主機與路徑、權杖更新、敏感資料不外露、額度限制、loopback 與回呼來源；測試通過不代表已完成真實帳號授權。

## 已確認單一開關的 UIID 182

使用者確認每台各有一個實體開關。協定回傳四個 `switches` 槽位，但中控只對應主開關 outlet=0；不顯示或控制其餘三個欄位。`POST /integrations/ewelink/devices/{cloud_id}/import` 要求 `{single_switch_confirmed:true}`，僅接受最近已授權清單內的 UIID 182。重複接入保留名稱、位置與設備 ID，沒有根據名稱猜測 IP 或座標。

統一設備 API 的電源命令只送出 `params.switches=[{outlet:0,switch:on|off}]`。依既有指令識別碼防止重送；逾時結果未知標為 unconfirmed，沒有自動重試。回讀透過官方 GET /v2/device/thing/status，雲端狀態吻合也不標成實體已驗證，另列 cloud_state_matches。

功率 W、電壓 V、電流 A 解析為有限非負數，單位依官方電量協定。快照只代表雲端最後回報值，GET 不代表裝置即時重新量測，也不發送 uiActive 或其他變更設定。來源、查詢時間與未提供量測時間都在介面呈現。

[官方 UIID 與設備參數協定](https://github.com/CoolKit-Technologies/eWeLink-API/blob/main/en/UIIDProtocol.md)。內網 IP 尚待由 MAC 與路由器／網路觀測核對，不用帳號名稱推測。
