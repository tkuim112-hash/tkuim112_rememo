import json
import time

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, field_validator

from auth import get_current_therapist_id

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
PITCH_VAR_EXCITED   = 50.0   # Hz²：音高變異閾值（焦躁/亢奮，無有效個人校正基準時的退回值）
PITCH_VAR_STD_K     = 2.0    # 個人化門檻＝baseline + k×標準差 的 k，見 _pitch_threshold
HANDTIP_ACTIVE_MIN  = 0.05   # m/s：手部主動互動速度閾值
AUDIO_RMS_STD_K     = 4.0    # 個人化語音門檻＝底噪 baseline + k×標準差 的 k，見 _audio_threshold
AUDIO_RMS_THRESHOLD_MIN = 0.006  # RMS：個人化門檻的下限，避免底噪本身的量測雜訊被誤判成語音

# C 階段：身體收縮姿勢（草稿，未經真實資料校準）。文獻上收縮/封閉姿勢
# （手臂收緊貼近軀幹）跟「低激動+負向情緒」明確相關，比展開姿勢的證據
# 一致（展開姿勢跟正向/激動的關係是 mixed，沒有採用），見專案文獻查證。
#
# 手肘外展距離跟身高/肩寬/手臂長度高度相關，固定一個絕對值對所有體型都
# 不公平（大個子放鬆坐姿本來就比較外展，可能永遠測不到「收縮」；小個子
# 則可能被誤判成經常收縮）。查過 Kinect 人體測量學文獻後，這裡採用的正規化
# 方式是「用肩寬當比例尺」（T-pose 校正量身體尺寸、距離換算成不受體型影響
# 的比例，是這個測量領域本身的慣例，不是跨領域借來的統計方法），不是拿
# 「這個人平常姿勢的統計量」當基準——不需要額外要求 T-pose 動作，
# ShoulderLeft/ShoulderRight 本來就在 15 秒校正期間被記錄（原本是給游標
# 映射用的，見 KinectCalibrationManager.cs 的 _shoulderLXBuffer/
# _shoulderRXBuffer），見 _shoulder_width_baseline。
ELBOW_FLARE_CONSTRICTED_MAX    = 0.15   # m：手肘離脊椎中心線橫向距離，小於此值視為收縮（無校正基準時）
ELBOW_FLARE_SHOULDER_RATIO_MAX = 0.5    # 有肩寬基準時：現在距離 / 肩寬 < 此比例，視為收縮

# 臉部訊號改用 py-feat（face-service）分析 Kinect 彩色畫面得到的 FACS AU 強度，
# 取代 Kinect 內建 Face API 只有 8 個粗糙布林屬性的做法（詳見專案記憶
# project_openface_kinect_emotion_redesign）。這裡的門檻是初版映射：
AU_PRESENT_MIN = 0.5   # AU 強度判定為「有出現」的門檻（py-feat 輸出範圍依模型版本而定）
YAW_AWAY_MAYBE = 15.0   # 度：頭部偏轉角度，視線「可能」離開畫面
YAW_AWAY_YES   = 25.0   # 度：頭部偏轉角度，視線「確定」離開畫面

# 負向表情 AU 集合：悲傷型（AU01 內眉上揚+AU04 皺眉+AU15 嘴角下垂）跟憤怒型
# （AU04 皺眉+AU05 上眼瞼提起+AU07 眼瞼收緊+AU23 抿嘴）合併判斷「有沒有負向
# 表情」，不在這裡分兩種——是哪一種負向（低落還是焦躁）交給 arousal（骨架晃動
# +音高變異）去分，這是換成 py-feat 之後才做得到的涵蓋範圍，Kinect 內建 Face
# API 完全沒有這幾個 AU。
AU_VALENCE_NEGATIVE = ("AU01", "AU04", "AU05", "AU07", "AU15", "AU23")

# 正向表情 AU 集合：AU06（臉頰上提）+ AU12（嘴角上揚）決定真笑/社交笑的區分
# （見 _au_duchenne_smile/_au_social_smile）。跟 AU_VALENCE_NEGATIVE 合併起來
# 是校正期間要逐一收集個人基準的完整 AU 集合（見 AU_CALIBRATED_CODES、
# _au_baseline）。
AU_VALENCE_POSITIVE = ("AU06", "AU12")
AU_CALIBRATED_CODES = AU_VALENCE_POSITIVE + AU_VALENCE_NEGATIVE

# Circumplex Model（Russell 1980）的低激動門檻：agitation 低於此值視為安靜/
# 低能量狀態，這時 valence（happiness）中性、engagement 也偏低，光看
# valence×arousal 分不出是「安穩參與」還是「放空退縮」，見 _classify_from_scores。
AROUSAL_LOW_MAX          = 0.35
ENGAGEMENT_WITHDRAWN_MAX = -0.5

# 三維分數正規化範圍（供治療師端顯示 0-100% 量表用）。範圍是把
# _face_engagement/_skel_engagement/audio_eng 等各子分數的理論上下界依
# _ema_classify 的加權公式加總得出，不是憑感覺訂的：
#   engagement = face_engagement[-3,2]*0.30 + skel_engagement[-2,2]*0.50 + audio_eng{0,1}*0.20
#   happiness  = face_happiness[-2,3]
#   agitation  = 0.60*sway_ratio[0,1] + 0.25*pitch_ratio[0,1] + 0.15*tension_ratio[0,1]
#
# 聳肩（_skel_tension）2026-09-05 定案放進 agitation（肢體與語調）的加權項，
# 不是 happiness（表情訊號）的修正項——查證過的心理生理學文獻：斜方肌
# （trapezius）肌電活動是被學界認可的壓力/激動程度量測方式（跟心跳、膚電
# 反應同一類），聳肩本質上是 arousal 的訊號，不是 valence 的訊號；姿勢研究
# 也指出聳肩對應焦慮/戰逃反應（高激動+負向），跟低落的「垂肩」姿勢方向相反，
# 放進表情訊號當「情緒是不是不好」的通用負向修正項並不精確。
#
# 已知風險（權重刻意壓低到 0.15 的原因）：這個系統操作介面要求長者舉手控制
# 游標、停留在按鈕上觸發（見 HandCursorRemapper.cs），維持舉手動作本身就會
# 讓 Kinect 追蹤到的肩關節位置抬高——這是姿態辨識文獻裡有名字的問題
# （"motion artifact" / "irrelevant gestures"：操作手勢造成的無關動作雜訊），
# 跟「正在操作系統」直接掛鉤、幾乎每場都會發生，不像喬姿勢那種偶發雜訊。
# 目前只用低權重緩解，沒有真的濾掉這個污染（例如判斷當下是不是正在舉手
# 操作、暫停採計），之後如果要做更根本的修正要往這個方向想。
ENGAGEMENT_RANGE = (-1.9, 1.8)
HAPPINESS_RANGE  = (-2.0, 3.0)
AGITATION_RANGE  = (0.0, 1.0)

