import os
import re
from datetime import datetime, timezone
from ckip_transformers.nlp import CkipWordSegmenter, CkipPosTagger
from langchain_qdrant import QdrantVectorStore
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

class AdvancedCleaner:
    def __init__(self):
        self.ws = CkipWordSegmenter(model="albert-base", device=-1)
        self.pos = CkipPosTagger(model="albert-base", device=-1)
        
    def base_clean(self, text):
        text = re.sub(r'<[^>]+>|https?://\S+', '', text)
        punc = {"！": "!", "？": "?", "，": ",", "。": ".", "：": ":"}
        for old, new in punc.items():
            text = text.replace(old, new)
        return re.sub(r'\s+', ' ', text).strip()

    def ckip_refine(self, text_list):
        word_results = self.ws(text_list)
        pos_results = self.pos(word_results)   # ⭐ pos 要吃 ws 的輸出，不是原始 text
        cleaned_list = []
        keep_pos = {'Na', 'Nb', 'Nc', 'Nd', 'VA', 'VC', 'V_2', 'A'}
        for i, (words, pos) in enumerate(zip(word_results, pos_results)):
            if words is None or pos is None:
                cleaned_list.append(text_list[i])  # fallback 用原文
                continue
            filtered = [w for w, p in zip(words, pos) if p in keep_pos]
            cleaned_list.append("".join(filtered) if filtered else text_list[i])
        return cleaned_list


_cleaner = None

def _get_cleaner():
    """CKIP 模型只載入一次（每次請求重載要花數秒 + 大量記憶體）"""
    global _cleaner
    if _cleaner is None:
        _cleaner = AdvancedCleaner()
    return _cleaner

def process_and_save(elder_id, raw_text, session_id="", emotion=""):
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.getenv("QDRANT_API_KEY") or None
    qdrant_collection = os.getenv("QDRANT_COLLECTION", "safe_reminiscence")
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    embedding_model = os.getenv("EMBEDDING_MODEL", "bge-m3")
    embeddings = OllamaEmbeddings(model=embedding_model, base_url=ollama_host)
    
    cleaner = _get_cleaner()
    base = cleaner.base_clean(raw_text)
    refined = cleaner.ckip_refine([base])[0]
    
    # 效能優化平衡點 chunk_size=50
    splitter = RecursiveCharacterTextSplitter(chunk_size=50, chunk_overlap=10)
    chunks = splitter.split_text(refined)
    created_at = datetime.now(timezone.utc).isoformat()
    metadatas = [
        {
            "elder_id": elder_id,
            "session_id": session_id,
            "emotion": emotion,
            "created_at": created_at,
        }
        for _ in chunks
    ]
    
    if chunks:
        QdrantVectorStore.from_texts(
            texts=chunks,
            embedding=embeddings,
            metadatas=metadatas,
            url=qdrant_url,
            api_key=qdrant_api_key,
            collection_name=qdrant_collection
        )
        return len(chunks)
    return 0