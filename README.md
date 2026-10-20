
# Discord CPSAgent

Windows 本機測試請見 [Windows 設定指南](WINDOWS_SETUP.md)。AI 功能已改為使用 `GEMINI_API_KEY`，不需要 OpenAI key。
Discord CPSAgent is a collaborative brainstorming and discussion analysis tool developed for CPS (Collaborative Problem Solving) research.

The project integrates Discord, tldraw, and Fly.io to provide an interactive environment where users can collaborate on shared whiteboards, organize ideas using sticky notes, and generate AI-powered discussion summaries.

## 1. Technologies Used

### (1) Discord Chatbot

Discord serves as the main interface for user interaction and collaboration.

The chatbot is developed by Python, supporting Slash Commands to manage collaborative whiteboards and trigger AI analysis.

**Key Features:**
- `/create-board` — Create a new collaborative whiteboard and return its URL.
- `/analyze-board` — Retrieve sticky notes from a whiteboard and generate an AI-powered analysis.
- **Discussion Monitoring** — Analyze conversations and user participation within Discord channels.
- **AI-Powered Summarization** — Generate discussion summaries, identify key ideas, and suggest potential action items.

### (2) tldraw React Whiteboard SDK

[tldraw](https://tldraw.dev/) is a React-based whiteboard SDK that provides an interactive canvas for real-time collaboration.

It allows users to freely create, edit, and organize sticky notes on shared whiteboards.

**Key Features:**
- **Sticky Notes** — Create and organize ideas using editable notes.
- **Multi-User Collaboration** — Synchronize whiteboard changes in real time through WebSocket connections.
- **Structured Data Access** — Retrieve whiteboard content as structured JSON data for backend analysis.

### (3) Fly.io

[Fly.io](https://fly.io/) is a cloud application platform used to deploy and host the project's services.

The system uses two independent Fly.io applications:

| Application | Description |
|---|---|
| **Discord CPSAgent Bot** | Runs the Python Discord chatbot as a background worker, handling user commands and LLM analysis. |
| **tldraw Whiteboard** | Hosts the React frontend and Node.js backend, providing REST APIs and real-time WebSocket synchronization. |

**Key Features:**
- **Cloud Deployment** — Host application services independently.
- **HTTPS / WebSocket** — Support secure API communication and real-time collaboration.
- **Public Whiteboard Access** — Allow users to access collaborative whiteboards through shared URLs.
- **Independent Services** — Deploy and manage the Discord chatbot and whiteboard backend separately.

## 2. System Architecture

The system consists of three main components:

1. **Discord Bot (Python):** Handles Slash Commands, user interactions, and AI-powered analysis.
2. **Whiteboard Service (React + Node.js):** Provides the collaborative canvas, REST APIs, and WebSocket synchronization.
3. **Fly.io:** Hosts the Discord Bot and Whiteboard Service as separate cloud applications.

![System Structure](images/systemStructure.png)


### Workflow

1. A user enters `/create-board` in a Discord channel.
2. The Discord Bot requests a new whiteboard through the backend REST API.
3. The user receives a whiteboard URL and shares it with other participants.
4. Multiple users collaborate by creating and editing sticky notes in real time.
5. A user enters `/analyze-board` to retrieve the current whiteboard content.
6. The backend passes the extracted sticky notes to an LLM for analysis.
7. The Discord Bot returns the analysis results to the channel.

![demo-create-board](images/demo-create-board.png)
![demo-analyze-board](images/demo-analyze-board.png)
![Whiteboard](images/Whiteboard.png)

## 3. Project Goals

This project aims to explore how AI-assisted tools can support **Collaborative Problem Solving (CPS)** by:

- Facilitating real-time idea sharing and group brainstorming.
- Collecting structured discussion content from collaborative whiteboards.
- Analyzing group discussions and identifying key insights.
- Evaluating participation and collaboration patterns.
- Supporting AI-assisted reflection and discussion summarization.

The project is designed as a research-oriented prototype for exploring AI-supported collaborative learning and problem-solving environments.

## 頻道獨立運作（tool-agent）

討論紀錄、訊息序號、參與度與摘要依 `(伺服器 ID, 頻道 ID)` 分開處理。
新建立的頻道與討論串會自動使用獨立紀錄，不依賴名稱或事先登記 ID。
同頻道的 AI 處理會依序執行，不同頻道可以同時進行。

- `!ping`：在目前頻道確認連線。
- 明確 @提及機器人：只摘要目前頻道自啟動後收集的訊息。
- `/create-board title:...`：建立白板並綁定目前頻道，可建立多張。
- `/analyze-board`：省略 `board` 時使用本頻道最新白板；也可指定本頻道建立的白板 URL 或 UUID。
  不接受其他頻道建立的白板。預設結果僅操作者可見，`private:False` 則回覆目前頻道。

Bot 必須具有各頻道的查看、發送、嵌入連結與附加檔案權限，並在 Developer Portal
啟用 Message Content Intent。私人新頻道需讓本 Bot 的角色取得權限。
`DISCORD_GUILD_ID=1499044385793507350` 可用於快速同步該伺服器的指令；此變數不是頻道存取限制。
其他機器人訊息仍會忽略；私人訊息不納入討論紀錄。

目前紀錄與白板綁定存於單一 Bot 程序的記憶體：重啟後清空，舊白板不能再透過此 Bot 分析，
需要重新建立白板；白板後端重啟也會清空白板內容。請只執行一個 Bot 實例。
頻道隔離不是白板網站的存取權限：持有白板網址的人仍可開啟及編輯它。
AI 功能使用 Gemini；不會自動部署或登入 Discord。

離線測試（先安裝 `requirements.txt` 相依套件）：

```sh
python -m unittest discover -s tests -v
```

直接貼上純文字 `@工具代理 摘要` 或 `@工具代理` 也會觸發本頻道摘要，不必從 Discord 選單選取；正式 Discord @提及仍支援。一般聊天不自動呼叫 AI。

Discord 的同名「工具代理」角色提及也可觸發摘要；其他角色的提及不會觸發。

## 其他 Bot 呼叫摘要

允許 Bot 使用者 ID `1560949145030893618` 在目標頻道發送
`<@1562006162856222725> 摘要` 以要求本頻道摘要。其他 Bot 仍忽略，工具代理自身訊息永不觸發。
此授權僅用於呼叫；授權 Bot 的引導發言與呼叫訊息不納入對話紀錄、參與度計數。
若沒有參與者紀錄，回覆提示而不呼叫 Gemini。兩個 Bot 都需具備目標頻道的存取權限。
