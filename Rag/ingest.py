import os
import re
from datetime import datetime, timezone
from ckip_transformers.nlp import CkipWordSegmenter, CkipPosTagger
from langchain_qdrant import QdrantVectorStore
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

def base_clean(text):
    text = re.sub(r'<[^>]+>|https?://\S+', '', text)
    punc = {"！": "!", "？": "?", "，": ",", "。": ".", "：": ":"}
    for old, new in punc.items():
        text = text.replace(old, new)
    return re.sub(r'\s+', ' ', text).strip()


class AdvancedCleaner:
    """2026-08-20 稽核：ckip_refine 目前沒有任何呼叫端在用（存入 Qdrant 的
    文字改用 base_clean 的完整原句，見 process_and_save 說明），這裡保留
    給之後如果要另外做關鍵字標籤/分類用。CkipWordSegmenter/CkipPosTagger
    是重量級模型，故不隨模組載入就初始化，只有真的呼叫 ckip_refine 時才建。"""
    def __init__(self):
        self.ws = CkipWordSegmenter(model="albert-base", device=-1)
        self.pos = CkipPosTagger(model="albert-base", device=-1)

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
    
    base = base_clean(raw_text)
    # 2026-08-20 稽核：改用 base（完整原句）而非 ckip_refine 過的詞性白名單
    # 結果去做 embedding/存入——ckip_refine 只留 Na/Nb/Nc/Nd/VA/VC/V_2/A 再
    # join() 硬拼接，會把句子拆成失去語序/文法的詞語沙拉（實測庫內資料出現
    # 「背字佳人煮飯」「抖子音樂腰晃腦」這類不可讀字串），bge-m3 這類 embedding
    # 模型是針對自然語言訓練的，餵詞語沙拉會讓向量失真，導致檢索撈到看似關鍵字
    # 重疊、實際語意無關的記憶。ckip_refine/AdvancedCleaner 保留在程式碼裡，
    # 之後如果要另外做關鍵字標籤/分類可以用，但不再用來決定存入 Qdrant 的內容。

    # chunk_size 從 50 調到 120：50 字（CKIP過濾後常常剩不到10個字）太容易把
    # 句子從中間切斷，語意稀薄的短片段 embedding 更容易跟其他主題混淆。
    splitter = RecursiveCharacterTextSplitter(chunk_size=120, chunk_overlap=20)
    chunks = splitter.split_text(base)
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