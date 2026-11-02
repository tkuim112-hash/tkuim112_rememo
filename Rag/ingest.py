import os
import re
from datetime import datetime, timezone
from ckip_transformers.nlp import CkipWordSegmenter, CkipPosTagger
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from embeddings import get_embeddings

def base_clean(text):
    text = re.sub(r'<[^>]+>|https?://\S+', '', text)
    punc = {"！": "!", "？": "?", "，": ",", "。": ".", "：": ":"}
    for old, new in punc.items():
        text = text.replace(old, new)
    return re.sub(r'\s+', ' ', text).strip()


# Whisper 在靜音/雜音片段常見的幻覺輸出：同一個字或短句一直重複（「對對對對…」
# 「要用手要用手要用手…」），或是訓練資料裡的字幕台詞。這類片段沒有語意，
# 對任何查詢都可能拿到中等分數、被撈去生圖，所以不存入。
_REPEAT_RUN = re.compile(r"(.{1,6}?)\1{3,}")   # 1-6 字的單位連續重複 4 次以上
_HALLUCINATION_PHRASES = ("明鏡與點點", "點贊訂閱", "訂閱轉發", "打賞支持", "字幕由", "Amara.org", "請不吝")


def is_junk_chunk(chunk):
    """判斷片段是不是 STT 幻覺/無意義內容，是的話不存入 Qdrant。

    重複內容佔比門檻取 0.5，太低會誤擋「好好好好，我們去公園走走」這種正常口語。"""
    text = re.sub(r"[\s,.!?:;、]", "", chunk)
    if not text:
        return True
    if any(p in chunk for p in _HALLUCINATION_PHRASES):
        return True
    covered = sum(len(m.group(0)) for m in _REPEAT_RUN.finditer(text))
    if covered / len(text) >= 0.5:
        return True
    # 長片段但幾乎都是同幾個字（重複單位超過 6 字、上面的規則抓不到時的備援）
    return len(text) >= 20 and len(set(text)) / len(text) < 0.2


class AdvancedCleaner:
    """目前沒有呼叫端在用（存入 Qdrant 的是完整原句，見 process_and_save），
    保留給之後做關鍵字標籤/分類。CKIP 模型很重，只在真的呼叫時才建立。"""
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
    # get_embeddings() 底層的 SentenceTransformer 模型是程序內 singleton
    # （見 embeddings.py _get_model()），這裡呼叫不會每次都重新載入權重。
    embeddings = get_embeddings()

    base = base_clean(raw_text)
    # 存完整原句，不用 ckip_refine 的詞性過濾結果：過濾後硬拼接會變成失去語序
    # 的詞語沙拉（如「抖子音樂腰晃腦」），embedding 失真、檢索會撈到語意無關的記憶。

    # chunk_size 120：太短容易把句子從中間切斷，語意稀薄的片段容易跟其他主題混淆。
    splitter = RecursiveCharacterTextSplitter(chunk_size=120, chunk_overlap=20)
    chunks = splitter.split_text(base)
    kept = [c for c in chunks if not is_junk_chunk(c)]
    if len(kept) < len(chunks):
        print(f"[ingest] elder_id={elder_id} 略過 {len(chunks) - len(kept)}/{len(chunks)} 個 STT 幻覺/無意義片段")
    chunks = kept
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