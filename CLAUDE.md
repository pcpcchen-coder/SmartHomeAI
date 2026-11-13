# CLAUDE.md — 武陵居中控（SmartHomeAI）

給在這個 repo 工作的開發 agent（Claude Code 等）與開發者。

## 用語

- **「鴨子」＝ Open Duck Mini V2**：家裡自製的雙足機器人（[apirrone/Open_Duck_Mini](https://github.com/apirrone/Open_Duck_Mini) `v2`）。開發在 fork [pcpcchen-coder/Open_Duck_Mini](https://github.com/pcpcchen-coder/Open_Duck_Mini) 的 `nous_duck/`，那邊的 `CLAUDE.md`、架構文件與 ADR 是鴨子的權威來源。
- **Nous／AIOS**：Mac mini 上的語音與 Agent 系統（[pcpcchen-coder/AIOS](https://github.com/pcpcchen-coder/AIOS)）；本中控對它的接入規劃見 [`docs/AIOS接入規劃.md`](docs/AIOS接入規劃.md)。

## 鴨子與中控的界線

- 中控**不直接指揮鴨子**，也不是鴨子協定（duck-link）的端點。鴨子只透過 duck-link 與 Nous 溝通；要讓鴨子動，一律經 Nous 的 `app.duck.*` 工具（fork 的 ADR-0001、ADR-0007）。
- 鴨子大約 25 × 25 cm、高約 49 cm，最快約 0.15 m/s。它的室內定位由鴨子本機負責（AprilTag＋視覺慣性里程計，fork 的 ADR-0002），不由中控推算。
- 若之後要在中控顯示鴨子，只能唯讀，而且資料要來自 Nous 或鴨子回報的狀態；沒有真實回報時不顯示推測位置，這和本 repo「不產生模擬讀值」的原則一致。

## 隱私（本 repo 是公開的）

- 不把房屋平面圖、掃地機器人地圖、房間尺寸、門寬、真實設備清單、IP／MAC、帳號或金鑰放進 repo，包括 commit 訊息與 PR。私人圖面依 `.gitignore` 放在 `assets/`，或放在中控的本機資料目錄。
- 掃地機器人（Deebot）匯出的地圖可以當鴨子建圖的參考，但只存在本機。

## 慣例

- 文件、commit 與 PR 說明用繁體中文。
- 開發與測試方式見 [`README.md`](README.md)；架構與 API 合約見 [`docs/architecture.md`](docs/architecture.md)。
