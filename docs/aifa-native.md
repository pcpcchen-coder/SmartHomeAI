# AIFA 原廠防霉按鍵

目前支援 AIFA Smart 已配對、設定碼為 19968 的 type=0/subType=0 冷氣。設定碼需由官方手機 App 完成下載，清單同步本身不下載或改寫控制器代碼。

## 指令來源與範圍

AIFA Smart Android 1.2.9（套件 tw.com.aifa.ictrl_pro，versionCode 234）的防霉按鈕使用一般冷氣按鍵，而非 Plus「智慧防霉」排程設定。僅靜態檢查套件，未安裝或執行 Android App，也未公開第三方 App 二進位或反組譯產物。

獨立核對的協定事實：

- `AirConditionerBottomIcons` 的 `ControllerScreen.AcMold` callback 傳入整數 38（0x26，Dart Smi 76）。
- `AirConditionerPage` 將 callback 連到 `_ICtrlPageState._onButtonPress`；此按鍵走 `DevicePacketCommand`。0x98/0x99 的 Plus 防霉設定分支不適用於 0x26。
- `SubDeviceTypeX.code` 對冷氣 subtype 0 回傳 0xA0。
- `DevicePacketCommand` 序列化為 `[0xFF, subtype_code, command, 0xF0]`，因此單次按鍵封包為 `FF A0 26 F0`。
- 不使用 `DevicePacketSetAntiMold`，不由查詢回覆推測 setter，不傳送五位設定碼截斷後的 BCD 封包。

檢查樣本 libapp.so SHA-256：`24b074d54d97a50a1bc9da4ba906608c7ed35be2e54947db8f0a29455be08f26`。樣本由 APKPure 提供，來源與官方發行相容性仍有第三方鏡像限制；封包格式測試不等於實體冷氣驗證。官方套件資訊：[Google Play](https://play.google.com/store/apps/details?id=tw.com.aifa.ictrl_pro)。

已由使用者確認：官方手機 AIFA Smart 套用 19968 後出現防霉按鈕，按下會開始乾燥。中控另外保存每台家電的實測確認，預設 false；手機 App 的成功不會自動標成中控已驗證。

## API

以下路徑皆以 `/api/v1/integrations/aifa/devices/{hub}/children/{child}` 為前綴，必須通過既有授權與來源檢查。

| 方法 | 路徑 | 內容 |
| --- | --- | --- |
| GET | /native-mold | 讀取可用設定碼、發送來源與實測紀錄，不連雲端、不控制 |
| POST | /native-mold | 空 body，必填 Idempotency-Key，按一次防霉 |
| PUT | /native-mold/verify | `{command_id, verified}`，由使用者確認最近一次中控實測 |

僅接受 24 小時內授權清單中的 subtype 0 冷氣及設定碼 19968。AIFA 操作共用鎖與兩秒間隔；防霉按鍵另限制至少 30 秒，避免短時間重複觸發。相同識別碼回傳原結果，連線中斷、重啟或未知結果皆不自動重送。開始此操作會取消舊中控定時，以免再關機中斷乾燥。沒有十分鐘後關機指令。

紅外線單向控制，按鍵可能依目前狀態切換功能。畫面不把控制器回覆視為冷氣實際乾燥狀態；要求先現場確認已完全停止，再操作。實測確認綁定設定碼與最近一次中控指令，不能用手機 App 的動作代替。

既有 Tuya 學習保留，分頁改名「Tuya 按鍵學習」，避免把 Tuya 發送誤認為 AIFA。五位設定碼 19968 的完整溫度／模式封包仍待核對，未沿用舊 173 格式。
