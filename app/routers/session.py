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
from routers.sensor import _pct, ENGAGEMENT_RANGE, HAPPINESS_RANGE, AGITATION_RANGE
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

# 跟 orchestrator.py 的 _NO_RESPONSE_MARKER 同一個字串（GameController.cs 的
# NoResponseMarker），治療師按跳過（/session/{id}/control action=="skip_scene"）
# 時填的佔位內容，不是長者真的說的話。2026-08-20 commit a6161e9「移除逾時
# 跳題」之後，Unity 已經不會自動倒數逾時送出這個字串了，現在唯一觸發來源
# 就是治療師手動按跳過（2026-09-07更新，這則註解原本寫的是那次改動之前的
# 舊行為）。_generate_story_summary／_generate_round_summary 要把這種
# 「幾乎沒有實質內容」的回合排除掉，不然 LLM 收到空蕩蕩的內容還是會被要求
# 生出摘要，容易編造出長者根本沒說過的細節（見這兩支函式內的說明）。
_NO_RESPONSE_MARKER = "（長者未回應）"
_MIN_SUMMARIZABLE_LEN = 6  # 少於這個字數視同沒有實質內容，不夠摘要


def _has_substantive_content(text: str | None) -> bool:
    if not text:
        return False
    # patient_response 可能是同一回合多次回應用換行接起來的（見 _save_round_
    # response），不能整段一起判斷：同回合裡如果某一題 30 秒逾時被自動送出
    # 「（長者未回應）」、但另一題長者有真的回答，還是要算有實質內容，不能
    # 因為剛好有一行是未回應標記就把整個回合都當成沒有內容而濾掉。
    return any(
        len(line.strip()) >= _MIN_SUMMARIZABLE_LEN and _NO_RESPONSE_MARKER not in line
        for line in text.split("\n")
    )


