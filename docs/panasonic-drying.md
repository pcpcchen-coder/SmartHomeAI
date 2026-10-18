# Panasonic 冷氣原廠功能與乾燥防霉

查核：2026-10-19。機型未知時，不能把某系列的時間與控制碼當作所有 Panasonic 冷氣規格。

## 查到的原廠說明與實作

- [Panasonic 台灣 FAQ 121](https://pstw.panasonic.com.tw/FAQ/Article/121)：文件列出的 PX／RX／UX／VX 等系列，啟用乾燥防霉後，冷房／除濕達指定條件再手動關機，會執行約十分鐘流程；部分系列可以待機時啟動並選擇時間。適用機種、條件須以該機說明書為準。
- [Panasonic FAQ 2982](https://pstw.panasonic.com.tw/FAQ/Article/2982)：其他乾燥程序可能約三十分鐘；不能把「十分鐘」寫成所有型號的保證。
- [Mobile01：K 系列討論](https://www.mobile01.com/topicdetail.php?f=731&t=6613667)：2022 年使用者比較不同 K 系列型號及出廠時間，回報有純送風或先結霜後送風的差異。這是使用者經驗，尚不能證明任一未確認型號的動作。
- [Mobile01：定時關機討論](https://www.mobile01.com/topicdetail.php?f=731&t=6361058)：有使用者回報原廠定時關機與手動關機的防霉行為不同。中控定時送一般關機或學習的原廠關機訊號，是否符合機種啟動條件仍需實測。
- [日本使用者實作 A75C3777 特殊清潔訊號](https://diysmartmatter.com/archives/3022)：該遙控器長按除臭鍵可發送特殊訊號，程序約二十分鐘。證明特定機種存在專用指令，但不是台灣 K 系列的通用十分鐘防霉碼；本專案沒有複製或發送該訊號。
- [IRremoteESP8266 Panasonic 實作](https://github.com/crankyoldgit/IRremoteESP8266/blob/master/src/ir_Panasonic.cpp)：共通狀態介面的 clean 欄位不能直接作為台灣 K 系列防霉控制的證據。
- [AIFA 社群相容實作](https://github.com/PinLin/aifa-smart-homeassistant)：設定碼 173 的目錄提供自動、冷房、除濕；目前沒有已確認的原廠防霉寫入方法。不把未知位元或計時欄位直接發送給設備。

## 中控提供的操作

1. 基本遙控：溫度、模式、風速、擺風、睡眠／強力／節能；以已識別設定碼限制選項，未知功能不猜測。特殊運轉部分欄位來自社群推定，仍需實體確認。
2. 原廠專用按鍵：使用既有已授權的 Tuya 紅外線控制器學習實際遙控器訊號。可分別保存「啟用乾燥防霉」、「防霉關機」、「單獨啟動」、「立即停止」及靜音、清淨、自訂按鍵。自訂按鍵可多個保存。
3. 學習有 60 秒期限，以本次開始時間讀取訊號，避免撿到舊碼。先收到並保存，再由使用者明確按測試。學習／保存本身不重播；原廠遙控器在學習時仍可能同時被家電收到。
4. 訊號加密存在本機，透過 Tuya 官方學習 API 保存於選定控制器的專用 DIY 遙控器。沒有擴充付費服務；仍使用既有 API 額度。
5. 送出後只標示「待現場確認」。必須先測試，使用者親自確認有效後，才開放原廠防霉關機定時。標記有效是人工觀察，不是假裝有家電回報。
6. 背景定時：1 分鐘到 24 小時，可設定開機、一般關機、已驗證的原廠防霉關機。每台保留一個待執行項目，可取消；手動電源動作取消舊定時。原廠防霉只發送一次學習訊號，不再安排十分鐘後第二次關機。
7. Mac 需清醒並連網，重新開機後需啟動中控。錯過超過兩分鐘標記錯過，不補送。送出過程重啟或結果不明不自動重試。

紅外線冷氣訊號常包含完整電源／溫度／模式設定。每個學習按鍵須記住當時的遙控器狀態。即使同品牌，也不可直接套用另一型號的清潔碼。若學習到的訊號不支援、截斷或實際無效，取消有效標記，保留原廠遙控器操作；需要確切室內機與遙控器型號後再分析協定。

## API

均位於 `/api/v1/integrations/aifa/devices/{hub}/children/{child}`，沿用完整權限 Bearer 或本機工作階段與來源檢查。

- `GET /native-keys`：已保存按鍵、可用控制器及本次學習狀態，沒有原始訊號或憑證。
- `POST /native-keys/learn`：`controller_id`, `kind`, 可選 `label`；自訂用途為 `custom_` 加 12 位十六進位識別碼，必須命名。
- `POST /native-keys/sessions/{id}/capture|save|cancel`：讀取本次訊號、保存、退出學習。
- `POST /native-keys/{kind}/send`：需要 `Idempotency-Key`；不自動重送。
- `PUT /native-keys/{kind}/verify`：`verified: boolean`；必須先由中控測試過才可確認。
- `GET /timers`：背景服務狀態與最近 15 筆該家電定時。
- `POST /timers`：`action: on|off|native_off`, `delay_seconds: 60..86400`；需要 `Idempotency-Key`。
- `DELETE /timers/{id}`：取消待執行定時。已開始送出不能保證取消。

單一 Uvicorn worker。測試以假雲端驗證學習、冪等、逾時、取消與重啟，不代表家中實體冷氣已完成驗證。
