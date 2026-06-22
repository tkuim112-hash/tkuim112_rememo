"""
TTS Service - 使用 Edge-TTS(微軟 Edge 雲端語音合成)。
預設台灣女聲 zh-TW-HsiaoChenNeural(曉臻),語速 -10% 對長者較友善。
"""
from pathlib import Path
import edge_tts


class TTSService:
    """文字轉語音服務。"""
    
    DEFAULT_VOICE = "zh-TW-HsiaoChenNeural"
    DEFAULT_RATE = "-10%"
    
    def __init__(self, output_dir: str = "/media/audio"):
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
        把文字合成為 mp3 存檔。
        
        Returns:
            檔案路徑,例如 media/audio/sess_001/round_1_turn_2.mp3
        """
        session_dir = self.output_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        if turn_number is None:
            filename = f"round_{round_number}.mp3"
        else:
            filename = f"round_{round_number}_turn_{turn_number}.mp3"
        filepath = session_dir / filename
        
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice or self.DEFAULT_VOICE,
            rate=rate or self.DEFAULT_RATE,
        )
        await communicate.save(str(filepath))
        
        return str(filepath)
    
    async def close(self):
        """Edge-TTS 無持久連線,留空保留介面相容。"""
        pass