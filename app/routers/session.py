import asyncio
import json
import time
import uuid
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
from services.closing_templates import build_closing_invitation
from services.audio_bank import lookup_audio_key
import ws_registry

router = APIRouter(prefix="/session", tags=["session"])

# patient:{patient_id}:active（見 /session/pending、/session/{id}/metrics、
# /session/{id}/control）採心跳式續命，不是設一次就管 2 小時：
# _ACTIVE_INITIAL_TTL 是 Unity 選定病患當下先給的緩衝時間，撐到治療師端「活動
# 觀察頁」開始 polling metrics 接手續命為止（校正+啟動流程跑完可能要一兩分鐘）；
# 之後只要 /metrics 還在被 2 秒一次正常 polling，就會不斷刷新成 _ACTIVE_HEARTBEAT_TTL，
# 視窗關掉／斷線／Unity 當機只要停止 polling，最多這麼多秒後就會自動消失，
# 不用等完整 2 小時、也不用依賴任何一方乾淨地送出「結束」訊號。
_ACTIVE_INITIAL_TTL = 180
_ACTIVE_HEARTBEAT_TTL = 30


def _to_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


async def _synthesize_safe(tts, **kwargs) -> str | None:
    """TTS 服務離線/逾時（例如本機 GPU 資源被 Ollama 占用時沒開 TTS）不該擋住整個
    回合開場/回應流程，跟圖片生成失敗一樣採不影響主流程的降級：長者端這段沒有語音，
    但場景文字、問題、圖片仍正常運作。"""
    try:
        return await tts.synthesize(**kwargs)
    except Exception as e:
        print(f"[TTS] 語音合成失敗（不影響主流程，長者端這段沒有語音）: {e}")
        return None


async def _synthesize_edge_safe(tts, **kwargs) -> str | None:
    """跟 _synthesize_safe 一樣是不影響主流程的降級呼叫，差別是走
    tts.synthesize_edge（edge-tts／HsiaoYu 即時生成），給 _PRE_IMAGE_Q1_
    QUESTION_TEMPLATE／_PRE_IMAGE_Q1_FALLBACK_QUESTION 這段帶著治療師自由
    輸入今日主題、永遠無法預錄的動態文字用（見 orchestrator.py
    _build_pre_image_question 的 tts_text 說明）。"""
    try:
        return await tts.synthesize_edge(**kwargs)
    except Exception as e:
        print(f"[TTS] edge-tts 語音合成失敗（不影響主流程，長者端這段沒有語音）: {e}")
        return None


async def _synthesize_or_key(tts, text: str, **kwargs) -> tuple[str | None, str | None]:
    """
    2026-08-18新增：orchestrator.py 產出的固定字串模板（非LLM即時生成，見
    app/services/audio_bank.py 開頭說明）改成前端播放內建預錄音檔，後端不用
    再為這些句子即時呼叫TTS。text 若命中 audio_bank 的對照表，直接回傳
    (None, key)，跳過TTS；查無對應（LLM動態生成的內容，或還沒收錄進
    audio_bank 的固定句，見 audio_bank.py 說明）就照舊呼叫 _synthesize_safe，
    回傳 (path, None)——同一個欄位只會有 path 或 key 其中一個有值，呼叫端
    （Unity）兩個都要檢查：有 key 就播內建音檔，沒有才退回下載 audio_path
    播放。

    五感/W維度保底問句、出示圖片承接語保底句這三處不用在這裡另外判斷——
    全部屬於回合2「自由追問」才會生成的內容，回合2/3的呼叫端（本檔案的
    session_round／session_respond）本來就整段跳過 TTS/audio_key，這支
    函式根本不會被叫到，見 audio_bank.py 檔頭說明。
    """
    key = lookup_audio_key(text)
    if key:
        return None, key
    return await _synthesize_safe(tts, text=text, **kwargs), None


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


async def _get_cached_start_result(r, session_id: str) -> dict | None:
    """
    /session/start（第一回合）現在有兩個呼叫者：治療師網頁按下「啟動療程」，
    以及 Unity 的 GameController 在 WarmupScene 等到治療師啟動後自己也會呼叫一次
    （見 WarmupController，兩邊已换到同一組 session_id）。

    第二個呼叫者直接重播第一次的結果，避免 orchestrator 重新生成場景/問題、
    TTS 重新合成、round_exchanges 被寫入兩筆重複的第一題。

    question_asked_at 改成重播當下的時間，因為長者實際看到/聽到這一題的時間點
    是 Unity 收到回覆的當下，不是治療師端最早呼叫的那一刻，
    否則 /session/respond 算出的反應時間會被兩者間的等待時間灌水。
    """
    raw = await r.get(f"session:{session_id}:round1_result")
    if not raw:
        return None
    result = json.loads(raw)
    if result.get("state"):
        result["state"]["question_asked_at"] = int(time.time() * 1000)
    return result


async def _cache_start_result(r, session_id: str, result: dict) -> None:
    await r.set(f"session:{session_id}:round1_result", json.dumps(result), ex=86400)


