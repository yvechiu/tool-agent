# 工具代理與 AI引導者的階段串接

這份文件描述工具代理端已提供的介面。AI引導者端仍須實作呼叫；不代表兩個系統已完成連線。

## 行為

- 階段切換只接受受驗證的後端 API，不在 Discord 發布通知。
- 參與者與授權 AI引導者皆可 `@工具代理 <本次整理要求>`。
- 呼叫文字與整理要求不列為參與者討論，也不增加參與計數；摘要不再顯示或要求參與度分析。
- 只有目前階段的參與者文字會送給 Gemini。不把先前階段、其他頻道、其他 Bot 的引導發言或工具代理回覆送進摘要。
- `@工具代理 摘要` 使用預設架構；`@工具代理 請整理機會方向並保留分歧` 則使用該次自訂要求。全形 `＠`、正式使用者提及及同名角色提及都支援。
- 同階段可重複摘要，不會清空資料或切換階段。
- 沒有階段：提示「尚未開始階段」，不回退為整頻道摘要。階段前訊息不會補收。
- 階段沒有對話：提示「本階段尚無可摘要的參與者對話」，不呼叫 Gemini。
- 聊天中的 `@工具代理 開始階段 ...` 只會回覆提示，不改變階段（AI引導者 Bot 也不能用聊天切換）。
- `/create-board` 與 `/analyze-board` 保持原有的頻道綁定；這次階段範圍只適用於聊天摘要，不會自動切換或清空白板。

## 工具代理設定

在工具代理根目錄 `.env` 加入，保留原有 Discord／Gemini／白板設定：

```dotenv
DISCORD_GUILD_ID=1499044385793507350
PHASE_SYNC_TOKEN=自行產生的至少32字元隨機密碼
PHASE_CONTROL_HOST=127.0.0.1
PHASE_CONTROL_PORT=5860
PHASE_DB_PATH=data/phases.sqlite3
```

`PHASE_SYNC_TOKEN` 不能留空：留空時 API 不會啟動，新環境也就不能開始階段。
這是工具代理與 AI引導者後端之間的新密碼，不要重用白板密碼或其他 API key。
本機產生方式（輸出只留在自己的電腦，不要貼到聊天）：

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Windows 更新後，在有 `app.py` 的 `tool-agent` 資料夾執行：

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -u app.py
```

只運行一個工具代理實例。控制 API 與 Bot 由同一個程序啟動，不需額外開一個 API 視窗。
啟動時應看到 `Phase control API started on port 5860`，並等待 `Bot 登入成功` 才呼叫切換。

## AI引導者設定與請求

AI引導者的**後端**設定：

```dotenv
TOOL_AGENT_CONTROL_URL=http://127.0.0.1:5860
PHASE_SYNC_TOKEN=與工具代理端完全相同的密碼
```

同機本機測試用 loopback HTTP。不同主機的 `127.0.0.1` 不相通，必須另設後端可到達的 HTTPS 或受保護私人網路位址；不要直接公開未保護的 HTTP 連接埠。
不要將密碼放到瀏覽器前端、Git、URL 或日誌。

### 開始／切換階段

```http
POST /internal/v1/phase-transitions
Authorization: Bearer <PHASE_SYNC_TOKEN>
Content-Type: application/json
```

```json
{
  "event_id": "唯一事件ID",
  "experiment_id": "實驗場次ID",
  "guild_id": "1499044385793507350",
  "channel_id": "本次實驗頻道ID",
  "phase_id": "本次階段唯一ID",
  "phase_name": "發想",
  "sequence": 1
}
```

Discord ID 必須是純數字字串；第一階段 `sequence=1`，下一階段為 2，依此類推。
階段名稱可重複，但 `phase_id` 每次切換須是新 ID；每個頻道綁定一個實驗場次，新的實驗請建立新頻道。
工具代理會透過 Discord 確認頻道存在於允許的伺服器、為文字頻道／討論串且對 Bot 可見。

成功 `200`：

```json
{
  "status": "applied",
  "event_id": "唯一事件ID",
  "channel_id": "本次實驗頻道ID",
  "phase_id": "本次階段唯一ID",
  "sequence": 1,
  "effective_at": "工具代理實際套用時的UTC時間"
}
```

請先同步成功，再在 AI引導者介面標示「已開始」並發送新階段引導內容。
階段起點是**工具代理實際處理切換的時刻**，不回溯舊訊息。API 與訊息處理使用同一把頻道鎖，依處理順序劃分。
若該頻道正在摘要，API 會等待摘要完成；逾時請沿用原事件重試，不能另建事件。

- `400`：欄位格式錯誤。
- `401`：缺少／錯誤密碼。
- `403`：伺服器不在允許範圍。
- `409`：事件內容衝突、序號不連續、重用 phase_id 或頻道已綁定不同實驗。
- `503`：Bot 尚未就緒，或頻道無法存取；確認設定後再有限次重試。

重試必須使用**相同 event_id 與完全相同 payload**。API 將回傳原成功結果，不重設起點；即使後來已切到下一階段，重試舊事件也不會倒退階段。
AI引導者需持久化待處理事件與狀態，不能只放在 Streamlit session。只有成功通知後才更新前台狀態。

### 查詢目前階段

```http
GET /internal/v1/channels/<guild_id>/<channel_id>/phase
Authorization: Bearer <PHASE_SYNC_TOKEN>
```

回傳目前階段的 experiment_id、phase_id、phase_name、sequence、effective_at。尚無階段時回 `404`。
若重試舊事件得到原結果，不代表它仍是目前階段；使用這個查詢確認。

### Python 後端呼叫範例

```python
import os
import httpx

async def sync_phase(event):
    # event 必須先持久化；重試時沿用，不能在本函式內重建 ID。
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            os.environ['TOOL_AGENT_CONTROL_URL'].rstrip('/')
            + '/internal/v1/phase-transitions',
            headers={'Authorization': 'Bearer ' + os.environ['PHASE_SYNC_TOKEN']},
            json=event,
        )
        response.raise_for_status()
        return response.json()
```

此範例不含 UI、重試／待處理事件持久化；請由 AI引導者系統整合。不要將網頁使用者的任意 payload 直接轉發，後端應確認主持者權限與實驗／頻道對應。

## 保存與限制

階段、切換事件與參與者對話存在 `PHASE_DB_PATH` 的 SQLite 檔案，重啟 Bot 可恢復。檔案在 Git 忽略的 `data/` 下，但不是加密資料庫，請保護檔案權限、備份並依研究的同意與保存政策清理。若改變路徑或刪除資料庫，會失去既有資料。

白板及其頻道綁定仍沿用原有記憶體保存方式，重啟後需重新建立；這次並未實作白板持久化。
不補讀 Bot 離線期間、階段開始前或被忽略的 Bot 訊息。超長階段可能超出 Gemini 上下文或配額；這次不截斷資料冒充完整摘要。

超過 Discord 單則顯示限制的摘要會截短預覽並附完整 `phase_summary.md`。
摘要錯誤不會換階段。尚未使用實際 Discord／Gemini 金鑰驗證跨系統流程；需由 AI引導者端完成呼叫後實測。
