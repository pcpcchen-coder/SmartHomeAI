# 武陵居中控 · SmartHomeAI

本機執行的家庭中控，提供繁體中文介面、平面圖／3D 空間、人工設備定位、家電控制，以及感測數值儀表板。其他系統可透過 REST v1、OpenAPI 與 SSE 接入。

公開儲存庫只有程式與通用示例，**沒有私人房屋圖面、真實設備清單、帳號金鑰或實測資料庫**。首次啟動會建立三筆尚未接入的示例設備，不會產生模擬讀值。現有資料庫不會被示例覆蓋。

## 啟動

使用 Python 3.12+、Node.js 20.19+ 與 pnpm。先建置介面，再啟動單一服務：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8788
```

開啟 <http://127.0.0.1:8788>；Mac 本機可直接登入。跨裝置使用時，服務需綁定家中區網介面，並從「系統接入」建立可撤銷的控制權杖。所有權杖目前均有完整管理權限，尚無唯讀角色。此版供私人區網使用，沒有公開遠端部署。

macOS 預設資料目錄為 `~/Library/Application Support/WulingHomeControl/`；其他平台為 `~/.local/share/wuling-home-control/`，可用 `WULING_DATA_DIR` 另設。請使用單一 Uvicorn worker。區網觀測主要驗證於 macOS；Windows 的原生連線觀測未支援。

## 介面

- **全屋總覽**：從平面圖或 3D 模型選取設備，依實際能力控制；多路開關持續呈現各通道。
- **環境儀表板**：依空間、設備名稱或數值類別瀏覽溫濕度、CO₂、PM、甲醛、VOC、照度、電池、用電與感應狀態。只顯示設備真正提供的項目，缺值為「—」。
- **歷史紀錄**：成功取得數值後存入本機，保留 30 天；可選 1 小時、24 小時、7 天或 30 天。最多顯示 360 個原始點，超過 3 分鐘或來源不同的點不連線。
- **設備詳情**：同時顯示可用控制、感測值、單位、來源與接收／查詢時間。
- **設備定位／空間設定**：人工點圖、調整房間／高度、校準尺寸與模型、上傳自有 GLB。設備清單也包含只支援網路觀測的手機與電腦。

Tuya 雲端快照採**按需查詢**，每台至少相隔 60 秒，背景只讀本機快取，不會自動輪詢 Tuya 雲端。雲端查詢會使用平台 API 額度，服務效期與可用資源依帳號而定。未提供裝置量測時間時，介面明確標示快照及查詢時間，不把它當成即時量測，也不設定推測的健康警示門檻。

## 設備接入與限制

| 轉接器 | 支援範圍 |
| --- | --- |
| LIFX | 真實電源、亮度、色溫及顏色；核對 MAC |
| Google Cast | 音量、靜音、已有媒體工作階段的播放控制；查詢不主動啟動播放器 |
| Tuya | local key／協定版本／DPS 對應；閘道子設備、多路開關與感測數值；窗簾送出後需確認實際動作 |
| LG ThinQ | PAT 與官方能力查詢；除濕機依可寫能力開放電源，洗乾衣設備先讀狀態 |
| Home Assistant | 實體狀態、選定感測數值與受支援控制；透過真正的 service 呼叫 |
| 一般網路設備 | 連線觀測、分類與位置；沒有攝影機原生串流／雲台，或手機／電腦遠端電源 |

設備詳情可設定接入與憑證。Tuya 帳號授權匯入由 `backend/tuya_import.py` 提供程式介面，尚無完整的雲端專案設定精靈。金鑰留在本機 Fernet 加密區，不會傳至前端；解密金鑰與主權杖檔權限 0600、資料夾 0700。這是本機檔案加密，並非硬體金鑰保護。

控制前驗證能力與狀態，送出後回讀並回報 `confirmed / unconfirmed / failed`。回讀符合指令不代表所有機械動作已完成；不可用網路在線推論家電可控制。

私人圖面與初始清單請參考 [資產說明](assets/README.md)。完整備份應在服務停止後複製資料目錄（包含隱藏金鑰檔）。UI 配置匯出不含憑證，但含 IP／MAC 等私人資訊；目前尚無完整 UI 還原流程。

## 開發與 API

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

測試使用通用示例與假轉接器，無須家中設備。涵蓋登入、來源檢查、指令冪等／回讀、三路開關、資料持久化、感測換算、雲端簽章／授權／查詢間隔、歷史去重及來源標示。

[架構與 API 合約](docs/architecture.md) 說明外部系統接入；執行服務後可查看 `/api/docs`。目前沒有使用者角色、排程／情境引擎、自動 DHCP 追蹤與通用 Google Home 控制橋接。

`macos/main.swift` 與 `scripts/build_mac_app.py` 可建立獨立 WebKit 視窗。打包版仍依賴指定的 Python 3.12 路徑，並非可攜至任意 Mac 的通用安裝包；打包需額外安裝 PyMuPDF、Xcode Command Line Tools，且目標 App 不可已存在。現有 App 的私人資產不在本儲存庫。
