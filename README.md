# Rememo

透過語音對話與懷舊活動陪伴長者，並提供輔導員儀表板追蹤活動紀錄。整套環境使用 Docker Compose 在本地主機上架設。LLM、STT 為本地端部署；語音合成使用 Microsoft edge-tts 雲端服務。

## 架構總覽

| 模組 | 服務 | 說明 |
|------|------|------|
| M7 系統編排器 | `app` | FastAPI，中央狀態機（[orchestrator.py](app/orchestrator.py)），串接 STT/LLM/RAG/TTS，實作三回合懷舊療法流程 |
| M2 本地大腦 | `ollama` | 本地端 LLM 推論，模型 `cwchang/llama-3-taiwan-8b-instruct:q4_k_m` |
| RAG 服務 | `rag-service` + `qdrant` | 將每位長者的對話內容向量化存入 Qdrant（`safe_reminiscence` collection），供下次療程檢索過往記憶、銜接懷舊治療上下文；embedding 模型 `jinaai/jina-embeddings-v3`（透過 sentence-transformers 直接載入 HuggingFace 權重，見 [Rag/embeddings.py](Rag/embeddings.py)），程式見 [Rag/](Rag/) |
| M3 語音合成 | edge-tts（雲端） | Microsoft edge-tts（`zh-TW-HsiaoYuNeural` 台灣女聲）即時合成場景旁白、以及輔導員自由輸入主題的開場問題（無法預錄的動態文字）語音 wav 檔；其餘固定問句模板走前端內建預錄音檔，不即時合成，供長者端 Unity 播放，見 [services/tts.py](app/services/tts.py) |
| M1 語音辨識 | `stt` | faster-whisper-server，載入 Whisper checkpoint `Systran/faster-whisper-large-v3` |
| M6 資料庫 | `db` | PostgreSQL，長者/輔導員/機構/療程資料，schema 見 [database/](database/) |
| 前端 | `frontend` | Next.js 輔導員後台：註冊/登入（含忘記密碼）、個案（長者）管理、療程紀錄查看，見 [therapist-dashboard/](therapist-dashboard/) |
| 長者互動 App | — | Unity 應用程式，輔導員以 JWT 登入後操作、帶長者進行懷舊對話與生圖，見 [Unity/Rememo/](Unity/Rememo/) |
| 實體 Kinect 感測器 | — | 骨架/姿態情緒偵測（低頭、前傾、聳肩、晃動）、可替代麥克風輸入、手勢懸停操作 UI；姿態與臉部畫面即時送 [POST /sensor/emotion](app/routers/sensor.py) 分類後存 Redis 供 LLM 語氣調整參考，見 [Unity/Rememo/.../Scripts/Kinect/](Unity/Rememo/Rememo/Assets/Scenes/Scripts/Kinect/) |
| 臉部情緒分析 | `face-service` | 用 py-feat 分析 Kinect 傳來的臉部畫面，輸出 FACS Action Unit (AU) 強度，取代 Kinect 內建 Face API；情緒判讀邏輯留在 `app/routers/sensor.py`（感測與解讀分離），見 [face-service/server.py](face-service/server.py) |
| 其他 | `redis`、`pgadmin`、`redisinsight`、`qdrant-backup`、`db-backup` | 快取、資料庫管理介面、Qdrant/PostgreSQL 自動備份 |

各服務的容器編排、環境變數與備份策略定義於 [docker-compose.yml](docker-compose.yml)（內含詳細註解說明各項設定的原因）。

## 環境需求

- Docker / Docker Compose
- 支援 NVIDIA GPU 的主機（`ollama`、`stt` 皆需要 GPU）
- WSL2（若在 Windows 主機上，需掛載 `/usr/lib/wsl/lib` 驅動路徑）

## 快速開始

1. 複製環境變數範本並依實際金鑰/密碼填入：

   ```bash
   cp .env.example .env
   ```

   `.env.example` 內列出所有必填變數，包含 Ollama、STT、TTS、Stability/OpenAI 生圖金鑰、JWT、資料庫、Redis、Qdrant、Anthropic、Resend 等設定，並附有用途說明。

2. 啟動所有服務：

   ```bash
   docker compose up -d --build
   ```

3. 首次啟動套用資料庫遷移（正式的 schema 版本控管方式，會建立 [database/m6_db_schema.sql](database/m6_db_schema.sql) 對應的完整結構）：

   ```bash
   docker compose exec app alembic upgrade head
   ```

   若需要測試資料，可另外匯入 [database/seed.sql](database/seed.sql)。

4. 服務預設連接埠：
   - 後端 API：`http://localhost:8000`（`ENVIRONMENT=development` 時開放 `/docs`）
   - 前端儀表板：`http://localhost:3000`

## 目錄結構

```
app/                  後端主服務（FastAPI）：routers、services、orchestrator、privacy、alembic migrations
Rag/                  RAG 服務：長者對話向量化 ingest（CKIP 斷詞）與檢索（Qdrant + jina-embeddings-v3 embedding）
face-service/         臉部情緒分析服務（py-feat FACS Action Unit 強度分析，取代 Kinect 內建 Face API）
therapist-dashboard/  輔導員後台前端（Next.js）
Unity/Rememo/         長者互動 Unity 應用程式（輔導員登入操作）
database/             M6 資料庫 schema（m6_db_schema.sql）與測試資料（seed.sql）
media/                執行期產生的圖片/語音檔案（掛載進 app 容器，經 /images 靜態路由對外提供）
```