def _to_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _to_float(val) -> float | None:
    try:
        return float(val)
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
    patient_id: int | None = None,
    therapist_id: int | None = None,
) -> None:
    """
    把長者原話累加到 rounds.patient_response（真相源，之後可重建向量庫），
    同回合多次回應以換行分隔。emotion 不在這裡寫——改由回合結束時
    _finalize_round_emotion 依整回合累積的 frame 數多數決寫入，見該函式說明。
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
        # 2026-08-26稽核（使用者回報 round 2 答案沒進來後追查發現，code review
        # 再稽核修正）：前端把 question_number 存在往返的 state 裡，如果某次
        # /session/respond 的回應沒有真的套用到前端（例如網路請求失敗/逾時），
        # 前端下一次還是會帶著同一個舊 question_number 送出，這裡如果單純
        # SELECT 再 INSERT，兩個幾乎同時抵達的請求可能都通過「查無此題號」
        # 檢查、各自插入一列（正式資料庫也真的抓到一組：round_id=718,
        # question_number=2）——不只白佔一筆資料，_fill_round_exchange_answer
        # 之後查到兩筆會丟 MultipleResultsFound，長者這題答案就永遠補不進去。
        # 跟 _get_or_create_round 對 rounds(session_id, round_number) 同一套
        # 修法（見該函式說明、migration f1a3c9e7b2d4）：改用
        # round_exchanges(round_id, question_number) 唯一約束搭配
        # ON CONFLICT DO NOTHING，插入本身就是原子操作，不會有 race window。
        stmt = (
            pg_insert(RoundExchange)
            .values(
                round_id=round_row.id,
                question_number=question_number,
                question=question,
                stage=stage,
            )
            .on_conflict_do_nothing(index_elements=["round_id", "question_number"])
        )
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount == 0:
            print(
                f"[DB] round_exchanges 同一題號已存在，略過重複新增: "
                f"round={round_number} q#={question_number}"
            )
        else:
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
) -> bool:
    """長者回答後，補上對應 round_exchanges 列的 answer。

    回傳 True 代表這是第一次補上這題的答案；回傳 False 代表這一列早就有
    answer 了（或整列查不到）——呼叫端（session_respond）用這個判斷這次
    請求是不是前端逾時重試造成的重複提交，藉此避免 _save_round_response
    被同一句回答重複觸發兩次（見 2026-08-26 code review 稽核：
    GameController.cs 新增的重試機制，如果只是回應在路上弄丟、後端其實
    已經處理成功，重送會帶著同一組 round+question_number，答案文字會被
    寫進 patient_response 兩次）。
    """
    try:
        round_row = await _get_or_create_round(db, session_id, round_number)
        if round_row is None:
            return False
        # 用 .first()（依 id 排序取最新一筆）而不是 scalar_one_or_none()：
        # _save_round_exchange 現在已經用 DB 唯一約束＋ON CONFLICT DO NOTHING
        # 擋掉新的重複 insert（見 migration f1a3c9e7b2d4），但這筆修復前就
        # 累積的舊資料仍可能有重複列，scalar_one_or_none() 遇到多筆會直接丟
        # MultipleResultsFound，讓長者這題答案整個補不進去；取最新一筆更
        # 符合「長者剛答的是最近這題」的實際情況。
        exchange = (
            await db.execute(
                select(RoundExchange).where(
                    RoundExchange.round_id == round_row.id,
                    RoundExchange.question_number == question_number,
                ).order_by(RoundExchange.id.desc())
            )
        ).scalars().first()
        if exchange is None:
            return False
        already_answered = bool(exchange.answer)
        exchange.answer = answer
        await db.commit()
        print(f"[DB] round_exchanges 補上答案: round={round_number} q#={question_number}")
        return not already_answered
    except Exception as e:
        print(f"[DB] round_exchanges answer 寫入失敗（不影響主流程）: {e}")
        await db.rollback()
        return False


async def _finalize_round_response_time(
    db: AsyncSession,
    r,
    session_id: str,
    round_number: int,
    patient_id: int | None = None,
    therapist_id: int | None = None,
) -> None:
    """回合結束（end_round / end_session）時，把這回合累積的平均反應時間（秒）寫進
    rounds.response_time。sum_ms/count 由 sensor.py _update_session_stats 直接用
    Unity 送來的 response_time_ms 累加（2026-09-06 改版，理由見該函式說明），
    這裡只負責讀出來取平均、寫進 DB、清掉 Redis 暫存，不在乎資料是誰寫的。"""
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


async def _finalize_round_emotion(
    db: AsyncSession,
    r,
    session_id: str,
    round_number: int,
    patient_id: int | None = None,
    therapist_id: int | None = None,
) -> None:
    """回合結束時，把 sensor.py _update_session_stats 依回合分桶累積的 emo_*
    frame 數取多數決，寫入 rounds.emotion，取代舊版只存「回答那一瞬間」EMA
    快照的作法——單一瞬間容易受雜訊影響（例如剛好在回答問題時比較有活力），
    跟整回合（含看圖、聽故事、思考）的實際情緒觀感不一致，多數決更能代表
    整回合狀態，也才會跟療程整體情緒（_compute_and_save_assessment 的
    dominant_emotion）用同一套邏輯、可比較。"""
    key = f"session:{session_id}:round:{round_number}:emotion"
    raw = await r.hgetall(key)
    emo = {k: int(raw.get(k, 0)) for k in ("happy", "excited", "angry", "sad")}
    if any(emo.values()):
        dominant = max(emo, key=emo.get)
        emotion_label = _EMOTION_LABEL_MAP.get(dominant, "適當")
        try:
            round_row = await _get_or_create_round(db, session_id, round_number, patient_id, therapist_id)
            if round_row is not None:
                round_row.emotion = emotion_label
                await db.commit()
                print(f"[DB] rounds.emotion 寫入成功: round={round_number} emotion={emotion_label}")
        except Exception as e:
            print(f"[DB] rounds.emotion 寫入失敗（不影響主流程）: {e}")
            await db.rollback()
    await r.delete(key)


async def _finalize_round_signals(
    db: AsyncSession,
    r,
    session_id: str,
    round_number: int,
    patient_id: int | None = None,
    therapist_id: int | None = None,
) -> None:
    """回合結束時，把 sensor.py _update_session_stats 依回合分桶累積的訊號
    代碼出現頻率，取前幾名寫入 rounds.signal_codes；三維分數則是把這個回合
    自己每一幀分數的總和（_sum_eng/_sum_hap/_sum_agi）除以幀數，算出這個
    回合「自己的平均」寫入 rounds.*_pct——不是讀整場療程的 EMA 快照，跟
    _finalize_round_emotion 的多數決一樣，是這個回合獨立算出來的，不會被
    前一回合的殘留影響，供治療師端「判斷依據」顯示用。"""
    signal_key = f"session:{session_id}:round:{round_number}:signals"
    raw = await r.hgetall(signal_key)
    frame_count = int(raw.get("_frame_count", 0))
    if frame_count > 0:
        rates = {
            code: int(count) / frame_count
            for code, count in raw.items()
            if code not in ("_frame_count", "_sum_eng", "_sum_hap", "_sum_agi")
        }
        top_codes = sorted(
            (code for code, rate in rates.items() if rate >= 0.15),
            key=lambda code: rates[code],
            reverse=True,
        )[:4]

        avg_eng = float(raw.get("_sum_eng", 0)) / frame_count
        avg_hap = float(raw.get("_sum_hap", 0)) / frame_count
        avg_agi = float(raw.get("_sum_agi", 0)) / frame_count
        engagement_pct = _pct(avg_eng, *ENGAGEMENT_RANGE)
        happiness_pct = _pct(avg_hap, *HAPPINESS_RANGE)
        agitation_pct = _pct(avg_agi, *AGITATION_RANGE)

        try:
            round_row = await _get_or_create_round(db, session_id, round_number, patient_id, therapist_id)
            if round_row is not None:
                round_row.engagement_pct = engagement_pct
                round_row.happiness_pct = happiness_pct
                round_row.agitation_pct = agitation_pct
                round_row.signal_codes = json.dumps(top_codes, ensure_ascii=False)
                await db.commit()
                print(f"[DB] rounds 判斷依據寫入成功: round={round_number} signals={top_codes}")
        except Exception as e:
            print(f"[DB] rounds 判斷依據寫入失敗（不影響主流程）: {e}")
            await db.rollback()
    await r.delete(signal_key)


# ════════════ 評估分數計算輔助 ════════════════════════════════════════

# 有些比率的分子分母都只在「真的有訊號」的幀才累加（face_detected_n／
# emo_valid_n，見 sensor.py 說明），這樣分母不會被稀釋，但相對地失去了
# 「樣本夠大、比率天生不容易衝到極端值」這個附帶的保護——真實訊號的幀數
# 佔全部幀數的比例低於這個門檻時，比率是小樣本統計（例如整場只有 2 幀有
# 訊號，剛好都判成負面），容易被雜訊帶去極端值，這裡統一當作「資料不足，
# 不該讓這幾幀的結果主導分數」的判斷門檻（2026-09-06 稽核：換分母解決稀釋
# 問題後才發現的副作用，跟原本的問題是同一個硬幣的兩面）。
MIN_VALID_SAMPLE_RATIO = 0.2

ATTENTION_DEFAULT_SCORE = 3


def _score_attention(looking_away_rate: float, eye_closed_rate: float) -> int:
    """注意力：視線離開比率越低分數越高。"""
    raw = 1.0 - looking_away_rate - eye_closed_rate * 0.5
    if raw < 0.25: return 1
    if raw < 0.50: return 2
    if raw < 0.75: return 3
    return 4


def _score_engagement(looking_away_rate: float, mouth_moved_rate: float, angry_rate: float) -> int:
    """
    參與度：干擾(焦躁) > 不注意 > 主動說話 的優先判斷順序。

    原本用「晃動比率」（high_sway_rate，body_sway > SWAY_AGITATION_MIN 的比率）
    判斷干擾，但 body_sway 這顆訊號在即時分類（_ema_classify/_classify_from_
    scores）裡代表的是 arousal（激動程度），激動不等於負面：同樣晃動高，配上
    正向/中性表情會被即時分類判成「亢奮」（_classify_from_scores 裡是可接受
    的結果），配上負向表情才是「焦躁」。原本這裡完全不看情緒方向，晃動比率
    一超過門檻就直接砍到參與度最低分，會出現長者聊到懷念往事很興奮、比手畫
    腳（晃動高），即時畫面判「亢奮」，但同一份報告的參與度卻寫「1分:干擾」
    的自相矛盾（2026-09-06 稽核）。

    改用 angry_rate（這回合被分類成「焦躁」的頻率）取代——「焦躁」本身就是
    「高激動 + 負向情緒」的組合（見 _classify_from_scores），已經排除了正向
    的「亢奮」，跟即時分類的解讀維持一致，不用另外重新判斷情緒方向。
    """
    if angry_rate > 0.50:                                    return 1  # 干擾
    if looking_away_rate > 0.50:                             return 2  # 被動
    if mouth_moved_rate > 0.30 and looking_away_rate < 0.30: return 4  # 主動
    return 3                                                          # 可配合


def _score_persistence(sad_rate: float, looking_away_rate: float,
                       skel_absent_rate: float, far_rate: float = 0.0) -> tuple[int, str]:
    """持續力（AES）：離座（骨架消失或 SpineBase 移遠）或情緒極度低落 = 1分。

    回傳 (分數, 原因)——1分／2分這兩級各自可能是兩種完全不同的原因觸發
    （1分：真的離座 vs 情緒極度低落；2分：情緒偏低落 vs 常看向別處），
    只回傳分數的話，前端沒辦法知道該顯示「擅自離開」還是「情緒低落」，
    固定寫死其中一種會跟另一種原因觸發的實際情況兜不起來（2026-09-06
    稽核）。「離座」比「低落」更具體、對治療師來說更值得優先知道，兩個
    條件都成立時優先回報離座。
    """
    if skel_absent_rate > 0.30 or far_rate > 0.20:
        return 1, "left"
    if sad_rate > 0.50:
        return 1, "sad"
    if sad_rate > 0.30:
        return 2, "sad"
    if looking_away_rate > 0.60:
        return 2, "distracted"
    if sad_rate > 0.10:
        return 3, "default"
    return 4, "default"


def _score_emotion(emo: dict[str, int],
                   high_pitch_rate: float = 0.0,
                   pitch_baseline: float = 0.0) -> tuple[int, str]:
    """情緒狀況（OERS）：以 EMA 主導情緒為基底，音高變異作修正。

    回傳 (分數, 最終判定的情緒 key)——emotional_status 文字標籤（治療師在
    個案總覽頁「最近活動」列表看到的彩色標籤）必須用這裡回傳的 key 反查
    _EMOTION_LABEL_MAP，不能自己另外從 emo 重算一次 dominant，否則分數
    換算的總分進度條跟這個文字標籤可能講不同的故事。

    2026-09-07 拿掉 silence_rate 修正：這條規則會讓「大部分時間沒回應、
    但一開口/一有表情大多是正向」的長者，整場情緒狀況被直接蓋成「低落」
    （1分），跟 rounds.emotion（見 _finalize_round_emotion，逐回合只看
    dominant、不看沉默率）矛盾——治療師會看到每回合都是「適當」，總體卻是
    「低落」。「完全不說話」這個訊號本來就由 _score_interaction
    （response_count == 0）負責，不需要在這裡疊加一次判斷。

    high_pitch_rate 的計數門檻已在 sensor.py 個人化（baseline × 2），
    有基準時用 0.40；無基準時退守 0.55（計數仍用固定 50 Hz²，較不可靠）。

    音高修正只在 dominant 本來就不是 sad 時才生效：這個修正原本要抓的是
    「dominant 看起來還好（happy/excited），但音高起伏顯示可能有被蓋掉的
    焦躁」，不該反過來蓋掉本來就已經是 sad 的結論——長者真正低落/哭泣時
    講話聲音一樣會發抖、音高起伏大，不是只有焦躁的人才會這樣，若不排除
    sad，會把「低落」（這張量表 1 分，最差）誤修成「焦躁」（2 分）。
    """
    dominant = max(emo, key=emo.get) if any(emo.values()) else "happy"
    pitch_agitation_bar = 0.40 if pitch_baseline > 1.0 else 0.55
    if dominant != "sad" and high_pitch_rate > pitch_agitation_bar and emo.get("angry", 0) >= emo.get("happy", 0):
        return 2, "angry"
    return {"sad": 1, "angry": 2, "excited": 3, "happy": 4}[dominant], dominant


def _score_interaction(response_count: int, speech_chars: int,
                       avg_response_ms: float | None = None,
                       hand_active_rate: float = 0.0) -> tuple[int, str]:
    """互動頻率（Social Engagement Scale）：語音 + 反應延遲 + 手部動作。

    回傳 (分數, 原因)——2分有兩種完全不同的原因：整場完全零語音回應（只靠
    肢體動作）、或是有回應但都是「嗯/好/有」這種極短回覆，只回傳分數的話
    前端沒辦法分辨該顯示哪一種，固定寫死「僅指令回應」對完全沒開口的長者
    來說是錯的敘述（2026-09-06 稽核）。
    """
    if response_count == 0 and hand_active_rate < 0.05:   return 1, "default"
    if response_count == 0:                                return 2, "no_speech"  # 有肢體動作但無語音
    avg = speech_chars / response_count
    # 反應延遲懲罰：平均超過 10 秒視為需持續引導
    if avg_response_ms is not None and avg_response_ms > 10_000:
        avg *= 0.75
    if avg < 10:   return 2, "short_replies"  # 僅指令回覆（嗯/好/有）
    if avg < 25:   return 3, "default"        # 需引導互動
    return 4, "default"                       # 主動互動（完整句子/故事）


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
      RAG 檢索／TTS 合成不保證跑完）。
    warmup_ready：治療師網頁的暖身頁面是否已經真的載入、開始 poll
      warmup_progress（見 session_warmup_progress_get 那支設定這個旗標）。
      Unity 的 WarmupController 除了 requested，還要等這個旗標才會切去
      WarmupGameScene——治療師網頁跳轉到暖身頁面（router.push）不會等
      /session/start 那支耗時的生成流程跑完，但頁面切換、React 掛載還是
      需要一點時間，如果 Unity 只看 requested 就切場景，有機會比治療師
      網頁還早進暖身頁面，治療師端還沒開始 polling 就已經漏看最前面幾張
      卡片的回報。多等這個旗標，確保 Unity 一定是「治療師網頁已經在看」
      之後才開始跑，不用賭兩邊時間差。
    started：/session/start 是否已完整跑完（session:{id}:meta 已建立），代表第一回合
      內容真的生成好了。InstructionScene 靠這個訊號決定何時把內容拿回來、進場 GameScene。
    """
    r = request.app.state.redis
    calibrated = bool(await r.exists(f"session:{session_id}:calibration"))
    calibrating = (not calibrated) and ws_registry.is_calibrating(session_id)
    requested = bool(await r.exists(f"session:{session_id}:requested"))
    warmup_ready = bool(await r.exists(f"session:{session_id}:warmup_ready"))
    started = bool(await r.exists(f"session:{session_id}:meta"))
    return {
        "calibrated": calibrated,
        "calibrating": calibrating,
        "requested": requested,
        "warmup_ready": warmup_ready,
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
            # response_time 是 sensor.py 直接累加 Unity 送來的 response_time_ms
            # 寫進同一份 metrics hash，跟這裡的場景/問題資訊共用同一個 key，欄位
            # 本身沒有「屬於哪一題」的概念——不重置的話，新問題一出現，治療師
            # 頁面在長者按下麥克風之前，會一直沿用上一題量到的舊反應時間，看
            # 起來像長者還沒回答就已經有數字（2026-09-07 稽核：治療師反映長者
            # 還沒回答、反應時間卻顯示數字）。這裡重置回 "--"，等這一題長者
            # 真的按下麥克風、sensor.py 收到新的 response_time_ms 才會覆蓋掉。
            response_time="--",
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
        if round_number == 3 and carryover is not None:
            # 回合3開場要看得到回合1、2的完整逐字稿，不只是回合2最後一句話
            # （見 _build_prior_rounds_transcript 說明、orchestrator.py
            # _start_round3_closing 對這個 carryover 欄位的使用方式）。
            carryover["prior_rounds_transcript"] = await _build_prior_rounds_transcript(db, session_id)
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
            # 同 session_start：換題就把上一題殘留的反應時間清掉，見該處說明。
            response_time="--",
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
        patient_id = None
        if meta_raw:
            patient_id = _to_int(json.loads(meta_raw).get("patient_id"))
            if patient_id:
                await r.delete(f"patient:{patient_id}:active")
        # 治療師提前手動結束時，長者當下正在進行的那個回合不會走到
        # /session/respond 的 state is None 分支（那才是 _finalize_round_*
        # 系列平常唯一的呼叫點，見上面 result.get("state") is None 說明），
        # 導致這個回合累積在 Redis 的反應時間／情緒多數決／判斷依據
        # （engagement_pct 等）永遠沒機會寫進 rounds 資料表，治療師事後在
        # 歷史活動只會看到這個回合整排空白（2026-09-06 稽核發現：session
        # 855 手動結束時 round 1 已經真的收到 17 幀鏡頭分析，資料卻孤兒在
        # Redis 沒寫進 DB）。這裡補呼叫一次，把「正在進行中」的這個回合也
        # 收尾掉。三支函式都是「Redis 裡沒東西就直接跳過、清掉 key」的
        # no-op 設計，就算這個回合剛好已經正常收尾過一次（key 已被刪除），
        # 重複呼叫也不會出錯或造成資料錯亂。
        current_round = _to_int(await r.hget(f"session:{session_id}:metrics", "current_round")) or 1
        await _finalize_round_response_time(
            db, r, session_id, current_round, patient_id=patient_id, therapist_id=therapist_id,
        )
        await _finalize_round_emotion(
            db, r, session_id, current_round, patient_id=patient_id, therapist_id=therapist_id,
        )
        await _finalize_round_signals(
            db, r, session_id, current_round, patient_id=patient_id, therapist_id=therapist_id,
        )
        # /closing 那條自動寫入路徑不會被觸發——這裡補上，讓評估分數與
        # status="completed" 一定會落地，治療師隨後在 /activity/{id}/end
        # 頁面看到的才是真實數據而不是前端的預設分數。若長者剛好已經正常
        # 走完心得，_compute_and_save_assessment 早就算過一次並清掉 Redis
        # stats，這裡重複呼叫會因為讀不到 stats 而丟 404，直接吞掉即可
        # （不是錯誤，是正常的「已經結束過了」）。
        try:
            await _compute_and_save_assessment(request.app.state, session_id, db, therapist_id)
        except HTTPException as e:
            print(f"[Control] end 觸發評估略過: {e.detail}")
    return {"ok": True, "delivered": delivered}


class WarmupProgressPayload(BaseModel):
    card_key: str
    card_index: int
    total_cards: int
    progress_ratio: float = 0.0
    all_completed: bool = False
    report_seq: int = 0


@router.post("/{session_id}/warmup_progress", summary="Unity 換暖身動作卡時回報目前卡片，供治療師網頁同步顯示")
async def session_warmup_progress(
    request: Request,
    session_id: str,
    body: WarmupProgressPayload,
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    暖身動作卡的圖片存在治療師網頁前端本機（見 therapist-dashboard 的
    WARMUP_CARDS 對照表），這裡只轉發 card_key 這個穩定識別碼，不傳圖片本身。
    同一個 poseType（例如原地踏步跟踢腿的偵測邏輯都是 CountLegLift）可能對應
    不同卡片內容，所以用卡片自己的 cardKey 當識別碼，不能用 poseType
    （見 WarmupCardController.ActionCard.cardKey 說明）。

    progress_ratio：目前這張卡做到多少百分比（0~1），Unity 在卡片進行中會
    節流持續回報（見 WarmupCardController.progressReportInterval）。網頁目前
    只用 card_index 畫一跳一跳的進度條，還沒用到這個欄位，先存著留給之後
    要做卡片內即時填色時直接用，不用再改一次 Unity/後端。

    all_completed：5 張卡都做完時 Unity 會回報一次 true（見
    WarmupCardController.ReportAllCardsCompleted），供治療師網頁把「進入
    活動」按鈕從 disabled 改成可以按——Unity 這時停在原地等治療師端送出
    enter_activity 控制指令才會真的切場景（見 /warmup_control）。

    這支發生在 /session/start 之前（長者還在 WarmupGameScene，尚未進第一
    回合），所以不依賴 session:{id}:meta 存在，直接寫獨立的 warmup hash。

    report_seq：Unity 每次呼叫這支 API（換卡或進度節流回報）都會帶一個遞增
    序號（見 WarmupCardController.reportSeq）。這些請求是各自獨立、
    fire-and-forget 送出的，中間可能經過 Cloudflare Tunnel 等會重排序的
    路徑，網路較差時較舊的一筆有機率比較新的一筆晚到，若直接覆蓋會讓治療師
    網頁的進度條「倒退」。這裡用序號擋掉比目前已存序號還舊的請求，只接受
    嚴格遞增的回報，維持 last-writer-by-intent 而不是 last-arrived。
    """
    r = request.app.state.redis
    key = f"session:{session_id}:warmup"
    existing_seq = _to_int(await r.hget(key, "report_seq")) or 0
    if body.report_seq < existing_seq:
        return {"ok": True, "skipped": True}
    await r.hset(
        key,
        mapping={
            "card_key": body.card_key,
            "card_index": body.card_index,
            "total_cards": body.total_cards,
            "progress_ratio": body.progress_ratio,
            "all_completed": "1" if body.all_completed else "",
            "report_seq": body.report_seq,
        },
    )
    await r.expire(key, 3600)
    return {"ok": True}


@router.get("/{session_id}/warmup_progress", summary="取得暖身動作目前卡片（供治療師網頁 polling）")
async def session_warmup_progress_get(
    request: Request,
    session_id: str,
    therapist_id: int = Depends(get_current_therapist_id),
):
    r = request.app.state.redis
    # 治療師網頁跳轉到暖身頁面後，這支是它第一支會呼叫、而且會持續每 300ms
    # 呼叫一次的 API，用它的第一次呼叫當「暖身頁面真的載入了」的訊號，供
    # /session/{id}/status 的 warmup_ready 欄位讀取（見該端點說明），讓 Unity
    # 知道可以放心切場景了，不用賭治療師網頁的頁面切換/掛載時間。這裡每次
    # 呼叫都重設，不差這一點成本，換來不用另外維護「有沒有設過」的邊界情況。
    await r.set(f"session:{session_id}:warmup_ready", "1", ex=3600)
    data: dict = await r.hgetall(f"session:{session_id}:warmup")
    return {
        "card_key": data.get("card_key", ""),
        "card_index": _to_int(data.get("card_index")) or 0,
        "total_cards": _to_int(data.get("total_cards")) or 0,
        "progress_ratio": _to_float(data.get("progress_ratio")) or 0.0,
        "all_completed": bool(data.get("all_completed")),
    }


_WARMUP_CONTROL_ACTIONS = {"complete", "skip", "enter_activity"}


class WarmupControlPayload(BaseModel):
    action: str


@router.post("/{session_id}/warmup_control", summary="治療師網頁暖身頁面控制（標記完成／跳過此動作／允許進入活動）")
async def session_warmup_control(
    request: Request,
    session_id: str,
    body: WarmupControlPayload,
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    Unity 在暖身階段沒有像主活動那樣常駐的 /ws/stt 連線可以即時轉發控制指令
    （那條連線綁著 STT 音訊串流，是 GameScene 才建立的，暖身階段硬借來用
    風險較高，見 ws_stt.py／ws_registry.py），改用輕量 polling：這裡把指令
    推進一個 Redis list，WarmupCardController 每隔 controlPollInterval 秒
    poll 一次 GET /warmup_control 取走最舊的一筆並清掉。

    action：
      complete       — 治療師手動標記目前這張卡完成，等同 Kinect 真的偵測到
                        動作，Unity 會播放過關音效／徽章後換下一張卡
      skip           — 跳過目前這張卡，不算數、不播音效／徽章，直接換下一張
                        （這張卡不會在本次療程再出現，見 SkipCurrentCard 說明）
      enter_activity — 5 張卡都做完後，Unity 會停在原地等這個訊號才切去
                        InstructionScene（見 ReportAllCardsCompleted／
                        WaitForEnterActivityThenLoadScene）
    """
    if body.action not in _WARMUP_CONTROL_ACTIONS:
        raise HTTPException(status_code=400, detail=f"不支援的暖身控制動作: {body.action}")
    r = request.app.state.redis
    await r.rpush(f"session:{session_id}:warmup_control_queue", body.action)
    await r.expire(f"session:{session_id}:warmup_control_queue", 3600)
    return {"ok": True}


@router.get("/{session_id}/warmup_control", summary="Unity polling 是否有治療師下的暖身控制指令")
async def session_warmup_control_get(
    request: Request,
    session_id: str,
    therapist_id: int = Depends(get_current_therapist_id),
):
    r = request.app.state.redis
    action = await r.lpop(f"session:{session_id}:warmup_control_queue")
    return {"action": action}


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

    # session_completed 要看明確的 completed 旗標（見 _compute_and_save_assessment
    # 設定該旗標處的說明），不能用「meta 不存在」推斷——/session/start 設好
    # requested 旗標後、_init_session_meta 真正寫入 meta 前還有一段 LLM/RAG/
    # 生圖/TTS 生成時間，這段期間 meta 同樣不存在但療程根本還沒開始。
    completed = bool(await r.exists(f"session:{session_id}:completed"))

    data: dict = await r.hgetall(f"session:{session_id}:metrics")
    try:
        suggestions = json.loads(data.get("ai_suggestions", "[]"))
    except json.JSONDecodeError:
        suggestions = []
    try:
        signal_codes = json.loads(data.get("signal_codes", "[]"))
    except json.JSONDecodeError:
        signal_codes = []
    return {
        "emotion": data.get("emotion", "適當"),
        "response_time": data.get("response_time", "--"),
        "current_scene": data.get("current_scene", ""),
        "elder_response": data.get("elder_response", ""),
        "ai_suggestions": suggestions,
        "current_round": _to_int(data.get("current_round")) or 1,
        "total_rounds": _to_int(data.get("total_rounds")) or 3,
        # 情緒判斷依據（供治療師端顯示三維量表+訊號標籤）：sensor.py
        # receive_sensor 每幀寫入，值來自 Kinect 特徵換算，不是 LLM 生成。
        "engagement_pct": _to_int(data.get("engagement_pct")) or 0,
        "happiness_pct": _to_int(data.get("happiness_pct")) or 0,
        "agitation_pct": _to_int(data.get("agitation_pct")) or 0,
        "signal_codes": signal_codes,
        # 長者這一題/心得剛講完、還卡在等治療師審核時："" | "pending_round" | "pending_closing"，
        # 供治療師平板「長者的回應」分頁決定要不要顯示可編輯框（見 /{id}/review_request、
        # /{id}/closing/review_request）。elder_response_draft 是還沒被確認的草稿文字，
        # 跟上面已確認的 elder_response 分開存，確認前後兩者不會互相覆蓋。
        "review_status": data.get("review_status", ""),
        "elder_response_draft": data.get("elder_response_draft", ""),
        # completed 旗標只有在 _compute_and_save_assessment 算完評估分數、療程
        # 真正結束時才會被設起來（見該函式），一旦這裡是 true 就代表心得已經
        # 答完、評估算完了，可以自動跳轉到結束頁面，不用等治療師自己按
        # 「結束活動」。
        "session_completed": completed,
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


async def _build_prior_rounds_transcript(db: AsyncSession, session_id: str) -> str:
    """
    回合3開場（orchestrator.py _start_round3_closing／_generate_closing）用：
    組一份「這場療程目前為止所有回合」的逐字稿，取代原本只傳「長者上一句話」
    （carryover["last_elder_response"]）的做法——原本那樣寫承接語時只看得到
    回合2的最後一句，完全看不到回合1訪談（例如生圖前Q1/Q2）裡更豐富的細節，
    容易生出脫離脈絡的內容（例如把「做料理」這個主題腦補成「跟大家一起上
    料理課」）。

    查詢邏輯跟 _generate_story_summary 相同（只收錄長者真的有實質回答的回合，
    generated_scene 只當背景參考不當長者發言），但這裡是在回合3「開始」的
    當下呼叫，此時回合3自己的資料列還沒有內容，天然只會撈到回合1、2，不需要
    額外過濾 round_number。
    """
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
            if not _has_substantive_content(rnd.patient_response):
                continue
            label = f"第{rnd.round_number}回合"
            if rnd.generated_scene:
                parts.append(f"【{label}｜AI呈現的情境（僅供參考背景，不是長者說的話）】{rnd.generated_scene}")
            parts.append(f"【{label}｜長者實際所說】{rnd.patient_response}")
        return "\n".join(parts)
    except Exception as e:
        print(f"[Orchestrator] 回合3開場逐字稿撈取失敗（改用單句 fallback）: {e}")
        return ""


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

        # 只收錄長者真的有實質回答的回合——generated_scene 是 AI 呈現給長者看的
        # 引導畫面文字，不是長者說的話，不能單獨拿來充當「長者分享的內容」；
        # 如果這回合長者沒有實質回答，連同該回合的場景說明一起跳過，避免 LLM
        # 誤把 AI 寫的情境當成長者自己講的故事。
        parts = []
        for rnd in rounds:
            if not _has_substantive_content(rnd.patient_response):
                continue
            label = "心得" if rnd.type == "心得" else f"第{rnd.round_number}回合"
            if rnd.generated_scene:
                parts.append(f"【{label}｜AI呈現的情境（僅供參考背景，不是長者說的話）】{rnd.generated_scene}")
            parts.append(f"【{label}｜長者實際所說】{rnd.patient_response}")
        if not parts:
            return ""
        transcript = "\n".join(parts)

        messages = [
            {"role": "system", "content": (
                "你是懷舊療法的紀錄整理助手，負責把一場療程的對話內容整理成簡短的故事摘要，"
                "給治療師和家屬快速了解今天聊了什麼。你只能根據標記【長者實際所說】的內容摘要，"
                "標記【AI呈現的情境】的段落只是背景參考、不是長者說的話，絕對不能當成長者的發言寫進摘要。"
                "禁止編造逐字稿裡沒有出現過的具體細節、人名、地點或事件。"
            )},
            {"role": "user", "content": (
                f"以下是今天療程的記錄：\n\n{transcript}\n\n"
                "請用100字以內、第三人稱、溫暖但客觀的語氣，只根據長者實際所說的內容摘要他今天分享的回憶與整體狀態。"
                "全篇一律用「長者」稱呼，不要用「阿公」「阿嬤」「爺爺」「奶奶」「他」「她」等會透露或臆測性別的稱謂或代名詞，"
                "因為逐字稿裡沒有提供長者的性別資訊。"
                "如果長者實際所說的內容很少、講得很簡短籠統，摘要也要如實反映內容有限，不要延伸編造沒說過的細節。"
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
            {"role": "system", "content": (
                "你是懷舊療法的紀錄整理助手，負責把長者在一個回合裡的發言整理成一句話重點摘要，"
                "給治療師快速瀏覽。只能根據下面實際提供的內容摘要，禁止編造內容裡沒有出現過的"
                "具體細節、人名、地點或事件。"
            )},
            {"role": "user", "content": (
                f"長者這個回合說的話：\n{patient_response}\n\n"
                "請用20字以內、第三人稱的一句話摘要重點，一律用「長者」稱呼、不要用「阿公」「阿嬤」「他」「她」等"
                "會臆測性別的稱謂或代名詞，只回摘要文字本身，不要加任何說明或標點以外的內容。"
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
        # SQL 只能濾掉 NULL，「（長者未回應）」佔位字串跟內容太短（幾乎沒有
        # 實質內容）這兩種情況要在這裡濾掉，不然 LLM 拿到空蕩蕩的內容還是會
        # 被逼著生出一句摘要，容易編造長者根本沒說過的細節（見 _has_substantive_
        # content 說明）。這些回合就讓 rounds.summary 留空，前端會 fallback
        # 顯示原文（例如「（長者未回應）」），比生一句編出來的假摘要誠實。
        rounds = [rnd for rnd in rounds if _has_substantive_content(rnd.patient_response)]
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
    app_state,
    session_id: str,
    db: AsyncSession,
    therapist_id: int,
) -> dict:
    """計算五指標評估分數、產出故事摘要與整體情緒，寫入 PostgreSQL，並清除 Redis session 暫存。

    參數原本是 request: Request，只用得到 request.app.state.redis／
    llm_service，改成直接收 app_state（FastAPI app.state，HTTP handler
    傳 request.app.state，WebSocket handler 傳 websocket.app.state）——
    ws_stt.py 的 _mark_abnormal_end 是在 WebSocket 斷線時呼叫，沒有 Request
    物件可傳，這樣兩種呼叫端都能重用同一套評分邏輯，不用另外複製一份
    （2026-09-06 稽核：/ws/stt 不正常斷線原本直接把 status 標成 completed，
    完全沒有算分數，這裡才是真正把它接上 _compute_and_save_assessment）。
    """
    r = app_state.redis
    raw: dict = await r.hgetall(f"session:{session_id}:stats")
    if not raw:
        raise HTTPException(status_code=404, detail="找不到此療程的統計資料，請確認 session_id 正確且療程已進行")

    frame_count       = max(int(raw.get("frame_count",         0)), 1)
    face_detected_n   = int(raw.get("face_detected_n",       0))
    looking_away_n    = int(raw.get("looking_away_n",        0))
    eye_closed_n      = int(raw.get("eye_closed_n",          0))
    mouth_moved_n     = int(raw.get("mouth_moved_n",         0))
    mouth_window_n    = int(raw.get("mouth_window_n",        0))
    high_sway_n       = int(raw.get("high_sway_n",           0))
    skel_absent_n     = int(raw.get("skel_absent_n",         0))
    response_count    = int(raw.get("response_count",        0))
    speech_chars      = int(raw.get("speech_chars",          0))
    # A 階段擴充
    rt_sum            = int(raw.get("response_time_sum",     0))
    rt_count          = int(raw.get("response_time_count",   0))
    # B 階段擴充（Unity 尚未傳送時 = 0，不影響評分）
    far_n             = int(raw.get("far_n",                 0))
    spinebase_valid_n = int(raw.get("spinebase_valid_n",     0))
    high_pitch_var_n  = int(raw.get("high_pitch_var_n",      0))
    pitch_window_n    = int(raw.get("pitch_window_n",        0))
    hand_active_n     = int(raw.get("hand_active_n",         0))
    emo_valid_n       = int(raw.get("emo_valid_n",           0))
    emo = {
        "happy":   int(raw.get("emo_happy",   0)),
        "excited": int(raw.get("emo_excited", 0)),
        "angry":   int(raw.get("emo_angry",   0)),
        "sad":     int(raw.get("emo_sad",     0)),
    }

    # looking_away/eye_closed/mouth_moved 只有這一幀真的偵測到臉才會有訊號
    # （見 sensor.py _update_session_stats），分母要用 face_detected_n（實際
    # 有臉部資料的幀數），不能用 frame_count（含沒偵測到臉的幀）——否則鏡頭
    # 角度/光線不佳導致偵測率低時，比率會被「沒資料」的幀稀釋，看起來比長者
    # 實際表現更好（2026-09-06 稽核：注意力分數因此跟治療師現場觀察對不上）。
    face_valid_n      = max(face_detected_n, 1)
    looking_away_rate = looking_away_n   / face_valid_n
    eye_closed_rate   = eye_closed_n     / face_valid_n
    # mouth_moved_rate 分母改用 mouth_window_n（只算「已進入回答等待期、且
    # 有臉部資料」的幀數），不能沿用 face_valid_n——narration 播放中/估算
    # 閱讀時間還沒過時長者本來就不該開口，那段期間的幀不該算進「有沒有主動
    # 說話」這個比率的分母，否則分子已經排除 narration 幀、分母卻沒排除，
    # 比率會被稀釋到不合理地低（見 sensor.py mouth_window_n 說明，2026-09-08
    # 稽核：長者聽題目本來就不開口，參與度被拉低）。
    mouth_valid_n     = max(mouth_window_n, 1)
    mouth_moved_rate  = mouth_moved_n    / mouth_valid_n
    # 臉部偵測率過低時（face_detected_n 只佔 frame_count 一小部分），上面兩個
    # 比率是小樣本統計，容易失真（例如整場只有 3 幀有臉，剛好都判成看向別處
    # 就變成 100%）。歸零讓依賴它們的分項退回各自「沒有負面訊號」的預設分支
    # ——參與度／持續力的公式在輸入 0 時本來就會落到中性判斷，不需要另外
    # 特殊處理；只有注意力的公式在輸入全 0 時會誤判成滿分（1.0 - 0 - 0 = 滿分
    # 注意力），所以注意力額外用 ATTENTION_DEFAULT_SCORE 蓋掉，不能靠歸零。
    face_data_sufficient = face_detected_n / frame_count >= MIN_VALID_SAMPLE_RATIO
    if not face_data_sufficient:
        looking_away_rate = 0.0
    # mouth_moved_rate 用自己的分母（mouth_window_n）判斷樣本是否足夠，不
    # 沿用 face_data_sufficient——一場長者大多在聽長篇 narration 的回合，
    # face_detected_n 可能很充足，但 mouth_window_n（回答等待期內的幀）仍
    # 可能偏少，兩者是獨立的樣本量問題。
    mouth_data_sufficient = mouth_window_n / frame_count >= MIN_VALID_SAMPLE_RATIO
    if not mouth_data_sufficient:
        mouth_moved_rate = 0.0
    skel_absent_rate  = skel_absent_n    / frame_count
    # sad_rate／angry_rate 分母是 emo_valid_n，不是 frame_count——emo_* 計數
    # 只在這一幀臉部或骨架至少有一個真的偵測到時才累加（見 sensor.py
    # _update_session_stats 的 emo_has_signal 說明），沒訊號的幀分類結果只是
    # 複製上一次的 EMA 值、不是新證據，分母也要一併排除，不然長者長時間
    # 離座/追丟時，最後一刻的情緒會被稀釋或放大成不成比例的比率。
    sad_rate          = emo["sad"]   / max(emo_valid_n, 1)
    # 「焦躁」= 高激動 + 負向情緒（見 _classify_from_scores），拿來判斷參與度
    # 的干擾行為比單看晃動比率（high_sway_rate）更準確，見 _score_engagement
    # 的完整說明——同樣高激動，正向的「亢奮」不該被當成干擾。high_sway_n
    # 這個累積值仍保留在 Redis 供其他用途，只是評分不再用它。
    angry_rate        = emo["angry"] / max(emo_valid_n, 1)
    # emo_valid_n 太小時（例如整場只有 2 幀真的有訊號），上面兩個比率是小
    # 樣本統計，容易被雜訊帶去 0 或 1 的極端值（例如 2 幀剛好都判成焦躁，
    # angry_rate 直接變 1.0），足以誤觸發 _score_engagement／_score_persistence
    # 的門檻，把參與度/持續力判成最差分——歸零後理由跟上面 face_data_sufficient
    # 一樣，這兩個分項的公式在輸入 0 時會落到中性分支，不需要額外處理
    # （2026-09-06 稽核：換分母修好稀釋問題後才浮現的副作用）。
    emo_data_sufficient = emo_valid_n / frame_count >= MIN_VALID_SAMPLE_RATIO
    if not emo_data_sufficient:
        sad_rate   = 0.0
        angry_rate = 0.0
    # far_rate 分母是 spinebase_valid_n（真的追蹤到 SpineBase 的幀數），不是
    # frame_count（含骨架完全追丟的幀）——跟 looking_away_rate 改用
    # face_detected_n 是同一種修正，見上面的說明。骨架追丟時 skel_absent_rate
    # 已經會單獨反映「長者可能不在座位上」，這裡只是不讓 far_rate 本身被
    # 追丟的幀稀釋掉。
    far_rate          = far_n / max(spinebase_valid_n, 1)
    # 分母改用 pitch_window_n（只算「已進入回答等待期」的幀數），理由同
    # mouth_moved_rate 改用 mouth_window_n——見 sensor.py pitch_window_n 說明。
    high_pitch_rate   = high_pitch_var_n / max(pitch_window_n, 1)
    hand_active_rate  = hand_active_n    / frame_count
    avg_response_ms   = rt_sum / rt_count if rt_count > 0 else None

    # 讀取個人音高校正基準（必須在 scores 計算之前）
    calib_raw      = await r.get(f"session:{session_id}:calibration")
    calib          = json.loads(calib_raw) if calib_raw else {}
    pitch_baseline = float(calib.get("pitchVarianceBaseline", 0.0))

    attention_score = (
        _score_attention(looking_away_rate, eye_closed_rate)
        if face_data_sufficient
        else ATTENTION_DEFAULT_SCORE
    )
    emotion_score, final_emotion_key = _score_emotion(emo, high_pitch_rate, pitch_baseline)
    endurance_score, endurance_reason = _score_persistence(sad_rate, looking_away_rate, skel_absent_rate, far_rate)
    interaction_score, interaction_reason = _score_interaction(
        response_count, speech_chars, avg_response_ms, hand_active_rate
    )

    scores = {
        "參與度":   _score_engagement(looking_away_rate, mouth_moved_rate, angry_rate),
        "注意力":   attention_score,
        "持續力":   endurance_score,
        "情緒狀況": emotion_score,
        "互動頻率": interaction_score,
    }

    # 從 Redis meta 讀取 patient_id / therapist_id
    meta_key = f"session:{session_id}:meta"
    meta_raw = await r.get(meta_key)
    meta = json.loads(meta_raw) if meta_raw else {"session_id": session_id}

    # emotional_status 文字標籤（治療師個案總覽頁「最近活動」列表的彩色標籤）
    # 直接用 _score_emotion 回傳的 final_emotion_key 反查，不能自己另外從 emo
    # 重算一次 dominant——否則套用了 silence_rate／音高修正後，分數換算的
    # 總分進度條跟這個文字標籤可能講不同的故事，見 _score_emotion 說明。
    emotional_status = _EMOTION_LABEL_MAP.get(final_emotion_key, "適當")
    story_summary = await _generate_story_summary(app_state.llm_service, db, session_id)

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
                endurance_reason=endurance_reason,
                score_emotion=scores["情緒狀況"],
                score_interaction=scores["互動頻率"],
                interaction_reason=interaction_reason,
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
                    "endurance_reason": endurance_reason,
                    "score_emotion": scores["情緒狀況"],
                    "score_interaction": scores["互動頻率"],
                    "interaction_reason": interaction_reason,
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
        await _generate_and_save_round_summaries(app_state.llm_service, db, session_id)

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
        # 療程真正結束的明確旗標，供 session_metrics 判斷 session_completed 用。
        # 不能只靠「meta 不存在」推斷完成——/session/start 把 requested 旗標設好
        # 之後、_init_session_meta 真正寫入 meta 之前還有一段 LLM/RAG/生圖/TTS
        # 的生成時間，這段期間 meta 同樣不存在，若長者暖身動作做得比生成快，
        # 治療師端一進活動頁就會被誤判成「已結束」直接跳去結束量表頁
        # （2026-09-08 稽核：暖身進活動誤跳量表頁）。
        await r.set(f"session:{session_id}:completed", "1", ex=3600)
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
    return await _compute_and_save_assessment(request.app.state, session_id, db, therapist_id)


class ConfirmResponsePayload(BaseModel):
    """治療師確認（可能編輯過）長者回答的請求體，回合1-3與心得回合的確認端點共用。"""
    elder_response: str


async def _finalize_closing_response(
    request: Request,
    session_id: str,
    text: str,
    therapist_id: int,
    db: AsyncSession,
    answered_at_ms: int | None = None,
) -> dict:
    """
    處理長者的心得收尾回答。原本是 /session/{id}/closing 端點本體，現在被治療師
    審核流程（/{id}/closing/review_request + /{id}/closing/confirm_response）共用。
    answered_at_ms 是 review_request 收到 STT 結果的時間，不能算成治療師編輯完
    按確認的時間——這裡跟主回合 1-3 不一樣，心得回合的 rounds.response_time
    仍是伺服器用 closing_asked_at 算時間差（見下面），不是 Unity 端量的，
    這個時間戳記還是需要的（跟 _finalize_elder_response 不同，那邊的反應
    時間已經改用 sensor.py 直接累加 Unity 送來的 response_time_ms，
    answered_at_ms 已經沒用而拿掉了）。

    做兩件事：
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

    if text.strip():
        try:
            round_row = await _get_or_create_round(
                db, session_id, 4, patient_id, therapist_id, round_type="心得",
            )
            if round_row is not None:
                round_row.patient_response = text
                if closing_asked_at_raw:
                    now_ms = answered_at_ms if answered_at_ms is not None else int(time.time() * 1000)
                    elapsed_ms = max(0, now_ms - int(closing_asked_at_raw))
                    round_row.response_time = round(elapsed_ms / 1000, 1)
                await db.flush()
                db.add(RoundExchange(
                    round_id=round_row.id,
                    question_number=1,
                    question=closing_question,
                    answer=text,
                ))
                await db.commit()
                print(f"[DB] 心得回合寫入成功: session={session_id}")
        except Exception as e:
            print(f"[DB] 心得回合寫入失敗（不影響評估流程）: {e}")
            await db.rollback()
    await r.delete(f"session:{session_id}:closing_asked_at")

    # 心得環節開始時 current_round 已經推進到 4（見 session_respond 對
    # end_session 的處理），期間 Kinect 送來的感測幀會分桶進
    # session:{id}:round:4:emotion／signals，這裡比照回合1-3收尾時的做法
    # （session_respond 的 result.get("state") is None 分支）把這桶資料取出、
    # 寫進 rounds.emotion／engagement_pct／happiness_pct／agitation_pct／
    # signal_codes，心得回合才會跟其他回合一樣有「判斷依據」可以在治療師端
    # 展開（2026-09-07 稽核：心得卡片原本完全沒有這幾個欄位，因為從來沒有
    # 任何地方呼叫過這兩支函式處理 round_number=4）。不管長者這題有沒有
    # 說話都要收尾，理由跟回合1-3一致：這兩支函式反映的是整段時間感測到的
    # 狀態，不是「有沒有講出心得」決定的。
    await _finalize_round_emotion(db, r, session_id, 4, patient_id=patient_id, therapist_id=therapist_id)
    await _finalize_round_signals(db, r, session_id, 4, patient_id=patient_id, therapist_id=therapist_id)

    # 2026-08-17起：承接語＋系統整合肯定＋感謝語已經在心得環節「開場」那一步
    # 呼應回合3的回答講完了（見 app/services/closing_templates.py
    # build_closing_invitation、app/routers/session.py session_respond 對
    # end_session 的處理），長者回答完這裡的開場邀請語後，答案只需要記錄、
    # 不再另外生成第二段收尾訊息——closing_message 留空字串，ShareController.cs
    # 的 PostClosingAnswer 對空字串本來就會跳過顯示、直接轉場（見該檔）。
    try:
        scores = await _compute_and_save_assessment(request.app.state, session_id, db, therapist_id)
    except HTTPException as e:
        print(f"[Closing] 自動評估略過: {e.detail}")
        scores = None

    return {"ok": True, "closing_message": "", "assessment": scores}


@router.post("/{session_id}/closing", summary="記錄三回合結束後的心得回合，並自動觸發評估寫入")
async def session_closing(
    request: Request,
    session_id: str,
    body: TranscriptPayload,
    db: AsyncSession = Depends(get_db),
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    薄封裝，供兩種呼叫端使用：
    1. 不走治療師審核流程的直接送出（心得回合長者沉默直接送出空字串、或
       /closing/review_request 重試用盡退回舊流程時，ShareController.cs
       直接呼叫這支）。
    2. 長者按下「送出故事」、真正觸發評估流程（2026-09-06 改版：治療師
       確認只推播確認後的文字，不在確認當下就先寫資料庫/算評估，見
       session_closing_confirm_response 說明；長者按送出時 Unity 才呼叫
       這支，等同直接送出流程）。

    如果這個 session 還留著 pending_closing_review（代表 body.text 是剛
    經過治療師審核確認的心得文字），要用其中存的 received_at 當反應時間
    起算點——長者實際開口回答是在治療師審核**之前**。用完就刪掉、把
    review_status 清空，這才是真正結束、治療師網頁不用再讓治療師修改
    的訊號（見 session_closing_confirm_response 的 awaiting_closing_submit
    說明）。找不到就是原本的直接送出情境，行為不變。
    """
    r = request.app.state.redis
    answered_at_ms = None
    raw = await r.get(f"session:{session_id}:pending_closing_review")
    if raw:
        pending = json.loads(raw)
        answered_at_ms = pending.get("received_at")
        await r.delete(f"session:{session_id}:pending_closing_review")
        await _update_live_view(r, session_id, review_status="")
    return await _finalize_closing_response(
        request, session_id, body.text, therapist_id, db,
        answered_at_ms=answered_at_ms,
    )


class ClosingReviewRequestPayload(BaseModel):
    text: str


@router.post("/{session_id}/closing/review_request", summary="Unity STT辨識完成，送治療師平板審核心得回答")
async def session_closing_review_request(
    request: Request,
    session_id: str,
    body: ClosingReviewRequestPayload,
    therapist_id: int = Depends(get_current_therapist_id),
):
    r = request.app.state.redis
    payload = {"text": body.text, "received_at": int(time.time() * 1000)}
    await r.set(
        f"session:{session_id}:pending_closing_review",
        json.dumps(payload, ensure_ascii=False),
        ex=1800,
    )
    await _update_live_view(
        r, session_id,
        elder_response_draft=body.text,
        review_status="pending_closing",
    )
    return {"ok": True}


@router.post("/{session_id}/closing/confirm_response", summary="治療師確認（可能編輯過）長者的心得回答，推播回 Unity，等長者按送出才觸發評估")
async def session_closing_confirm_response(
    request: Request,
    session_id: str,
    body: ConfirmResponsePayload,
    therapist_id: int = Depends(get_current_therapist_id),
    db: AsyncSession = Depends(get_db),
):
    """
    2026-09-06 改版（跟回合1-3的 session_confirm_response 同一套理由）：
    這裡不再直接呼叫 _finalize_closing_response（寫資料庫＋觸發五指標
    評估＋故事摘要 LLM 呼叫）。原本設計是治療師一確認就先做完，代價是
    治療師確認後、長者還沒按送出、療程就被結束時，這些已經算好的結果
    會被丟棄，白白浪費一次 LLM 摘要呼叫，跟回合1-3踩到的問題是同一種。

    改成這裡只把確認後的文字推播給 Unity，長者真的按下「送出故事」時
    才會呼叫 /session/closing 觸發評估（見 ShareController.cs 的
    OnSubmit／SubmitClosing）。

    pending_closing_review 故意不刪除、只更新 text 欄位為確認後的文字：
    received_at 要留給長者真的送出時的 /session/closing 用，反應時間才
    能算到長者當初開口回答的那一刻。這也代表治療師可以在長者按送出前
    重複呼叫這支修改文字，安全覆蓋、不會浪費任何已經觸發的流程。

    review_status 改成 "awaiting_closing_submit"（不是共用回合1-3那個
    "awaiting_round_submit"，也不是清空成 ""）：清空代表「完全結束」，
    會讓治療師網頁把編輯框收掉，但長者這時候其實還沒按送出，治療師應該
    還能改；跟回合1-3分開命名是因為治療師網頁要靠這個值判斷「重新確認
    時該打哪一支 API」，如果兩邊共用同一個值，重新編輯時就分不出來是
    回合1-3還是心得回合（見 LiveSessionView.tsx isClosing 判斷）。
    """
    r = request.app.state.redis
    raw = await r.get(f"session:{session_id}:pending_closing_review")
    if not raw:
        raise HTTPException(status_code=409, detail="沒有待確認的長者心得")
    pending = json.loads(raw)
    pending["text"] = body.elder_response
    await r.set(
        f"session:{session_id}:pending_closing_review",
        json.dumps(pending, ensure_ascii=False),
        ex=1800,
    )
    await _update_live_view(
        r, session_id, elder_response=body.elder_response, review_status="awaiting_closing_submit",
    )
    delivered = await ws_registry.send_message(session_id, {
        "type": "confirmed_closing_text",
        "elder_response": body.elder_response,
    })
    # 同 session_confirm_response：狀態已經寫進 _update_live_view，長者端
    # Unity 之後 polling /metrics 還是撈得到（見 ShareController.cs
    # PollForConfirmedText），這裡只是讓治療師網頁知道要不要重試。
    if not delivered:
        raise HTTPException(status_code=503, detail="長者端連線中斷，可能還沒收到，請重試")
    return {"ok": True}


async def _finalize_elder_response(
    request: Request,
    elder_response: str,
    state: SessionState,
    therapist_id: int,
    db: AsyncSession,
) -> dict:
    """
    處理長者這一題的回答，取得下一步動作。原本是 /session/respond 端點本體，
    2026-09-06 改版後（見 session_confirm_response 說明）治療師確認只推播
    文字，不觸發生成，實際上只有 session_respond 這一個呼叫端——不管是
    「不走審核直接送出」還是「長者按下送出故事」，Unity 都是呼叫
    /session/respond，才會走到這支函式。

    反應時間不在這裡算：長者從看完/聽完題目到開始回答的時間，只有 Unity
    端知道（見 KinectSensorSender.cs OnQuestionAsked／response_time_ms），
    伺服器這裡收到請求的時間點含了治療師審核＋長者按送出的延遲，拿來算
    反應時間並不準。改由 sensor.py _update_session_stats 直接用 Unity 送
    來的 response_time_ms 累加進 session:{id}:round:{n}:timing，回合結束
    時一樣走 _finalize_round_response_time 寫進 rounds.response_time。

    回傳的 action：
      open_followup    → 話題豐富，繼續順著長者深入（含 scene_text + question）
      ask_supplement_w → 話題結束，切入未問的W維度（含 scene_text + question）
      end_round        → 本回合完成，用 next_round 呼叫 /session/round
      end_session      → 三回合結束，療程收尾
    """
    orchestrator = request.app.state.orchestrator
    try:
        r = request.app.state.redis
        metrics = await r.hgetall(f"session:{state.session_id}:metrics")
        emotion = metrics.get("emotion_raw", "")  # 沒有 Kinect 數據時存空值，不假造 happy
        # 2026-08-26稽核（code review 發現）：先查/補這題的 round_exchanges
        # 答案，順便判斷這是不是前端逾時重試造成的重複提交（見
        # _fill_round_exchange_answer 說明）——GameController.cs 新增的重試
        # 機制，如果只是回應在路上弄丟、後端其實已經處理成功，重送會帶著
        # 同一組 round+question_number。是重複提交的話，底下落地逐字稿／
        # 累加反應時間都要跳過，不然長者同一句話會被記兩次、反應時間統計
        # 也會被重複累加。
        is_first_answer = await _fill_round_exchange_answer(
            db, state.session_id, state.round,
            question_number=state.question_number, answer=elder_response,
        )
        if is_first_answer:
            # 先落地逐字稿（真相源），後續 LLM 流程失敗也不遺失長者的話。
            # rounds.emotion 不在這裡寫，改由回合結束時 _finalize_round_emotion
            # 依整回合累積的 frame 數多數決寫入（見該函式說明）。
            await _save_round_response(
                db, state.session_id, state.round,
                text=elder_response,
                patient_id=_to_int(state.user_id), therapist_id=therapist_id,
            )
            # 反應時間不在這裡算：改由 sensor.py _update_session_stats 直接用
            # Unity 送來的 response_time_ms 累加進 session:{id}:round:{n}:timing
            # （2026-09-06 稽核後改版，見該函式說明）——「聽/看完題目到長者
            # 開始回答」這個定義只有 Unity 自己知道，伺服器用 question_asked_at
            # 算時間差含了語音合成/播放的時間，不準。
        else:
            print(
                f"[DB] round={state.round} q#={state.question_number} "
                f"這題已經有答案，判定為重複提交，跳過逐字稿累加"
            )
        await _update_live_view(
            request.app.state.redis, state.session_id,
            elder_response=elder_response,
        )
        result = await orchestrator.process_response(
            elder_response=elder_response,
            state=state.model_dump(),
            emotion=emotion,
            on_generating_image=lambda: ws_registry.send_control(
                state.session_id, "generating_image"
            ),
        )

        # orchestrator 判定長者這句話需要情緒安撫，代表語意上有明確負向訊號，
        # 寫進 Redis 供 sensor.py _ema_classify 的 text_negative 讀取（見該處
        # 說明）。90 秒是給長者聽完安撫語、回答下一句引導問題留的窗口。
        if result.get("action") == "emotional_support":
            await r.set(
                f"session:{state.session_id}:text_negative_until",
                str(time.time() + 90),
                ex=90,
            )

        if result.get("image_path"):
            # action=="scene_ready"：長者剛答完生圖前的引導問題，這裡才第一次
            # 真的生出圖片（見 orchestrator.py _start_scene_after_detail）。
            # /session/start、/session/round 那兩支端點呼叫 start_round 時還
            # 沒有圖，寫進 DB 的 scene_image 會是空字串，要等這裡才補上真正的
            # 圖片路徑與場景文字。
            await _save_round_image(
                db, state.session_id, state.round, result["image_path"],
                scene_text=result.get("scene_text", ""),
                patient_id=_to_int(state.user_id), therapist_id=therapist_id,
            )

        if result.get("state") is None:
            # 回合結束（end_round / end_session），把這回合累積的平均反應時間寫進 rounds.response_time
            await _finalize_round_response_time(
                db, r, state.session_id, state.round,
                patient_id=_to_int(state.user_id), therapist_id=therapist_id,
            )
            await _finalize_round_emotion(
                db, r, state.session_id, state.round,
                patient_id=_to_int(state.user_id), therapist_id=therapist_id,
            )
            await _finalize_round_signals(
                db, r, state.session_id, state.round,
                patient_id=_to_int(state.user_id), therapist_id=therapist_id,
            )
            # 2026-09-08 稽核：上面三支 _finalize_round_* 結算完當下就把
            # Redis 的回合桶子清掉，但 current_round 原本要等前端另外呼叫
            # /session/round 才會推進到下一回合——中間這段生圖/TTS的空窗期
            # （可能長達數秒），Unity 照常送來的感測幀讀到的 current_round
            # 還是剛結束的舊值，會在已結算的桶子裡重新開一個「幽靈」桶子，
            # 這幾幀資料最後不會被任何回合採計到（實測 session 1162 round1
            # 出現：結算前 80 幀正常寫進 DB，結算後又冒出幾幀從頭計數，
            # 從沒再被結算過）。這裡結算完就立刻把 current_round 推進，
            # 讓「結算」跟「回合切換」在同一個請求裡完成，不留空窗期；
            # /session/round 稍後還是會再寫一次同樣的值，冪等、不衝突。
            if result.get("action") == "end_round":
                await _update_live_view(
                    r, state.session_id,
                    current_round=state.round + 1,
                )
            if result.get("action") == "end_session":
                # 三回合正常跑完、準備轉場到 ShareScene 問心得——GameController 的
                # /ws/stt 連線會在轉場時斷線（ShareController 開的是另一條沒帶
                # session_id 的連線，見該檔 ConnectWebSocket），這個斷線不是治療師
                # 按「結束活動」、也不是不正常斷線，是正常流程的一部分，這裡先標記
                # 起來供 ws_stt.py 的 finally 區塊排除，避免誤判成不正常結束。
                await r.set(
                    f"session:{state.session_id}:reached_closing",
                    "1", ex=3600,
                )
                # 心得問題出現的時間點，供 /session/{id}/closing 計算心得回合的反應時間
                await r.set(
                    f"session:{state.session_id}:closing_asked_at",
                    str(int(time.time() * 1000)), ex=3600,
                )
                # current_round 推進到 4（心得）：sensor.py _update_session_stats
                # 是靠這個值把感測幀分桶進 session:{id}:round:{n}:emotion／signals
                # （見該函式說明）。沒有這行的話，心得環節長者答題時 Kinect 送來的
                # 幀會一直被算進 round 3 的桶子裡（current_round 卡在最後一次
                # /session/round 設的值，從沒被推進過），導致心得回合自己完全沒有
                # 情緒/判斷依據資料可用（2026-09-07 稽核：治療師反映歷史活動的心得
                # 卡片沒有「判斷依據」可以展開）。
                await _update_live_view(r, state.session_id, current_round=4)
                # 心得環節開場邀請語是純規則模板（見 app/services/closing_
                # templates.py），orchestrator._end_action 對 end_session 只回
                # 空字串，這裡才是真正填入內容的地方——固定是「感謝語＋問題」
                # 兩段（2026-09-08起拿掉了原本呼應回合3回答的承接語，見
                # closing_templates.py build_closing_invitation 說明）。
                invitation = await build_closing_invitation()
                result["thanks_text"] = invitation["thanks_text"]
                result["question"] = invitation["question"]
                # 心得環節的音檔全部是前端內建預錄音檔（見 audio_bank.py／
                # closing_templates.py），不用即時TTS，直接把 key 列表帶過去
                # 給前端；下面 1233 行那個 action=="end_session" 就跳過TTS的
                # 分支維持不動，這裡是唯一填入這兩個欄位的地方。
                result["thanks_audio_keys"] = invitation["thanks_audio_keys"]
                result["question_audio_keys"] = invitation["question_audio_keys"]
            if result.get("action") == "end_round" and state.round in (1, 2):
                # round 2/3 開場需要承接這裡：round 1 結束時記畫面元素／話題／
                # 生圖前訪談內容＋這句話，round 2 結束時只需要這句話（round 3
                # 的 closing 不需要畫面），見 orchestrator.py start_round 說明。
                carryover = {"last_elder_response": elder_response, "emotion": emotion}
                if state.round == 1:
                    carryover.update({
                        "scene_elements": state.scene_elements,
                        "scene_composition": state.scene_composition,
                        "pre_image_detail": state.pre_image_detail,
                        "topic_category": state.topic_category,
                        "topic_senses": state.topic_senses,
                        # round 1 結束時已經自然涵蓋的 W 維度——round 2 開場用來
                        # 排除補問候選，不要再問長者已經在 round 1 講過的具體事實
                        # （見 orchestrator.py _start_round2_free_followup 的
                        # known_facts_w 說明）。
                        "round1_covered_w": state.covered_w,
                        # round 1 最後一題問了什麼——round 2 開場生成時當
                        # 參考資訊（見 orchestrator.py _start_round2_free_
                        # followup 說明）。2026-08-18稽核（第四次，使用者
                        # 提案）：這裡原本帶的是round 1整份round_qa_log，
                        # 改成只帶最後一題的單一字串，理由見
                        # SessionState.last_question_text 上方註解。
                        "round1_last_question": state.last_question_text,
                        # round 1 結束時已經自然涵蓋的感官——round 2 開場用來排除
                        # 感官選項，不要再問長者已經在 round 1 答過的感官（例如
                        # 味覺/嗅覺），跟上面 round1_covered_w 同一個問題、同一種
                        # 修法（見 orchestrator.py _start_round2_free_followup 的
                        # known_senses 說明）。只帶 covered_senses、不帶
                        # skipped_senses——skipped_senses 是「問了但長者沒答到」，
                        # round 2 沒理由跟著避開，跟 round1_covered_w 只帶
                        # covered_w、不帶 skipped_w 是同一個理由。
                        "round1_covered_senses": state.covered_senses,
                    })
                await _cache_round_carryover(r, state.session_id, state.round, carryover)
                await _append_session_topic(r, state.session_id, state.topic_category)

        if result.get("question"):
            # state 不是 None 代表回合還在繼續（open_followup / ask_supplement_w），
            # 這一題是本回合的新問題，question_number 往下一號並存進 round_exchanges；
            # state 是 None 代表 end_round/end_session，問題本身留給下一回合開場或
            # /session/{id}/closing 處理，這裡只負責播音檔。
            next_qn = state.question_number + 1
            # 第二回合（自由追問）全程不合成語音，STT 仍照常。end_session 的
            # thanks_text/question 是 build_closing_invitation 補上的心得環節
            # 收尾語＋開場問題（見上面 action=="end_session" 分支），這一段
            # 全部不需要即時TTS，音檔走前端內建預錄檔（thanks_audio_keys／
            # question_audio_keys），這裡只當畫面上的文字。
            if state.round != 2 and result.get("action") != "end_session":
                tts = request.app.state.tts_service
                if result.get("scene_text"):
                    scene_audio_path, scene_audio_key = await _synthesize_or_key(
                        tts,
                        result["scene_text"],
                        session_id=state.session_id,
                        round_number=state.round,
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
                        session_id=state.session_id,
                        round_number=state.round,
                        turn_number=next_qn if result.get("state") is not None else None,
                    )
                result["question_audio_path"] = question_audio_path
                result["question_audio_key"] = question_audio_key
                result["audio_path"] = question_audio_path  # 向下相容
            await _update_live_view(
                request.app.state.redis, state.session_id,
                # scene_text／thanks_text 互斥，兩者都要拼上去（thanks_text 只在
                # end_session 才有值，見上面 build_closing_invitation 分支）。
                current_scene=result.get("scene_text", "") + result.get("thanks_text", "") + result["question"],
                ai_suggestions=[result["question"]],
                # 同 session_start 的說明：這裡涵蓋回合內追問（Q2/Q3）跟心得
                # 環節開場邀請語，只要是換題就要清掉上一題殘留的反應時間。
                response_time="--",
            )
            if result.get("state") is not None:
                result["state"]["question_number"] = next_qn
                result["state"]["question_asked_at"] = int(time.time() * 1000)
                last_type = (result.get("state") or {}).get("last_question_type", "")
                await _save_round_exchange(
                    db, state.session_id, state.round,
                    question_number=next_qn, question=result["question"],
                    patient_id=_to_int(state.user_id), therapist_id=therapist_id,
                    stage="pre_image" if last_type.startswith("pre_image") else None,
                )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"處理回應失敗: {str(e)}")


@router.post("/respond")
async def session_respond(
    request: Request,
    body: RespondRequest,
    therapist_id: int = Depends(get_current_therapist_id),
    db: AsyncSession = Depends(get_db),
):
    """
    薄封裝，供兩種呼叫端使用：
    1. 不走治療師審核流程的直接送出（Unity 端在 STT 沒辨識到文字、或
       /review_request 重試用盡退回舊流程時直接呼叫）。
    2. 長者按下「送出故事」、真正觸發生成（2026-09-06 改版：治療師確認
       只推播確認後的文字，不在確認當下就先生成，見 session_confirm_
       response 說明；長者按送出時 Unity 才呼叫這支，等同直接送出流程）。

    如果這個 session 還留著 pending_review（代表 body.elder_response 是
    剛經過治療師審核確認的文字），用完就刪掉，避免下一句話誤用到這次的
    舊狀態。找不到 pending_review 就是原本的直接送出情境，行為不變。
    （反應時間不在這裡算，見 _finalize_elder_response 說明——這裡曾經用
    pending_review 存的 received_at 當反應時間起算點，2026-09-06 改版後
    反應時間改由 Unity 端量、sensor.py 直接累加，這個時間戳記不再需要。）

    這裡同時是「這一題真正結束、治療師網頁不用再讓治療師修改」的訊號
    （review_status 清空成 ""，見 session_confirm_response 的
    awaiting_round_submit 說明）——長者真的按下送出之前，pending_review
    都還在，治療師網頁的編輯框要保持開著；長者按下送出、這支被呼叫、
    pending_review 被消耗掉的這一刻，才是真的不能再改了。
    """
    r = request.app.state.redis
    raw = await r.get(f"session:{body.state.session_id}:pending_review")
    if raw:
        await r.delete(f"session:{body.state.session_id}:pending_review")
        await _update_live_view(r, body.state.session_id, review_status="")
    return await _finalize_elder_response(
        request, body.elder_response, body.state, therapist_id, db,
    )


class ReviewRequestPayload(BaseModel):
    elder_response: str
    state: SessionState


@router.post("/{session_id}/review_request", summary="Unity STT辨識完成，送治療師平板審核（取代直接讓長者送出）")
async def session_review_request(
    request: Request,
    session_id: str,
    body: ReviewRequestPayload,
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    長者這一題的 STT 最終結果先暫存在 Redis，不落地資料庫、不驅動 orchestrator——
    要等治療師在 /confirm_response 確認（可能編輯過）、長者真的按下送出後，
    Unity 呼叫 /session/respond 才真的處理（見 session_confirm_response、
    session_respond 說明）。state 一併存進來，因為 /session/respond 需要
    完整的回合狀態，而後端本來就不持有這份狀態（一直是 Unity 端在往返傳遞）。
    """
    r = request.app.state.redis
    payload = {
        "elder_response": body.elder_response,
        "state": body.state.model_dump(),
    }
    await r.set(
        f"session:{session_id}:pending_review",
        json.dumps(payload, ensure_ascii=False),
        ex=1800,
    )
    await _update_live_view(
        r, session_id,
        elder_response_draft=body.elder_response,
        review_status="pending_round",
    )
    return {"ok": True}


@router.post("/{session_id}/confirm_response", summary="治療師確認（可能編輯過）長者這一題的回答，推播回 Unity，等長者按送出才觸發生成")
async def session_confirm_response(
    request: Request,
    session_id: str,
    body: ConfirmResponsePayload,
    therapist_id: int = Depends(get_current_therapist_id),
    db: AsyncSession = Depends(get_db),
):
    """
    2026-09-06 改版：這裡不再直接呼叫 _finalize_elder_response（LLM 分類／
    RAG／生圖那些耗時流程，一次要 20~30 秒）。原本設計是治療師一確認就先
    把這些都做完、把完整結果推播給 Unity，讓長者按下「送出故事」時感覺是
    瞬間完成——但代價是治療師確認後，只要長者還沒按送出、療程就被結束
    （或長者中途離開/忘記按），這些已經算好的結果（包含真的花 API 成本
    生成的圖片）就整份被丟棄，白白浪費運算與生圖成本（稽核：同一天測試
    就踩到兩次，圖片明明生成成功，卻因為長者沒按送出、療程被結束而完全
    沒被用到）。

    改成這裡只把確認後的文字推播給 Unity，長者真的按下「送出故事」時
    才會呼叫 /session/respond 觸發生成（見 GameController.cs 的
    OnSubmit／SendResponse）——生成一定會被用到，代價是長者按下送出後
    要重新等一次生成時間，兩種設計互有取捨，這次選擇「不做白工」優先。

    pending_review 這裡故意不刪除、只更新 elder_response 欄位為確認後的
    文字：received_at／state 都要留給長者真的送出時的 /session/respond
    用（見該端點說明），反應時間才能算到長者當初開口回答的那一刻，不是
    治療師審核或長者按送出的時間——這也代表治療師可以在長者按送出前
    重複呼叫這支修改文字，pending_review 還在就會直接覆蓋成最新版本，
    不會有任何生成流程已經跑掉、改了也沒用的問題。

    review_status 故意不清空成 ""，改成 "awaiting_round_submit"：清空
    代表「這一題完全結束」，會讓治療師網頁把編輯框收掉、切成唯讀顯示
    （見 LiveSessionView.tsx），但長者這時候其實還沒按送出，真正的生成
    流程根本還沒開始——治療師如果這時候發現要修改，應該還要能重新叫出
    編輯框再送一次，不是被鎖死看唯讀文字。真正的「完全結束」訊號延後到
    /session/respond 確認長者已經送出、pending_review 被消耗掉的那一刻
    才發出（見該端點）。命名跟心得回合的 "awaiting_closing_submit" 分開
    （不共用同一個值），治療師網頁要靠這個值判斷重新確認時該打哪一支
    API，兩種回合共用同一個值的話，重新編輯時會分不出來（見
    LiveSessionView.tsx isClosing 判斷）。
    """
    r = request.app.state.redis
    raw = await r.get(f"session:{session_id}:pending_review")
    if not raw:
        raise HTTPException(status_code=409, detail="沒有待確認的長者回應")
    pending = json.loads(raw)
    pending["elder_response"] = body.elder_response
    await r.set(
        f"session:{session_id}:pending_review",
        json.dumps(pending, ensure_ascii=False),
        ex=1800,
    )
    await _update_live_view(
        r, session_id, elder_response=body.elder_response, review_status="awaiting_round_submit",
    )
    delivered = await ws_registry.send_message(session_id, {
        "type": "confirmed_text",
        "elder_response": body.elder_response,
    })
    # 上面 _update_live_view 已經寫進去了，長者端 Unity 之後 polling /metrics
    # 還是撈得到這次確認結果（見 GameController.cs PollForConfirmedText），
    # 這裡回 503 單純是讓治療師網頁知道「WS 沒送達」要不要重試，不是說這次
    # 確認整個失敗、需要重新整套再做一次。
    if not delivered:
        raise HTTPException(status_code=503, detail="長者端連線中斷，可能還沒收到，請重試")
    return {"ok": True}