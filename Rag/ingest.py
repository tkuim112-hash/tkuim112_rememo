import os
import re
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
        for words, pos in zip(word_results, pos_results):
            if words is None or pos is None:
                cleaned_list.append(text_list[0])  # fallback 用原文
                continue
            filtered = [w for w, p in zip(words, pos) if p in keep_pos]
            cleaned_list.append("".join(filtered) if filtered else text_list[0])
        return cleaned_list

def process_and_save(elder_id, raw_text):
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    embeddings = OllamaEmbeddings(model="bge-m3", base_url=ollama_host)
    
    cleaner = AdvancedCleaner()
    base = cleaner.base_clean(raw_text)
    refined = cleaner.ckip_refine([base])[0]
    
    # 效能優化平衡點 chunk_size=50
    splitter = RecursiveCharacterTextSplitter(chunk_size=50, chunk_overlap=10)
    chunks = splitter.split_text(refined)
    metadatas = [{"elder_id": elder_id} for _ in chunks]
    
    if chunks:
        QdrantVectorStore.from_texts(
            texts=chunks,
            embedding=embeddings,
            metadatas=metadatas,
            url=qdrant_url,
            collection_name="safe_reminiscence"
        )
        return len(chunks)
    return 0