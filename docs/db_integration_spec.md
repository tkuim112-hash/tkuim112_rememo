# Rememo 資料庫串接規格書

**版本**：v1.0  
**日期**：2026-06-25  
**分支**：`feature/client`  
**負責人**：待分工（見各模組）

---

## 1. 背景與目標

永久資料庫（PostgreSQL）已就緒，現有程式碼有以下問題需要解決：

| 問題 | 現況 | 目標 |
|------|------|------|
| 長者資料 | `MockUserProfileClient` 假資料 | 從 DB `patients` 表讀取 |
| Session 寫入 | 寫入舊的 `therapy_sessions` 表（欄位不符）| 寫入正確的 `sessions` 表 |
| Rounds 寫入 | 完全未實作 | 療程結束時批次從 Redis 寫入 |
| Auth | 無認證機制 | JWT 登入（機構 / 治療師）|
| DB 連線 | `asyncpg` pool（main.py）與 SQLAlchemy（db/session.py）並存 | 統一為 SQLAlchemy AsyncSession |
| patient_id 格式 | 字串（`"user_001"`）| DB 整數 ID（`patients.id`）|

---

## 2. 技術決策

### 2.1 DB 連線統一 → SQLAlchemy AsyncSession ✅ 已完成

- **保留**：`app/db/session.py`（engine + AsyncSessionLocal）
- **保留**：`app/db/models.py`（ORM models）
- ~~**移除**：`main.py` 裡的 `asyncpg.create_pool` 與手動建表邏輯~~ → **已完成**
- **注入方式**：FastAPI `Depends(get_db)` pattern（`app/db/deps.py` 已存在）

```python
# app/db/deps.py（新增此檔）
from db.session import AsyncSessionLocal

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

### 2.2 Auth → JWT（Access + Refresh Token）

| 項目 | 設定 |
|------|------|
| 演算法 | HS256 |
| Access Token 有效期 | 30 分鐘 |
| Refresh Token 有效期 | 7 天 |
| 傳遞方式 | `Authorization: Bearer <token>` header |
| Refresh Token 儲存 | Redis（key: `refresh:{token_hash}`，TTL 7天）|
| 依賴套件 | `python-jose[cryptography]`, `passlib[bcrypt]` |

選擇 JWT 的原因：Unity 客戶端不支援 Cookie；不需要額外 session store 基礎設施。

### 2.3 patient_id 格式 → DB 整數 ID

Unity / 前端必須傳 `patients.id`（整數）作為 `patient_id`。  
`session_id` 維持 Unity 端產生的 UUID 字串，在 `SESSIONS` 表新增 `session_uuid TEXT UNIQUE` 欄位對應（見 §4.3）。

---

## 3. 資料庫 Schema 補充說明

使用 `database/database/m6_db_schema.sql`，補充以下欄位與索引。

> **注意**：`session_uuid` 欄位已定義於 `app/db/models.py`。若 DB 是透過 `Base.metadata.create_all` 建立（即標準 Docker 部署），則不需要執行 ALTER TABLE。
> 只有在 DB 是以舊版手動 SQL 建立的情況下，才需要執行下列 ALTER TABLE：

```sql
-- 僅舊版手動建表的 DB 才需執行：
ALTER TABLE sessions ADD COLUMN session_uuid TEXT UNIQUE;

-- 建議索引
CREATE INDEX idx_sessions_patient ON sessions(patient_id);
CREATE INDEX idx_sessions_therapist ON sessions(therapist_id);
CREATE INDEX idx_rounds_session ON rounds(session_id);
CREATE INDEX idx_patients_org ON patients(organization_id);
CREATE INDEX idx_therapists_org ON therapists(organization_id);
CREATE INDEX idx_pwd_reset_email ON password_reset_codes(email);
```

---

## 4. 各模組規格

### M1：Auth 模組

**負責人**：_____  
**新增檔案**：`app/routers/auth.py`、`app/services/auth.py`

#### 4.1.1 機構登入

```
POST /auth/org/login
Content-Type: application/json
Body: { "email": str, "password": str }

Response 200:
{
  "access_token": str,
  "refresh_token": str,
  "token_type": "bearer",
  "org_id": int,
  "org_name": str
}

Error:
  401 → 帳號或密碼錯誤