async def _acquire_start_lock(r, session_id: str) -> bool:
    """
    /session/start 有兩個呼叫者（見 _get_cached_start_result 說明），但「檢查
    快取」（session_start 開頭）到「寫入快取」（_cache_start_result，整個
    orchestrator.start_round 都跑完才會執行）中間隔著 LLM 分類／RAG 檢索／
    生圖／TTS 合成，耗時經常長達數秒到數十秒，兩個呼叫者常常會前後腳都通過
    「還沒快取」的檢查，各自完整跑一次 start_round。

    2026-08-20稽核（使用者實測案例）：治療師端帶明確 topic 呼叫、Unity 端
    沒帶 topic 呼叫，兩次 orchestrator.start_round 各自算出不同的
    today_topic（沒帶 topic 的那次會退回 patient.preferences 算出的預設
    主題，見 DBUserProfileClient.get_user 說明），兩邊的生成結果混進同一個
    round 1，出現「開場問題問A主題、長者沒回應退回RAG記憶生圖時卻查到B
    主題」的精神分裂畫面。加一把短期 lock，讓兩個呼叫者裡只有一個真的
    執行 start_round，另一個改成輪詢等待快取（見 session_start 呼叫處），
    不再各自獨立生成。
    """
    return bool(await r.set(f"session:{session_id}:start_lock", "1", ex=60, nx=True))


async def _release_start_lock(r, session_id: str) -> None:
    await r.delete(f"session:{session_id}:start_lock")


async def _get_session_topic(r, session_id: str) -> str | None:
    """讀出這場療程啟動時（round 1）設定的今日主題，round 2/3 沿用。"""
    meta_raw = await r.get(f"session:{session_id}:meta")
    if not meta_raw:
        return None
    return json.loads(meta_raw).get("topic") or None


async def _cache_round_carryover(r, session_id: str, round_number: int, data: dict) -> None:
    """round 1/2 結束時，把下一回合開場（round 2/3）需要承接的內容存進 Redis
    （見 orchestrator.py start_round 對 carryover 參數的說明）——前端在回合
    邊界不會把 state 傳回來（Unity StartRound 呼叫 /session/round 不帶
    state），只能由後端自己記住上一回合結束時的內容。"""
    await r.set(
        f"session:{session_id}:round{round_number}_carryover",
        json.dumps(data, ensure_ascii=False), ex=86400,
    )


async def _get_round_carryover(r, session_id: str, round_number: int) -> dict | None:
    raw = await r.get(f"session:{session_id}:round{round_number}_carryover")
    return json.loads(raw) if raw else None


async def _append_session_topic(r, session_id: str, topic_category: str | None) -> None:
    """記錄這場療程實際分類到的16大主題（見 orchestrator.py
    _classify_topic_category），供三回合結束後心得環節的 build_closing_
    affirmation 判斷要用「撐過來」還是「美好時光」的收尾語氣。round 2 沿用
    round 1 的分類（round 2 本身不重新分類），round 3 是 closing 沒有分類，
    所以只有 round 1/2 結束時會呼叫這裡。"""
    if not topic_category:
        return
    key = f"session:{session_id}:topics"
    await r.rpush(key, topic_category)
    await r.expire(key, 86400)


