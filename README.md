# Quote-bot

<p align="center">
  <img src="https://raw.githubusercontent.com/Warmlatte/Quote-bot/main/assets/TRB_LOGO.png" alt="The Roll Bar logo" width="140">
</p>

<p align="center">
  <strong>Discord 3D 列印報價機器人</strong><br>
  從 Google Drive 讀取 STL / OBJ 模型，計算樹脂與加工費，產生 PDF 報價單，並同步報價紀錄至 Google Sheets。
</p>

<p align="center">
  <a href="https://github.com/Warmlatte/Quote-bot/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Warmlatte/Quote-bot/ci.yml?branch=main&label=CI&logo=github"></a>
  <a href="https://www.python.org/"><img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white"></a>
  <a href="https://github.com/Warmlatte/Quote-bot/blob/main/LICENSE"><img alt="License MIT" src="https://img.shields.io/github/license/Warmlatte/Quote-bot"></a>
  <img alt="Discord Bot" src="https://img.shields.io/badge/Discord-Bot-5865F2?logo=discord&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white">
  <img alt="Zeabur" src="https://img.shields.io/badge/Deploy-Zeabur-7B61FF">
</p>

## 功能特色

- Discord slash command `/quote` 建立 3D 列印估價單。
- 從 Google Drive 資料夾遞迴讀取 `.stl` / `.obj` 模型檔。
- 自動計算模型體積、件數、材料費、加工費、低消、免運與折扣。
- 支援 RPG 高精度樹脂、透明樹脂與透明樹脂調色加價。
- 可在 Discord 互動式調整折扣、運費、寄送地址與模型件數。
- 接受報價後產生 PDF 報價單，並回傳 Discord 附件。
- 使用 SQLite 即時保存接受 / 拒絕紀錄。
- 每 24 小時自動同步未同步紀錄至 Google Sheets，也可用 `/sync_sheets` 手動同步。
- 內建 Dockerfile 與 Zeabur 部署設定，適合長時間運行。
- CI 會執行 ruff、pytest coverage 與 Docker build smoke test。

## 使用流程

1. 工作人員在指定 Discord 伺服器執行 `/quote`。
2. 填入客戶名稱與 Google Drive 資料夾連結。
3. 選擇樹脂種類，透明樹脂可切換是否調色。
4. Bot 下載模型檔並計算體積、件數與報價。
5. 估價結果發布到頻道，可再調整折扣、運費或個別檔案件數。
6. 點選接受報價後，Bot 產生 PDF 報價單並寫入 SQLite。
7. 報價紀錄定期或手動同步至 Google Sheets 報表。

## Slash Commands

| 指令 | 權限 | 說明 |
| --- | --- | --- |
| `/quote` | 指定 guild + 指定 member role | 建立 3D 列印估價單 |
| `/sync_sheets` | Discord administrator | 立即同步 SQLite 中尚未同步的報價紀錄到 Google Sheets |

## 報價規則摘要

| 項目 | 規則 |
| --- | --- |
| RPG 高精度樹脂 | 體積 ml × 3.5，無條件進位 |
| 透明樹脂 | 體積 ml × 3.5，無條件進位 |
| 透明樹脂調色 | 體積 ml × 7.0，無條件進位 |
| 加工費 | 件數級距：前 2 件每件 80、接續 3 件每件 70、接續 3 件每件 60、接續 3 件每件 50、其餘每件 40 |
| 未達低消 | 商品小計低於 NT$ 500 時補足至低消 |
| 免運 | 小計達 NT$ 4,000 自動免運 |
| 自動折扣 | 小計達 NT$ 7,000 套用 95 折並免運 |
| 手動折扣 | 支援百分比，例如 `80%`，或固定金額，例如 `-100` |

## 環境變數

建立 `.env` 或在部署平台設定下列變數：

```env
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=123456789012345678
MEMBER_ROLE_ID=123456789012345678

GOOGLE_AUTH_TYPE=service_account
GOOGLE_SHEETS_ID=
GOOGLE_SERVICE_ACCOUNT_JSON=

DB_PATH=/data/quote_bot.db
```

| 變數 | 必填 | 說明 |
| --- | --- | --- |
| `DISCORD_TOKEN` | 是 | Discord bot token |
| `GUILD_ID` | 是 | 允許使用 slash commands 的 Discord guild ID |
| `MEMBER_ROLE_ID` | 是 | 可使用 `/quote` 的 Discord role ID |
| `GOOGLE_SHEETS_ID` | 是 | 報表同步目標 Google Sheets ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 是 | Google service account JSON 字串，支援原始 JSON 或 Base64 |
| `DB_PATH` | 否 | SQLite 檔案路徑，預設 `/data/quote_bot.db` |

## Google 權限設定

1. 建立 Google Cloud service account。
2. 啟用 Google Drive API 與 Google Sheets API。
3. 將 service account JSON 放入 `GOOGLE_SERVICE_ACCOUNT_JSON`。
4. 將報價模型資料夾與 Google Sheets 分享給 service account email。
5. Google Drive 資料夾需包含 `.stl` 或 `.obj` 檔案，Bot 會遞迴讀取子資料夾。

## 本機開發

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m bot.main
```

執行測試：

```bash
python -m pytest -q
```

執行 lint：

```bash
python -m ruff check bot/ tests/
```

## Docker

```bash
docker build -t quote-bot:latest .
docker run --rm --env-file .env -v quote-bot-data:/data quote-bot:latest
```

Docker image 會安裝 `fonts-noto-cjk`，用於 PDF 報價單的中文輸出。

## Zeabur 部署

SQLite 資料庫需要掛載 Volume 以持久化資料，否則容器重啟後資料會清空。

1. 在 Zeabur 專案中新增 Volume。
2. 將 Volume 掛載路徑設為 `/data`。
3. 設定環境變數 `DB_PATH=/data/quote_bot.db`。
4. 設定 Discord 與 Google 相關環境變數。
5. 部署後 Bot 啟動時會自動建立資料庫與資料表，不需手動初始化。

## 報表同步

Bot 會先將報價紀錄寫入 SQLite，再把未同步資料推送至 Google Sheets。

- 背景排程：每 24 小時自動同步一次。
- 手動同步：Discord administrator 可執行 `/sync_sheets`。
- 接受報價：同步客戶名稱、報價編號、Drive 連結與最終總價。
- 拒絕報價：同步客戶名稱、總價、檔案明細與拒絕原因。

## 專案結構

```text
bot/
  commands/      Discord slash commands 與互動式 UI
  db/            SQLite client 與資料表初始化
  drive/         Google Drive 檔案查詢與下載
  pdf_gen/       PDF 報價單產生
  pricing/       模型讀取與報價規則
  sheets/        Google Sheets 寫入 client
  config.py      環境變數載入
  main.py        Bot 啟動與排程同步
tests/           單元測試與整合測試
assets/          Logo 與 PDF 字型資源
```

## 技術棧

- Python 3.12
- discord.py
- trimesh
- reportlab
- gspread
- google-api-python-client
- SQLite
- pytest / pytest-asyncio
- Docker / Zeabur

## License

MIT License. See [LICENSE](LICENSE).
