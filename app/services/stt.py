"""
STT 服務客戶端 — 跟 faster-whisper-server 容器溝通。
faster-whisper-server 提供 OpenAI 相容的 API,
所以這裡直接用 httpx 呼叫 /v1/audio/transcriptions 端點。
"""
import httpx
from pathlib import Path
from config import settings


class STTService:
    """faster-whisper-server 客戶端。"""

    def __init__(self):
        self.host = settings.stt_host
        self.model = settings.stt_model
        # 音訊轉錄可能很慢(尤其是大檔),timeout 設長一點
        self.client = httpx.AsyncClient(timeout=120.0)

    async def transcribe_file(self, audio_path: str | Path, language: str = "zh") -> str:
        """
        從檔案路徑轉錄一段音訊。

        Args:
            audio_path: 音訊檔案路徑(wav/mp3 等)
            language: 語言代碼,預設 "zh"(中文)

        Returns:
            轉錄出的文字
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"找不到音訊檔: {audio_path}")

        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/wav")}
            data = {
                "model": self.model,
                "language": language,
                "response_format": "json",
            }
            response = await self.client.post(
                f"{self.host}/v1/audio/transcriptions",
                files=files,
                data=data,
            )

        response.raise_for_status()
        return response.json()["text"]

    async def transcribe_bytes(self, audio_bytes: bytes, filename: str = "audio.wav", language: str = "zh") -> str:
        """
        從 bytes 轉錄一段音訊(之後 WebSocket 收到 audio chunks 時會用)。
        """
        files = {"file": (filename, audio_bytes, "audio/wav")}
        data = {
            "model": self.model,
            "language": language,
            "response_format": "json",
        }
        response = await self.client.post(
            f"{self.host}/v1/audio/transcriptions",
            files=files,
            data=data,
        )
        response.raise_for_status()
        return response.json()["text"]

    async def close(self):
        await self.client.aclose()