async def _get_session_topics(r, session_id: str) -> list[str]:
    return await r.lrange(f"session:{session_id}:topics", 0, -1)


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
    stage: str | None = None,
) -> None:
    """AI 每問一個新問題就新增一筆 round_exchanges（answer 先留空，長者回答後由
    _fill_round_exchange_answer 補上）。stage="pre_image" 代表這題是生圖前的
    引導問題（見 orchestrator.py 的 pre_image_q1/pre_image_q2），供歷史療程
    檢視頁區分第一回合的問題是生圖前還是生圖後問的。"""
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
            stage=stage,
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
    scene_composition: str = ""  # 生圖時的英文構圖描述，供追問維持畫面一致性
    covered_w: list[str] = []
    skipped_w: list[str] = []
    last_question_type: str = "step1"
    last_w_asked: str = ""
    question_number: int = 1  # 本回合目前問到第幾題，供 round_exchanges 配對與 TTS 檔名編號
    question_asked_at: int = 0  # 目前這一題送出的時間（epoch ms），供計算 rounds.response_time
    # question_count / supplement_count 是 orchestrator 的回合題數上限/補問上限
    # 計數器（見 orchestrator.py _MAX_QUESTIONS_PER_ROUND）。這兩個欄位先前沒有
    # 宣告在這個 pydantic model 裡，導致長者每答一次話、前端把 state 傳回來時
    # 都被 FastAPI 的請求驗證直接丟棄、重置回預設值——單回合題數/補問上限
    # 因此透過正式 API 從未真正生效過（每次呼叫都從預設值重新起算）。2026-08
    # 修復：補上這兩個欄位讓它們能正常透過 API 往返存活。
    question_count: int = 1
    supplement_count: int = 0
    # 生圖前 Q1 開場時就分類過 today_topic 屬於16大主題分類的哪一類（見
    # orchestrator.py _classify_topic_category），供之後若需要問 Q2 縮小
    # 範圍時直接複用，不用重複分類。同樣需要宣告在這裡才能透過 API 往返存活，
    # 理由同上面 question_count/supplement_count 的說明。
    topic_category: str | None = None
    # 生圖前 Q1 開場時就對 today_topic 本身分類過適合哪些感官（見
    # orchestrator.py _classify_topic_senses、_SENSE_EXCLUDED_TOPIC_
    # CATEGORIES），供後續感官追蹤（covered_senses／skipped_senses）需要
    # 判斷「這個主題還剩哪些相關感官沒問過」時直接複用，不用重複分類。
    # 同樣需要宣告在這裡才能透過 API 往返存活，理由同上面 topic_category。
    topic_senses: list[str] = []
    # 情境2成立、且主題是sub_item granularity時分類出的子項目（見
    # orchestrator.py _classify_pre_image_sub_item、get_scenario2_
    # followup 的 excluded_fields 說明）。同樣需要宣告在這裡才能透過 API
    # 往返存活，否則 pre_image_q2 情境2分支第二輪起讀到的 sub_item 永遠是
    # None，等於白分類一次、排除規則沒生效。
    pre_image_sub_item: str | None = None
    # start_round 一開始就撈好的 RAG 候選記憶（見 orchestrator.py
    # _retrieve_candidate_memories），供 _start_scene_after_detail 需要
    # RAG fallback 時直接讀，不用重新查一次。同樣需要宣告在這裡才能透過
    # API 往返存活，理由同上面 question_count/supplement_count 的說明。
    cached_rag_memories: list[dict] = []
    # 生圖前 Q1 的回答，供 pre_image_q2 階段跟 Q2 合併當生圖記憶來源（見
    # orchestrator.py process_response 的 pre_image_q1/pre_image_q2 分支）。
    # 2026-08 發現：這個欄位原本沒宣告在這裡，導致每次長者答完Q1、前端把
    # state 傳回來問Q2時就已經被 FastAPI 驗證丟棄——Q1+Q2合併生圖實際上
    # 從未真正生效過，Q2階段只會拿到Q2單獨那句。理由同上面 question_count
    # 等欄位的說明，這裡補上宣告修復。
    pre_image_q1_answer: str = ""
    # 生圖前 Q1(+Q2) 的完整內容，供長者看完圖後若沒有真正反應（quick_end）
    # 時，STEP1問題前面能接一句具體呼應這段內容的話，而不是完全通用的固定
    # 過渡句（見 orchestrator.py _start_scene_after_detail、
    # _generate_quick_end_recap）。同樣需要宣告在這裡才能透過 API 往返存活。
    pre_image_detail: str = ""
    # round 1 結束時已經自然涵蓋的 W 維度，round 2 開場用來排除補問候選、
    # 避免重複問長者上一回合已經明確答過的事實（見 orchestrator.py
    # _start_round2_free_followup 的 known_facts_w 說明）。2026-08-17稽核
    # （實測後補）：這個欄位當初漏宣告在這裡——只在 manual_test_full_round.py
    # （不走這個 pydantic model，直接傳裸 dict）測試時有效，透過正式 API
    # 其實從一開始就被 FastAPI 驗證悄悄丟棄，round 2 一直沒真的用上，是跟
    # 上面這幾個欄位同一種踩過的坑，這裡一併補上。
    known_facts_w: list[str] = []
    # 上一題問了什麼，當模型生成下一題時的參考資訊（2026-08-18稽核，第四次，
    # 使用者提案：原本這裡還有一個 round_qa_log 欄位，累積整場療程的Q&A摘要
    # 塞進prompt，但實測回報累積的內容越長，承接語／問題品質反而越差——
    # 本地量化基底模型對長prompt的指令遵循本來就不穩定。已移除，改成只留
    # 這個單一字串。第十次稽核：一度加上生成後事後核對＋重打，比對commit
    # 版本後發現那套機制本身也會拖累品質，已經拿掉，這裡純粹是參考資訊，
    # 不驅動任何強制核對）。同樣必須宣告在這裡才能透過 API 往返存活。
    last_question_text: str = ""
    # 這回合到目前為止已經自然涵蓋哪些感官（視覺/聽覺/嗅覺/味覺/觸覺），
    # 供 STEP2/3 選感官切入角度時避免重複問同一種、或問跟主題不相關的
    # 感官（見 orchestrator.py process_response 對 covered_senses 的說明、
    # _relevant_uncovered_senses）。同樣必須宣告在這裡才能透過 API 往返存活。
    covered_senses: list[str] = []
    # 2026-08-18新增：跟 covered_w／skipped_w 對稱——covered_senses 只記錄
    # 「長者的回答有沒有自然涵蓋某個感官」，last_sense_asked／skipped_senses
    # 補上「這題問了哪個感官、長者有沒有答到」的追蹤（見 orchestrator.py
    # _relevant_uncovered_senses 的 skipped_senses 說明）。同樣必須宣告在這裡
    # 才能透過 API 往返存活，否則會被 FastAPI 驗證悄悄丟棄——這幾個新欄位
    # 剛加時就是因為漏宣告在這裡，透過正式 API 完全沒生效過（question_count
    # 等欄位都踩過同一個坑，見上面說明）。
    last_sense_asked: str = ""
    skipped_senses: list[str] = []
    # 生圖前Q2「情境2（缺維度，直接問缺的那個W）」的輪次追蹤（見
    # orchestrator.py process_response 的 pre_image_q2 分支、
    # get_scenario2_followup）。同樣必須宣告在這裡才能透過 API 往返存活，
    # 否則會被 FastAPI 驗證悄悄丟棄——踩的是跟上面這幾個欄位同一個坑：
    # scenario 讀不到就永遠當情境1處理，情境1那條分支沒有輪數上限保護，
    # 疊加 _detect_pre_image_w_coverage 本身非決定性、W維度判斷結果會飄動，
    # 會導致單回合一路追問下去、卡在第1回合出不去。
    pre_image_q2_scenario: int = 1
    pre_image_q2_round: int = 1
    pre_image_q2_max_rounds: int = 1
    pre_image_q2_last_w: str = ""