_EMOTION_LABEL: dict[str, str] = {
    "happy":   "適當",
    "excited": "亢奮",
    "angry":   "焦躁",
    "sad":     "低落",
}


# ════════════ Schema ══════════════════════════════════════════════════

class SensorPayload(BaseModel):
    session_id: str
    # 臉部訊號不再由 Unity 送 Kinect Face API 的布林值，改由 Unity 另外送一張
    # JPEG 畫面（multipart 的 frame 欄位），後端呼叫 face-service（py-feat）
    # 分析出 AU 強度，見本檔案下方 receive_sensor / _face_engagement / _face_happiness。
    # 骨架量測（公尺；Unity 未追蹤時送 -999）
    skel_head_drop:      float | None = None   # head.y − spineShoulder.y
    skel_lean_forward:   float | None = None   # spineBase.z − spineMid.z
    skel_shoulder_raise: float | None = None   # avgShoulder.y − spineShoulder.y
    skel_spinebase_z:    float | None = None   # SpineBase 深度（Proxemics 離席偵測，B 階段）
    # 手肘離身體中心線的橫向距離（C 階段，身體收縮姿勢偵測）。分左右兩隻手
    # 各自送，不是後端自己拆——因為長者操作介面（HandCursorRemapper.cs）
    # 只用單手舉手控制游標，另一隻手才是判斷收縮姿勢時真正可信的那一側，
    # 兩隻手分開送才能讓後端用 min() 挑「沒有在操作介面」的那一側，見
    # _is_body_constricted 的說明。
    skel_elbow_flare_left:  float | None = None   # |elbowLeft.x − spineMid.x|
    skel_elbow_flare_right: float | None = None   # |elbowRight.x − spineMid.x|
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
        "skel_elbow_flare_left", "skel_elbow_flare_right",
        mode="before",
    )
    @classmethod
    def sentinel_to_none(cls, v):
        """Unity 以 -999 表示關節未追蹤，後端轉為 None 跳過計算。"""
        if isinstance(v, (int, float)) and v < -500:
            return None
        return v


# ════════════ 臉部 AU 判斷輔助函式 ══════════════════════════════════════
# 這幾個函式是「顯示的依據」（_reasoning_signals）跟「真的拿去分類的依據」
# （_face_engagement/_face_happiness）共用的同一套判斷邏輯，不要各自重寫一份
# 相似但不同步的門檻——這是延續 Kinect 版本原本就有的設計原則。

def _au(au: dict, code: str) -> float:
    return au.get(code, 0.0)


def _au_baseline(calib: dict | None, code: str) -> float:
    """
    個人 AU 強度基準（校正時 15 秒收集的平均值）。用 key/value 平行陣列格式
    （auBaselineCodes/auBaselineValues）而不是 dict，跟 _shoulder_width_baseline
    的 jointKeys/jointX 是同一種繞法——JsonUtility（Unity 端）不支援直接
    序列化 Dictionary。找不到、或校正資料不存在時回傳 0.0（沒有基準可扣，
    等同沒校正過的舊行為）。

    每個 AU 各自存一份基準，不是像舊版 happyBaseline/frownBaseline 那樣整包
    混成一個數字：老年人常見的皮膚鬆弛/法令紋通常只讓特定 AU（例如 AU04）
    天生偏高，不代表其他 AU 也偏高，混在一起平均會連帶稀釋/污染其他 AU
    真正的訊號。
    """
    if not calib:
        return 0.0
    codes = calib.get("auBaselineCodes")
    vals = calib.get("auBaselineValues")
    if not codes or not vals or len(codes) != len(vals):
        return 0.0
    return dict(zip(codes, vals)).get(code, 0.0)


def _au_c(au: dict, code: str, calib: dict | None) -> float:
    """扣掉個人基準後的 AU 強度。基準代表「這個人靜止時這個 AU 本來就有多強」，
    量到比基準還低不代表額外的負向證據，夾在 0 下限，不會反過來加分。"""
    return max(0.0, _au(au, code) - _au_baseline(calib, code))


def _au_graded(au: dict, code: str, calib: dict | None) -> float:
    """
    AU 強度換算成 [0,1] 的漸進分數，取代單純的「有沒有過門檻」二元判斷——
    剛好卡在 AU_PRESENT_MIN 邊緣、跟大幅超過門檻，證據力理應不同，不該給
    同樣的分數（查證 PSPI 疼痛強度公式等臉部強度量測文獻後採用同一套精神：
    AU 強度是連續量，用漸進計分比二元門檻更準確反映證據強弱）。

    沒有另外定義一個新的「飽和點」常數：py-feat 輸出的實際數值範圍依模型
    版本而定（見 AU_PRESENT_MIN 定義處說明），沒有實測依據就硬訂一個上限
    不夠嚴謹，改用 2×AU_PRESENT_MIN 當滿分點——這個常數本身已經是校準過的
    「有出現」基準，往上抓一倍當「明顯出現」，是目前唯一有實際依據的錨點。
    """
    v = _au_c(au, code, calib)
    if v <= AU_PRESENT_MIN:
        return 0.0
    return min(1.0, (v - AU_PRESENT_MIN) / AU_PRESENT_MIN)


def _au_duchenne_smile(au: dict, calib: dict | None = None) -> bool:
    """AU06（臉頰上提）+ AU12（嘴角上揚）同時出現＝真笑（Duchenne marker）。
    Kinect 內建 Face API 沒有 AU06，判斷不出這個區別，是換成 py-feat 的
    主要理由之一。calib 有給的話，門檻判斷用扣過個人基準的強度（_au_c），
    不是原始強度——校正資料還沒收集完成（例如校正端點 face_calibration_sample
    自己取樣時）calib 為 None，退回原始強度判斷，等同沒校正過的行為。"""
    return _au_c(au, "AU06", calib) >= AU_PRESENT_MIN and _au_c(au, "AU12", calib) >= AU_PRESENT_MIN


def _au_social_smile(au: dict, calib: dict | None = None) -> bool:
    """只有 AU12、沒有 AU06：社交性微笑，正向程度給得比真笑低。"""
    return _au_c(au, "AU12", calib) >= AU_PRESENT_MIN and not _au_duchenne_smile(au, calib)


def _au_frown(au: dict, calib: dict | None = None) -> bool:
    """AU_VALENCE_NEGATIVE 裡任一個 AU（扣過個人基準後）明顯出現、且沒有
    微笑訊號＝負向表情（悲傷型或憤怒型皺眉都算，兩者的區分交給 arousal，
    見 _classify_from_scores）。"""
    negative = any(_au_c(au, code, calib) >= AU_PRESENT_MIN for code in AU_VALENCE_NEGATIVE)
    return negative and _au_c(au, "AU12", calib) < AU_PRESENT_MIN


