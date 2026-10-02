"""
TTS Service - edge-tts（微軟雲端 TTS，HsiaoYu 台灣女聲）即時生成語音，
存成 Unity LocalAudioPlayer.cs 用 AudioType.WAV 解碼所需的 wav 格式。

2026-10 稽核：原本還有一條走本地 BlueMagpie-TTS（OpenFormosa 台灣繁中語音）
的路徑，但其聲學骨幹 VoxCPM2 是中國（OpenBMB/清華）開源模型，已移除，
全部統一用這裡的 edge-tts；跟 generate_tts_cache_edge.py 產預錄音檔用
同一個 VOICE/RATE，保持長者聽到的音色一致；edge-tts 只吐得出 mp3，用
imageio_ffmpeg 內建的靜態 ffmpeg binary（不依賴容器另外裝系統 ffmpeg）
轉成 wav。
"""
import asyncio
import subprocess
from pathlib import Path

import edge_tts
import imageio_ffmpeg

_EDGE_VOICE = "zh-TW-HsiaoYuNeural"
_EDGE_RATE = "+0%"
_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


class TTSService:
    def __init__(self, output_dir: str = "/media/audio"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _filepath(self, session_id: str, round_number: int, turn_number: int | None) -> Path:
        session_dir = self.output_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        if turn_number is None:
            filename = f"round_{round_number}.wav"
        else:
            filename = f"round_{round_number}_turn_{turn_number}.wav"
        return session_dir / filename

    async def synthesize_edge(
        self,
        text: str,
        session_id: str,
        round_number: int,
        turn_number: int | None = None,
    ) -> str:
        """用 edge-tts（HsiaoYu）即時合成，存成 wav，回傳檔案路徑。"""
        filepath = self._filepath(session_id, round_number, turn_number)
        mp3_path = filepath.with_suffix(".mp3.tmp")

        communicate = edge_tts.Communicate(text, _EDGE_VOICE, rate=_EDGE_RATE)
        await communicate.save(str(mp3_path))
        try:
            # subprocess.run 是同步阻塞呼叫，會整個卡住 asyncio event loop，
            # 導致這段期間 Unity 端 /session/{id}/status polling 完全排不到隊，
            # 一路卡到轉檔完才有回應（2026-08-19 稽核發現：這是治療師按下啟動療程後，
            # 長者端沒有馬上跳轉到說明頁的根因）。丟到 thread pool 執行，
            # 不要佔用主事件迴圈。
            await asyncio.to_thread(
                subprocess.run,
                [_FFMPEG, "-y", "-i", str(mp3_path), "-ar", "24000", "-ac", "1", str(filepath)],
                check=True, capture_output=True,
            )
        finally:
            mp3_path.unlink(missing_ok=True)

        return str(filepath)

    async def close(self):
        pass