```

#### 4.1.2 治療師登入

```
POST /auth/therapist/login
Content-Type: application/json
Body: { "email": str, "password": str }

Response 200:
{
  "access_token": str,
  "refresh_token": str,
  "token_type": "bearer",
  "therapist_id": int,
  "therapist_name": str,
  "org_id": int
}

Error:
  401 → 帳號或密碼錯誤
```

#### 4.1.3 Token 刷新

```
POST /auth/refresh
Body: { "refresh_token": str }

Response 200:
{
  "access_token": str,
  "token_type": "bearer"
}

Error:
  401 → refresh_token 已過期或不存在
```

#### 4.1.4 登出

```
POST /auth/logout
Headers: Authorization: Bearer <access_token>
Body: { "refresh_token": str }

Response 200: { "ok": true }
（將 refresh_token 從 Redis 刪除）
```

#### 4.1.5 密碼重置（三步驟）

```
# Step 1: 申請驗證碼（寫入 PASSWORD_RESET_CODES）
POST /auth/password-reset/request
Body: { "email": str }
Response 200: { "ok": true }
（無論信箱是否存在都回 200，避免帳號枚舉攻擊）
實作：產生 6 碼數字碼，寫入 password_reset_codes，expires_at = now + 10分鐘
      寄送 email（使用 SMTP 或第三方服務，key 放 .env）

# Step 2: 驗證碼確認
POST /auth/password-reset/verify
Body: { "email": str, "code": str }
Response 200: { "reset_token": str }  ← 一次性 token，有效 15 分鐘，存 Redis
Error:
  400 → 驗證碼錯誤或已過期

# Step 3: 設定新密碼
POST /auth/password-reset/confirm
Body: { "reset_token": str, "new_password": str }
Response 200: { "ok": true }
Error:
  400 → reset_token 無效或已使用
  422 → 密碼強度不足（最少 8 字元）
```

#### 4.1.6 Auth 服務內部邏輯（`app/services/auth.py`）

```python
# 必須實作的函式
verify_password(plain: str, hashed: str) -> bool
hash_password(plain: str) -> str
create_access_token(data: dict, expires_delta: timedelta) -> str
create_refresh_token(data: dict) -> str  # 同時寫入 Redis
decode_token(token: str) -> dict  # 回傳 payload，失效拋 401
get_current_therapist(token: str, db: Session) -> Therapist  # Depends 用
get_current_org(token: str, db: Session) -> Organization
```

#### 4.1.7 JWT Payload 結構

```json
// Therapist
{ "sub": "therapist:42", "org_id": 3, "exp": 1234567890 }

