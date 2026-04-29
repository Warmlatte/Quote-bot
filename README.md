# Quote-bot

## 環境變數

```env
DISCORD_TOKEN=
GUILD_ID=
MEMBER_ROLE_ID=
GOOGLE_SHEETS_ID=
GOOGLE_SERVICE_ACCOUNT_JSON=
DB_PATH=/data/quote_bot.db
```

## Zeabur 部署（SQLite Volume）

SQLite 資料庫需掛載 Volume 以持久化資料，否則容器重啟後資料清空：

1. 在 Zeabur 專案中新增 **Volume**，掛載路徑設為 `/data`
2. 設定環境變數 `DB_PATH=/data/quote_bot.db`
3. Bot 啟動時會自動建立資料庫與資料表（無需手動初始化）

## 報表同步

Bot 使用 SQLite 即時儲存報價記錄，每 24 小時自動批次同步至 Google Sheets（老闆報表介面）。