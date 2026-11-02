"""jina-embeddings-v3 embedding 模型載入。

模型來源必須是非中國機構（因此不用 bge-m3），jina-embeddings-v3 來自 Jina AI
（德國），可地端自架。它用 LoRA adapter 依任務切換權重，Ollama 不支援（見
https://github.com/ollama/ollama/issues/6922），所以用 sentence-transformers
直接載入 HuggingFace 權重（必須 trust_remote_code=True）。
langchain_huggingface.HuggingFaceEmbeddings 對這顆模型的 task 參數傳遞有
已知問題，所以自己包一層薄的 langchain Embeddings 介面。

⚠️ 換模型後，Qdrant 裡既有的向量要整批重新 embedding——不同模型的向量空間
不相容，新舊混用會讓檢索失真；檢索門檻也要重新校準（見 brain.py）。
"""
import os
from threading import Lock
from typing import List

from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer

_model = None
_model_lock = Lock()


def _get_model() -> SentenceTransformer:
    """SentenceTransformer 是重量級模型，整個程序只載入一次
    （跟 ingest.py 的 CKIP _get_cleaner() 同樣考量：每次請求重載要花
    數秒 + 大量記憶體，尤其這裡換成本機權重後沒有 Ollama 幫忙常駐）。"""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                model_name = os.getenv("EMBEDDING_MODEL", "jinaai/jina-embeddings-v3")
                _model = SentenceTransformer(model_name, trust_remote_code=True)
    return _model


class JinaEmbeddings(Embeddings):
    """langchain Embeddings 介面的薄包裝。

    jina-embeddings-v3 的查詢／段落要用不同的 task 字串選擇 LoRA adapter
    才能發揮非對稱檢索（asymmetric retrieval）的效果，所以 embed_query 跟
    embed_documents 故意分開傳不同的 task，不能共用同一個 encode 呼叫。
    """

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return _get_model().encode(texts, task="retrieval.passage").tolist()

    def embed_query(self, text: str) -> List[float]:
        return _get_model().encode([text], task="retrieval.query")[0].tolist()


def get_embeddings() -> Embeddings:
    return JinaEmbeddings()
