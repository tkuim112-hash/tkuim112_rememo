import json
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, field_validator

router = APIRouter(prefix="/sensor", tags=["sensor"])

# ════════════ 判斷閾值（臨床意義見下方說明）════════════════════════════
HEAD_DROP_MIN      = 0.12   # m：頭部低於 SpineShoulder 的門檻（低頭/疲勞）
LEAN_FWD_MIN       = 0.05   # m：SpineBase.z − SpineMid.z 前傾門檻（投入）
SHOULDER_RAISE_MIN = 0.04   # m：聳肩門檻（焦慮緊張）
SWAY_AGITATION_MIN  = 0.025  # m：SpineBase 晃動標準差門檻（焦躁動作）
AUDIO_SPEECH_MIN    = 0.015  # RMS：說話音量門檻
EMA_ALPHA           = 0.25   # EMA 平滑係數（~8 次 × 2s = 16s 收斂）
# B 階段擴充閾值（Proxemics / 音高 / 手部速度）
SPINEBASE_LEAVING_Z = 3.0    # m：SpineBase Z 超過此深度視為移離遊戲區域
PITCH_VAR_EXCITED   = 50.0   # Hz²：音高變異閾值（焦躁/亢奮）
HANDTIP_ACTIVE_MIN  = 0.05   # m/s：手部主動互動速度閾值

_EMOTION_LABEL: dict[str, str] = {
    "happy":   "適當",
    "excited": "亢奮",
    "angry":   "焦躁",
    "sad":     "低落",
}


# ════════════ Schema ══════════════════════════════════════════════════

class SensorPayload(BaseModel):
    session_id: str
    # 臉部特徵（Kinect DetectionResult 字串：Yes / No / Maybe / Unknown）
    face_happy:            str = "Unknown"
    face_looking_away:     str = "Unknown"
    face_mouth_moved:      str = "Unknown"
    face_mouth_open:       str = "Unknown"
    face_left_eye_closed:  str = "Unknown"
    face_right_eye_closed: str = "Unknown"
    # 骨架量測（公尺；Unity 未追蹤時送 -999）
    skel_head_drop:      float | None = None   # head.y − spineShoulder.y
    skel_lean_forward:   float | None = None   # spineBase.z − spineMid.z
    skel_shoulder_raise: float | None = None   # avgShoulder.y − spineShoulder.y
    skel_spinebase_z:    float | None = None   # SpineBase 深度（Proxemics 離席偵測，B 階段）
    # 彙整訊號
    body_sway: float = 0.0   # SpineBase 位置標準差（公尺）
    audio_rms: float = 0.0   # 麥克風 RMS
    # B 階段擴充欄位（Unity 尚未送出時保持預設值，不影響現有評分）
    audio_pitch_variance:  float = 0.0   # 音高變異 Hz²（eGeMAPS 特徵）
    skel_handtip_velocity: float = 0.0   # 手部末梢速度 m/s（HandTip 動作量）
    # 其他
    response_time_ms: int         = -1    # -1 = 本次不更新 Redis 反應時間
    timestamp:        float | None = None

    @field_validator(
        "skel_head_drop", "skel_lean_forward", "skel_shoulder_raise", "skel_spinebase_z",
        mode="before",
    )
    @classmethod
    def sentinel_to_none(cls, v):
        """Unity 以 -999 表示關節未追蹤，後端轉為 None 跳過計算。"""
        if isinstance(v, (int, float)) and v < -500:
            return None
        return v


# ════════════ 訊號計算函式 ════════════════════════════════════════════

def _face_engagement(p: SensorPayload) -> float:
    """
    臉部注意力分數 [−3, +2]。
    視線離開螢幕是 MCI 長者疲勞或混亂的強烈訊號（比表情更可靠）。
    """
    score = 0.0
    if   p.face_looking_away == "Yes":   score -= 2.0
    elif p.face_looking_away == "Maybe":  score -= 1.0
    if p.face_left_eye_closed == "Yes" and p.face_right_eye_closed == "Yes":
        score -= 2.0  # 雙眼閉合 = 打瞌睡
    if p.face_mouth_moved == "Yes":
        score += 1.0  # 說話中 = 有參與
    return max(-3.0, min(2.0, score))


def _face_happiness(p: SensorPayload) -> float:
    """
    臉部情緒效價 [−2, +3]。
    MCI 長者面部肌肉活動較弱，Maybe 在正向情緒時很常見，給予正面加分。
    """
    if p.face_happy == "Yes":   return  3.0
    if p.face_happy == "Maybe": return  1.0
    if p.face_happy == "No":    return -2.0
    return 0.0  # Unknown


