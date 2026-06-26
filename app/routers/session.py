import json
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.deps import get_db
from db.models import TherapySession

router = APIRouter(prefix="/session", tags=["session"])


async def _init_session_meta(r, session_id: str, patient_id: str, therapist_id: str = "") -> None:
    """首次啟動療程時寫入 meta（已存在則略過，避免 start/round 重複呼叫時覆蓋 start_at）。"""
    key = f"session:{session_id}:meta"
    if not await r.exists(key):
        meta = {
            "session_id":   session_id,
            "patient_id":   patient_id,
            "therapist_id": therapist_id,
            "start_at":     int(time.time() * 1000),
            "status":       "active",
        }
        await r.set(key, json.dumps(meta))


# ════════════ 評估分數計算輔助 ════════════════════════════════════════

def _score_attention(looking_away_rate: float, eye_closed_rate: float) -> int:
    """注意力：視線離開比率越低分數越高。"""
    raw = 1.0 - looking_away_rate - eye_closed_rate * 0.5
    if raw < 0.25: return 1
    if raw < 0.50: return 2
    if raw < 0.75: return 3
    return 4


def _score_engagement(looking_away_rate: float, mouth_moved_rate: float, high_sway_rate: float) -> int:
    """參與度：干擾行為(晃動) > 不注意 > 主動說話 的優先判斷順序。"""
    if high_sway_rate > 0.50:                              return 1  # 干擾
    if looking_away_rate > 0.50:                           return 2  # 被動
    if mouth_moved_rate > 0.30 and looking_away_rate < 0.30: return 4  # 主動
    return 3                                                          # 可配合


def _score_persistence(sad_rate: float, looking_away_rate: float,
                       skel_absent_rate: float, far_rate: float = 0.0) -> int:
    """持續力（AES）：離座（骨架消失或 SpineBase 移遠）或情緒極度低落 = 1分。"""
    if sad_rate > 0.50 or skel_absent_rate > 0.30 or far_rate > 0.20:  return 1
    if sad_rate > 0.30 or looking_away_rate > 0.60:                     return 2
    if sad_rate > 0.10:                                                  return 3
    return 4


def _score_emotion(emo: dict[str, int],
                   high_pitch_rate: float = 0.0,
                   pitch_baseline: float = 0.0) -> int:
    """情緒狀況（OERS）：以 EMA 主導情緒為基底，音高變異作修正。

    silence_rate 已從此函式移除：silence_n 計整個療程靜默 frame，
    長者正常參與時 silence_rate 仍超過 0.90，用它作情緒門檻會讓所有人得 1 分。
    「完全不說話」信號由 _score_interaction（response_count == 0）負責。

    high_pitch_rate 的計數門檻已在 sensor.py 個人化（baseline × 2），
    有基準時用 0.40；無基準時退守 0.55（計數仍用固定 50 Hz²，較不可靠）。
    """
    pitch_agitation_bar = 0.40 if pitch_baseline > 1.0 else 0.55
    if high_pitch_rate > pitch_agitation_bar and emo.get("angry", 0) >= emo.get("happy", 0):
        return 2
    dominant = max(emo, key=emo.get) if any(emo.values()) else "happy"
    return {"sad": 1, "angry": 2, "excited": 3, "happy": 4}[dominant]


def _score_interaction(response_count: int, speech_chars: int,
                       avg_response_ms: float | None = None,
                       hand_active_rate: float = 0.0) -> int:
    """互動頻率（Social Engagement Scale）：語音 + 反應延遲 + 手部動作。"""
    if response_count == 0 and hand_active_rate < 0.05:   return 1
    if response_count == 0:                                return 2  # 有肢體動作但無語音
    avg = speech_chars / response_count
    # 反應延遲懲罰：平均超過 10 秒視為需持續引導
    if avg_response_ms is not None and avg_response_ms > 10_000:
        avg *= 0.75
    if avg < 10:   return 2   # 僅指令回覆（嗯/好/有）
    if avg < 25:   return 3   # 需引導互動
    return 4                  # 主動互動（完整句子/故事）


