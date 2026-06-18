from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略 .env 裡有但這裡沒定義的變數
    )

    # === LLM (Ollama) ===
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "llama3:8b-instruct-q4_K_M"

    # === STT (faster-whisper-server) ===
    stt_host: str = "http://kinect:8000"
    stt_model: str = "Systran/faster-whisper-large-v3"

    # === TTS (CosyVoice) ===
    tts_host: str = "http://tts:8188"

    # === Stability AI ===
    stability_api_key: str = ""


# 建立一個全域實例，整個 app 共用
settings = Settings()