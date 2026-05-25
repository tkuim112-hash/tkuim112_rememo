import os
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
from langchain_ollama import ChatOllama, OllamaEmbeddings

class ElderlyAI:
    def __init__(self, model_name="llama3:8b-instruct-q4_K_M"):
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        
        # 保留一個輕量 LLM 僅用於「RAG-Fusion 關鍵字改寫」
        self.llm = ChatOllama(model=model_name, temperature=0.3, base_url=ollama_host)
        self.embeddings = OllamaEmbeddings(model="bge-m3", base_url=ollama_host)
        
        self.client = QdrantClient(url=qdrant_url)
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
        except:
            return [original_query]

    def _rrf_score(self, results_list, k=60, limit=3):
        """RRF 排名融合演算法：計算融合得分"""
        fused_scores = {}
        for docs in results_list:
            for rank, doc in enumerate(docs):
                content = doc.page_content
                fused_scores[content] = fused_scores.get(content, 0.0) + 1 / (k + rank)
        
        # 排序並將分數正規化，取出前 limit 名
        sorted_res = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
        
        output = []
        for text, score in sorted_res[:limit]:
            output.append({
                "text": text,
                "score": round(score, 4) # 輸出 RRF 融合權重分
            })
        return output

    def retrieve_memories(self, elder_id, query, limit=3):
        """純檢索端點核心邏輯：RAG-Fusion + RRF"""
        # 1. 生成多路查詢
        multi_queries = self._generate_multi_queries(query)
        
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