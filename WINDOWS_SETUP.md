# Windows 本機測試

先安裝 Git、Python 3.13 與 Node.js 22.16 以上（建議 Node.js 24 LTS）。
以下指令在 PowerShell 執行；不用修改 PowerShell 執行原則。

## 1. 取得程式與安裝

第一次下載：

```powershell
git clone https://github.com/yvechiu/tool-agent.git
cd tool-agent
```

已有此倉庫時，在該資料夾執行 `git pull --ff-only`，保留自己的未提交變更。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .\config\windows.env.example .\.env
Copy-Item .\config\whiteboard.env.example .\whiteboard\.env
notepad .\.env
```

Copy-Item 僅在設定檔不存在時執行，不要覆寫已填好的設定。
在根目錄 `.env` 填入：

- `DISCORD_APPLICATION_TOKEN`：Discord Developer Portal → 你的「工具代理」Application → Bot → Token。不是 Application ID 或 Public Key。
- `GEMINI_API_KEY`：從 Google AI Studio 取得的 Gemini API key。不需要 OpenAI key。
- `TLDRAW_SERVICE_API_KEY`：自行產生的隨機值，至少 20 字元。

可用以下指令在自己的電腦產生白板共用金鑰，再貼入**兩份** `.env` 的 `TLDRAW_SERVICE_API_KEY`：

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
notepad .\whiteboard\.env
```

不要將上述設定檔、金鑰或含金鑰的截圖上傳 GitHub／聊天。範本不含真實金鑰。
`GEMINI_MODEL` 預設 `gemini-2.5-flash`；如你的帳號無法使用，改為 Google AI Studio 列出的可用模型名稱。

## 2. Discord 設定

在 Developer Portal 的 Bot 頁面開啟 **Message Content Intent**。
若尚未邀請，使用 Installation／OAuth2 URL Generator 的 `bot`、`applications.commands` 邀請到伺服器。

每個實驗頻道都要給工具代理：檢視頻道、傳送訊息、嵌入連結、附加檔案權限。
討論串還需要「在討論串傳送訊息」權限。私人新頻道也必須明確授權其角色。
根目錄範本已填入你的伺服器 ID；不用填每個實驗頻道 ID。

## 3. 啟動白板（第一個 PowerShell 視窗）

從 `tool-agent` 資料夾執行：

```powershell
cd whiteboard
npm ci
npm run build
npm start
```

保持此視窗開啟。另一個視窗檢查：

```powershell
Invoke-RestMethod http://127.0.0.1:5858/api/healthz
```

應看到 `ok: True`。若 tldraw 提示授權要求，請依其官方授權條件處理；正式部署需確認適用授權。

## 4. 啟動 Bot（第二個 PowerShell 視窗）

先切換到 `tool-agent` 根目錄：

```powershell
.\.venv\Scripts\python.exe -u app.py
```

看到「Bot 登入成功」及指令同步紀錄後，在 Discord 測試：

1. 兩個頻道各輸入 `!ping`，皆應收到 Pong。
2. 第一組討論「蘋果」，第二組討論「火車」，各自 @工具代理要求摘要，確認內容不混合。
3. 各頻道執行 `/create-board`，開啟連結新增便利貼。
4. `/analyze-board` 留空使用本頻道最新白板；輸入另一頻道的白板應被拒絕。

Gemini 的意圖判斷、摘要、參與分析、白板分析都會使用你的 Gemini key，可能產生 API 費用。
其他 Bot 的訊息仍不納入。所有可見的伺服器頻道都可能被處理，目前沒有頻道名稱篩選。

## 本機測試限制

- `127.0.0.1` 白板連結只能在執行服務的這台電腦開啟，其他參與者無法使用；多人遠端實驗需另行部署公開 HTTPS 白板。
- 電腦睡眠、斷網或關閉 PowerShell 後 Bot 會離線；這不是持續運作的部署。
- Bot 重啟會清空對話與白板綁定；白板服務重啟會清空白板內容。
- 只啟動一個 Bot 實例，避免重複回覆。
- `401/403` 檢查金鑰及權限；Gemini `429` 檢查配額；指令未出現時確認邀請包含 `applications.commands`、伺服器 ID 正確並重新啟動 Bot。

離線測試：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