def _au_mouth_active(au: dict) -> bool:
    """AU25（嘴唇分開）或 AU26（下顎張開）＝嘴巴有動作，對應舊版 MouthMoved。"""
    return _au(au, "AU25") >= AU_PRESENT_MIN or _au(au, "AU26") >= AU_PRESENT_MIN


def _au_eyes_closed(au: dict) -> bool:
    """AU43（眼睛閉合）明顯＝打瞌睡/閉眼，對應舊版雙眼閉合。"""
    return _au(au, "AU43") >= AU_PRESENT_MIN


def _looking_away_level(pose: dict) -> str:
    """依頭部 Yaw 角度判斷視線是否偏離畫面，對應舊版 Kinect LookingAway。
    沒有 pose 資料（face-service 沒偵測到臉/沒給頭部姿態）時視為沒偏離——
    沿用舊版 Unknown 不扣分的保守處理，避免「沒資料」被誤判成「有負面訊號」。"""
    yaw = abs(pose.get("Yaw", 0.0))
    if yaw >= YAW_AWAY_YES:
        return "yes"
    if yaw >= YAW_AWAY_MAYBE:
        return "maybe"
    return "no"


# ════════════ 訊號計算函式 ════════════════════════════════════════════

def _face_engagement(au: dict, pose: dict) -> float:
    """
    臉部注意力分數 [−3, +2]。
    視線離開螢幕是 MCI 長者疲勞或混亂的強烈訊號（比表情更可靠）。
    """
    score = 0.0
    away = _looking_away_level(pose)
    if away == "yes":
        score -= 2.0
    elif away == "maybe":
        score -= 1.0
    if _au_eyes_closed(au):
        score -= 2.0  # 眼睛閉合 = 打瞌睡
    if _au_mouth_active(au):
        score += 1.0  # 嘴巴有動作 = 有參與
    return max(-3.0, min(2.0, score))


def _face_happiness(au: dict, calib: dict | None = None) -> float:
    """
    臉部情緒效價 [−2, +3]。
    MCI 長者面部肌肉活動較弱，社交性微笑（只有 AU12）也給正面加分，
    只是力度比真笑（AU06+AU12）低。

    分支判斷（哪一種表情）維持不變，但分支內的分數改用 _au_graded 漸進
    計分，不是寫死的常數——同樣過門檻，AU 強度剛好卡邊緣跟大幅超過，
    證據力不該一樣（見 _au_graded 的文獻依據說明）。真笑落在 [1.0,3.0]、
    社交笑落在 [0,1.0]、皺眉落在 [−2.0,0]，範圍分別內縮在舊版固定值以內，
    是既有範圍內的精細化，不會超出 HAPPINESS_RANGE。

    calib 傳給每個分支判斷函式跟 _au_graded，個人校正基準逐一 AU 扣除
    （見 _au_c），取代舊版整包 happyBaseline／frownBaseline 純量套用。
    """
    if _au_duchenne_smile(au, calib):
        intensity = (_au_graded(au, "AU06", calib) + _au_graded(au, "AU12", calib)) / 2.0
        return 1.0 + intensity * 2.0
    if _au_social_smile(au, calib):
        return _au_graded(au, "AU12", calib) * 1.0
    if _au_frown(au, calib):
        intensity = max(_au_graded(au, code, calib) for code in AU_VALENCE_NEGATIVE)
        return -intensity * 2.0
    return 0.0  # 沒有明確訊號（沒偵測到臉、或表情中性）