def _skel_engagement(p: SensorPayload) -> float:
    """
    骨架參與度 [−2, +2]。
    低頭（疲勞/低落）和前傾（投入/興趣）是比臉部更穩定的老年人行為指標。
    """
    score, count = 0.0, 0

    if p.skel_head_drop is not None:
        if p.skel_head_drop < HEAD_DROP_MIN:
            ratio  = min(1.0, (HEAD_DROP_MIN - p.skel_head_drop) / HEAD_DROP_MIN)
            score -= ratio * 2.0  # 低頭程度越深扣分越多
        else:
            score += 0.5          # 頭部正常高度 = 小加分
        count += 1

    if p.skel_lean_forward is not None:
        if p.skel_lean_forward > LEAN_FWD_MIN:
            score += min(p.skel_lean_forward / LEAN_FWD_MIN, 2.0)  # 前傾 = 投入
        elif p.skel_lean_forward < -0.03:
            score -= 0.5  # 後仰 = 輕微退縮
        count += 1

    return max(-2.0, min(2.0, score / count)) if count else 0.0


def _skel_tension(p: SensorPayload) -> float:
    """
    骨架緊張度 [0, +2]。
    聳肩是焦慮的典型姿勢，對老年人尤其明顯。
    此分數作為情緒效價的負向修正項。
    """
    if p.skel_shoulder_raise is None or p.skel_shoulder_raise <= SHOULDER_RAISE_MIN:
        return 0.0
    return min(p.skel_shoulder_raise / SHOULDER_RAISE_MIN, 2.0)


def _classify_from_scores(engagement: float, happiness: float, agitation: float) -> str:
    """
    依三個 EMA 平滑分數決定四分類。

    低落  — 整體不參與（視線離開、低頭、無表情、沉默、身體靜止）
    焦躁  — 有參與但情緒負向，加上身體不安靜（焦慮性動作）
    亢奮  — 正向情緒，加上積極肢體表達或高投入度
    適當  — 穩定參與、情緒中性偏正向
    """
    if engagement + happiness < -2.0:
        return "sad"
    if happiness < -0.5 and agitation > 0.4:
        return "angry"
    if happiness > 1.5 and (agitation > 0.3 or engagement > 1.0):
        return "excited"
    return "happy"


# ════════════ 校正基準載入 ════════════════════════════════════════════

async def _load_calibration(r, session_id: str) -> dict | None:
    raw = await r.get(f"session:{session_id}:calibration")
    return json.loads(raw) if raw else None


def _pitch_threshold(calib: dict | None) -> float:
    """個人化音高變異門檻（有效校正基準時為 baseline × 2，否則退回固定值）。"""
    if calib:
        baseline = calib.get("pitchVarianceBaseline", 0.0)
        if baseline > 1.0:
            return max(PITCH_VAR_EXCITED, baseline * 2.0)
    return PITCH_VAR_EXCITED


# ════════════ EMA 平滑（狀態存 Redis）════════════════════════════════

async def _ema_classify(r, session_id: str, p: SensorPayload, calib: dict | None = None) -> str:
    """
    從 Redis 讀取上一次的 EMA 分數 → 計算本次原始分數 →
    套用個人校正基準（若有）→ EMA 更新 → 寫回 Redis → 分類。

    EMA 平滑的必要性：MCI 長者訊號不穩定（偶發性視線離開、臉部動作弱），
    單點分類雜訊大，需要時間平滑才能反映真實狀態。
    """
    ema_key = f"session:{session_id}:ema"
    ema = await r.hgetall(ema_key)

    prev_eng = float(ema.get("engagement", 0))
    prev_hap = float(ema.get("happiness",  0))
    prev_agi = float(ema.get("agitation",  0))

    # 計算本次原始三維分數
    audio_eng = 1.0 if p.audio_rms > AUDIO_SPEECH_MIN else 0.0
    raw_eng = (
        _face_engagement(p) * 0.30 +   # 臉部注意力（老年人較弱，權重 30%）
        _skel_engagement(p) * 0.50 +   # 骨架姿勢（更可靠，權重 50%）
        audio_eng            * 0.20    # 音量（說話 = 有參與，權重 20%）
    )
    raw_hap = _face_happiness(p) - _skel_tension(p)  # 正向表情 − 聳肩緊張
    # B 階段：音高變異混入焦躁計算（以個人校正基準 ×2 作為門檻，Paper 2 §4.1.1）
    pitch_agi = min(1.0, p.audio_pitch_variance / max(_pitch_threshold(calib), 1e-6))
    raw_agi   = (
        max(0.0, min(1.0, p.body_sway / max(SWAY_AGITATION_MIN, 1e-6) - 1.0)) * 0.70
        + pitch_agi * 0.30
    )

    # 套用個人校正基準（補償天生習慣，避免誤判）
    if calib:
        # 若長者校正時視線自然偏移，降低 looking_away 懲罰
        if p.face_looking_away in ("Yes", "Maybe"):
            raw_eng += calib.get("lookingAwayBaseline", 0.0) * 2.0
        # 校正期間的快樂基準反映靜止表情，從當前分數扣除避免虛高
        raw_hap -= calib.get("happyBaseline", 0.0) * 1.5

    # EMA 更新
    new_eng = prev_eng + EMA_ALPHA * (raw_eng - prev_eng)
    new_hap = prev_hap + EMA_ALPHA * (raw_hap - prev_hap)
    new_agi = prev_agi + EMA_ALPHA * (raw_agi - prev_agi)

    await r.hset(ema_key, mapping={
        "engagement": str(new_eng),
        "happiness":  str(new_hap),
        "agitation":  str(new_agi),
    })
    await r.expire(ema_key, 86400)

    return _classify_from_scores(new_eng, new_hap, new_agi)


