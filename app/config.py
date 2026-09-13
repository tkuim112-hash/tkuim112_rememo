from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略 .env 裡有但這裡沒定義的變數
    )

    # === LLM (Ollama) ===
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "cwchang/llama-3-taiwan-8b-instruct:q4_k_m"
    # 128k版模型的Modelfile預設num_ctx=131072，需要19.7GB記憶體，會讓Ollama
    # OOM回500（踩過一次正式環境中斷）——換128k模型時務必同時把這個值調低。
    # 2026-09-08實測：16GB顯卡上這個值超過~24576，模型就會被擠出GPU、部分
    # 改用CPU算，單次生成從1.5-3.6秒拖慢到5-7秒；24576是目前prompt實際
    # 用量（約14000 tokens）之上留足安全餘裕、又能維持整個模型留在GPU的
    # 上限（原本留8192，比實際用量還小、長期在截斷prompt前段，已調高）。
    # 部署到不同GPU的機器時，調整這個值後務必用 ollama ps 確認 PROCESSOR
    # 欄位仍是 100% GPU，避免被擠到CPU算反而更慢。
    ollama_num_ctx: int = 24576

    # === STT (faster-whisper-server) ===
    stt_host: str = "http://stt:8000"
    # 中文微調過的 Whisper checkpoint（BELLE-2），interim（即時預覽）跟
    # 最終辨識統一都用這個模型：即時預覽文字實際上只有分享頁的打字機動畫
    # 會顯示給人看（見 Unity ShareController.cs），其餘場景長者根本看不到
    # 辨識中的文字（GameController.cs／MicController.cs），BELLE-2 常駐 GPU
    # 又只處理短音訊片段，沒有必要為了 interim 額外維護一個較不準的快模型。
    stt_model: str = "XA9/Belle-faster-whisper-large-v3-zh-punct"

    # === TTS (BlueMagpie-TTS 本地語音合成) ===
    tts_host: str = "http://tts:8080"

    # === Face Emotion (py-feat，取代 Kinect 內建 Face API) ===
    face_service_host: str = "http://face-service:8000"

    # === Stability AI ===
    stability_api_key: str = ""

    # === OpenAI（gpt-image-2 生圖，見 services/image.py OpenAIImageService）===
    openai_api_key: str = ""

    # === Redis ===
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = ""
    redis_url: str = ""  # 若為空，由 build_urls 自動組裝

    # === JWT (Unity 治療師登入) ===
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60 * 12  # 12 小時

    # === CORS（允許呼叫這個後端的前端來源，逗號分隔）===
    cors_origins: str = "http://localhost:3000,https://re-memo.com"

    # === 執行環境 ===
    # 預設 production（fail-safe）：漏設這個變數時寧可docs被關掉，也不要
    # 一台忘記設定的機器意外把 /docs、/redoc、/openapi.json 曝露給公開網域
    # （這個後端會被 NEXT_PUBLIC_API_URL 指到的公開網域直接呼叫，見
    # main.py FastAPI() 建構）。本機開發要看 Swagger UI 就在 .env 設
    # ENVIRONMENT=development。
    environment: str = "production"

    # === 治療師後台的瀏覽器 session cookie（Next.js 簽的另一組 HS256 JWT）===
    # /session、/sensor 有些端點同時被 Unity（帶 Authorization: Bearer）跟
    # 治療師後台瀏覽器（帶 rememo_session cookie）呼叫，這裡驗證後者用。
    session_secret: str = ""

    # === PostgreSQL ===
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_user: str = "user"
    postgres_password: str = "password"
    postgres_db: str = "m6_db"
    postgres_dsn: str = ""  # 若為空，由 build_urls 自動組裝

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() != "development"

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.jwt_secret:
            raise ValueError(
                "JWT_SECRET 未設定：這會讓任何人都能偽造合法的治療師登入 token，"
                "請在 .env 設定 JWT_SECRET 後再啟動服務。"
            )
        if not self.session_secret:
            raise ValueError(
                "SESSION_SECRET 未設定：/session、/sensor 端點會驗證治療師後台的"
                "登入 cookie，請在 .env 設定跟 Next.js 前端相同的 SESSION_SECRET。"
            )
        if not self.redis_url:
            if self.redis_password:
                self.redis_url = f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}"
            else:
                self.redis_url = f"redis://{self.redis_host}:{self.redis_port}"
        if not self.postgres_dsn:
            self.postgres_dsn = (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self


# 建立一個全域實例，整個 app 共用
settings = Settings()