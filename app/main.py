from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from config import settings
from services.llm import LLMService
from services.stt import STTService
from services.user_profile_db import DBUserProfileClient   # ⭐ 改用 DB 版
from services.image import StabilityImageService
from services.rag_client import MockRAGClient
from privacy.deidentifier import Deidentifier
from orchestrator import TherapyOrchestrator
from fastapi.responses import FileResponse          # ⭐ 新增
from services.tts import TTSService                  # ⭐ 新增


llm_service: LLMService | None = None
stt_service: STTService | None = None
tts_service: TTSService | None = None                # ⭐ 新增
user_profile_client: DBUserProfileClient | None = None
deidentifier: Deidentifier | None = None
image_service: StabilityImageService | None = None
rag_client: MockRAGClient | None = None
orchestrator: TherapyOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global llm_service, stt_service, tts_service, user_profile_client, deidentifier
    global image_service, rag_client, orchestrator
    
    print("🚀 啟動服務...")
    llm_service = LLMService()
    stt_service = STTService()
    tts_service = TTSService()                     # ⭐ 新增初始化
    user_profile_client = DBUserProfileClient()    # ⭐ DB 版
    deidentifier = Deidentifier()
    image_service = StabilityImageService()
    rag_client = MockRAGClient()
    
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
    await tts_service.close()                        # ⭐ 新增
    await user_profile_client.close()
    await image_service.close()


app = FastAPI(title="Rememo Backend", version="0.2.0", lifespan=lifespan)


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
#        "tts_host": settings.tts_host,
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


@app.post("/test/tts")
async def test_tts(text: str = "您好,今天天氣很好,想跟您聊聊運動會的回憶。"):
    """
    測試 Edge-TTS 台灣女聲合成。
    Swagger UI 會直接回傳 mp3,可線上播放。
    """
    path = await tts_service.synthesize(
        text=text,
        session_id="test",
        round_number=1,
    )
    return FileResponse(
        path=path,
        media_type="audio/mpeg",
        filename="tts_test.mp3",
    )


@app.get("/test/user/{user_id}")
async def test_user_profile(user_id: str):
    user = await user_profile_client.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return user


@app.post("/test/image")
async def test_image(prompt: str = "watercolor painting of 1940s Taiwan elementary school sports day relay race"):
    path = await image_service.generate(prompt=prompt, session_id="test", round_number=1)
    return {"prompt": prompt, "saved_to": path}


# ════════════ 完整療程端點(orchestrator) ════════════

@app.post("/session/start")
async def session_start(
    user_id: str,
    session_id: str,
    start_scene: str,   # ⭐ 新增:今日主題,由治療師在前端選好傳進來
):
    """
    啟動一場療程的開場流程。
    
    Args:
        user_id:     patients.id 字串(例如 "1")
        session_id:  本次療程 ID(由前端產生,Phase 2 會改成由 db 給)
        start_scene: 今日主題(例如「運動會」「童年遊戲」)
    """
    try:
        result = await orchestrator.start_session_opening(
            user_id=user_id,
            session_id=session_id,
            today_topic=start_scene,   # ⭐ 注入給 orchestrator
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"療程開場失敗: {str(e)}")