# ════════════ Session 統計累積 ════════════════════════════════════════

async def _update_session_stats(r, session_id: str, p: SensorPayload, emotion_raw: str, calib: dict | None = None):
    """
    每收到一個 sensor frame 就累積統計至 session:{id}:stats。
    供療程結束後計算五指標評估表使用。
    """
    key = f"session:{session_id}:stats"
    await r.hsetnx(key, "session_start", str(p.timestamp or time.time()))

    pipe = r.pipeline(transaction=False)
    pipe.hincrby(key, "frame_count",    1)
    pipe.hincrby(key, f"emo_{emotion_raw}", 1)

    if p.face_looking_away in ("Yes", "Maybe"):
        pipe.hincrby(key, "looking_away_n", 1)
    if p.face_left_eye_closed == "Yes" and p.face_right_eye_closed == "Yes":
        pipe.hincrby(key, "eye_closed_n", 1)
    if p.face_mouth_moved == "Yes":
        pipe.hincrby(key, "mouth_moved_n", 1)
    if p.body_sway > SWAY_AGITATION_MIN:
        pipe.hincrby(key, "high_sway_n", 1)
    # 三個骨架欄位同時 None → Unity 未追蹤到身體，長者可能離開座位
    skel_absent = (
        p.skel_head_drop is None
        and p.skel_lean_forward is None
        and p.skel_shoulder_raise is None
    )
    if skel_absent:
        pipe.hincrby(key, "skel_absent_n", 1)

    # A 階段：靜默（長者在場但無語音，排除骨架消失狀態）
    if p.audio_rms < AUDIO_SPEECH_MIN and not skel_absent:
        pipe.hincrby(key, "silence_n", 1)

    # B 階段：SpineBase 深度（長者後退離開遊戲區域）
    if p.skel_spinebase_z is not None and p.skel_spinebase_z > SPINEBASE_LEAVING_Z:
        pipe.hincrby(key, "far_n", 1)

    # B 階段：音高變異（焦躁/亢奮的聲學特徵，以個人基準 ×2 作為門檻）
    if p.audio_pitch_variance > _pitch_threshold(calib):
        pipe.hincrby(key, "high_pitch_var_n", 1)

    # B 階段：手部主動動作（遊戲互動肢體指標）
    if p.skel_handtip_velocity > HANDTIP_ACTIVE_MIN:
        pipe.hincrby(key, "hand_active_n", 1)

    # A 階段：反應延遲累計（計算平均反應時間）
    if p.response_time_ms >= 0:
        pipe.hincrby(key, "response_time_sum",   p.response_time_ms)
        pipe.hincrby(key, "response_time_count", 1)

    await pipe.execute()
    await r.expire(key, 86400)


# ════════════ 端點 ════════════════════════════════════════════════════

@router.post("/emotion", summary="接收 Kinect 原始感測資料，後端分類後存 Redis")
async def receive_sensor(request: Request, body: SensorPayload):
    r = request.app.state.redis
    calib = await _load_calibration(r, body.session_id)

    emotion_raw   = await _ema_classify(r, body.session_id, body, calib)
    await _update_session_stats(r, body.session_id, body, emotion_raw, calib)
    emotion_label = _EMOTION_LABEL.get(emotion_raw, "適當")
    ts            = body.timestamp or time.time()

    updates: dict[str, str] = {
        "emotion":     emotion_label,
        "emotion_raw": emotion_raw,
        "updated_at":  str(ts),
    }
    if body.response_time_ms >= 0:
        updates["response_time"] = f"{round(body.response_time_ms / 1000)}s"

    key = f"session:{body.session_id}:metrics"
    await r.hset(key, mapping=updates)
    await r.expire(key, 86400)

    # latest:emotion TTL 5s — 超過未更新代表 Unity 端斷線
    await r.set(f"session:{body.session_id}:latest:emotion", emotion_label, ex=5)

    return {"ok": True, "emotion": emotion_label}
