from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import redis.asyncio as aioredis
from config import settings
from db.session import engine
from db.models import Base
from services.llm import LLMService
from services.stt import STTService
from services.tts import TTSService 
from services.user_profile_db import DBUserProfileClient
from services.image import StabilityImageService
from services.rag_client import RealRAGClient   
from privacy.deidentifier import Deidentifier
from orchestrator import TherapyOrchestrator
from routers import ws_stt, ws_calibration, session, sensor


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 啟動服務...")
    app.state.redis              = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.llm_service        = LLMService()
    app.state.stt_service        = STTService()
    app.state.tts_service        = TTSService()  
    app.state.user_profile       = DBUserProfileClient()
    app.state.deidentifier       = Deidentifier()
    app.state.image_service      = StabilityImageService()
    app.state.rag_client    = RealRAGClient()   
    app.state.orchestrator       = TherapyOrchestrator(
        llm=app.state.llm_service,
        image=app.state.image_service,
        rag=app.state.rag_client,
        user_profile=app.state.user_profile,
        deidentifier=app.state.deidentifier,
    )

    # PostgreSQL — SQLAlchemy AsyncSession
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ PostgreSQL (SQLAlchemy) 連線成功")
    except Exception as e:
        print(f"⚠️  PostgreSQL 連線失敗，持久化功能停用: {e}")

    yield

    print("👋 關閉服務...")
    await app.state.redis.aclose()
    await app.state.llm_service.close()
    await app.state.stt_service.close()
    await app.state.tts_service.close()  
    await app.state.user_profile.close()
    await app.state.image_service.close()
    await engine.dispose()


app = FastAPI(title="Rememo Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws_stt.router)
app.include_router(ws_calibration.router)
app.include_router(session.router)
app.include_router(sensor.router)

_media_dir = Path("/media/images")
_media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(_media_dir)), name="images")


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
async def test_llm(request: Request, prompt: str = "請用繁體中文回答:你好嗎?"):
    reply = await request.app.state.llm_service.ask(prompt)
    return {"prompt": prompt, "reply": reply}


@app.post("/test/stt")
async def test_stt(request: Request, file: UploadFile = File(...)):
    audio_bytes = await file.read()
    text = await request.app.state.stt_service.transcribe_bytes(audio_bytes, filename=file.filename)
    return {"filename": file.filename, "transcript": text}


@app.get("/test/user/{user_id}")
async def test_user_profile(request: Request, user_id: str):
    user = await request.app.state.user_profile.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return user


@app.get("/test/deidentify/{user_id}")
async def test_deidentify(request: Request, user_id: str):
    user = await request.app.state.user_profile.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    example_prompt = (
        f"水彩畫風,描繪{user['name']}的回憶。"
        f"場景:{user['birth_year']} 年{user['birth_place']},"
        f"從事{user['main_occupation']}的場景。"
        f"主題:{user['today_topic']}。"
    )

    deidentifier = request.app.state.deidentifier
    desensitized_prompt  = deidentifier.desensitize_text(example_prompt, taboos=user["taboos"])
    desensitized_profile = deidentifier.desensitize_profile(user)

    return {
        "original": {"raw_profile": user, "raw_prompt": example_prompt},
        "desensitized": {
            "safe_profile_for_cloud": desensitized_profile,
            "safe_prompt_for_stability": desensitized_prompt,
        }
    }


@app.post("/test/image")
async def test_image(
    request: Request,
    prompt: str = "watercolor painting of 1940s Taiwan elementary school sports day relay race",
):
    path = await request.app.state.image_service.generate(
        prompt=prompt, session_id="test", round_number=1
    )
    return {"prompt": prompt, "saved_to": path}

@app.post("/test/tts")
async def test_tts(request: Request, text: str = "您好，今天天氣很好，想跟您聊聊運動會的回憶。"):
    """測試 Edge-TTS 台灣女聲合成。"""
    from fastapi.responses import FileResponse
    path = await request.app.state.tts_service.synthesize(
        text=text, session_id="test", round_number=1,
    )
    return FileResponse(path=path, media_type="audio/mpeg", filename="tts_test.mp3")
