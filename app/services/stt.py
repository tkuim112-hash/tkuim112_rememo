"""
STT 服務客戶端 — 跟 faster-whisper-server 容器溝通。
faster-whisper-server 提供 OpenAI 相容的 API,
所以這裡直接用 httpx 呼叫 /v1/audio/transcriptions 端點。
"""
import httpx
from pathlib import Path
from config import settings

# ⭐ OpenCC：簡體 → 繁體(台灣正體)
try:
    import opencc
    _converter = opencc.OpenCC("s2twp")   # s2twp = 簡體→台灣繁體+慣用詞
except ImportError:
    _converter = None


def _to_traditional(text: str) -> str:
    """把 STT 輸出的簡體字轉成繁體，沒裝 opencc 就原文回傳。"""
    if _converter is None:
        return text
    return _converter.convert(text)


class STTService:
    """faster-whisper-server 客戶端。"""

    def __init__(self):
        self.host = settings.stt_host
        self.model = settings.stt_model
        self.client = httpx.AsyncClient(timeout=120.0)

    async def transcribe_file(
        self, audio_path: str | Path, language: str = "zh", model: str | None = None
    ) -> str:
        """
        從檔案路徑轉錄一段音訊。
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"找不到音訊檔: {audio_path}")

        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/wav")}
            data = {
                "model": model or self.model,
                "language": language,
                "response_format": "json",
                "vad_filter": "true",
            }
            response = await self.client.post(
                f"{self.host}/v1/audio/transcriptions",
                files=files,
                data=data,
            )

        response.raise_for_status()
        text = response.json()["text"]
        return _to_traditional(text)   # ⭐ 轉繁體

    async def transcribe_bytes(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        language: str = "zh",
        model: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """
        從 bytes 轉錄一段音訊。

        timeout: 覆蓋預設的 120 秒逾時。模型第一次被叫到時 faster-whisper-server
        要現場從 HuggingFace 下載+載入，可能遠超過 120 秒，暖機呼叫要帶長一點的值。
        """
        files = {"file": (filename, audio_bytes, "audio/wav")}
        data = {
            "model": model or self.model,
            "language": language,
            "response_format": "json",
            "vad_filter": "true",
        }
        extra = {"timeout": timeout} if timeout is not None else {}
        response = await self.client.post(
            f"{self.host}/v1/audio/transcriptions",
            files=files,
            data=data,
            **extra,
        )
        response.raise_for_status()
        text = response.json()["text"]
        return _to_traditional(text)   # ⭐ 轉繁體

    async def close(self):
        await self.client.aclose()