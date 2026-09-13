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

    async def ask(self, prompt: str, temperature: float | None = None) -> str:
        """
        送出單一純文字 prompt，內部包裝為 user message 呼叫 /api/chat。

        Args:
            prompt: 完整的提示文字（包含 system 指令和 user 內容）
            temperature: 覆寫本次呼叫的 temperature，不傳則用模組預設值
                TEMPERATURE。翻譯/重構這類要求「忠實、不遺漏」而非發散
                創意的任務，應該傳 0 降低隨機性遺漏內容的機率。

        Returns:
            LLM 的回應文字
        """
        return await self.chat(
            [{"role": "user", "content": prompt}], temperature=temperature
        )

    async def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        format: dict | str | None = None,
    ) -> str:
        """
        送出 messages 格式的對話，對齊 DPO 訓練時的 prompt 結構。

        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
            temperature: 覆寫本次呼叫的 temperature，不傳則用模組預設值 TEMPERATURE。
            format: 選用，Ollama 的結構化輸出參數——傳一份 JSON Schema（dict）
                強制模型用 grammar-constrained decoding 輸出符合該 schema 的
                JSON，schema 裡列的每個 key 都保證會出現在輸出裡（不保證內容
                品質，但保證欄位本身不會被模型漏掉，解決本地小模型在多段
                標籤格式下常見的「漏欄位」問題）；傳 "json" 則只要求輸出是
                合法 JSON、不檢查結構。不傳則沿用原本的自由文字輸出。

        Returns:
            LLM 的回應文字
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": TEMPERATURE if temperature is None else temperature,
                # 明確指定，不吃模型Modelfile自己的預設值（見 config.py
                # ollama_num_ctx 註解）
                "num_ctx": settings.ollama_num_ctx,
            },
        }
        if format is not None:
            payload["format"] = format
        response = await self.client.post(f"{self.host}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"]

    async def close(self):
        """關閉 HTTP 客戶端（程式關閉時呼叫）"""
        await self.client.aclose()
