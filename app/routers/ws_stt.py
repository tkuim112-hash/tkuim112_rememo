import asyncio
import io
import json
import wave

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import update

from auth import get_therapist_id_from_ws_token
from config import settings
from db.models import TherapySession
from db.session import AsyncSessionLocal
import ws_registry

router = APIRouter()


async def _mark_abnormal_end(session_id: str) -> None:
    """/ws/stt 斷線，但既不是治療師按「結束活動」（ws_registry.consume_ending）、
    也不是三回合正常跑完轉場去問心得（session:{id}:reached_closing，見
    session.py session_respond 對 end_session 的處理）——代表長者端 App 或
    治療師網頁被直接關掉、當機、斷線，療程不正常中止。status 卡在 in_progress
    會讓治療師頁面（cases/[id]/page.tsx）誤判成「還在進行中」、把治療師導去
    永遠不會再更新的即時監控頁，這裡把它視同已結束。只在還是 in_progress 時
    才動（避免蓋掉本來就是 completed / scheduled 的資料）。"""
    if not session_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(TherapySession)
                .where(
                    TherapySession.session_uuid == session_id,
                    TherapySession.status == "in_progress",
                )
                .values(status="completed")
            )
            await db.commit()
    except Exception as e:
        print(f"[WS/STT] 標記不正常結束失敗: {e}")


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """將裸 PCM int16 bytes 包成 WAV 格式供 Whisper 解析。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


@router.websocket("/ws/stt")
async def ws_stt(websocket: WebSocket, session_id: str = "", token: str = ""):
    """
    接收 Unity 送來的 PCM int16 mono 16kHz 音訊 chunks。

    連線網址需帶登入時拿到的 JWT 與 session_id：
    ws://host/ws/stt?session_id=<id>&token=<JWT>

    session_id 讓 /session/{id}/control（治療師網頁的重播/跳過/暫停/繼續按鈕）
    能查到這場療程對應哪一條連線，把 control 訊框轉發過來（見 ws_registry.py）。

    控制訊息 (text frame，Unity → 後端):
      {"type": "start"} — 清空緩衝區，開始新一段錄音
      {"type": "end"}   — 對完整緩衝區做最終辨識，isFinal=true

    音訊資料 (binary frame，Unity → 後端):
      raw PCM int16, 16 kHz, mono

    回傳 (text frame，後端 → Unity):
      {"type": "transcript", "text": "...", "isFinal": true|false}
      {"type": "control", "action": "replay_audio"|"skip_scene"|"pause"|"resume"}
    """
    try:
        await get_therapist_id_from_ws_token(websocket.app.state.redis, token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    stt_service = websocket.app.state.stt_service
    r = websocket.app.state.redis

    await websocket.accept()
    ws_registry.register(session_id, websocket)
    audio_buf: bytearray = bytearray()

    SAMPLE_RATE = 16000
    INTERIM_BYTES = SAMPLE_RATE * 2 * 3   # 每 3 秒觸發一次 interim
    last_interim_at = 0
    interim_running = False
    ended = False

    async def run_interim(snapshot: bytes) -> None:
        nonlocal interim_running
        try:
            text = await stt_service.transcribe_bytes(_pcm_to_wav(snapshot))
            if text.strip() and not ended:
                await websocket.send_json(
                    {"type": "transcript", "text": text, "isFinal": False}
                )
        except Exception:
            pass
        finally:
            interim_running = False

    try:
        while True:
            msg = await websocket.receive()

            if msg["type"] == "websocket.disconnect":
                break

            if msg.get("text"):
                try:
                    ctrl = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue

                if ctrl.get("type") == "start":
                    ended = False
                    audio_buf.clear()
                    last_interim_at = 0

                elif ctrl.get("type") == "end":
                    ended = True
                    if len(audio_buf) > SAMPLE_RATE * 2 * 0.3:
                        wav = _pcm_to_wav(bytes(audio_buf))
                        # 最終結果會存進資料庫、餵給 LLM，準確度優先於速度，
                        # 用中文微調過的模型；interim 預覽文字才用預設的快模型。
                        text = await stt_service.transcribe_bytes(
                            wav, model=settings.stt_model_final
                        )
                        await websocket.send_json(
                            {"type": "transcript", "text": text, "isFinal": True}
                        )
                    audio_buf.clear()
                    last_interim_at = 0

            elif msg.get("bytes"):
                audio_buf.extend(msg["bytes"])

                if not interim_running and len(audio_buf) - last_interim_at >= INTERIM_BYTES:
                    last_interim_at = len(audio_buf)
                    interim_running = True
                    asyncio.create_task(run_interim(bytes(audio_buf)))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS/STT] {e}")
    finally:
        ws_registry.unregister(session_id, websocket)
        ended_via_control = ws_registry.consume_ending(session_id)
        if not ended_via_control:
            reached_closing = await r.get(f"session:{session_id}:reached_closing")
            if not reached_closing:
                await _mark_abnormal_end(session_id)
