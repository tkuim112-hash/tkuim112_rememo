import json
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from audit import log_access
from auth import get_current_therapist_id
from db.deps import get_db
from db.models import TherapySession, TherapyRound, RoundExchange

router = APIRouter(prefix="/session", tags=["session"])


def _to_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


async def _init_session_meta(
    r, session_id: str, patient_id: str, therapist_id: str = "", topic: str = "",
) -> None:
    """首次啟動療程時寫入 meta（已存在則略過，避免 start/round 重複呼叫時覆蓋 start_at）。

    topic 是治療師啟動療程時手動輸入的今日主題（見 /session/start 的 topic 參數），
    只在第一次（round 1）寫入，round 2/3 靠 _get_session_topic 讀出來沿用同一個主題。
    """
    key = f"session:{session_id}:meta"
    if not await r.exists(key):
        meta = {
            "session_id":   session_id,
            "patient_id":   patient_id,
            "therapist_id": therapist_id,
            "start_at":     int(time.time() * 1000),
            "status":       "active",
            "topic":        topic,
        }
        await r.set(key, json.dumps(meta))


async def _get_session_topic(r, session_id: str) -> str | None:
    """讀出這場療程啟動時（round 1）設定的今日主題，round 2/3 沿用。"""
    meta_raw = await r.get(f"session:{session_id}:meta")
    if not meta_raw:
        return None
    return json.loads(meta_raw).get("topic") or None


async def _update_live_view(r, session_id: str, **fields) -> None:
    """把場景/長者回應/AI建議寫進 metrics hash，供治療師 live 頁每 2 秒 polling。"""
    try:
        mapping = {}
        for k, v in fields.items():
            if v is None:
                continue
            mapping[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, list) else str(v)
        if mapping:
            await r.hset(f"session:{session_id}:metrics", mapping=mapping)
    except Exception as e:
        print(f"[LiveView] metrics 更新失敗（不影響主流程）: {e}")


async def _get_or_create_round(
    db: AsyncSession,
    session_id: str,
    round_number: int,
    patient_id: int | None = None,
    therapist_id: int | None = None,
    round_type: str | None = None,
) -> TherapyRound | None:
    """upsert sessions 佔位記錄（等 assessment 填分數），回傳對應的 rounds row（沒有就建）。

    rounds 的 upsert 走 ON CONFLICT DO NOTHING（搭配 rounds(session_id, round_number)
    唯一約束），避免同一回合被併發請求（例如前端重試）SELECT-then-INSERT 出重複列。
    """
    stmt = (
        pg_insert(TherapySession)
        .values(
            session_uuid=session_id,
            patient_id=patient_id,
            therapist_id=therapist_id,
            date=date.today(),
            mode="interactive",
        )
        .on_conflict_do_nothing(index_elements=["session_uuid"])
    )
    await db.execute(stmt)
    await db.flush()

    session_row = (
        await db.execute(
            select(TherapySession).where(TherapySession.session_uuid == session_id)
        )
    ).scalar_one_or_none()

    if session_row is None:
        return None

    round_stmt = (
        pg_insert(TherapyRound)
        .values(
            session_id=session_row.id,
            round_number=round_number,
            type=round_type,
        )
        .on_conflict_do_nothing(index_elements=["session_id", "round_number"])
    )
    await db.execute(round_stmt)
    await db.flush()

    round_row = (
        await db.execute(
            select(TherapyRound).where(
                TherapyRound.session_id == session_row.id,
                TherapyRound.round_number == round_number,
            )
        )
    ).scalar_one_or_none()

    if round_row is not None and round_type and not round_row.type:
        round_row.type = round_type

    return round_row


