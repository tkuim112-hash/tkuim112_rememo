"""
TTS Service - 呼叫本地 BreezyVoice 服務（MediaTek Research 台灣繁中語音）
透過 HTTP POST /v1/audio/speech，回傳 WAV bytes 存成檔案。

長句會斷句分次合成再拼接，避免 BreezyVoice 長句唸不完整的問題。
"""
import io
import re
import wave
from pathlib import Path

import httpx

from config import settings

#  OpenCC：繁體 → 簡體（BreezyVoice 底層是簡體模型） 
try:
    import opencc
    _converter = opencc.OpenCC("tw2sp")
except ImportError:
    _converter = None


def _to_simplified(text: str) -> str:
    """把輸入的繁體字轉成簡體，讓 BreezyVoice 正確處理。"""
    if _converter is None:
        return text
    return _converter.convert(text)

# 每段最多幾個字（中文字符），超過就切斷
_MAX_CHUNK_CHARS = 20


class TTSService:
    """呼叫 BreezyVoice TTS 服務"""

    def __init__(self, output_dir: str = "/media/audio"):
        self.base_url = settings.tts_host
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── 斷句 ──────────────────────────────────────────────────────

    def _split_sentences(self, text: str) -> list[str]:
        """在標點符號處切句，每段不超過 _MAX_CHUNK_CHARS 字。"""
        parts = re.split(r"(?<=[，。！？、；：])", text)
        chunks = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            while len(part) > _MAX_CHUNK_CHARS:
                chunks.append(part[:_MAX_CHUNK_CHARS])
                part = part[_MAX_CHUNK_CHARS:]
            if part:
                chunks.append(part)
        return chunks if chunks else [text]

    # ── WAV 拼接 ──────────────────────────────────────────────────

    def _merge_wav_bytes(self, wav_list: list[bytes]) -> bytes:
        """把多個 WAV bytes 串接成一個 WAV（採用第一個檔案的 header 參數）。"""
        if len(wav_list) == 1:
            return wav_list[0]

        with wave.open(io.BytesIO(wav_list[0])) as w:
            params = w.getparams()

        all_frames = b""
        for wav_bytes in wav_list:
            with wave.open(io.BytesIO(wav_bytes)) as w:
                all_frames += w.readframes(w.getnframes())

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setparams(params)
            w.writeframes(all_frames)
        return buf.getvalue()

    # ── 主要介面 ──────────────────────────────────────────────────

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
        長句自動斷句分次合成再拼接。
        Returns:
            檔案路徑，例如 /media/audio/sess_001/round_1_turn_2.wav
        """
        session_dir = self.output_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        if turn_number is None:
            filename = f"round_{round_number}.wav"
        else:
            filename = f"round_{round_number}_turn_{turn_number}.wav"

        filepath = session_dir / filename

        chunks = self._split_sentences(_to_simplified(text))
        wav_parts: list[bytes] = []

        async with httpx.AsyncClient(timeout=60.0) as client:
            for chunk in chunks:
                response = await client.post(
                    f"{self.base_url}/v1/audio/speech",
                    json={"model": "", "input": chunk},
                )
                response.raise_for_status()
                wav_parts.append(response.content)

        merged = self._merge_wav_bytes(wav_parts)
        filepath.write_bytes(merged)

        return str(filepath)

    async def close(self):
        pass