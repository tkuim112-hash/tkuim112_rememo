"""
臉部情緒訊號服務客戶端 — 跟 face-service（py-feat）容器溝通。

取代 Kinect 內建 Face API：Unity 端改送一張 JPEG 畫面，這裡呼叫 face-service
拿回 FACS Action Unit 強度，給 app/routers/sensor.py 換算成 engagement/happiness
兩軸的訊號。face-service 掛掉/逾時時回傳空結果，呼叫端要能正常處理「這次沒有
臉部資料」——這是病患即時互動路徑，容錯比完整度重要，不能讓臉部分析失敗拖垮
整個 /sensor/emotion 請求。
"""
import logging

import httpx
from config import settings

logger = logging.getLogger(__name__)


class FaceEmotionService:
    """face-service（py-feat）客戶端。"""

    def __init__(self):
        self.host = settings.face_service_host
        self.client = httpx.AsyncClient(timeout=10.0)

    async def analyze_bytes(self, image_bytes: bytes, filename: str = "frame.jpg") -> dict:
        """
        分析一張畫面，回傳 {"face_detected": bool, "aus": {...}, "pose": {...}}。

        任何失敗（服務未啟動、逾時、畫面無法解析）都回傳
        {"face_detected": False, "aus": {}, "pose": {}}，不往外拋例外——呼叫端
        （sensor.py）把這當成「這次沒有臉部訊號」處理，跟 Kinect 原本
        DetectionResult=Unknown 的語意一致。
        """
        empty = {"face_detected": False, "aus": {}, "pose": {}}
        try:
            files = {"frame": (filename, image_bytes, "image/jpeg")}
            response = await self.client.post(f"{self.host}/analyze", files=files)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"[face-service] 分析失敗，視為本次沒有臉部訊號: {e}")
            return empty

    async def close(self):
        await self.client.aclose()