async def _save_round_image(
    db: AsyncSession,
    session_id: str,
    round_number: int,
    image_path: str,
    scene_text: str = "",
    patient_id: int | None = None,
    therapist_id: int | None = None,
) -> None:
    """把回合開場的圖片路徑與場景文字寫入 rounds.scene_image / generated_scene。"""
    try:
        round_row = await _get_or_create_round(
            db, session_id, round_number, patient_id, therapist_id
        )
        if round_row is None:
            return
        round_row.scene_image = image_path
        if scene_text:
            round_row.generated_scene = scene_text
        await db.commit()
        print(f"[DB] rounds.scene_image/generated_scene 寫入成功: round={round_number} path={image_path}")

    except Exception as e:
        print(f"[DB] rounds.scene_image 寫入失敗（不影響主流程）: {e}")
        await db.rollback()


async def _save_round_response(
    db: AsyncSession,
    session_id: str,
    round_number: int,
    text: str,
    emotion: str = "",
    patient_id: int | None = None,
    therapist_id: int | None = None,
) -> None:
    """
    把長者原話累加到 rounds.patient_response（真相源，之後可重建向量庫），
    同回合多次回應以換行分隔；emotion 記錄該回合最後一次偵測值。
    """
    try:
        round_row = await _get_or_create_round(
            db, session_id, round_number, patient_id, therapist_id
        )
        if round_row is None:
            return
        if round_row.patient_response:
            round_row.patient_response += "\n" + text
        else:
            round_row.patient_response = text
        if emotion:
            round_row.emotion = emotion
        await db.commit()
        print(f"[DB] rounds.patient_response 寫入成功: round={round_number} len={len(text)}")

    except Exception as e:
        print(f"[DB] rounds.patient_response 寫入失敗（不影響主流程）: {e}")
        await db.rollback()


async def _save_round_exchange(
    db: AsyncSession,
    session_id: str,
    round_number: int,
    question_number: int,
    question: str,
    round_type: str | None = None,
    patient_id: int | None = None,
    therapist_id: int | None = None,
) -> None:
    """AI 每問一個新問題就新增一筆 round_exchanges（answer 先留空，長者回答後由
    _fill_round_exchange_answer 補上）。"""
    try:
        round_row = await _get_or_create_round(
            db, session_id, round_number, patient_id, therapist_id, round_type
        )
        if round_row is None:
            return
        await db.flush()
        db.add(RoundExchange(
            round_id=round_row.id,
            question_number=question_number,
            question=question,
        ))
        await db.commit()
        print(f"[DB] round_exchanges 新增問題: round={round_number} q#={question_number}")
    except Exception as e:
        print(f"[DB] round_exchanges 寫入失敗（不影響主流程）: {e}")
        await db.rollback()


async def _fill_round_exchange_answer(
    db: AsyncSession,
    session_id: str,
    round_number: int,
    question_number: int,
    answer: str,
) -> None:
    """長者回答後，補上對應 round_exchanges 列的 answer。"""
    try:
        round_row = await _get_or_create_round(db, session_id, round_number)
        if round_row is None:
            return
        exchange = (
            await db.execute(
                select(RoundExchange).where(
                    RoundExchange.round_id == round_row.id,
                    RoundExchange.question_number == question_number,
                )
            )
        ).scalar_one_or_none()
        if exchange is None:
            return
        exchange.answer = answer
        await db.commit()
        print(f"[DB] round_exchanges 補上答案: round={round_number} q#={question_number}")
    except Exception as e:
        print(f"[DB] round_exchanges answer 寫入失敗（不影響主流程）: {e}")
        await db.rollback()


async def _accumulate_round_response_time(r, session_id: str, round_number: int, elapsed_ms: int) -> None:
    """長者這題花了多久回答，累加進本回合的 Redis 暫存，回合結束時取平均寫進 rounds.response_time。"""
    key = f"session:{session_id}:round:{round_number}:timing"
    pipe = r.pipeline(transaction=False)
    pipe.hincrby(key, "sum_ms", elapsed_ms)
    pipe.hincrby(key, "count", 1)
    await pipe.execute()
    await r.expire(key, 86400)


