"""
TTS Service - 呼叫本地 BreezyVoice 服務（MediaTek Research 台灣繁中語音）
透過 HTTP POST /v1/audio/speech，回傳 WAV bytes 存成檔案。
"""
from pathlib import Path
from config import settings
import httpx

class TTSService:
    """呼叫 BreezyVoice TTS 服務"""

    def __init__(self, output_dir: str = "/media/audio"):
        self.base_url = settings.tts_host
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def synthesize(
        self,
        text: str,
        session_id: str,
        round_number: int,
        turn_number: int | None = None,
        voice: str | None = None,
        rate: str | None = None,
    ) -> str:
        """
        合成語音存成 wav，回傳檔案路徑。
        Returns:
            檔案路徑，例如 media/audio/sess_001/round_1_turn_2.wav
        """
        session_dir = self.output_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        if turn_number is None:
            filename = f"round_{round_number}.wav"
        else:
            filename = f"round_{round_number}_turn_{turn_number}.wav"

        filepath = session_dir / filename

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/audio/speech",
                json={"model": "", "input": text},
            )
            response.raise_for_status()
            filepath.write_bytes(response.content)

        return str(filepath)

    async def close(self):
        pass