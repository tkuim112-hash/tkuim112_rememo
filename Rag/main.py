import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from brain import ElderlyAI
from ingest import process_and_save

app = FastAPI(title="TKU Smart Care RAG Service", version="2.0")

# 初始化 RAG 檢索器
ai_engine = ElderlyAI()

# Pydantic 輸出入格式定義
class IngestRequest(BaseModel):
    elder_id: str
    text: str

class RetrieveRequest(BaseModel):
    elder_id: str
    query: str
    limit: int = 3

@app.post("/api/v1/memory/ingest")
async def api_ingest_memory(payload: IngestRequest):
    try:
        total_chunks = process_and_save(payload.elder_id, payload.text)
        return {
            "status": "success",
            "message": f"資料成功寫入 Qdrant！總片段數: {total_chunks}",
            "elder_id": payload.elder_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/memory/retrieve")
async def api_retrieve_memory(payload: RetrieveRequest):
    try:
        memories = ai_engine.retrieve_memories(
            elder_id=payload.elder_id,
            query=payload.query,
            limit=payload.limit
        )
        return {
            "status": "success",
            "memories": memories
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)