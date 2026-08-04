import os
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
from langchain_ollama import OllamaEmbeddings

class ElderlyAI:
    def __init__(self):
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        qdrant_api_key = os.getenv("QDRANT_API_KEY") or None
        qdrant_collection = os.getenv("QDRANT_COLLECTION", "safe_reminiscence")
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        embedding_model = os.getenv("EMBEDDING_MODEL", "bge-m3")

        self.embeddings = OllamaEmbeddings(model=embedding_model, base_url=ollama_host)

        self.client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)

        # 如果 collection 不存在就自動建立
        collections = [c.name for c in self.client.get_collections().collections]
        if qdrant_collection not in collections:
            self.client.create_collection(
                collection_name=qdrant_collection,
                vectors_config=models.VectorParams(
                    size=1024,   # bge-m3 的維度
                    distance=models.Distance.COSINE,
                )
            )
            print(f"✅ Qdrant collection '{qdrant_collection}' 建立成功")

        # elder_id 過濾用的 payload index（重複呼叫是冪等的，對既有資料也會回溯建立）
        self.client.create_payload_index(
            collection_name=qdrant_collection,
            field_name="metadata.elder_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

        self.db = QdrantVectorStore(
            client=self.client,
            collection_name=qdrant_collection,
            embedding=self.embeddings
        )

    def retrieve_memories(self, elder_id, query, limit=3, score_threshold=0.5):
        """單次向量搜尋：查一次、依 Qdrant 回傳的真實相似度排序，低於門檻的結果直接排除（避免硬湊不相關記憶）。"""
        hits = self.db.similarity_search_with_score(
            query, k=limit,
            filter=models.Filter(must=[
                models.FieldCondition(key="metadata.elder_id", match=models.MatchValue(value=elder_id))
            ])
        )
        output = []
        for doc, score in hits:
            if score < score_threshold:
                continue
            meta = doc.metadata or {}
            output.append({
                "text": doc.page_content,
                "score": round(score, 4),
                "session_id": meta.get("session_id", ""),
                "emotion": meta.get("emotion", ""),
                "created_at": meta.get("created_at", ""),
            })
        return output