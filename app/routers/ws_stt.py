import asyncio
import io
import json
import wave

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import update

from auth import get_therapist_id_from_ws_token
from db.models import Patient, TherapySession
from db.session import AsyncSessionLocal
from routers.session import _compute_and_save_assessment
import ws_registry

router = APIRouter()


async def _mark_abnormal_end(app_state, session_id: str, therapist_id: int) -> None:
    """/ws/stt 斷線，但既不是治療師按「結束活動」（ws_registry.consume_ending）、
    也不是三回合正常跑完轉場去問心得（session:{id}:reached_closing，見
    session.py session_respond 對 end_session 的處理）——代表長者端 App 或
    治療師網頁被直接關掉、當機、斷線，療程不正常中止。status 卡在 in_progress
    會讓治療師頁面（cases/[id]/page.tsx）誤判成「還在進行中」、把治療師導去
    永遠不會再更新的即時監控頁，這裡把它視同已結束。

    2026-09-06 稽核：這裡原本只是把 status 直接改成 completed，完全沒有算
    分數——治療師點開這種療程的結束頁，看到 status 顯示「已完成」卻五個
    分數全是 null，前端 DEFAULT_SCORES 補位機制會顯示一組編出來的假分數，
    看起來像是真的評估過。改成優先呼叫 _compute_and_save_assessment，跟
    治療師手動結束的路徑（session.py session_control 的 end action）用
    同一套邏輯——長者離線前只要有送過幾幀感測資料，就能算出一個真實（哪怕
    資料有限）的評估。只有 Redis 連 session:{id}:stats 都沒有（一幀都沒收到
    就斷線）這種完全沒東西可算的情況，才退回原本單純標記 completed 的行為，
    不然評估流程本身無法失敗式地產生分數。"""
    if not session_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            try:
                await _compute_and_save_assessment(app_state, session_id, db, therapist_id)
                return
            except HTTPException:
                pass  # session:{id}:stats 不存在，真的一幀資料都沒收到，退回下面的舊行為
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


async def _build_patient_prompt(r, session_id: str) -> str:
    """從 session:{id}:meta 查出 patient_id/topic，組出 STT 最終辨識用的 initial prompt。

    把長者的姓名/故鄉/家人/興趣，加上這場療程的今日主題（治療師啟動療程時
    手動輸入的自由文字，見 _init_session_meta 的 topic 參數）餵給 Whisper
    當前文脈絡，同音字辨識時會偏向選這裡出現過的詞或同主題詞彙，藉此提升
    人名、地名，以及當天話題相關詞彙的辨識率（見 app/services/stt.py transcribe_bytes 的 prompt 參數）。
    topic 是自由文字、沒有固定詞庫，換成任何主題都能直接沿用，不需要為
    每個主題另外維護詞彙表。查不到就回傳空字串，上層會直接跳過 prompt，
    不影響原本的辨識行為。
    """
    try:
        meta_raw = await r.get(f"session:{session_id}:meta")
        if not meta_raw:
            return ""
        meta = json.loads(meta_raw)
        patient_id = meta.get("patient_id")
        topic = meta.get("topic")
        if not patient_id:
            return ""

        async with AsyncSessionLocal() as db:
            patient = await db.get(Patient, int(patient_id))
        if not patient:
            return ""

        parts = [f"長者{patient.name}"]
        if patient.hometown:
            parts.append(f"故鄉在{patient.hometown}")
        if patient.family:
            parts.append(f"家人有{patient.family}")
        if patient.preferences:
            parts.append(f"興趣是{patient.preferences}")
        if topic:
            parts.append(f"今天聊的主題是{topic}")
        return "，".join(parts) + "。"
    except Exception as e:
        print(f"[WS/STT] 組 initial_prompt 失敗: {e}")
        return ""


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
        therapist_id = await get_therapist_id_from_ws_token(websocket.app.state.redis, token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    stt_service = websocket.app.state.stt_service
    r = websocket.app.state.redis

    await websocket.accept()
    ws_registry.register(session_id, websocket)
    audio_buf: bytearray = bytearray()
    # 整條連線對應同一場療程、同一位長者，只在連線建立時查一次即可。
    stt_prompt = await _build_patient_prompt(r, session_id)

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
                        # 最終結果會存進資料庫、餵給 LLM，用中文微調過的模型
                        # （interim 預覽文字現在也是同一個模型，見 config.py stt_model）。
                        # timeout 拉長：BELLE 現在雖然靠 PRELOAD_MODELS+WHISPER__TTL=-1
                        # 常駐在 stt，但萬一它重啟又要冷啟動（可能超過10分鐘），
                        # 預設 120 秒的 httpx timeout 會讓這裡拋例外、把整條 WebSocket
                        # 連線打斷（見本函式外層 except），辨識文字就永遠送不到後端。
                        text = await stt_service.transcribe_bytes(
                            wav, timeout=600.0, prompt=stt_prompt,
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
                await _mark_abnormal_end(websocket.app.state, session_id, therapist_id)
