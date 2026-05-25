from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from config import settings
from services.llm import LLMService
from services.stt import STTService
from services.user_profile_client import MockUserProfileClient
from services.image import StabilityImageService
from services.rag_client import MockRAGClient
from privacy.deidentifier import Deidentifier
from orchestrator import TherapyOrchestrator


llm_service: LLMService | None = None
stt_service: STTService | None = None
user_profile_client: MockUserProfileClient | None = None
deidentifier: Deidentifier | None = None
image_service: StabilityImageService | None = None
rag_client: MockRAGClient | None = None
orchestrator: TherapyOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global llm_service, stt_service, user_profile_client, deidentifier
    global image_service, rag_client, orchestrator
    
    print("🚀 啟動服務...")
    llm_service = LLMService()
    stt_service = STTService()
    user_profile_client = MockUserProfileClient()
    deidentifier = Deidentifier()
    image_service = StabilityImageService()
    rag_client = MockRAGClient()
    
    # ⭐ 把所有 service 注入 orchestrator
    orchestrator = TherapyOrchestrator(
        llm=llm_service,
        image=image_service,
        rag=rag_client,
        user_profile=user_profile_client,
        deidentifier=deidentifier,
    )
    
    yield
    
    print("👋 關閉服務...")
    await llm_service.close()
    await stt_service.close()
    await user_profile_client.close()
    await image_service.close()


app = FastAPI(title="Rememo Backend", version="0.1.0", lifespan=lifespan)


# ════════════ 基礎端點 ════════════

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "tku-smart-care backend",
        "message": "Rememo is alive 🌱"
    }


@app.get("/config")
async def show_config():
    return {
        "ollama_host": settings.ollama_host,
        "ollama_model": settings.ollama_model,
        "stt_host": settings.stt_host,
        "tts_host": settings.tts_host,
    }


# ════════════ 個別 service 測試端點 ════════════

@app.get("/test/llm")
async def test_llm(prompt: str = "請用繁體中文回答:你好嗎?"):
    reply = await llm_service.ask(prompt)
    return {"prompt": prompt, "reply": reply}


@app.post("/test/stt")
async def test_stt(file: UploadFile = File(...)):
    audio_bytes = await file.read()
    text = await stt_service.transcribe_bytes(audio_bytes, filename=file.filename)
    return {"filename": file.filename, "transcript": text}


@app.get("/test/user/{user_id}")
async def test_user_profile(user_id: str):
    user = await user_profile_client.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return user


@app.get("/test/deidentify/{user_id}")
async def test_deidentify(user_id: str):
    user = await user_profile_client.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    
    example_prompt = (
        f"水彩畫風,描繪{user['name']}的回憶。"
        f"場景:{user['birth_year']} 年{user['birth_place']},"
        f"從事{user['main_occupation']}的場景。"
        f"主題:{user['today_topic']}。"
    )
    
    desensitized_prompt = deidentifier.desensitize_text(example_prompt, taboos=user["taboos"])
    desensitized_profile = deidentifier.desensitize_profile(user)
    
    return {
        "original": {"raw_profile": user, "raw_prompt": example_prompt},
        "desensitized": {
            "safe_profile_for_cloud": desensitized_profile,
            "safe_prompt_for_stability": desensitized_prompt,
        }
    }


@app.post("/test/image")
async def test_image(prompt: str = "watercolor painting of 1940s Taiwan elementary school sports day relay race"):
    path = await image_service.generate(prompt=prompt, session_id="test", round_number=1)
    return {"prompt": prompt, "saved_to": path}


# ════════════ 完整療程端點(orchestrator) ════════════

@app.post("/session/start")
async def session_start(user_id: str, session_id: str):
    """
    🎯 主要端點:啟動一場療程的開場流程。
    
    試試:
      http://localhost:8000/docs
      → POST /session/start
      → user_id=user_001, session_id=sess_test_001
      → Execute
    
    這會跑完整流程:
      個人資料 → LLM 規劃 → 脫敏 → 生圖 → RAG 檢索 → LLM 生問題
    
    預期時間:15-25 秒(主要是 Stability 生圖)
    """
    try:
        result = await orchestrator.start_session_opening(
            user_id=user_id,
            session_id=session_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"療程開場失敗: {str(e)}")