async def _finalize_round_response_time(
    db: AsyncSession,
    r,
    session_id: str,
    round_number: int,
    patient_id: int | None = None,
    therapist_id: int | None = None,
) -> None:
    """回合結束（end_round / end_session）時，把這回合累積的平均反應時間（秒）寫進 rounds.response_time。"""
    key = f"session:{session_id}:round:{round_number}:timing"
    raw = await r.hgetall(key)
    count = int(raw.get("count", 0))
    if count > 0:
        avg_seconds = int(raw.get("sum_ms", 0)) / count / 1000
        try:
            round_row = await _get_or_create_round(db, session_id, round_number, patient_id, therapist_id)
            if round_row is not None:
                round_row.response_time = round(avg_seconds, 1)
                await db.commit()
                print(f"[DB] rounds.response_time 寫入成功: round={round_number} avg={avg_seconds:.1f}s")
        except Exception as e:
            print(f"[DB] rounds.response_time 寫入失敗（不影響主流程）: {e}")
            await db.rollback()
    await r.delete(key)


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
    question_number: int = 1  # 本回合目前問到第幾題，供 round_exchanges 配對與 TTS 檔名編號
    question_asked_at: int = 0  # 目前這一題送出的時間（epoch ms），供計算 rounds.response_time


class RespondRequest(BaseModel):
    elder_response: str
    state: SessionState


@router.post("/start")
async def session_start(
    request: Request,
    user_id: str,
    session_id: str,
    topic: str = "",
    therapist_id: int = Depends(get_current_therapist_id),
    db: AsyncSession = Depends(get_db),
):
    """
    啟動療程第一回合（backward-compat，等同 /session/round?round_number=1）。

    topic：治療師手動輸入的今日主題（不填則沿用 Patient.scene_weights / 預設值）。
    回傳包含 state 欄位，請儲存並傳給後續 /session/respond。
    """
    orchestrator = request.app.state.orchestrator
    topic = topic.strip()
    try:
        result = await orchestrator.start_round(
            user_id=user_id,
            session_id=session_id,
            round_number=1,
            topic_override=topic or None,
        )
        result["state"]["question_number"] = 1
        result["state"]["question_asked_at"] = int(time.time() * 1000)
        tts = request.app.state.tts_service
        audio_path = await tts.synthesize(
            text=result["scene_text"] + result["question"],
            session_id=session_id,
            round_number=1,
            turn_number=1,
        )
        result["audio_path"] = audio_path
        await _init_session_meta(request.app.state.redis, session_id, user_id, str(therapist_id), topic=topic)
        await _update_live_view(
            request.app.state.redis, session_id,
            current_scene=result.get("scene_text", "") + result.get("question", ""),
            elder_response="",
            ai_suggestions=[result["question"]] if result.get("question") else [],
            current_round=1,
            total_rounds=3,
        )
        await log_access(
            therapist_id=therapist_id,
            patient_id=_to_int(user_id),
            action="start_session",
            resource=f"session:{session_id}",
        )
        await _save_round_image(
            db, session_id, 1, result.get("image_path", ""),
            scene_text=result.get("scene_text", ""),
            patient_id=_to_int(user_id), therapist_id=therapist_id,
        )
        if result.get("question"):
            await _save_round_exchange(
                db, session_id, 1, question_number=1, question=result["question"],
                patient_id=_to_int(user_id), therapist_id=therapist_id,
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"療程開場失敗: {str(e)}")