class RespondRequest(BaseModel):
    elder_response: str
    state: SessionState


@router.get("/pending", summary="依 case/patient_id 取得（或建立）尚未啟動的 session_id")
async def session_pending(
    request: Request,
    patient_id: str,
    source: str = "web",
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    治療師網頁開啟「開始療程」頁與 Unity 選定病患後（WarmupScene 校正開始前）
    都會呼叫這支，用同一個 patient_id 換到同一組 session_id，
    讓 Unity 校正資料（/ws/calibration）跟治療師之後啟動的療程綁在同一個 session。

    session_id 建立後有 2 小時 TTL；/session/start 成功後會清掉這個 key，
    避免下次同一位病患開新療程時誤用到舊的（已結束的）session_id。

    source：Unity 呼叫時帶 "unity"（見 SessionService.FetchPendingSession），
    只有這個來源才會標記病患活動中——治療師光是打開「開始療程」頁不代表
    長者端真的坐上 Kinect 開始被服務，不該顯示活動中。
    """
    r = request.app.state.redis
    key = f"case:{patient_id}:pending_session"
    candidate = str(uuid.uuid4())
    was_set = await r.set(key, candidate, ex=7200, nx=True)
    if source == "unity":
        await r.set(f"patient:{patient_id}:active", "1", ex=_ACTIVE_INITIAL_TTL)
    if was_set:
        return {"session_id": candidate}
    existing = await r.get(key)
    return {"session_id": existing or candidate}


@router.get("/active-patients", summary="目前活動中（Unity 已選定或療程進行中）的病患 id 清單")
async def active_patients(
    request: Request,
    therapist_id: int = Depends(get_current_therapist_id),
):
    """供治療師個案列表的「活動中」徽章 polling 用。TTL 到期（見 /session/pending）
    或療程結束（見 _compute_and_save_assessment）都會讓病患從這份清單消失。"""
    r = request.app.state.redis
    ids = [key.split(":")[1] async for key in r.scan_iter(match="patient:*:active")]
    return {"patient_ids": ids}


@router.get("/{session_id}/status", summary="供治療師網頁 polling Unity 校正狀態")
async def session_status(
    request: Request,
    session_id: str,
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    calibrated：Unity 是否已把校正基準存進 session:{id}:calibration（見 ws_calibration.py）。
    calibrating：Unity 目前是否連著 /ws/calibration、正在跑校正流程但還沒完成
      （見 ws_registry.py）；跟 calibrated 互斥，一旦 calibrated 為 true 就不算 calibrating。
    requested：治療師是否已按下「啟動療程」（/session/start 已被呼叫，但 LLM 分類／
      RAG 檢索／TTS 合成不保證跑完）。Unity 的 WarmupController 只需要這個訊號就能
      切去 InstructionScene，讓真正耗時的生成過程用說明頁的進度條呈現，不用在
      WarmupScene 乾等。
    started：/session/start 是否已完整跑完（session:{id}:meta 已建立），代表第一回合
      內容真的生成好了。InstructionScene 靠這個訊號決定何時把內容拿回來、進場 GameScene。
    """
    r = request.app.state.redis
    calibrated = bool(await r.exists(f"session:{session_id}:calibration"))
    calibrating = (not calibrated) and ws_registry.is_calibrating(session_id)
    requested = bool(await r.exists(f"session:{session_id}:requested"))
    started = bool(await r.exists(f"session:{session_id}:meta"))
    return {
        "calibrated": calibrated,
        "calibrating": calibrating,
        "requested": requested,
        "started": started,
    }


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
    r = request.app.state.redis
    # 一進來就標記「治療師已按下啟動療程」，讓 Unity 的 WarmupController 立刻切去
    # InstructionScene；下面的 LLM 分類／RAG 檢索／TTS 合成才是真正耗時的部分，
    # 讓說明頁的進度條去撐，不要讓長者停在 WarmupScene 乾等。
    await r.set(f"session:{session_id}:requested", "1")
    cached = await _get_cached_start_result(r, session_id)
    if cached:
        return cached

    if not await _acquire_start_lock(r, session_id):
        # 沒搶到鎖：代表治療師網頁／Unity 另一個呼叫者正在跑同一個 session
        # 的 start_round（見 _acquire_start_lock 說明），輪詢等它寫入快取，
        # 不要自己也跑一次、各自算出不同的 today_topic。輪詢上限抓超過
        # 「LLM分類＋RAG＋生圖＋TTS」實測最壞情況的時間，真的等超時了才
        # 視為持鎖者已經掛掉，自己接手跑一次（不然鎖到期前不會有人補上
        # 快取，長者會被晾在原地）。
        for _ in range(60):
            await asyncio.sleep(0.5)
            cached = await _get_cached_start_result(r, session_id)
            if cached:
                return cached
        await _acquire_start_lock(r, session_id)

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
        scene_audio_path = scene_audio_key = None
        if result.get("scene_text"):
            scene_audio_path, scene_audio_key = await _synthesize_or_key(
                tts,
                result["scene_text"],
                session_id=session_id,
                round_number=1,
                turn_number=None,
            )
        # Q1邀請語帶著治療師自由輸入的今日主題，orchestrator.start_round
        # 已經拆好 question_tts_text（該即時TTS的動態部分，用edge-tts／
        # HsiaoYu生成——這段治療師自由輸入、沒辦法預錄）跟 question_audio_key
        # （後半段邀請語的預錄音檔key，見 orchestrator.py _build_pre_image_
        # question 說明）——沒有這兩個欄位（理論上不會，round 1 一定是走 Q1）
        # 才退回對整句 question 即時TTS。
        question_audio_path = await _synthesize_edge_safe(
            tts,
            text=result.get("question_tts_text", result["question"]),
            session_id=session_id,
            round_number=1,
            turn_number=1,
        )
        question_audio_key = result.get("question_audio_key")
        result["scene_audio_path"] = scene_audio_path
        result["scene_audio_key"] = scene_audio_key
        result["question_audio_path"] = question_audio_path
        result["question_audio_key"] = question_audio_key
        result["audio_path"] = question_audio_path  # 向下相容
        await _init_session_meta(request.app.state.redis, session_id, user_id, str(therapist_id), topic=topic)
        await request.app.state.redis.delete(f"case:{user_id}:pending_session")
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
            last_type = (result.get("state") or {}).get("last_question_type", "")
            await _save_round_exchange(
                db, session_id, 1, question_number=1, question=result["question"],
                patient_id=_to_int(user_id), therapist_id=therapist_id,
                stage="pre_image" if last_type.startswith("pre_image") else None,
            )
        await _cache_start_result(r, session_id, result)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"療程開場失敗: {str(e)}")
    finally:
        await _release_start_lock(r, session_id)


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
        r = request.app.state.redis
        topic_override = await _get_session_topic(r, session_id)
        # round 2/3 開場需要承接上一回合結束時的內容（round 1 的畫面元素／
        # 話題、或 round 2 最後一句話），見 orchestrator.py start_round 說明。
        carryover = (
            await _get_round_carryover(r, session_id, round_number - 1)
            if round_number in (2, 3) else None
        )
        result = await orchestrator.start_round(
            user_id=user_id,
            session_id=session_id,
            round_number=round_number,
            topic_override=topic_override,
            carryover=carryover,
        )
        result["state"]["question_number"] = 1
        result["state"]["question_asked_at"] = int(time.time() * 1000)
        # 第二、三回合不生圖也不合成語音（STT 仍照常），見這次改動需求：
        # 第二回合自由追問、第三回合 closing 都只靠畫面文字＋長者口說回應。
        if result.get("question") and round_number not in (2, 3):
            tts = request.app.state.tts_service
            scene_audio_path = scene_audio_key = None
            if result.get("scene_text"):
                scene_audio_path, scene_audio_key = await _synthesize_or_key(
                    tts,
                    result["scene_text"],
                    session_id=session_id,
                    round_number=round_number,
                    turn_number=None,
                )
            # 這個分支 round_number 一定是 1（round 2/3 被上面的
            # not in (2, 3) 擋掉），一定是 Q1 邀請語，見 session_start 那份
            # 一樣的說明。
            question_audio_path = await _synthesize_edge_safe(
                tts,
                text=result.get("question_tts_text", result["question"]),
                session_id=session_id,
                round_number=round_number,
                turn_number=1,
            )
            question_audio_key = result.get("question_audio_key")
            result["scene_audio_path"] = scene_audio_path
            result["scene_audio_key"] = scene_audio_key
            result["question_audio_path"] = question_audio_path
            result["question_audio_key"] = question_audio_key
            result["audio_path"] = question_audio_path  # 向下相容
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
            last_type = (result.get("state") or {}).get("last_question_type", "")
            await _save_round_exchange(
                db, session_id, round_number, question_number=1, question=result["question"],
                patient_id=_to_int(user_id), therapist_id=therapist_id,
                stage="pre_image" if last_type.startswith("pre_image") else None,
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回合開場失敗: {str(e)}")


_CONTROL_ACTIONS = {"replay_audio", "skip_scene", "pause", "resume", "end"}


class ControlPayload(BaseModel):
    action: str


@router.post("/{session_id}/control", summary="治療師網頁即時控制療程（重播/跳過/暫停/繼續）")
async def session_control(
    request: Request,
    session_id: str,
    body: ControlPayload,
    db: AsyncSession = Depends(get_db),
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    轉發給長者端 Unity 目前開著的 /ws/stt 連線（見 ws_registry.py）。
    action 對應 Unity GameController.HandleSTTMessage 的 control 分支：
      replay_audio → 重播目前這一題的語音
      skip_scene   → 跳過目前這一題（不是跳過整個回合），視同長者未回應直接進下一步
      pause/resume → 暫停/繼續本回合（暫停時取消反應逾時、鎖住麥克風與送出鈕）
      end          → 治療師按「結束活動」，Unity 收到後 Application.Quit()

    長者端如果目前沒有連線（例如療程還沒進到 GameScene），delivered 會是 false，
    不當錯誤處理——網頁不需要特別跳錯誤訊息給治療師。

    action=="end" 先呼叫 mark_ending：Unity Quit() 之後 /ws/stt 連線隨即斷線，
    ws_stt.py 的 finally 區塊要知道這次斷線是治療師主動結束、不是不正常斷線
    （見該檔 _mark_abnormal_end 說明），才不會把治療師稍後在 /activity/{id}/end
    頁面選的「稍後填寫」（故意留著 in_progress）蓋回去。

    action=="end" 也順便清掉 patient:{patient_id}:active（見 /session/pending），
    治療師手動結束療程不用等 2 小時 TTL 到期，個案列表的「活動中」徽章能立刻消失。
    """
    if body.action not in _CONTROL_ACTIONS:
        raise HTTPException(status_code=400, detail=f"不支援的控制動作: {body.action}")
    if body.action == "end":
        ws_registry.mark_ending(session_id)
    delivered = await ws_registry.send_control(session_id, body.action)
    if body.action == "end":
        r = request.app.state.redis
        meta_raw = await r.get(f"session:{session_id}:meta")
        if meta_raw:
            patient_id = json.loads(meta_raw).get("patient_id")
            if patient_id:
                await r.delete(f"patient:{patient_id}:active")
        # 治療師提前手動結束（長者可能還沒念到心得回合），/closing 那條
        # 自動寫入路徑不會被觸發——這裡補上，讓評估分數與 status="completed"
        # 一定會落地，治療師隨後在 /activity/{id}/end 頁面看到的才是真實
        # 數據而不是前端的預設分數。若長者剛好已經正常走完心得，
        # _compute_and_save_assessment 早就算過一次並清掉 Redis stats，
        # 這裡重複呼叫會因為讀不到 stats 而丟 404，直接吞掉即可（不是
        # 錯誤，是正常的「已經結束過了」）。
        try:
            await _compute_and_save_assessment(request, session_id, db, therapist_id)
        except HTTPException as e:
            print(f"[Control] end 觸發評估略過: {e.detail}")
    return {"ok": True, "delivered": delivered}


@router.get("/{session_id}/metrics", summary="取得即時檢測回饋（供治療師頁面 polling）")
async def session_metrics(
    request: Request,
    session_id: str,
    therapist_id: int = Depends(get_current_therapist_id),
):
    r = request.app.state.redis

    # 心跳續命：只要治療師端「活動觀察頁」還在正常 polling 這支 API，就代表
    # 這場療程還活著，把 patient:{patient_id}:active 的存活時間刷新回短 TTL。
    # 用 EXPIRE 不用 SET，key 不存在（例如已經被 /session/{id}/control 的
    # action=="end" 清掉）就不會誤把它救回來。
    meta_raw = await r.get(f"session:{session_id}:meta")
    if meta_raw:
        patient_id = json.loads(meta_raw).get("patient_id")
        if patient_id:
            await r.expire(f"patient:{patient_id}:active", _ACTIVE_HEARTBEAT_TTL)

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
        # session:{id}:meta 只有在 _compute_and_save_assessment 算完評估分數、
        # 療程真正結束時才會被清掉（見該函式），前端「活動觀察頁」還在 polling
        # 的當下 meta 一定存在，一旦這裡變 false 就代表心得已經答完、評估算完了，
        # 可以自動跳轉到結束頁面，不用等治療師自己按「結束活動」。
        "session_completed": not meta_raw,
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


async def _generate_round_summary(llm_service, patient_response: str) -> str:
    """把單一回合裡長者的完整發言整理成一句話重點摘要，給歷史療程「各回合紀錄」
    列表快速瀏覽用，避免把長者原話整段堆在畫面上。失敗回傳空字串，呼叫端
    （HistorySessionView.tsx）會 fallback 顯示原文，不影響評估寫入本身。"""
    try:
        messages = [
            {"role": "system", "content": "你是懷舊療法的紀錄整理助手，負責把長者在一個回合裡的發言整理成一句話重點摘要，給治療師快速瀏覽。"},
            {"role": "user", "content": (
                f"長者這個回合說的話：\n{patient_response}\n\n"
                "請用20字以內、第三人稱的一句話摘要重點，只回摘要文字本身，不要加任何說明或標點以外的內容。"
            )},
        ]
        summary = await llm_service.chat(messages, temperature=0.3)
        return summary.strip()
    except Exception as e:
        print(f"[LLM] round_summary 生成失敗（不影響評估寫入）: {e}")
        return ""


async def _generate_and_save_round_summaries(llm_service, db: AsyncSession, session_id: str) -> None:
    """療程結束、評估分數算完時，順便幫每個有長者發言的回合（不含心得回合，
    心得本身另有彈窗顯示原文，不需要摘要）各生成一句話摘要並寫回 rounds.summary。
    這支獨立於 _compute_and_save_assessment 的主要交易之外呼叫，失敗只印 log，
    不影響評估分數已經寫入成功這件事。"""
    try:
        session_row = (
            await db.execute(select(TherapySession).where(TherapySession.session_uuid == session_id))
        ).scalar_one_or_none()
        if session_row is None:
            return
        rounds = (
            await db.execute(
                select(TherapyRound)
                .where(TherapyRound.session_id == session_row.id)
                .where(TherapyRound.type.is_(None) | (TherapyRound.type != "心得"))
                .where(TherapyRound.patient_response.is_not(None))
            )
        ).scalars().all()
        if not rounds:
            return

        summaries = await asyncio.gather(
            *(_generate_round_summary(llm_service, rnd.patient_response) for rnd in rounds)
        )
        for rnd, summary in zip(rounds, summaries):
            if summary:
                rnd.summary = summary
        await db.commit()
    except Exception as e:
        print(f"[LLM] 回合摘要批次生成失敗（不影響評估寫入）: {e}")
        await db.rollback()


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
                status="completed",
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
                    "status": "completed",
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
        await _generate_and_save_round_summaries(request.app.state.llm_service, db, session_id)

        # DB 寫入成功後清除所有 session Redis key
        await r.delete(
            f"session:{session_id}:meta",
            f"session:{session_id}:requested",
            f"session:{session_id}:stats",
            f"session:{session_id}:metrics",
            f"session:{session_id}:ema",
            f"session:{session_id}:calibration",
            f"session:{session_id}:round1_result",
            f"session:{session_id}:round1_carryover",
            f"session:{session_id}:round2_carryover",
            f"session:{session_id}:topics",
        )
        if meta.get("patient_id"):
            await r.delete(f"patient:{meta['patient_id']}:active")
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

    # 2026-08-17起：承接語＋系統整合肯定＋感謝語已經在心得環節「開場」那一步
    # 呼應回合3的回答講完了（見 app/services/closing_templates.py
    # build_closing_invitation、app/routers/session.py session_respond 對
    # end_session 的處理），長者回答完這裡的開場邀請語後，答案只需要記錄、
    # 不再另外生成第二段收尾訊息——closing_message 留空字串，ShareController.cs
    # 的 PostClosingAnswer 對空字串本來就會跳過顯示、直接轉場（見該檔）。
    try:
        scores = await _compute_and_save_assessment(request, session_id, db, therapist_id)
    except HTTPException as e:
        print(f"[Closing] 自動評估略過: {e.detail}")
        scores = None

    return {"ok": True, "closing_message": "", "assessment": scores}


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
        # rounds.emotion 給治療師頁面顯示用，要存中文標籤（見 sensor.py 的
        # emotion_label），不能存這裡的英文 emotion_raw——上面 emotion 變數
        # 保留英文原值是因為下面 orchestrator.process_response 內部（含
        # closing_templates 判斷情緒是否觸發安撫）是拿英文值做比對。
        await _save_round_response(
            db, body.state.session_id, body.state.round,
            text=body.elder_response, emotion=metrics.get("emotion", ""),
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
            on_generating_image=lambda: ws_registry.send_control(
                body.state.session_id, "generating_image"
            ),
        )

        if result.get("image_path"):
            # action=="scene_ready"：長者剛答完生圖前的引導問題，這裡才第一次
            # 真的生出圖片（見 orchestrator.py _start_scene_after_detail）。
            # /session/start、/session/round 那兩支端點呼叫 start_round 時還
            # 沒有圖，寫進 DB 的 scene_image 會是空字串，要等這裡才補上真正的
            # 圖片路徑與場景文字。
            await _save_round_image(
                db, body.state.session_id, body.state.round, result["image_path"],
                scene_text=result.get("scene_text", ""),
                patient_id=_to_int(body.state.user_id), therapist_id=therapist_id,
            )

        if result.get("state") is None:
            # 回合結束（end_round / end_session），把這回合累積的平均反應時間寫進 rounds.response_time
            await _finalize_round_response_time(
                db, r, body.state.session_id, body.state.round,
                patient_id=_to_int(body.state.user_id), therapist_id=therapist_id,
            )
            if result.get("action") == "end_session":
                # 三回合正常跑完、準備轉場到 ShareScene 問心得——GameController 的
                # /ws/stt 連線會在轉場時斷線（ShareController 開的是另一條沒帶
                # session_id 的連線，見該檔 ConnectWebSocket），這個斷線不是治療師
                # 按「結束活動」、也不是不正常斷線，是正常流程的一部分，這裡先標記
                # 起來供 ws_stt.py 的 finally 區塊排除，避免誤判成不正常結束。
                await r.set(
                    f"session:{body.state.session_id}:reached_closing",
                    "1", ex=3600,
                )
                # 心得問題出現的時間點，供 /session/{id}/closing 計算心得回合的反應時間
                await r.set(
                    f"session:{body.state.session_id}:closing_asked_at",
                    str(int(time.time() * 1000)), ex=3600,
                )
                # 心得環節開場邀請語是純規則模板（見 app/services/closing_
                # templates.py），orchestrator._end_action 對 end_session 只回
                # 空字串，這裡才是真正填入內容的地方——topics 用這場療程三回合
                # 實際分類到的16大主題（見 _append_session_topic）。收尾語呼應
                # 長者剛才在回合3的回答（body.elder_response 就是那句），emotion
                # 沿用這次respond一開始讀到的Kinect情緒。
                topics = await _get_session_topics(r, body.state.session_id)
                invitation = await build_closing_invitation(
                    topics, body.elder_response, emotion, request.app.state.llm_service,
                )
                result["scene_text"] = invitation["scene_text"]
                result["thanks_text"] = invitation["thanks_text"]
                result["question"] = invitation["question"]
                # 心得環節的音檔全部是前端內建預錄音檔（見 audio_bank.py／
                # closing_templates.py），不用即時TTS，直接把 key 列表帶過去
                # 給前端；下面 1233 行那個 action=="end_session" 就跳過TTS的
                # 分支維持不動，這裡是唯一填入這三個欄位的地方。
                result["scene_audio_keys"] = invitation["scene_audio_keys"]
                result["thanks_audio_keys"] = invitation["thanks_audio_keys"]
                result["question_audio_keys"] = invitation["question_audio_keys"]
            if result.get("action") == "end_round" and body.state.round in (1, 2):
                # round 2/3 開場需要承接這裡：round 1 結束時記畫面元素／話題／
                # 生圖前訪談內容＋這句話，round 2 結束時只需要這句話（round 3
                # 的 closing 不需要畫面），見 orchestrator.py start_round 說明。
                carryover = {"last_elder_response": body.elder_response, "emotion": emotion}
                if body.state.round == 1:
                    carryover.update({
                        "scene_elements": body.state.scene_elements,
                        "scene_composition": body.state.scene_composition,
                        "pre_image_detail": body.state.pre_image_detail,
                        "topic_category": body.state.topic_category,
                        "topic_senses": body.state.topic_senses,
                        # round 1 結束時已經自然涵蓋的 W 維度——round 2 開場用來
                        # 排除補問候選，不要再問長者已經在 round 1 講過的具體事實
                        # （見 orchestrator.py _start_round2_free_followup 的
                        # known_facts_w 說明）。
                        "round1_covered_w": body.state.covered_w,
                        # round 1 最後一題問了什麼——round 2 開場生成時當
                        # 參考資訊（見 orchestrator.py _start_round2_free_
                        # followup 說明）。2026-08-18稽核（第四次，使用者
                        # 提案）：這裡原本帶的是round 1整份round_qa_log，
                        # 改成只帶最後一題的單一字串，理由見
                        # SessionState.last_question_text 上方註解。
                        "round1_last_question": body.state.last_question_text,
                        # round 1 結束時已經自然涵蓋的感官——round 2 開場用來排除
                        # 感官選項，不要再問長者已經在 round 1 答過的感官（例如
                        # 味覺/嗅覺），跟上面 round1_covered_w 同一個問題、同一種
                        # 修法（見 orchestrator.py _start_round2_free_followup 的
                        # known_senses 說明）。只帶 covered_senses、不帶
                        # skipped_senses——skipped_senses 是「問了但長者沒答到」，
                        # round 2 沒理由跟著避開，跟 round1_covered_w 只帶
                        # covered_w、不帶 skipped_w 是同一個理由。
                        "round1_covered_senses": body.state.covered_senses,
                    })
                await _cache_round_carryover(r, body.state.session_id, body.state.round, carryover)
                await _append_session_topic(r, body.state.session_id, body.state.topic_category)

        if result.get("question"):
            # state 不是 None 代表回合還在繼續（open_followup / ask_supplement_w），
            # 這一題是本回合的新問題，question_number 往下一號並存進 round_exchanges；
            # state 是 None 代表 end_round/end_session，問題本身留給下一回合開場或
            # /session/{id}/closing 處理，這裡只負責播音檔。
            next_qn = body.state.question_number + 1
            # 第二回合（自由追問）全程不合成語音，STT 仍照常。end_session 的
            # scene_text/question 是 build_closing_invitation 補上的心得環節
            # 收尾語＋開場問題（見上面 action=="end_session" 分支），這一段
            # 全部不需要語音，只當畫面上的文字。
            if body.state.round != 2 and result.get("action") != "end_session":
                tts = request.app.state.tts_service
                if result.get("scene_text"):
                    scene_audio_path, scene_audio_key = await _synthesize_or_key(
                        tts,
                        result["scene_text"],
                        session_id=body.state.session_id,
                        round_number=body.state.round,
                        turn_number=None,
                    )
                    result["scene_audio_path"] = scene_audio_path
                    result["scene_audio_key"] = scene_audio_key
                # orchestrator.process_response 的生圖前Q2情境2分支（
                # get_scenario2_followup，_FIVE_W1H_BANK 題庫）已經直接算好
                # question_audio_key 放進 result 了（同一句話在不同主題下
                # 可能重複出現，沒辦法只靠文字比對查表，見 audio_bank.py
                # five_w1h_key 說明）——這裡如果已經有值就不用再耗一次TTS
                # 呼叫，直接沿用；其餘情況才照舊查表/即時TTS。
                existing_question_key = result.get("question_audio_key")
                if existing_question_key:
                    question_audio_path, question_audio_key = None, existing_question_key
                else:
                    question_audio_path, question_audio_key = await _synthesize_or_key(
                        tts,
                        result["question"],
                        session_id=body.state.session_id,
                        round_number=body.state.round,
                        turn_number=next_qn if result.get("state") is not None else None,
                    )
                result["question_audio_path"] = question_audio_path
                result["question_audio_key"] = question_audio_key
                result["audio_path"] = question_audio_path  # 向下相容
            await _update_live_view(
                request.app.state.redis, body.state.session_id,
                current_scene=result.get("scene_text", "") + result["question"],
                ai_suggestions=[result["question"]],
            )
            if result.get("state") is not None:
                result["state"]["question_number"] = next_qn
                result["state"]["question_asked_at"] = int(time.time() * 1000)
                last_type = (result.get("state") or {}).get("last_question_type", "")
                await _save_round_exchange(
                    db, body.state.session_id, body.state.round,
                    question_number=next_qn, question=result["question"],
                    patient_id=_to_int(body.state.user_id), therapist_id=therapist_id,
                    stage="pre_image" if last_type.startswith("pre_image") else None,
                )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"處理回應失敗: {str(e)}")