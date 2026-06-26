import asyncio
import io
import json
import wave

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


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
async def ws_stt(websocket: WebSocket):
    """
    接收 Unity 送來的 PCM int16 mono 16kHz 音訊 chunks。

    控制訊息 (text frame):
      {"type": "start"} — 清空緩衝區，開始新一段錄音
      {"type": "end"}   — 對完整緩衝區做最終辨識，isFinal=true

    音訊資料 (binary frame):
      raw PCM int16, 16 kHz, mono

    回傳 (text frame):
      {"type": "transcript", "text": "...", "isFinal": true|false}
    """
    stt_service = websocket.app.state.stt_service

    await websocket.accept()
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
                        text = await stt_service.transcribe_bytes(wav)
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