// Organization
{ "sub": "org:3", "exp": 1234567890 }
```

---

### M2：機構 & 治療師管理

**負責人**：_____  
**新增檔案**：`app/routers/org.py`、`app/routers/therapist.py`

所有端點需要有效 JWT（除了密碼重置流程）。

#### 機構 CRUD

```
POST   /orgs                → 建立機構（系統管理員，初期可不做 auth）
GET    /orgs/{org_id}       → 取得機構資料（需登入）
PUT    /orgs/{org_id}       → 更新機構資料（需為該機構 admin）
DELETE /orgs/{org_id}       → 刪除機構（系統管理員）
```

#### 治療師 CRUD

```
POST   /therapists                  → 建立治療師（需機構登入）
GET    /therapists/{therapist_id}   → 取得治療師資料
PUT    /therapists/{therapist_id}   → 更新治療師資料（本人或機構 admin）
DELETE /therapists/{therapist_id}   → 刪除治療師（機構 admin）
GET    /orgs/{org_id}/therapists    → 列出機構所有治療師
```

**密碼處理**：所有密碼存入前必須 `bcrypt hash`，絕對不存明文。

---

### M3：患者管理（取代 MockUserProfileClient）

**負責人**：_____  
**新增 / 修改**：`app/routers/patient.py`、`app/services/db_user_profile_client.py`

#### 4.3.1 患者 CRUD

```
POST   /patients             → 建立患者（需治療師 JWT）
GET    /patients/{patient_id}→ 取得患者資料（*這個是 orchestrator 要用的）
PUT    /patients/{patient_id}→ 更新患者資料
DELETE /patients/{patient_id}→ 刪除患者
GET    /orgs/{org_id}/patients → 列出機構患者
```

#### 4.3.2 GET /patients/{patient_id} 回傳格式

此格式必須符合 `UserProfile` TypedDict（`app/services/user_profile_client.py`），  
使 orchestrator 的 `await self.user_profile.get_user(user_id)` 不需要改動。

```json
{
  "user_id": "42",
  "name": "王大明",
  "avatar_path": null,
  "birth_year": 1945,
  "birth_place": "台中",
  "main_occupation": "紡織廠工人",
  "family": [
    { "name": "王小美", "relation": "女兒" }
  ],
  "preferences": {
    "foods": ["肉圓"],
    "places": ["逢甲夜市"],
    "festivals": ["中秋節"]
  },
  "taboos": ["二兒子車禍"],
  "today_topic": "運動會",
  "topic_category": ["休閒", "童年經歷"]
}
```

> **DB 欄位對應說明**：
> - `user_id` → `str(patients.id)`
> - `birth_place` → DB 的 `hometown`
> - `main_occupation` → DB 的 `occupation`
> - `taboos` → `json.loads(patient.taboo_words or "[]")`（DB 欄位名為 `taboo_words`，`_to_profile` 轉換時需改名）
> - `family` → `json.loads(patient.family or "[]")`
> - `preferences` → `json.loads(patient.preferences or "{}")`
>
> `today_topic`、`topic_category` 目前不在 DB schema 中——**有兩個選項**：
> - 選項 A：在 sessions 表的 `start_scene` 欄位記錄（治療師開始療程時指定）
> - 選項 B：在 patients 表新增 `today_topic TEXT`（每次療程前治療師更新）
>
> **建議選項 B**，並加入 `topic_category TEXT`（JSON array 存字串）。  
> **待團隊確認後實作。**

#### 4.3.3 DBUserProfileClient 實作

```python
# app/services/db_user_profile_client.py
class DBUserProfileClient:
    """真實版 UserProfileClient，從 PostgreSQL 查詢。"""
    def __init__(self, db_session_factory):
        self._factory = db_session_factory

    async def get_user(self, user_id: str) -> UserProfile | None:
        async with self._factory() as db:
            patient = await db.get(Patient, int(user_id))
            if not patient:
                return None
            return _to_profile(patient)

    async def close(self) -> None:
        pass
```

`main.py` 中把 `MockUserProfileClient()` 換成 `DBUserProfileClient(AsyncSessionLocal)`。

---

### M4：療程串接

**負責人**：_____  
**修改檔案**：`app/routers/session.py`、`app/main.py`

#### 4.4.1 資料流總覽

```
Unity 呼叫 POST /session/start
  → 在 DB sessions 表 INSERT，取得 sessions.id（整數）
  → 同時在 Redis 寫 session:{session_uuid}:meta
  → 回傳 db_session_id（整數）給 Unity

Unity 持有兩個 ID：
  - session_uuid（Unity 自己產的 UUID，用於後續所有 /session/ API）
  - db_session_id（DB 整數 ID，供治療師後台查詢用）

療程進行中：Rounds 資料存在 Redis

GET /session/{session_uuid}/assessment
  → 從 Redis 讀 stats
  → 計算五指標分數
  → UPDATE sessions 表（score_*, total_score, story_summary, therapist_note）
  → INSERT rounds 表（批次寫入所有回合資料）
  → 清理 Redis 暫存
