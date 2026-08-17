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
    websocket = _connections.get(session_id)
    if websocket is None:
        return False
    try:
        await websocket.send_json({"type": "control", "action": action})
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
