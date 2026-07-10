import os
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
from langchain_ollama import ChatOllama, OllamaEmbeddings

class ElderlyAI:
    def __init__(self, model_name=None):
        # 對齊全專案使用的 DPO 微調模型（app/config.py 的 ollama_model）
        model_name = model_name or os.getenv("RAG_LLM_MODEL", "rememo-llama3")
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        
        # 保留一個輕量 LLM 僅用於「RAG-Fusion 關鍵字改寫」
        self.llm = ChatOllama(model=model_name, temperature=0.3, base_url=ollama_host)
        self.embeddings = OllamaEmbeddings(model="bge-m3", base_url=ollama_host)
        
        self.client = QdrantClient(url=qdrant_url)
        
        # 如果 collection 不存在就自動建立
        collections = [c.name for c in self.client.get_collections().collections]
        if "safe_reminiscence" not in collections:
            self.client.create_collection(
                collection_name="safe_reminiscence",
                vectors_config=models.VectorParams(
                    size=1024,   # bge-m3 的維度
                    distance=models.Distance.COSINE,
                )
            )
            print("✅ Qdrant collection 'safe_reminiscence' 建立成功")

        # elder_id 過濾用的 payload index（重複呼叫是冪等的，對既有資料也會回溯建立）
        self.client.create_payload_index(
            collection_name="safe_reminiscence",
            field_name="metadata.elder_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

        self.db = QdrantVectorStore(
            client=self.client,
            collection_name="safe_reminiscence",
            embedding=self.embeddings
        )

    def _generate_multi_queries(self, original_query):
        """RAG-Fusion: 改寫搜尋關鍵字"""
        prompt = f"請將這句回憶改寫成 3 個不同角度的繁體中文搜尋關鍵字，每行一個，不要有任何多餘文字：\n{original_query}"
        try:
            response = self.llm.invoke(prompt).content
            queries = [q.strip() for q in response.split('\n') if q.strip()]
            return queries[:3] if queries else [original_query]
        except Exception:
            return [original_query]

    def _rrf_score(self, results_list, k=60, limit=3):
        """RRF 排名融合演算法：計算融合得分"""
        fused_scores = {}
        doc_metadata = {}
        for docs in results_list:
            for rank, doc in enumerate(docs):
                content = doc.page_content
                fused_scores[content] = fused_scores.get(content, 0.0) + 1 / (k + rank)
                if content not in doc_metadata:
                    doc_metadata[content] = doc.metadata or {}

        # 排序後取前 limit 名，分數正規化到 0-1（除以理論最大值：每一路都排第一 = len/k）
        sorted_res = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
        max_score = len(results_list) / k if results_list else 1.0

        output = []
        for text, score in sorted_res[:limit]:
            meta = doc_metadata.get(text, {})
            output.append({
                "text": text,
                "score": round(score / max_score, 4),
                "session_id": meta.get("session_id", ""),
                "emotion": meta.get("emotion", ""),
                "created_at": meta.get("created_at", ""),
            })
        return output

    def retrieve_memories(self, elder_id, query, limit=3):
        """純檢索端點核心邏輯：RAG-Fusion + RRF"""
        # 1. 生成多路查詢（原始查詢保留為其中一路，防止 LLM 改寫偏題）
        multi_queries = self._generate_multi_queries(query)
        if query not in multi_queries:
            multi_queries.append(query)
        
        # 2. 多路並行檢索 (僅依據 elder_id 過濾)
        all_results = []
        for q in multi_queries:
            hits = self.db.similarity_search(
                q, k=5,
                filter=models.Filter(must=[
                    models.FieldCondition(key="metadata.elder_id", match=models.MatchValue(value=elder_id))
                ])
            )
            all_results.append(hits)

        # 3. RRF 演算法融合排序
        return self._rrf_score(all_results, limit=limit)