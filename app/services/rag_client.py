import asyncio
import random
from typing import TypedDict, Protocol

class MemoryResult(TypedDict):
    text: str
    summary: str
    emotion_tag: str
    importance: float
    timestamp: str

class RAGClient(Protocol):
    """介面合約 — 真實版和 Mock 版都要符合"""
    async def retrieve_memories(self, user_id: str, query: str, limit: int = 3) -> list[MemoryResult]: ...
    async def save_memory(self, user_id: str, session_id: str, text: str, emotion: str) -> bool: ...

class MockRAGClient:
    """模擬 RAG 行為，讓你不用等 RAG 同學"""
    
    FAKE_MEMORIES = [
        {"text": "上次提到去九份玩", "summary": "九份回憶", "emotion_tag": "懷念", "importance": 0.8, "timestamp": "2026-05-10"},
        {"text": "兒子結婚那天", "summary": "兒子婚禮", "emotion_tag": "喜悅", "importance": 0.95, "timestamp": "2026-05-12"},
        {"text": "年輕時當兵的事", "summary": "軍旅生活", "emotion_tag": "驕傲", "importance": 0.7, "timestamp": "2026-05-15"},
    ]
    
    async def retrieve_memories(self, user_id: str, query: str, limit: int = 3) -> list[MemoryResult]:
        await asyncio.sleep(0.1)  # 模擬網路延遲
        return random.sample(self.FAKE_MEMORIES, min(limit, len(self.FAKE_MEMORIES)))
    
    async def save_memory(self, user_id: str, session_id: str, text: str, emotion: str) -> bool:
        await asyncio.sleep(0.05)
        print(f"[Mock RAG] 假裝寫入: {text[:30]}... emotion={emotion}")
        return True

# 在 orchestrator.py 裡用：
# rag = MockRAGClient()  # 這週用 Mock
# rag = HTTPRAGClient(base_url="http://rag-service:8001")  # 下週切換