@router.post("/round")
async def session_round(
    request: Request,
    user_id: str,
    session_id: str,
    round_number: int = 1,
    therapist_id: int = Depends(get_current_therapist_id),
    db: AsyncSession = Depends(get_db),
):
    """
    開始指定回合（n=1,2,3）。

    收到 end_round 後，用 next_round 呼叫此端點繼續下一回合。
    回傳包含 state 欄位，請儲存並傳給後續 /session/respond。
    """
    orchestrator = request.app.state.orchestrator
    try:
        topic_override = await _get_session_topic(request.app.state.redis, session_id)
        result = await orchestrator.start_round(
            user_id=user_id,
            session_id=session_id,
            round_number=round_number,
            topic_override=topic_override,
        )
        result["state"]["question_number"] = 1
        result["state"]["question_asked_at"] = int(time.time() * 1000)
        if result.get("question"):
            tts = request.app.state.tts_service
            audio_path = await tts.synthesize(
                text=result["scene_text"] + result["question"],
                session_id=session_id,
                round_number=round_number,
                turn_number=1,
            )
            result["audio_path"] = audio_path
        await _init_session_meta(request.app.state.redis, session_id, user_id, str(therapist_id))
        await _update_live_view(
            request.app.state.redis, session_id,
            current_scene=result.get("scene_text", "") + result.get("question", ""),
            elder_response="",
            ai_suggestions=[result["question"]] if result.get("question") else [],
            current_round=round_number,
            total_rounds=3,
        )
        await _save_round_image(
            db, session_id, round_number, result.get("image_path", ""),
            scene_text=result.get("scene_text", ""),
            patient_id=_to_int(user_id), therapist_id=therapist_id,
        )
        if result.get("question"):
            await _save_round_exchange(
                db, session_id, round_number, question_number=1, question=result["question"],
                patient_id=_to_int(user_id), therapist_id=therapist_id,
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回合開場失敗: {str(e)}")


@router.get("/{session_id}/metrics", summary="取得即時檢測回饋（供治療師頁面 polling）")
async def session_metrics(
    request: Request,
    session_id: str,
    therapist_id: int = Depends(get_current_therapist_id),
):
    r = request.app.state.redis
    data: dict = await r.hgetall(f"session:{session_id}:metrics")
    try:
        suggestions = json.loads(data.get("ai_suggestions", "[]"))
    except json.JSONDecodeError:
        suggestions = []
    return {
        "emotion": data.get("emotion", "適當"),
        "response_time": data.get("response_time", "--"),
        "current_scene": data.get("current_scene", ""),
        "elder_response": data.get("elder_response", ""),
        "ai_suggestions": suggestions,
        "current_round": _to_int(data.get("current_round")) or 1,
        "total_rounds": _to_int(data.get("total_rounds")) or 3,
    }


class TranscriptPayload(BaseModel):
    text: str


@router.post("/{session_id}/response", summary="記錄 STT 最終辨識結果（供互動頻率統計）")
async def session_transcript(
    request: Request,
    session_id: str,
    body: TranscriptPayload,
    therapist_id: int = Depends(get_current_therapist_id),
):
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


_EMOTION_LABEL_MAP = {"happy": "適當", "excited": "亢奮", "angry": "焦躁", "sad": "低落"}


async def _generate_story_summary(llm_service, db: AsyncSession, session_id: str) -> str:
    """讀取這場療程所有回合的場景與長者發言，請 LLM 整理成簡短故事摘要。失敗時回傳空字串（不影響評估寫入）。"""
    try:
        session_row = (
            await db.execute(select(TherapySession).where(TherapySession.session_uuid == session_id))
        ).scalar_one_or_none()
        if session_row is None:
            return ""
        rounds = (
            await db.execute(
                select(TherapyRound)
                .where(TherapyRound.session_id == session_row.id)
                .order_by(TherapyRound.round_number)
            )
        ).scalars().all()

        parts = []
        for rnd in rounds:
            label = "心得" if rnd.type == "心得" else f"第{rnd.round_number}回合"
            if rnd.generated_scene:
                parts.append(f"【{label}｜場景】{rnd.generated_scene}")
            if rnd.patient_response:
                parts.append(f"【{label}｜長者所說】{rnd.patient_response}")
        if not parts:
            return ""
        transcript = "\n".join(parts)

        messages = [
            {"role": "system", "content": "你是懷舊療法的紀錄整理助手，負責把一場療程的對話內容整理成簡短的故事摘要，給治療師和家屬快速了解今天聊了什麼。"},
            {"role": "user", "content": (
                f"以下是今天療程的場景與長者發言記錄：\n\n{transcript}\n\n"
                "請用100字以內、第三人稱、溫暖但客觀的語氣，摘要長者今天分享的回憶內容與整體狀態。"
                "只回摘要文字，不要其他說明。"
            )},
        ]
        summary = await llm_service.chat(messages)
        return summary.strip()
    except Exception as e:
        print(f"[LLM] story_summary 生成失敗（不影響評估寫入）: {e}")
        return ""


async def _compute_and_save_assessment(
    request: Request,
    session_id: str,
    db: AsyncSession,
    therapist_id: int,
) -> dict:
    """計算五指標評估分數、產出故事摘要與整體情緒，寫入 PostgreSQL，並清除 Redis session 暫存。"""
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

    dominant_emotion = max(emo, key=emo.get) if any(emo.values()) else "happy"
    emotional_status = _EMOTION_LABEL_MAP.get(dominant_emotion, "適當")
    story_summary = await _generate_story_summary(request.app.state.llm_service, db, session_id)

    # PostgreSQL 永久寫入
    try:
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
                emotional_status=emotional_status,
                story_summary=story_summary or None,
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
                    "emotional_status": emotional_status,
                    # story_summary 若這次沒生成成功（例如 LLM 逾時），保留舊值，不要用空字串蓋掉
                    **({"story_summary": story_summary} if story_summary else {}),
                },
            )
        )
        await db.execute(stmt)
        await db.commit()
        await log_access(
            therapist_id=therapist_id,
            patient_id=_to_int(meta.get("patient_id")),
            action="generate_assessment",
            resource=f"session:{session_id}",
        )

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


