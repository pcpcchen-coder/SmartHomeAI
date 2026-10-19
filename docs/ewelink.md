# 易微聯官方 OAuth 接入

中控的「控制器與遙控器 → 帳號接入 → 易微聯」提供專屬應用憑證設定、官方帳號授權與設備清單同步。這階段提供讀取與核對，不提供尚未驗證的開關控制，也不把 Google Home 同名項目自動配對為實體設備。

## 設定步驟

1. 在 eWeLink Developer Center 完成個人開發者認證。
2. 建立自己的 OAuth2.0 應用，回呼網址必須完整填為 `http://127.0.0.1:8788/api/v1/integrations/ewelink/callback`。
3. 在這台中控 Mac 的介面填入 App ID／App Secret。只加密儲存，不回傳到瀏覽器，也不放入配置匯出或 Git。
4. 按「前往易微聯官方授權」，在官方頁面登入或掃碼。中控不收取手機 App 密碼。官方授權會提供這個帳號下平台允許的設備，不能假定僅限兩個名稱。
5. 授權回來後同步設備，核對品牌、型號與 UIID，再實作該設備確定支援的控制參數。平台可能限制免費應用可見的品牌或設備類型，設備總數與回傳筆數會分開呈現。

目前開發者認證已由官方頁面確認通過；控制台應用清單顯示 No Data，建立動作回報 `Reached the max limit to create Applications`，所以專屬憑證尚未取得。應用是否已在平台建立不能只由這個錯誤推斷，須等控制台顯示可核對的項目。未購買或升級方案。

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

目前僅手動同步，無背景輪詢。呼叫間隔至少一秒，每次網路嘗試計入本機 UTC 月計數，每區達 50,000 次即停止；這個計數不包含其他程式呼叫，不是官方剩餘額度。官方回報 412 或 HTTP 403 時顯示額度／權限錯誤，不自動付費升級。權杖、原始設備資料不會傳給第三方服務。

## 來源與驗證

[官方 OAuth 文件](https://github.com/CoolKit-Technologies/eWeLink-API/blob/main/en/OAuth2.0.md)，2026-10-19 核對。測試涵蓋簽章、一次性 state、到期／重播拒絕、固定主機與路徑、權杖更新、敏感資料不外露、額度限制、loopback 與回呼來源；測試通過不代表已完成真實帳號授權。
