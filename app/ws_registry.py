"""
治療師網頁的「療程控制」按鈕（重播/跳過/暫停/繼續）要即時轉發給長者端 Unity——
Unity 是透過 /ws/stt 這條長連線收 control 訊框的（GameController.cs 的
HandleSTTMessage），所以這裡維護一份 session_id -> WebSocket 對照表，供
session.py 的 /control 端點查到目前這場療程對應哪一條連線。
"""
from fastapi import WebSocket

_connections: dict[str, WebSocket] = {}


def register(session_id: str, websocket: WebSocket) -> None:
    if session_id:
        _connections[session_id] = websocket


def unregister(session_id: str, websocket: WebSocket) -> None:
    if _connections.get(session_id) is websocket:
        del _connections[session_id]


async def send_control(session_id: str, action: str) -> bool:
    """回傳是否真的送達。長者端目前沒連線（例如療程還沒進到 GameScene）不算錯誤，
    只是沒地方送，呼叫端應把 False 當「沒送達」處理，不用報錯給治療師看。"""
    return await send_message(session_id, {"type": "control", "action": action})


async def send_message(session_id: str, payload: dict) -> bool:
    """跟 send_control 同一份連線表，但可以送任意形狀的 JSON——治療師在
    /confirm_response、/closing/confirm_response 確認（可能編輯過）長者回應後，
    用這支把處理結果整包推給 Unity 顯示，不用像 send_control 那樣侷限在
    固定的 {type, action} 格式。"""
    websocket = _connections.get(session_id)
    if websocket is None:
        return False
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        _connections.pop(session_id, None)
        return False


# ── /ws/calibration 連線在場 = 治療師網頁「校正進行中」狀態的判斷依據 ──────────
#
# KinectCalibrationManager 在 WarmupScene 一啟動就連上 /ws/calibration 並保持
# 連線，直到蒐集完 15 秒基準值、通過穩定度檢查才送出最終 payload；期間後端
# 這支 handler 會卡在 receive_json() 等待，所以「這個 session_id 有登記在這裡」
# 就代表 Unity 正在跑校正流程（不是完全沒連線），供 /session/{id}/status 回傳
# 第三種狀態，跟純粹的 calibrated（已完成）／未連線區分開來。
_calibrating_sessions: set[str] = set()


def mark_calibrating(session_id: str) -> None:
    if session_id:
        _calibrating_sessions.add(session_id)


def unmark_calibrating(session_id: str) -> None:
    _calibrating_sessions.discard(session_id)


def is_calibrating(session_id: str) -> bool:
    return session_id in _calibrating_sessions


# ── 治療師按下「結束活動」= 排除 /ws/stt 斷線時的不正常結束判定 ─────────────
#
# session.py 的 /control 端點 action=="end" 時（轉發給 Unity 觸發
# Application.Quit()）呼叫 mark_ending；ws_stt.py 的 finally 區塊斷線時用
# consume_ending 判斷「這次斷線是不是治療師主動按的」——是的話，療程狀態交給
# 治療師網頁 /activity/{id}/end 頁面（選「儲存並完成」或「稍後填寫」）決定；
# 不是的話（App/網頁被直接關掉、當機、斷線）才視為不正常結束。
_ending_sessions: set[str] = set()


def mark_ending(session_id: str) -> None:
    if session_id:
        _ending_sessions.add(session_id)


def consume_ending(session_id: str) -> bool:
    if session_id in _ending_sessions:
        _ending_sessions.discard(session_id)
        return True
    return False