def _skel_engagement(p: SensorPayload) -> float | None:
    """
    骨架參與度 [−2, +2]。
    低頭（疲勞/低落）和前傾（投入/興趣）是比臉部更穩定的老年人行為指標。

    回傳 None（而不是 0.0）代表兩個關節都完全追丟，真的沒有任何骨架資料可用——
    跟「有資料、算出來的分數剛好是 0（中性）」要能區分開來，呼叫端（_ema_classify）
    才能在真的沒資料時跳過這一幀的 engagement EMA 更新，不要把「追丟」誤當成
    「骨架顯示中性」去平均，稀釋掉其他幀真正偵測到的訊號（跟 happiness 只信
    臉部單一管道時的稀釋問題是同一種，這裡因為 engagement 還有臉部/音量兩個
    來源撐著，影響較小，但邏輯上該一致處理）。
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

    return max(-2.0, min(2.0, score / count)) if count else None


def _skel_tension(p: SensorPayload) -> float:
    """
    骨架緊張度 [0, +2]。
    聳肩是焦慮/激動的典型姿勢，對老年人尤其明顯。這是 arousal（激動程度）
    的訊號，見 _ema_classify 的 raw_agi——斜方肌肌電活動是心理生理學裡
    認可的壓力/激動量測方式，跟身體晃動、音高變異是同一類「激動程度」
    輸入，2026-09-05 起併入 agitation 的加權項，不再是 happiness（表情
    訊號）的負向修正項。

    已知限制：這個系統操作介面要求長者舉手控制游標（HandCursorRemapper.cs
    的 hover 控制），維持舉手動作本身就會讓肩關節追蹤位置抬高——這是姿態
    辨識文獻裡的 "motion artifact"（操作動作造成的訊號污染），跟操作系統
    直接掛鉤、幾乎每場都會發生，不是偶發雜訊。_ema_classify 裡給這個訊號
    的權重刻意壓得比晃動/音高低（0.15），只是緩解，沒有真的濾掉污染。
    """
    if p.skel_shoulder_raise is None or p.skel_shoulder_raise <= SHOULDER_RAISE_MIN:
        return 0.0
    return min(p.skel_shoulder_raise / SHOULDER_RAISE_MIN, 2.0)


def _shoulder_width_baseline(calib: dict | None) -> float | None:
    """
    個人肩寬（校正時測一次，整場療程當固定比例尺用）。

    Kinect 人體測量學文獻的慣例是拿身體本身的尺寸（肩寬/手臂長，通常用
    T-pose 量）當比例尺，把距離類訊號換算成不受體型影響的比例，不是拿
    「這個人平常姿勢的統計量」當基準——這裡採用同一套邏輯，但不需要額外
    要求 T-pose 動作：ShoulderLeft/ShoulderRight 本來就在 15 秒校正期間
    被記錄（原本是給 KinectCalibrationManager.cs 的游標映射用的，見
    _shoulderLXBuffer/_shoulderRXBuffer），jointKeys/jointX 裡已經有這兩個
    關節的平均位置，不需要 Unity 端多送任何新資料。找不到這兩個關節、或
    根本沒有校正資料時回傳 None，呼叫端退回固定門檻
    ELBOW_FLARE_CONSTRICTED_MAX。
    """
    if not calib:
        return None
    keys = calib.get("jointKeys")
    xs = calib.get("jointX")
    if not keys or not xs or len(keys) != len(xs):
        return None
    joint_x = dict(zip(keys, xs))
    left = joint_x.get("ShoulderLeft")
    right = joint_x.get("ShoulderRight")
    if left is None or right is None:
        return None
    return abs(right - left)


def _is_body_constricted(p: SensorPayload, calib: dict | None = None) -> bool:
    """
    身體收縮/封閉姿勢（草稿，C 階段，未經真實資料校準）。

    只用 min(左手肘, 右手肘) 離身體中心線的距離判斷，不取平均——這個系統
    操作介面只用單手舉手控制游標（HandCursorRemapper.cs），操作中的那隻
    手肘會主動外展，距離只會變大不會變小；用 min 挑「離中心線比較近的那
    隻手」，可以保證舉手操作只會讓那隻手肘被踢出局（不再是 min），改看另一
    隻沒在操作、真正可信的手肘，不會被操作動作誤判成「沒有收縮」，也不會
    因為操作動作被誤判成「有收縮」——這跟聳肩訊號的污染方向不同，這裡是
    結構性安全的，不需要像聳肩那樣只能用低權重緩解。

    兩隻手都沒追蹤到（None）時保守回傳 False（不判定為收縮），跟其他骨架
    訊號缺資料時的處理方式一致。

    有個人肩寬基準時（見 _shoulder_width_baseline），改用「現在距離 / 肩寬
    < ELBOW_FLARE_SHOULDER_RATIO_MAX」判斷，而不是固定的絕對值門檻——手肘
    外展距離跟體型（身高/肩寬/手臂長度）高度相關，固定門檻對不同體型的
    長者不公平。肩寬太窄（<0.15m，可能是校正時關節沒追蹤好）時不採信，
    退回固定門檻。
    """
    flares = [f for f in (p.skel_elbow_flare_left, p.skel_elbow_flare_right) if f is not None]
    if not flares:
        return False
    current = min(flares)
    shoulder_width = _shoulder_width_baseline(calib)
    if shoulder_width is not None and shoulder_width > 0.15:
        return (current / shoulder_width) < ELBOW_FLARE_SHOULDER_RATIO_MAX
    return current < ELBOW_FLARE_CONSTRICTED_MAX


def _pct(value: float, lo: float, hi: float) -> int:
    """把一個原始分數線性換算成 0-100%，供治療師端畫量表用。"""
    ratio = (value - lo) / (hi - lo)
    return round(max(0.0, min(1.0, ratio)) * 100)


# 訊號代碼表：(code, category)。category 對應前端的圖示分類
# face／eye／body／speaker，中文文案跟圖示完全交給前端的 emotionSignals.ts，
# 這裡只送代碼字串，避免中英文案兩邊維護。門檻全部沿用上面既有的判斷閾值，
# 不另外新增門檻，確保「顯示的依據」跟「真的拿去分類的依據」是同一套。
_SIGNAL_DEFS: list[tuple[str, str]] = [
    ("face_not_detected", "face"),
    ("face_smile", "face"),
    ("face_smile_slight", "face"),
    ("face_frown", "face"),
    ("face_mouth_moved", "face"),
    ("eye_looking_away", "eye"),
    ("eye_closed_drowsy", "eye"),
    ("body_lean_forward", "body"),
    ("body_lean_back", "body"),
    ("body_head_drop", "body"),
    ("body_shoulder_raise", "body"),
    ("body_constricted", "body"),
    ("body_sway_high", "body"),
    ("body_left_seat", "body"),
    ("speaker_speaking", "speaker"),
    ("speaker_quiet", "speaker"),
    ("speaker_pitch_var_high", "speaker"),
]


def _reasoning_signals(
    p: SensorPayload, au: dict, pose: dict, calib: dict | None, face_detected: bool = True,
) -> list[str]:
    """算出這一幀偵測到的訊號代碼列表，供治療師端顯示「判斷依據」。

    face_detected 特別標示「這幀根本沒有臉部資料」（沒送畫面／face-service
    沒偵測到臉／逾時）跟「有偵測到臉但表情中性」的差別——這兩種情況下 au/pose
    都是空字典，_face_happiness/_face_engagement 算出來的分數也相同（都是
    中性值），但對治療師來說意義完全不同：前者是「這個百分比不可信，沒資料」，
    後者才是「AI 真的判斷長者表情平淡」，不加這個標籤的話兩者無法區分。
    """
    codes: list[str] = []

    if not face_detected:
        codes.append("face_not_detected")
    elif _au_duchenne_smile(au, calib):
        codes.append("face_smile")
    elif _au_social_smile(au, calib):
        codes.append("face_smile_slight")
    elif _au_frown(au, calib):
        codes.append("face_frown")

    if _au_mouth_active(au):
        codes.append("face_mouth_moved")

    if _looking_away_level(pose) in ("yes", "maybe"):
        codes.append("eye_looking_away")
    if _au_eyes_closed(au):
        codes.append("eye_closed_drowsy")

    if p.skel_lean_forward is not None:
        if p.skel_lean_forward > LEAN_FWD_MIN:
            codes.append("body_lean_forward")
        elif p.skel_lean_forward < -0.03:
            codes.append("body_lean_back")
    if p.skel_head_drop is not None and p.skel_head_drop < HEAD_DROP_MIN:
        codes.append("body_head_drop")
    if p.skel_shoulder_raise is not None and p.skel_shoulder_raise > SHOULDER_RAISE_MIN:
        codes.append("body_shoulder_raise")
    if _is_body_constricted(p, calib):
        codes.append("body_constricted")
    if p.body_sway > SWAY_AGITATION_MIN:
        codes.append("body_sway_high")
    # 離座偵測（Proxemics，B階段）：spinebase_z 超過門檻代表長者身體已經移離
    # Kinect 的有效追蹤範圍。這個訊號不像其他訊號那樣直接進三維分數的加權
    # 公式，但對治療師判讀「為什麼專注度這麼低」很關鍵——低專注度可能不是
    # 長者分心，而是根本已經不在座位上，這跟單純「視線游移」是完全不同的
    # 情況，值得單獨標示出來。
    if p.skel_spinebase_z is not None and p.skel_spinebase_z > SPINEBASE_LEAVING_Z:
        codes.append("body_left_seat")

    if p.audio_rms > _audio_threshold(calib):
        codes.append("speaker_speaking")
    else:
        codes.append("speaker_quiet")
    if p.audio_pitch_variance > _pitch_threshold(calib):
        codes.append("speaker_pitch_var_high")

    return codes


def _classify_from_scores(
    engagement: float, happiness: float, agitation: float, constricted: bool = False,
) -> str:
    """
    Valence（happiness，來自 AU）× Arousal（agitation，來自骨架晃動+音高變異）
    為主軸，對應 Russell (1980) Circumplex Model of Affect 的兩個正交維度，
    比舊版三個變數互相糾纏的門檻更有心理學文獻依據。

    engagement／constricted 是低激動象限裡的裁判：valence 非正向（中性或
    負向）、arousal 也低時，可能是「安穩參與」也可能是「放空退縮/安靜地
    情緒低落」，需要 engagement 明顯退縮**且**身體呈現收縮/封閉姿勢
    （constricted，見 _is_body_constricted）兩者同時成立才判低落，不會
    因為只是「安靜」就被誤判。

    constricted（C 階段，2026-09-05 新增）原本是跟 engagement 平行的獨立
    驗證路徑（or 關係，任一個成立就算數），2026-09-06 改成必須跟 engagement
    同時成立（and）：查證失智/老年淡漠（apathy）評估文獻後發現，臨床上
    對淡漠/退縮的評估明確要求跨行為/情緒/社交互動多面向一起看，不能只憑
    單一指標下結論（Apathy, cognitive function and motor function in
    Alzheimer's disease）；而臨床上已驗證跟淡漠有相關性的具體指標是表情
    豐富度（Correlations Between Facial Expressivity and Apathy in Elderly
    People With Neurocognitive Disorders）——這條路徑我們已經用 happiness
    的 AU 分數涵蓋了，body_constricted 這種純骨架幾何量測（手肘離身體中心
    線的距離）並沒有同等的臨床實證支持，且已知會被「單純坐姿習慣、怕冷、
    關節不適」等跟情緒無關的原因誤觸發（另見多模態情緒融合文獻查證：決策層
    融合方法 MAX/SUM/模糊積分/D-S證據理論本身沒有回答「單一線索夠不夠」，
    不能反過來當作 or 關係的支持）。engagement 是視線＋頭部＋嘴部＋音量
    綜合出來的複合分數，證據量遠比 constricted 這個單一幾何量測豐富，讓
    兩者用 or 並列、給同等否決力量並不合理——改成 and 之後，constricted
    的角色從「獨立就能判定」降為「跟 engagement 一起出現時的加強確認」，
    更符合「多面向證據需同時出現」的臨床方向，也更保守（跟本專案「寧可
    漏掉、不要誤判」的一貫原則一致，見下方 happiness<0 驗證的相同取捨）。

    這裡刻意不讓 happiness<0 單獨繞過驗證直接判低落，是討論過的取捨：
    聳肩（_skel_tension）2026-09-05 定案改進 agitation 的加權項（見上方
    ENGAGEMENT_RANGE 等常數說明），不再是 happiness 的一部分，happiness<0
    現在幾乎只會是 AU 明確偵測到皺眉（扣過個人校正基準後，見 _au_c）造成
    的。維持這個分支要求額外驗證，理由是長者臉部肌肉活動本來就弱、AU 單一
    管道的雜訊仍然偏高（見專案文獻查證），寧可漏掉一些安靜但確實低落的
    案例（假陰性），也不要把還在正常參與的長者誤判成低落（假陽性）——
    「低落」這個標籤在治療師端評估表是 1 分（最差），誤觸發的代價比較高。

    低落  — arousal 低 + （明顯退縮 且 身體收縮） + valence 沒有轉正
    焦躁  — arousal 高 + valence 負向
    亢奮  — arousal 高 + valence 非負向（含中性——沒有明確負向訊號時，
             高激動預設偏向亢奮而不是焦躁，比預設成負面標籤保守）
    適當  — 其餘情況（arousal 低但仍有參與、body 也沒收縮，或 valence 已轉正）

    """
    if agitation < AROUSAL_LOW_MAX:
        if engagement < ENGAGEMENT_WITHDRAWN_MAX and constricted and happiness <= 0:
            return "sad"
        return "happy"
    return "angry" if happiness < 0 else "excited"


# ════════════ 校正基準載入 ════════════════════════════════════════════

async def _load_calibration(r, session_id: str) -> dict | None:
    raw = await r.get(f"session:{session_id}:calibration")
    return json.loads(raw) if raw else None


def _pitch_threshold(calib: dict | None) -> float:
    """
    個人化音高變異門檻：baseline + k×標準差（校正期間量到的個人音高變異分布），
    沒有效校正基準時退回固定值 PITCH_VAR_EXCITED。

    原本是單純 baseline × 2，改成用校正期間量到的標準差設門檻，是因為
    KinectAudioSender.cs 的 UpdatePitch() 用自相關法算音高，沒有做正規化，
    對不同人可能有系統性偏差（傾向抓到較短週期／較高頻），但這個偏差在
    baseline 和即時值上是同一套算法量出來的，用「相對這個人校正期間分布」
    設門檻（而不是乘一個固定倍數），能大致抵消掉這個系統性偏差，不需要
    改動底層演算法。
    """
    if calib:
        baseline = calib.get("pitchVarianceBaseline", 0.0)
        std = calib.get("pitchVarianceStdDev", 0.0)
        if baseline > 1.0:
            return max(PITCH_VAR_EXCITED, baseline + PITCH_VAR_STD_K * std)
    return PITCH_VAR_EXCITED


def _audio_threshold(calib: dict | None) -> float:
    """
    個人化語音音量門檻：校正期間量到的底噪（環境噪音＋麥克風/Kinect陣列本身
    的量測雜訊）baseline + k×標準差，沒有效校正基準時退回固定值 AUDIO_SPEECH_MIN。

    跟 _pitch_threshold 刻意反方向：那邊的個人化門檻只會往上調（避免把天生
    音高起伏大的人誤判成焦躁），這裡的個人化門檻只會往下調、絕不會比固定值
    高（2026-09-06 稽核發現：長者說話音量較小、或 Kinect 陣列麥克風離長者
    較遠時，實際 audio_rms 可能整場都不到寫死的 0.015，STT 那邊有正常轉出
    逐字稿，代表音訊管線是通的，純粹是這個判斷門檻對這個人/這個現場環境
    設太高，語音永遠不會被判定成「有講話」，連帶讓 KinectSensorSender 也
    偵測不到反應時間）。

    用「校正期間量到的底噪」設門檻而不是像音高一樣量「說話時的分布」，是
    因為 15 秒校正窗口只要求長者坐穩、不要求開口說話，蒐集到的樣本本來就
    以環境音為主；門檻設在「底噪之上一點」足以把真正的語音跟環境音分開，
    且這個做法不需要額外要求長者在校正時開口，不用改動校正流程。
    """
    if calib:
        baseline = calib.get("audioRmsBaseline", 0.0)
        std = calib.get("audioRmsStdDev", 0.0)
        if baseline > 0.0:
            return min(AUDIO_SPEECH_MIN, max(AUDIO_RMS_THRESHOLD_MIN, baseline + AUDIO_RMS_STD_K * std))
    return AUDIO_SPEECH_MIN


# ════════════ EMA 平滑（狀態存 Redis）════════════════════════════════

async def _ema_classify(
    r, session_id: str, p: SensorPayload, au: dict, pose: dict, calib: dict | None = None
) -> tuple[str, float, float, float]:
    """
    從 Redis 讀取上一次的 EMA 分數 → 計算本次原始分數 →
    套用個人校正基準（若有）→ EMA 更新 → 寫回 Redis → 分類。

    EMA 平滑的必要性：MCI 長者訊號不穩定（偶發性視線離開、臉部動作弱），
    單點分類雜訊大，需要時間平滑才能反映真實狀態。

    happiness 的 EMA 只在這一幀真的偵測到臉（au 非空字典）時才更新，engagement
    的 EMA 只在骨架至少有一個關節追蹤到時才更新（見 _skel_engagement），沒有
    對應訊號的幀直接沿用上一次的值——「沒偵測到」跟「偵測到、但剛好是中性」
    是兩回事，沒有這個保護的話，長者只要移動導致鏡頭/骨架頻繁追丟，這些維度
    就會被不斷拉回中性，把真正偵測到的訊號稀釋掉（2026-09-06 稽核：某回合
    六成幀數沒偵測到臉，導致明顯的皺眉訊號被稀釋成中性偏負的分數）。
    happiness 100% 只靠臉部這一個管道，受影響最大；engagement 還有音量
    （20%）撐著，受影響較小；agitation 完全不依賴臉部/單一骨架關節（晃動、
    音高、聳肩三個來源都各自有自己的「沒訊號=沒有」合理預設，不是「假裝
    知道答案」，見各自函式說明），沒有這個問題，維持每幀都更新。
    """
    ema_key = f"session:{session_id}:ema"
    ema = await r.hgetall(ema_key)

    prev_eng = float(ema.get("engagement", 0))
    prev_hap = float(ema.get("happiness",  0))
    prev_agi = float(ema.get("agitation",  0))

    # 計算本次原始三維分數
    audio_eng = 1.0 if p.audio_rms > _audio_threshold(calib) else 0.0
    skel_eng  = _skel_engagement(p)  # None＝兩個骨架關節都完全追丟，見該函式說明
    # B 階段：音高變異混入焦躁計算（門檻＝個人校正基準 + k×標準差，見 _pitch_threshold）
    pitch_agi   = min(1.0, p.audio_pitch_variance / max(_pitch_threshold(calib), 1e-6))
    tension_agi = min(1.0, _skel_tension(p) / 2.0)  # _skel_tension 上界是 2.0，正規化成 [0,1]
    raw_agi     = (
        max(0.0, min(1.0, p.body_sway / max(SWAY_AGITATION_MIN, 1e-6) - 1.0)) * 0.60
        + pitch_agi   * 0.25
        + tension_agi * 0.15  # 聳肩，權重刻意壓低，見 _skel_tension 的已知限制說明
    )
    new_agi = prev_agi + EMA_ALPHA * (raw_agi - prev_agi)

    # EMA 更新：agitation 每一幀都更新，但 engagement／happiness 只在真的有對應
    # 訊號時才更新——「沒偵測到」跟「偵測到、但剛好是中性/沒有」是兩回事，如果
    # 每一幀都無條件把「沒偵測到」當成 0 套進 EMA，長者只要頻繁轉頭/移動導致
    # 追丟臉部或骨架，這個維度就會被不斷拉回中性，把真正偵測到的訊號稀釋掉，
    # 看起來比實際更平淡（2026-09-06 稽核：某回合 60% 幀數沒偵測到臉，導致
    # 有偵測到的皺眉訊號被稀釋成中性偏負的分數）。沒訊號的幀直接維持上一次的
    # EMA 不變——沒有新訊號就不該改變我們的估計，等真的再有訊號才繼續更新，
    # 而不是把「追丟」當成「轉中性」。engagement 的骨架分量完全追丟時是同樣
    # 處理，但因為 engagement 還有臉部（30%）／音量（20%）兩個獨立來源，
    # 影響比 100% 只靠臉部的 happiness 小很多。
    if skel_eng is not None:
        raw_eng = (
            _face_engagement(au, pose) * 0.30 +   # 臉部注意力（老年人較弱，權重 30%）
            skel_eng                    * 0.50 +   # 骨架姿勢（更可靠，權重 50%）
            audio_eng                   * 0.20     # 音量（說話 = 有參與，權重 20%）
        )
        # 套用個人校正基準（補償天生習慣，避免誤判）
        if calib:
            # 若長者校正時視線自然偏移，降低 looking_away 懲罰
            if _looking_away_level(pose) in ("yes", "maybe"):
                raw_eng += calib.get("lookingAwayBaseline", 0.0) * 2.0
        # 校正補償是加法，理論上可能把值推出 ENGAGEMENT_RANGE 宣告的範圍（見上方
        # 常數註解的公式推導）。這裡先夾回宣告範圍再做 EMA，確保存進 Redis 的
        # EMA 狀態本身就沒有超界，不是只靠 _pct() 在最後顯示時把百分比削平——
        # 後者只解決顯示層的當機/異常值，EMA 內部狀態超界仍會讓收斂後的分數失真。
        raw_eng = max(ENGAGEMENT_RANGE[0], min(ENGAGEMENT_RANGE[1], raw_eng))
        new_eng = prev_eng + EMA_ALPHA * (raw_eng - prev_eng)
    else:
        new_eng = prev_eng
    if au:
        # 個人校正基準已經在 _face_happiness 內部逐一 AU 處理（見 _au_c／
        # _au_baseline），不用在這裡再額外套用一次籠統的係數。
        raw_hap = _face_happiness(au, calib)  # 純粹是臉部表情，聳肩不影響這裡（見上方 raw_agi）
        raw_hap = max(HAPPINESS_RANGE[0], min(HAPPINESS_RANGE[1], raw_hap))
        new_hap = prev_hap + EMA_ALPHA * (raw_hap - prev_hap)
    else:
        new_hap = prev_hap

    await r.hset(ema_key, mapping={
        "engagement": str(new_eng),
        "happiness":  str(new_hap),
        "agitation":  str(new_agi),
    })
    await r.expire(ema_key, 86400)

    # constricted 直接用本次原始骨架資料判斷，沒有跟 eng/hap/agi 一樣做 EMA
    # 平滑——這是草稿版本的簡化，之後如果發現單幀手肘位置雜訊太大導致判斷
    # 抖動，需要另外幫它做平滑（例如也存進 Redis 累積成一個比率）。
    constricted = _is_body_constricted(p, calib)
    return _classify_from_scores(new_eng, new_hap, new_agi, constricted), new_eng, new_hap, new_agi


# ════════════ Session 統計累積 ════════════════════════════════════════

async def _update_session_stats(
    r, session_id: str, p: SensorPayload, emotion_raw: str, au: dict, pose: dict,
    calib: dict | None = None, signals: list[str] | None = None,
    eng: float = 0.0, hap: float = 0.0, agi: float = 0.0, face_detected: bool = True,
):
    """
    每收到一個 sensor frame 就累積統計至 session:{id}:stats。
    供療程結束後計算五指標評估表使用。
    """
    key = f"session:{session_id}:stats"
    await r.hsetnx(key, "session_start", str(p.timestamp or time.time()))

    # 回合層級情緒統計：以目前所在回合分桶累積 emo_* frame 數，回合結束時
    # 由 session.py _finalize_round_emotion 取多數決寫入 rounds.emotion，
    # 取代舊版「回答那一瞬間」EMA 快照的作法（單點雜訊大，容易跟整回合觀感
    # 不一致）。current_round 由 session.py _update_live_view 在每回合開場
    # 時寫入 session:{id}:metrics，理論上不會缺席，缺席時退回回合 1。
    current_round = await r.hget(f"session:{session_id}:metrics", "current_round") or "1"
    round_key = f"session:{session_id}:round:{current_round}:emotion"
    # 回合層級「判斷依據」統計：跟上面 round_key 同一個 pattern，_frame_count
    # 當分母。訊號代碼取出現頻率最高的幾個，_sum_eng/_sum_hap/_sum_agi 則是
    # 這個回合自己每一幀三維分數的累加總和，回合結束時 session.py
    # _finalize_round_signals 除以 _frame_count 得到「這個回合自己的平均」，
    # 不是讀整場療程的 EMA 快照——跟 rounds.emotion 的多數決一樣，是這個回合
    # 獨立算出來的，不會被前一回合的殘留影響。
    signal_key = f"session:{session_id}:round:{current_round}:signals"

    # 三個骨架欄位同時 None → Unity 未追蹤到身體，長者可能離開座位。提前算好
    # 供下面 emo_has_signal 判斷用（原本這行在函式後段，現在要在 emo_*
    # 累加之前就知道這一幀骨架是不是也追丟了）。
    skel_absent = (
        p.skel_head_drop is None
        and p.skel_lean_forward is None
        and p.skel_shoulder_raise is None
    )
    # emotion_raw 是 _ema_classify 分類出來的結果，但 eng/hap 這兩個維度在
    # 沒有對應訊號時只是沿用上一次的 EMA 值（見該函式說明），不是這一幀真的
    # 重新判斷過。如果臉部跟骨架這一幀都沒訊號（face_detected 為 False 且
    # skel_absent 為真），這次的 emotion_raw 100% 是複製舊值，不該當成新
    # 證據累加——不然長者中途離座/鏡頭長時間追丟時，最後一刻剛好判到的情緒
    # 會被複製到整段沒資料的時間，把一瞬間的情緒放大成半場的情緒，連動讓
    # _score_emotion 的 dominant、sad_rate／angry_rate 都失真，還可能跟持續力
    # 分數同時出現「離座卻情緒很好」的自相矛盾（2026-09-06 稽核）。
    emo_has_signal = face_detected or not skel_absent

    pipe = r.pipeline(transaction=False)
    pipe.hincrby(key, "frame_count",    1)
    if emo_has_signal:
        pipe.hincrby(key, "emo_valid_n", 1)
        pipe.hincrby(key, f"emo_{emotion_raw}", 1)
        pipe.hincrby(round_key, emotion_raw, 1)
    pipe.expire(round_key, 86400)

    pipe.hincrby(signal_key, "_frame_count", 1)
    pipe.hincrbyfloat(signal_key, "_sum_eng", eng)
    pipe.hincrbyfloat(signal_key, "_sum_hap", hap)
    pipe.hincrbyfloat(signal_key, "_sum_agi", agi)
    for code in signals or []:
        pipe.hincrby(signal_key, code, 1)
    pipe.expire(signal_key, 86400)

    # looking_away/eye_closed/mouth_moved 都是靠臉部 AU/pose 判斷，沒偵測到臉
    # 的幀完全沒有這些訊號（見 _looking_away_level 的保守假設：無資料視為
    # 「沒偏離」）。face_detected_n 記錄「這幀真的有臉部資料」的次數，療程
    # 結束時 session.py 拿它當這三個比率的分母，而不是拿 frame_count（含沒
    # 臉的幀），避免鏡頭角度/光線不佳導致偵測率低時，比率被稀釋到不合理地低
    # （2026-09-06 稽核：注意力分數會因此跟治療師現場觀察對不上）。
    if face_detected:
        pipe.hincrby(key, "face_detected_n", 1)
    if _looking_away_level(pose) in ("yes", "maybe"):
        pipe.hincrby(key, "looking_away_n", 1)
    if _au_eyes_closed(au):
        pipe.hincrby(key, "eye_closed_n", 1)
    if _au_mouth_active(au):
        pipe.hincrby(key, "mouth_moved_n", 1)
    if p.body_sway > SWAY_AGITATION_MIN:
        pipe.hincrby(key, "high_sway_n", 1)
    if skel_absent:
        pipe.hincrby(key, "skel_absent_n", 1)

    # B 階段：SpineBase 深度（長者後退離開遊戲區域）。跟 face_detected_n 是
    # 同一種分母問題：far_n 只在 skel_spinebase_z 有追蹤到時才判斷，分母不能
    # 用 frame_count（含骨架完全追丟的幀），不然骨架追蹤率低時 far_rate 會被
    # 稀釋，看起來比實際更少離座。spinebase_valid_n 記錄「這幀真的有追蹤到
    # SpineBase」的次數，療程結束時 session.py 拿它當 far_rate 的分母。
    if p.skel_spinebase_z is not None:
        pipe.hincrby(key, "spinebase_valid_n", 1)
        if p.skel_spinebase_z > SPINEBASE_LEAVING_Z:
            pipe.hincrby(key, "far_n", 1)

    # B 階段：音高變異（焦躁/亢奮的聲學特徵，門檻見 _pitch_threshold）
    if p.audio_pitch_variance > _pitch_threshold(calib):
        pipe.hincrby(key, "high_pitch_var_n", 1)

    # B 階段：手部主動動作（遊戲互動肢體指標）
    if p.skel_handtip_velocity > HANDTIP_ACTIVE_MIN:
        pipe.hincrby(key, "hand_active_n", 1)

    # A 階段：反應延遲累計（計算平均反應時間，供 _score_interaction 用）。
    # response_time_ms 是 Unity 自己用本地計時器量的，起點是 GameController/
    # ShareController 呼叫 OnQuestionAsked() 的那一刻（等語音真的播完/估算的
    # 閱讀時間過去才呼叫，見 KinectSensorSender.cs、LocalAudioPlayer.cs 說明）——
    # 比伺服器自己用 question_asked_at 算時間差準，因為伺服器那個起點在
    # 語音合成/播放之前，會把這段時間也算進反應時間裡。
    #
    # 同一個值也同時累加進這個回合自己的 session:{id}:round:{n}:timing
    # （欄位名沿用 session.py _finalize_round_response_time 原本讀的
    # sum_ms/count），讓「歷史活動」顯示的 rounds.response_time 跟這裡的
    # 互動頻率評分，用的是同一套 Unity 量出來的定義，不是兩條各自獨立、
    # 起點不同的邏輯（2026-09-06 稽核後改版：原本 rounds.response_time
    # 是伺服器自己算的，跟這裡完全不同套）。
    if p.response_time_ms >= 0:
        pipe.hincrby(key, "response_time_sum",   p.response_time_ms)
        pipe.hincrby(key, "response_time_count", 1)
        round_timing_key = f"session:{session_id}:round:{current_round}:timing"
        pipe.hincrby(round_timing_key, "sum_ms", p.response_time_ms)
        pipe.hincrby(round_timing_key, "count", 1)
        pipe.expire(round_timing_key, 86400)

    await pipe.execute()
    await r.expire(key, 86400)


@router.post(
    "/face_calibration_sample",
    summary="校正期間單張畫面取樣，回傳跟舊版 Kinect DetectionResult 同尺度的 0/0.5/1 分數",
)
async def face_calibration_sample(
    request: Request,
    frame: UploadFile = File(...),
    therapist_id: int = Depends(get_current_therapist_id),
):
    """
    KinectCalibrationManager.cs 的 15 秒個人校正流程用。取代原本直接讀
    KinectSensorSender.LastHappy/LastLookingAway/LastMouthMoved（Kinect Face
    API 本地即時值）的做法——臉部分析移到 face-service 之後，Unity 端沒有
    本地資料可以直接取樣，改成校正期間定期（不需要逐幀）拍一張畫面呼叫這支
    端點，Unity 收到後塞進對應的 buffer 取平均，WebSocket 送出去的
    CalibrationPayload 供 _au_baseline 逐一 AU 比對用。

    looking_away/mouth_moved 刻意對齊舊版 ToFloat(DetectionResult) 的尺度
    （Yes=1 / Maybe=0.5 / No,Unknown=0），跟 _face_engagement 用同一套
    _looking_away_level/_au_mouth_active 判斷邏輯，這兩個訊號不校正個人基準
    （見專案文獻查證：這類動作/朝向訊號跟表情正負向是不同類別的問題，
    沒有天生臉部紋路造成系統性偏差的疑慮）。

    au_codes/au_values 是 AU_CALIBRATED_CODES（正向 AU06/AU12＋負向 6 個 AU）
    這一幀各自的原始強度（未扣基準——這裡本身就是在收集基準，沒有基準可
    扣），Unity 端逐一 AU 累積成 auBaselineCodes/auBaselineValues 平均值，
    取代舊版單一 happy/frown 純量欄位（見 _au_baseline、_face_happiness 的
    校正邏輯說明）。
    """
    image_bytes = await frame.read()
    face_result = await request.app.state.face_emotion_service.analyze_bytes(
        image_bytes, filename=frame.filename or "frame.jpg"
    )
    au: dict = {}
    pose: dict = {}
    if face_result.get("face_detected"):
        au = face_result.get("aus", {})
        pose = face_result.get("pose", {})

    looking_away = {"yes": 1.0, "maybe": 0.5, "no": 0.0}[_looking_away_level(pose)]
    mouth_moved  = 1.0 if _au_mouth_active(au) else 0.0
    au_values = [_au(au, code) for code in AU_CALIBRATED_CODES]

    return {
        "looking_away": looking_away,
        "mouth_moved": mouth_moved,
        "au_codes": list(AU_CALIBRATED_CODES),
        "au_values": au_values,
    }


# ════════════ 端點 ════════════════════════════════════════════════════

@router.post("/emotion", summary="接收 Kinect 原始感測資料 + 可選臉部畫面，後端分類後存 Redis")
async def receive_sensor(
    request: Request,
    payload: str = Form(...),
    frame: UploadFile | None = File(None),
    therapist_id: int = Depends(get_current_therapist_id),
):
    body = SensorPayload.model_validate_json(payload)
    r = request.app.state.redis
    calib = await _load_calibration(r, body.session_id)

    # 臉部訊號：Unity 這一幀有抓到畫面才送 frame，face-service 沒偵測到臉/逾時
    # 時 face_detected 會是 False，au/pose 保持空字典——下游的判斷函式對空字典
    # 一律回傳中性值（等同舊版 Kinect DetectionResult=Unknown 的處理方式），
    # 不會因為這次沒有臉部資料就讓整個請求失敗。
    au: dict[str, float] = {}
    pose: dict[str, float] = {}
    face_detected = False
    if frame is not None:
        image_bytes = await frame.read()
        face_result = await request.app.state.face_emotion_service.analyze_bytes(
            image_bytes, filename=frame.filename or "frame.jpg"
        )
        face_detected = bool(face_result.get("face_detected"))
        if face_detected:
            au = face_result.get("aus", {})
            pose = face_result.get("pose", {})

    emotion_raw, eng, hap, agi = await _ema_classify(r, body.session_id, body, au, pose, calib)
    signals = _reasoning_signals(body, au, pose, calib, face_detected)
    await _update_session_stats(r, body.session_id, body, emotion_raw, au, pose, calib, signals, eng, hap, agi, face_detected)
    emotion_label = _EMOTION_LABEL.get(emotion_raw, "適當")
    ts            = body.timestamp or time.time()

    updates: dict[str, str] = {
        "emotion":     emotion_label,
        "emotion_raw": emotion_raw,
        "updated_at":  str(ts),
        # 判斷依據（供治療師端顯示）：三維分數換算成 0-100%，訊號代碼交給
        # 前端 emotionSignals.ts 轉成中文＋圖示，後端不重複維護文案。
        "engagement_pct": str(_pct(eng, *ENGAGEMENT_RANGE)),
        "happiness_pct":  str(_pct(hap, *HAPPINESS_RANGE)),
        "agitation_pct":  str(_pct(agi, *AGITATION_RANGE)),
        "signal_codes":   json.dumps(signals, ensure_ascii=False),
    }
    if body.response_time_ms >= 0:
        updates["response_time"] = f"{round(body.response_time_ms / 1000)}s"

    key = f"session:{body.session_id}:metrics"
    await r.hset(key, mapping=updates)
    await r.expire(key, 86400)

    # latest:emotion TTL 5s — 超過未更新代表 Unity 端斷線
    await r.set(f"session:{body.session_id}:latest:emotion", emotion_label, ex=5)

    return {"ok": True, "emotion": emotion_label}