class SessionState(BaseModel):
    user_id: str
    session_id: str
    round: int
    scene_elements: list[str]
    covered_w: list[str] = []
    skipped_w: list[str] = []
    last_question_type: str = "step1"
    last_w_asked: str = ""


class RespondRequest(BaseModel):
    elder_response: str
    state: SessionState


@router.post("/start")
async def session_start(request: Request, user_id: str, session_id: str, therapist_id: str = ""):
    """
    啟動療程第一回合（backward-compat，等同 /session/round?round_number=1）。

    回傳包含 state 欄位，請儲存並傳給後續 /session/respond。
    """
    orchestrator = request.app.state.orchestrator
    try:
        result = await orchestrator.start_round(
            user_id=user_id,
            session_id=session_id,
            round_number=1,
        )
        await _init_session_meta(request.app.state.redis, session_id, user_id, therapist_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"療程開場失敗: {str(e)}")


@router.post("/round")
async def session_round(request: Request, user_id: str, session_id: str, round_number: int = 1, therapist_id: str = ""):
    """
    開始指定回合（n=1,2,3）。

    收到 end_round 後，用 next_round 呼叫此端點繼續下一回合。
    回傳包含 state 欄位，請儲存並傳給後續 /session/respond。
    """
    orchestrator = request.app.state.orchestrator
    try:
        result = await orchestrator.start_round(
            user_id=user_id,
            session_id=session_id,
            round_number=round_number,
        )
        await _init_session_meta(request.app.state.redis, session_id, user_id, therapist_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回合開場失敗: {str(e)}")


@router.get("/{session_id}/metrics", summary="取得即時檢測回饋（供治療師頁面 polling）")
async def session_metrics(request: Request, session_id: str):
    r = request.app.state.redis
    data: dict = await r.hgetall(f"session:{session_id}:metrics")
    return {
        "emotion": data.get("emotion", "適當"),
        "response_time": data.get("response_time", "--"),
    }


class TranscriptPayload(BaseModel):
    text: str


@router.post("/{session_id}/response", summary="記錄 STT 最終辨識結果（供互動頻率統計）")
async def session_transcript(request: Request, session_id: str, body: TranscriptPayload):
    if not body.text.strip():
        return {"ok": True, "skipped": True}
    r = request.app.state.redis
    key = f"session:{session_id}:stats"
    pipe = r.pipeline(transaction=False)
    pipe.hincrby(key, "response_count", 1)
    pipe.hincrby(key, "speech_chars",   len(body.text.strip()))
    await pipe.execute()
    await r.expire(key, 86400)
    return {"ok": True}


@router.get("/{session_id}/assessment", summary="產出療程結束五指標評估分數（1-4 分）並寫入 PostgreSQL")
async def session_assessment(request: Request, session_id: str, db: AsyncSession = Depends(get_db)):
    r = request.app.state.redis
    raw: dict = await r.hgetall(f"session:{session_id}:stats")
    if not raw:
        raise HTTPException(status_code=404, detail="找不到此療程的統計資料，請確認 session_id 正確且療程已進行")

    frame_count       = max(int(raw.get("frame_count",         0)), 1)
    looking_away_n    = int(raw.get("looking_away_n",        0))
    eye_closed_n      = int(raw.get("eye_closed_n",          0))
    mouth_moved_n     = int(raw.get("mouth_moved_n",         0))
    high_sway_n       = int(raw.get("high_sway_n",           0))
    skel_absent_n     = int(raw.get("skel_absent_n",         0))
    response_count    = int(raw.get("response_count",        0))
    speech_chars      = int(raw.get("speech_chars",          0))
    # A 階段擴充
    silence_n         = int(raw.get("silence_n",             0))
    rt_sum            = int(raw.get("response_time_sum",     0))
    rt_count          = int(raw.get("response_time_count",   0))
    # B 階段擴充（Unity 尚未傳送時 = 0，不影響評分）
    far_n             = int(raw.get("far_n",                 0))
    high_pitch_var_n  = int(raw.get("high_pitch_var_n",      0))
    hand_active_n     = int(raw.get("hand_active_n",         0))
    emo = {
        "happy":   int(raw.get("emo_happy",   0)),
        "excited": int(raw.get("emo_excited", 0)),
        "angry":   int(raw.get("emo_angry",   0)),
        "sad":     int(raw.get("emo_sad",     0)),
    }

    looking_away_rate = looking_away_n   / frame_count
    eye_closed_rate   = eye_closed_n     / frame_count
    mouth_moved_rate  = mouth_moved_n    / frame_count
    high_sway_rate    = high_sway_n      / frame_count
    skel_absent_rate  = skel_absent_n    / frame_count
    sad_rate          = emo["sad"]       / frame_count
    silence_rate      = silence_n        / frame_count
    far_rate          = far_n            / frame_count
    high_pitch_rate   = high_pitch_var_n / frame_count
    hand_active_rate  = hand_active_n    / frame_count
    avg_response_ms   = rt_sum / rt_count if rt_count > 0 else None

    # 讀取個人音高校正基準（必須在 scores 計算之前）
    calib_raw      = await r.get(f"session:{session_id}:calibration")
    calib          = json.loads(calib_raw) if calib_raw else {}
    pitch_baseline = float(calib.get("pitchVarianceBaseline", 0.0))

    scores = {
        "參與度":   _score_engagement(looking_away_rate, mouth_moved_rate, high_sway_rate),
        "注意力":   _score_attention(looking_away_rate, eye_closed_rate),
        "持續力":   _score_persistence(sad_rate, looking_away_rate, skel_absent_rate, far_rate),
        "情緒狀況": _score_emotion(emo, high_pitch_rate, pitch_baseline),
        "互動頻率": _score_interaction(response_count, speech_chars, avg_response_ms, hand_active_rate),
    }

    # 從 Redis meta 讀取 patient_id / therapist_id
    meta_key = f"session:{session_id}:meta"
    meta_raw = await r.get(meta_key)
    meta = json.loads(meta_raw) if meta_raw else {"session_id": session_id}

    # PostgreSQL 永久寫入
    try:
        def _to_int(val) -> int | None:
            try:
                return int(val) or None
            except (TypeError, ValueError):
                return None

        total = sum(scores.values())
        stmt = (
            pg_insert(TherapySession)
            .values(
                session_uuid=session_id,
                patient_id=_to_int(meta.get("patient_id")),
                therapist_id=_to_int(meta.get("therapist_id")),
                date=date.today(),
                mode="interactive",
                score_participation=scores["參與度"],
                score_attention=scores["注意力"],
                score_endurance=scores["持續力"],
                score_emotion=scores["情緒狀況"],
                score_interaction=scores["互動頻率"],
                total_score=total,
            )
            .on_conflict_do_update(
                index_elements=["session_uuid"],
                set_={
                    "score_participation": scores["參與度"],
                    "score_attention": scores["注意力"],
                    "score_endurance": scores["持續力"],
                    "score_emotion": scores["情緒狀況"],
                    "score_interaction": scores["互動頻率"],
                    "total_score": total,
                },
            )
        )
        await db.execute(stmt)
        await db.commit()

        # DB 寫入成功後清除所有 session Redis key
        await r.delete(
            f"session:{session_id}:meta",
            f"session:{session_id}:stats",
            f"session:{session_id}:metrics",
            f"session:{session_id}:ema",
            f"session:{session_id}:calibration",
        )
    except Exception as e:
        print(f"[DB] 療程寫入失敗 ({session_id}): {e}")
        await db.rollback()

    return scores


@router.post("/respond")
async def session_respond(request: Request, body: RespondRequest):
    """
    長者說完話後呼叫此端點，取得下一步動作。

    回傳的 action：
      open_followup    → 話題豐富，繼續順著長者深入（含 scene_text + question）
      ask_supplement_w → 話題結束，切入未問的W維度（含 scene_text + question）
      end_round        → 本回合完成，用 next_round 呼叫 /session/round
      end_session      → 三回合結束，療程收尾

    前端每次收到回應後，用回傳的 state 取代本地的 state。
    """
    orchestrator = request.app.state.orchestrator
    try:
        return await orchestrator.process_response(
            elder_response=body.elder_response,
            state=body.state.model_dump(),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"處理回應失敗: {str(e)}")
