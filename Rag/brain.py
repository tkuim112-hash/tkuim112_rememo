import os
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate

class ElderlyAI:
    def __init__(self, model_name="llama3:8b-instruct-q4_K_M"):
        # 從環境變數讀取連線資訊
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        
        self.llm = ChatOllama(model=model_name, temperature=0.3, base_url=ollama_host)
        self.embeddings = OllamaEmbeddings(model="bge-m3", base_url=ollama_host)
        self.turn_count = 0
        self.time_info = {1: "清晨", 2: "中午", 3: "下午", 4: "晚上"}
        
        # 讀取禁忌詞用於生成端屏蔽
        self.forbidden_list = ""
        if os.path.exists("data/Forbidden_words.txt"):
            with open("data/Forbidden_words.txt", "r", encoding="utf-8") as f:
                self.forbidden_list = f.read().strip()

        # 連線至 Qdrant 容器
        self.client = QdrantClient(url=qdrant_url)
        self.db = QdrantVectorStore(
            client=self.client, 
            collection_name="safe_reminiscence", 
            embedding=self.embeddings
        )

    def _generate_multi_queries(self, original_query):
        """RAG-Fusion: 改寫搜尋問題"""
        prompt = f"請將這句回憶改寫成 3 個不同角度的繁體中文搜尋關鍵字，每行一個：\n{original_query}"
        try:
            response = self.llm.invoke(prompt).content
            queries = [q.strip() for q in response.split('\n') if q.strip()]
            return queries[:3] if queries else [original_query]
        except:
            return [original_query]

    def _rrf_score(self, results_list, k=60):
        """RRF 排名融合演算法"""
        fused_scores = {}
        for docs in results_list:
            for rank, doc in enumerate(docs):
                content = doc.page_content
                fused_scores[content] = fused_scores.get(content, 0) + 1 / (k + rank)
        sorted_res = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
        return [item[0] for item in sorted_res[:3]]

    def _execute_rag(self, elder_id, user_input):
        # 1. RAG-Fusion 多路檢索
        multi_queries = self._generate_multi_queries(user_input)
        all_results = []
        for q in multi_queries:
            # 物理過濾：僅檢索 is_forbidden 為 False 的安全記憶
            hits = self.db.similarity_search(
                q, k=5,
                filter=models.Filter(must=[
                    models.FieldCondition(key="metadata.elder_id", match=models.MatchValue(value=elder_id)),
                    models.FieldCondition(key="metadata.is_forbidden", match=models.MatchValue(value=False))
                ])
            )
            all_results.append(hits)

        # 2. RRF 融合
        fused_context = self._rrf_score(all_results)
        safe_context = "\n".join(fused_context)

        # 3. LLM 生成與指令屏蔽
        system_template = """你是一位專業台灣懷舊治療師。
        指令：1. 全程繁體中文。2. 嚴禁提到：{forbidden_list}。3. 以第三人稱、正向口吻續寫故事並提問。"""
        human_template = """當年時段：{time_name}\n提取記憶：{personal_context}\n長者說：{user_input}\n續寫故事："""

        prompt = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(system_template),
            HumanMessagePromptTemplate.from_template(human_template)
        ])

        chain = prompt | self.llm
        return chain.invoke({
            "forbidden_list": self.forbidden_list,
            "time_name": self.time_info.get(self.turn_count, "深夜"),
            "personal_context": safe_context,
            "user_input": user_input
        }).content

    def generate_response(self, elder_id, topic):
        self.turn_count = 1
        return self._execute_rag(elder_id, topic)

    def continue_story(self, elder_id, user_input):
        self.turn_count += 1
        return self._execute_rag(elder_id, user_input)