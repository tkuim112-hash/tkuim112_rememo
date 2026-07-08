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
    ollama_model: str = "rememo-llama3"

    # === STT (faster-whisper-server) ===
    stt_host: str = "http://kinect:8000"
    stt_model: str = "Systran/faster-whisper-large-v3"

    # === TTS (CosyVoice 已停用，改用 Edge-TTS) ===
    tts_host: str = "http://tts:8080"

    # === Stability AI ===
    stability_api_key: str = ""

    # === Redis ===
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = ""
    redis_url: str = ""  # 若為空，由 build_urls 自動組裝

    # === JWT (Unity 治療師登入) ===
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60 * 12  # 12 小時

    # === PostgreSQL ===
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_user: str = "user"
    postgres_password: str = "password"
    postgres_db: str = "m6_db"
    postgres_dsn: str = ""  # 若為空，由 build_urls 自動組裝

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.jwt_secret:
            raise ValueError(
                "JWT_SECRET 未設定：這會讓任何人都能偽造合法的治療師登入 token，"
                "請在 .env 設定 JWT_SECRET 後再啟動服務。"
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