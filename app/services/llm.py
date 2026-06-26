"""
LLM 服務客戶端 — 跟 Ollama 容器溝通。

使用 /api/chat（而非 /api/generate）以對齊 DPO 訓練時的 messages 格式，
確保微調後的模型收到與訓練時一致的輸入結構。
"""
import httpx
from config import settings

TEMPERATURE = 0.3  # 與 Rag/brain.py 保持一致，降低隨機性


class LLMService:
    """Ollama 客戶端，包裝 /api/chat 端點（chat-format，對齊 DPO 訓練格式）。"""

    def __init__(self):
        self.host = settings.ollama_host
        self.model = settings.ollama_model
        self.client = httpx.AsyncClient(timeout=60.0)

    async def ask(self, prompt: str) -> str:
        """
        送出單一純文字 prompt，內部包裝為 user message 呼叫 /api/chat。

        Args:
            prompt: 完整的提示文字（包含 system 指令和 user 內容）

        Returns:
            LLM 的回應文字
        """
        return await self.chat([{"role": "user", "content": prompt}])

    async def chat(self, messages: list[dict]) -> str:
        """
        送出 messages 格式的對話，對齊 DPO 訓練時的 prompt 結構。

        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": "..."}]

        Returns:
            LLM 的回應文字
        """
        response = await self.client.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": TEMPERATURE},
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"]

    async def close(self):
        """關閉 HTTP 客戶端（程式關閉時呼叫）"""
        await self.client.aclose()