```

#### 4.4.2 修改 POST /session/start 與 /session/round

**新增參數**：`patient_id: int`（必填，DB 的 `patients.id`）  
**參數改名**：`user_id: str` → `patient_id: int`、`session_id: str` → `session_uuid: str`（兩個參數都要改）  
**新增行為**：在 `sessions` 表 INSERT 一筆記錄

> 目前 `session.py` 的簽名是 `session_start(request, user_id: str, session_id: str, therapist_id: str = "")`，
> 修改後如下：

```python
# session.py 修改後的 session_start
@router.post("/start")
async def session_start(
    request: Request,
    patient_id: int,           # 原 user_id: str，改為 int 整數 ID
    session_uuid: str,         # 原 session_id: str，改名
    therapist_id: int = 0,     # 原 therapist_id: str，改為 int
    db: AsyncSession = Depends(get_db),
):
    # 1. 在 DB 建立 session 記錄
    new_session = TherapySession(
        patient_id=patient_id,
        therapist_id=therapist_id or None,
        organization_id=...,   # 從 therapist 查詢
        date=date.today(),
        mode="interactive",
        session_uuid=session_uuid,
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    db_session_id = new_session.id

    # 2. 寫 Redis meta
    await _init_session_meta(r, session_uuid, patient_id, therapist_id)

    # 3. 呼叫 orchestrator
    result = await orchestrator.start_round(
        user_id=str(patient_id),
        session_id=session_uuid,
        round_number=1,
    )
    result["db_session_id"] = db_session_id
    return result
```

#### 4.4.3 修改 GET /session/{session_id}/assessment

**目標**：
1. 計算五指標（現有邏輯保留）
2. UPDATE `sessions` 表對應記錄（用 `session_uuid` 查詢）
3. INSERT 所有 Rounds 記錄

```
Rounds 資料來源（Redis）：
  session:{session_uuid}:rounds → Hash，key = round_number（1/2/3）
  每個 round 存：
    response_time   FLOAT  （平均回應時間，秒）
    emotion         TEXT   （該回合主要情緒）
    generated_scene TEXT   （STEP1 生成的場景文字）
    patient_response TEXT  （該回合長者說話的完整文字，多段合併）

目前 Redis 未記錄 Rounds 細節 → 需要在 /session/respond 與 /session/response
端點補充寫入 Redis。
```

#### 4.4.4 補充寫入 Rounds 細節到 Redis（新增邏輯）

在 `POST /session/respond`（session.py 的 `session_respond`）：  
每次 orchestrator 回傳 `end_round` 或 `end_session` 時，在 Redis 記錄當回合摘要。

```
Redis key: session:{uuid}:round:{n}:summary
Fields:
  generated_scene    → orchestrator start_round 回傳的 scene_text
  patient_response   → 累積的長者發言（由 /session/{id}/response 累積）
  dominant_emotion   → 由 /session/{id}/metrics 的 emotion 欄位取最後一筆
  avg_response_time  → 從 stats hash 的 response_time 計算
```

> **型別注意**：`_init_session_meta` 寫入 Redis 時，`patient_id` 必須以 `str(patient_id)` 存入（Redis 不儲存 Python int）。
> `session_assessment` 讀取時透過 `_to_int()` 轉回整數，現有實作已支援此行為，勿改為直接存 int。

#### 4.4.5 Sessions 表完整欄位對應

| DB 欄位 | 資料來源 |
|---------|---------|
| patient_id | Unity 傳入 patient_id |
| therapist_id | Unity 傳入 therapist_id |
| organization_id | 從 therapist 記錄查詢 |
| date | 療程當天日期 |
| mode | 固定 `"interactive"` |
| start_scene | orchestrator 第一回合 today_topic |
| score_participation | `_score_engagement()` |
| score_attention | `_score_attention()` |
| score_endurance | `_score_persistence()` |
| score_emotion | `_score_emotion()` |
| score_interaction | `_score_interaction()` |
| total_score | 五項分數加總 |
| therapist_note | 選填，後台填寫（assessment 後 PUT）|
| story_summary | 未來由 LLM 生成摘要（暫時 NULL）|
| session_uuid | Unity UUID session_id |

---

### M5：DB 連線整合 ✅ 已完成

**負責人**：_____  
**修改檔案**：`app/main.py`、`app/db/session.py`

#### 4.5.1 main.py lifespan 修改 ✅ 已完成

`asyncpg.create_pool` 與手動建表邏輯已於 `app/main.py` 移除，目前 lifespan 已是：

```python
from db.session import engine, AsyncSessionLocal
from db.models import Base

# lifespan 中
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
print("✅ PostgreSQL (SQLAlchemy) 連線成功")
```

`asyncpg` 套件保留（`sqlalchemy[asyncio]` 底層依賴），但程式碼中不直接 `import asyncpg`。

#### 4.5.2 需要新增的套件

```
# requirements.txt 新增
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
```

---

## 5. API 端點完整清單

### Auth
| Method | Path | 說明 | Auth 需求 |
|--------|------|------|-----------|
| POST | `/auth/org/login` | 機構登入 | 無 |
| POST | `/auth/therapist/login` | 治療師登入 | 無 |
| POST | `/auth/refresh` | 刷新 Access Token | 無（需 refresh_token）|
| POST | `/auth/logout` | 登出 | Bearer |
| POST | `/auth/password-reset/request` | 申請重置碼 | 無 |
| POST | `/auth/password-reset/verify` | 驗證重置碼 | 無 |
| POST | `/auth/password-reset/confirm` | 設定新密碼 | 無（需 reset_token）|

### 機構
| Method | Path | 說明 | Auth 需求 |
|--------|------|------|-----------|
| POST | `/orgs` | 建立機構 | 系統管理員（初期免）|
| GET | `/orgs/{org_id}` | 取得機構資料 | Bearer |
| PUT | `/orgs/{org_id}` | 更新機構資料 | 機構 Admin |
| DELETE | `/orgs/{org_id}` | 刪除機構 | 系統管理員 |

### 治療師
| Method | Path | 說明 | Auth 需求 |
|--------|------|------|-----------|
| POST | `/therapists` | 建立治療師 | 機構 Bearer |
| GET | `/therapists/{id}` | 取得治療師資料 | Bearer |
| PUT | `/therapists/{id}` | 更新治療師資料 | 本人或機構 |
| DELETE | `/therapists/{id}` | 刪除治療師 | 機構 |
| GET | `/orgs/{org_id}/therapists` | 列出機構治療師 | Bearer |

### 患者
| Method | Path | 說明 | Auth 需求 |
|--------|------|------|-----------|
| POST | `/patients` | 建立患者 | 治療師 Bearer |
| GET | `/patients/{patient_id}` | 取得患者資料（orchestrator 用）| Bearer |
| PUT | `/patients/{patient_id}` | 更新患者資料 | 治療師 Bearer |
| DELETE | `/patients/{patient_id}` | 刪除患者 | 治療師 Bearer |
| GET | `/orgs/{org_id}/patients` | 列出機構患者 | Bearer |

### 療程（現有 + 修改）
| Method | Path | 說明 | 變更 |
|--------|------|------|------|
| POST | `/session/start` | 啟動療程（新增 DB 寫入）| **修改** |
| POST | `/session/round` | 開始指定回合 | 小修 |
| GET | `/session/{uuid}/metrics` | 即時情緒/反應 | 不變 |
| POST | `/session/{uuid}/response` | 記錄 STT 文字 | 不變 |
| GET | `/session/{uuid}/assessment` | 計算分數並寫 DB | **大改** |
| POST | `/session/respond` | 長者回應處理 | 不變 |

---

## 6. 實作順序建議

```
Phase 1（基礎建設，其他 Phase 的前置）
  └── M5: DB 連線整合（移除 asyncpg pool，統一 SQLAlchemy）
  └── M1: Auth 服務（auth.py — hash, JWT 工具函式）

Phase 2（可平行）
  ├── M1: Auth Router（登入、登出、刷新）
  ├── M2: 機構 & 治療師 CRUD
  └── M3: 患者 CRUD + DBUserProfileClient

Phase 3（依賴 Phase 2）
  └── M4: 療程串接（session start DB 寫入 + assessment 改寫）

Phase 4（整合測試）
  └── M1: 密碼重置（需要 email 服務配置）
  └── 全流程 E2E 測試
```

---

## 7. 待確認事項（Blockers）

| # | 問題 | 負責確認 | 截止 |
|---|------|---------|------|
| 1 | `today_topic` / `topic_category` 要存在 patients 表還是 sessions 表？| 全體 | — |
| 2 | Unity 端是否已改為傳 integer `patient_id`？| Unity 負責人 | — |
| 3 | 密碼重置 email 用哪個服務（SMTP/SendGrid/...）？| PM | — |
| 4 | 系統管理員（建立機構）的 auth 策略？| PM | — |
| 5 | `story_summary` 何時生成？（LLM 批次？即時？）| 後端負責人 | — |

---

## 8. 環境變數補充（.env.example 需更新）

```env
# JWT
JWT_SECRET_KEY=your-secret-key-min-32-chars
JWT_ALGORITHM=HS256
JWT_ACCESS_EXPIRE_MINUTES=30
JWT_REFRESH_EXPIRE_DAYS=7

# Email（密碼重置）
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=noreply@rememo.tw
```
