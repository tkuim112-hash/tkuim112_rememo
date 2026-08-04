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


def _smart_truncate(text: str, max_len: int = 20) -> str:
    """
    截斷到 max_len 字以內，優先在最接近上限的標點符號處切，不要無腦硬砍在
    字數上——2026-08 實測發現 summary 被硬截斷成「以前在台中第一市場賣菜，
    天還沒亮就要去批」這種斷頭斷尾的殘句時，會讓 _plan_image 選元素時更容易
    抓不到重點。只有在截斷範圍內完全找不到標點（找到的標點位置太早，切出來
    內容會少於一半）時才退回硬截斷，保留內容完整性優先於精準卡在字數上限。
    """
    if len(text) <= max_len:
        return text
    window = text[:max_len]
    best_idx = max(window.rfind(p) for p in "。！？，、")
    if best_idx >= max_len // 2:
        return window[: best_idx + 1]
    return window


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
        """
        從 RAG service 檢索長者回憶。

        2026-08 改動：呼叫失敗時不再吞掉例外、改回傳空list——這樣「RAG服務
        真的連不上/逾時」跟「這位長者本來就還沒有任何記憶（合法的空結果）」
        兩種情況在呼叫端會長得一模一樣（都是空list），導致 orchestrator.py
        原本的「撈空就等3秒重試」邏輯對每一位全新病患都會白白多等3秒，因為
        重試幾次結果都一樣是空。改成讓例外往上拋，呼叫端才能只在「真的失敗」
        時才重試，「合法的空結果」直接往下走即可。
        """
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
                "summary": _smart_truncate(m.get("text", "")),
                "emotion_tag": m.get("emotion") or "懷念",
                "importance": m.get("score", 0.5),
                "timestamp": m.get("created_at", ""),
            }
            for m in memories
        ]

    async def save_memory(self, user_id: str, session_id: str, text: str, emotion: str) -> bool:
        """把長者的回應存入 RAG service"""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/memory/ingest",
                json={
                    "elder_id": user_id,
                    "text": text,
                    "session_id": session_id,
                    "emotion": emotion,
                },
            )
            response.raise_for_status()
            print(f"[RAG] 儲存成功: {text[:30]}...")
            return True
        except Exception as e:
            print(f"[RAG] save_memory 失敗: {e}")
            return False

    async def close(self):
        await self.client.aclose()