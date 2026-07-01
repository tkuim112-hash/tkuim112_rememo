"""
RAG 服務客戶端 — 對接 rag-service 容器的 HTTP API。
"""
import httpx
from typing import TypedDict, Protocol


class MemoryResult(TypedDict):
    text: str
    summary: str
    emotion_tag: str
    importance: float
    timestamp: str


class RAGClient(Protocol):
    """RAG 客戶端介面定義"""
    async def retrieve_memories(self, user_id: str, query: str, limit: int = 3) -> list[MemoryResult]: ...
    async def save_memory(self, user_id: str, session_id: str, text: str, emotion: str) -> bool: ...


class RealRAGClient:
    """真實 RAG 客戶端，對接 rag-service 的 API。"""

    def __init__(self, base_url: str = "http://rag-service:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def retrieve_memories(self, user_id: str, query: str, limit: int = 3) -> list[MemoryResult]:
        """從 RAG service 檢索長者回憶"""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/memory/retrieve",
                json={"elder_id": user_id, "query": query, "limit": limit},
            )
            response.raise_for_status()
            data = response.json()
            memories = data.get("memories", [])

            return [
                {
                    "text": m.get("text", ""),
                    "summary": m.get("text", "")[:20],
                    "emotion_tag": "懷念",
                    "importance": m.get("score", 0.5),
                    "timestamp": "2026-01-01",
                }
                for m in memories
            ]
        except Exception as e:
            print(f"[RAG] retrieve_memories 失敗: {e}")
            return []

    async def save_memory(self, user_id: str, session_id: str, text: str, emotion: str) -> bool:
        """把長者的回應存入 RAG service"""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/memory/ingest",
                json={"elder_id": user_id, "text": text},
            )
            response.raise_for_status()
            print(f"[RAG] 儲存成功: {text[:30]}...")
            return True
        except Exception as e:
            print(f"[RAG] save_memory 失敗: {e}")
            return False

    async def close(self):
        await self.client.aclose()