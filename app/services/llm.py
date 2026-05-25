"""
LLM 服務客戶端 — 跟 Ollama 容器溝通。
"""
import httpx
from config import settings


class LLMService:
    """Ollama 客戶端，包裝 /api/generate 端點。"""

    def __init__(self):
        self.host = settings.ollama_host
        self.model = settings.ollama_model
        # timeout 設長一點，因為 LLM 第一次載入會慢
        self.client = httpx.AsyncClient(timeout=60.0)

    async def ask(self, prompt: str) -> str:
        """
        丟一個 prompt 給 LLM，回傳完整的文字答案。

        Args:
            prompt: 要問的問題（純文字）

        Returns:
            LLM 的回應文字
        """
        response = await self.client.post(
            f"{self.host}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,  # 先用非串流模式，簡單一點
            },
        )
        response.raise_for_status()  # 如果 HTTP 失敗就拋例外

        data = response.json()
        return data["response"]

    async def close(self):
        """關閉 HTTP 客戶端（程式關閉時呼叫）"""
        await self.client.aclose()