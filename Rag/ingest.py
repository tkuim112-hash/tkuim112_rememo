import os
import re
from ckip_transformers.nlp import CkipTagger
from langchain_qdrant import QdrantVectorStore
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

class AdvancedCleaner:
    def __init__(self):
        self.tagger = CkipTagger(level=3, model="albert-base", device=-1)
        
    def base_clean(self, text):
        text = re.sub(r'<[^>]+>|https?://\S+', '', text)
        punc = {"！": "!", "？": "?", "，": ",", "。": ".", "：": ":"}
        for old, new in punc.items():
            text = text.replace(old, new)
        return re.sub(r'\s+', ' ', text).strip()

    def ckip_refine(self, text_list):
        word_results = self.tagger.word_segmentation(text_list)
        pos_results = self.tagger.pos_tagging(text_list)
        cleaned_list = []
        keep_pos = {'Na', 'Nb', 'Nc', 'Nd', 'VA', 'VC', 'V_2', 'A'}
        for words, pos in zip(word_results, pos_results):
            filtered = [w for w, p in zip(words, pos) if p in keep_pos]
            cleaned_list.append("".join(filtered))
        return cleaned_list

def process_and_save(elder_id):
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    embeddings = OllamaEmbeddings(model="bge-m3", base_url=ollama_host)
    cleaner = AdvancedCleaner()
    
    def handle_file(file_path, is_forbidden):
        if not os.path.exists(file_path): return [], []
        with open(file_path, "r", encoding="utf-8") as f:
            raw = f.read()
        base = cleaner.base_clean(raw)
        refined = cleaner.ckip_refine([base])[0]
        
        # 效能優化：chunk_size 改為 50
        splitter = RecursiveCharacterTextSplitter(chunk_size=50, chunk_overlap=10)
        chunks = splitter.split_text(refined)
        metadatas = [{"elder_id": elder_id, "is_forbidden": is_forbidden} for _ in chunks]
        return chunks, metadatas

    mem_chunks, mem_meta = handle_file("data/grandpa_wang.txt", is_forbidden=False)
    forb_chunks, forb_meta = handle_file("data/Forbidden_words.txt", is_forbidden=True)

    all_chunks = mem_chunks + forb_chunks
    all_metas = mem_meta + forb_meta
    
    if all_chunks:
        # 使用 URL 連線到 Qdrant 容器
        QdrantVectorStore.from_texts(
            texts=all_chunks,
            embedding=embeddings,
            metadatas=all_metas,
            url=qdrant_url,
            collection_name="safe_reminiscence"
        )
        print(f"✅ 資料成功寫入 Qdrant！總片段數: {len(all_chunks)}")

if __name__ == "__main__":
    process_and_save("elder_001")