@router.get("/{session_id}/assessment", summary="產出療程結束五指標評估分數（1-4 分）並寫入 PostgreSQL")
async def session_assessment(
    request: Request,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    therapist_id: int = Depends(get_current_therapist_id),
):
    return await _compute_and_save_assessment(request, session_id, db, therapist_id)


@router.post("/{session_id}/closing", summary="記錄三回合結束後的心得回合，並自動觸發評估寫入")
async def session_closing(
    request: Request,
    session_id: str,
    body: TranscriptPayload,
    db: AsyncSession = Depends(get_db),
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    長者念完收尾心得後呼叫此端點：
      1. 把心得問答存成 rounds.round_number=4（type='心得'）+ round_exchanges
      2. 自動觸發 /assessment 邏輯，產出五指標評分、故事摘要、整體情緒並寫入 PostgreSQL

    收尾問題文字取自 /session/respond 觸發 end_session 時寫進 Redis metrics 的 ai_suggestions
    （心得回合只有這一次問答，不像 1-3 回合有多輪追問，所以問題和答案一次一起存）。
    """
    r = request.app.state.redis
    meta_raw = await r.get(f"session:{session_id}:meta")
    meta = json.loads(meta_raw) if meta_raw else {}
    patient_id = _to_int(meta.get("patient_id"))

    metrics = await r.hgetall(f"session:{session_id}:metrics")
    try:
        suggestions = json.loads(metrics.get("ai_suggestions", "[]"))
    except json.JSONDecodeError:
        suggestions = []
    closing_question = suggestions[0] if suggestions else ""

    # 心得問題出現的時間點（session_respond 觸發 end_session 時存的），用來算這回合的反應時間
    closing_asked_at_raw = await r.get(f"session:{session_id}:closing_asked_at")

    if body.text.strip():
        try:
            round_row = await _get_or_create_round(
                db, session_id, 4, patient_id, therapist_id, round_type="心得",
            )
            if round_row is not None:
                round_row.patient_response = body.text
                if closing_asked_at_raw:
                    elapsed_ms = max(0, int(time.time() * 1000) - int(closing_asked_at_raw))
                    round_row.response_time = round(elapsed_ms / 1000, 1)
                await db.flush()
                db.add(RoundExchange(
                    round_id=round_row.id,
                    question_number=1,
                    question=closing_question,
                    answer=body.text,
                ))
                await db.commit()
                print(f"[DB] 心得回合寫入成功: session={session_id}")
        except Exception as e:
            print(f"[DB] 心得回合寫入失敗（不影響評估流程）: {e}")
            await db.rollback()
    await r.delete(f"session:{session_id}:closing_asked_at")

    try:
        scores = await _compute_and_save_assessment(request, session_id, db, therapist_id)
    except HTTPException as e:
        print(f"[Closing] 自動評估略過: {e.detail}")
        scores = None

    return {"ok": True, "assessment": scores}


@router.post("/respond")
async def session_respond(
    request: Request,
    body: RespondRequest,
    therapist_id: int = Depends(get_current_therapist_id),
    db: AsyncSession = Depends(get_db),
):
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
        r = request.app.state.redis
        metrics = await r.hgetall(f"session:{body.state.session_id}:metrics")
        emotion = metrics.get("emotion_raw", "")  # 沒有 Kinect 數據時存空值，不假造 happy
        # 先落地逐字稿（真相源），後續 LLM 流程失敗也不遺失長者的話
        await _save_round_response(
            db, body.state.session_id, body.state.round,
            text=body.elder_response, emotion=emotion,
            patient_id=_to_int(body.state.user_id), therapist_id=therapist_id,
        )
        # 補上長者剛剛回答的那一題的 answer
        await _fill_round_exchange_answer(
            db, body.state.session_id, body.state.round,
            question_number=body.state.question_number, answer=body.elder_response,
        )
        # 這一題長者花了多久回答，累加進本回合的反應時間統計
        if body.state.question_asked_at:
            elapsed_ms = max(0, int(time.time() * 1000) - body.state.question_asked_at)
            await _accumulate_round_response_time(
                r, body.state.session_id, body.state.round, elapsed_ms
            )
        await _update_live_view(
            request.app.state.redis, body.state.session_id,
            elder_response=body.elder_response,
        )
        result = await orchestrator.process_response(
            elder_response=body.elder_response,
            state=body.state.model_dump(),
            emotion=emotion,
        )

        if result.get("state") is None:
            # 回合結束（end_round / end_session），把這回合累積的平均反應時間寫進 rounds.response_time
            await _finalize_round_response_time(
                db, r, body.state.session_id, body.state.round,
                patient_id=_to_int(body.state.user_id), therapist_id=therapist_id,
            )
            if result.get("action") == "end_session":
                # 心得問題出現的時間點，供 /session/{id}/closing 計算心得回合的反應時間
                await r.set(
                    f"session:{body.state.session_id}:closing_asked_at",
                    str(int(time.time() * 1000)), ex=3600,
                )

        if result.get("question"):
            # state 不是 None 代表回合還在繼續（open_followup / ask_supplement_w），
            # 這一題是本回合的新問題，question_number 往下一號並存進 round_exchanges；
            # state 是 None 代表 end_round/end_session，問題本身留給下一回合開場或
            # /session/{id}/closing 處理，這裡只負責播音檔。
            next_qn = body.state.question_number + 1
            tts = request.app.state.tts_service
            audio_path = await tts.synthesize(
                text=result["scene_text"] + result["question"],
                session_id=body.state.session_id,
                round_number=body.state.round,
                turn_number=next_qn if result.get("state") is not None else None,
            )
            result["audio_path"] = audio_path
            await _update_live_view(
                request.app.state.redis, body.state.session_id,
                current_scene=result.get("scene_text", "") + result["question"],
                ai_suggestions=[result["question"]],
            )
            if result.get("state") is not None:
                result["state"]["question_number"] = next_qn
                result["state"]["question_asked_at"] = int(time.time() * 1000)
                await _save_round_exchange(
                    db, body.state.session_id, body.state.round,
                    question_number=next_qn, question=result["question"],
                    patient_id=_to_int(body.state.user_id), therapist_id=therapist_id,
                )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"處理回應失敗: {str(e)}")