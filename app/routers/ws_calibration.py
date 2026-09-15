import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from auth import get_therapist_id_from_ws_token
import ws_registry

router = APIRouter()


@router.websocket("/ws/calibration")
async def ws_calibration(websocket: WebSocket, session_id: str = "", token: str = ""):
    """
    接收 Unity KinectCalibrationManager 送來的個人基準值（一次性）。

    URL: ws://localhost:8000/ws/calibration?session_id=xxx&token=<JWT>

    payload 格式（JSON）：
      {
        "type": "calibration",
        "duration": 15.0,
        "lookingAwayBaseline": 0.05,
        "mouthMovedBaseline": 0.03,
        "pitchVarianceBaseline": 32.5,
        "pitchVarianceStdDev": 8.1,
        "audioRmsBaseline": 0.004,
        "audioRmsStdDev": 0.001,
        "auBaselineCodes": ["AU06", "AU12", "AU01", "AU04", "AU05", "AU07", "AU15", "AU23"],
        "auBaselineValues": [0.1, 0.2, 0.05, 0.6, 0.1, 0.15, 0.05, 0.1],
        "jointKeys": [...],
        "jointX": [...],
        "jointY": [...],
        "jointZ": [...],
        "bodySwayBaseline": 0.018
      }

    寫入 Redis key: session:{session_id}:calibration（TTL 7200 秒，與 /session/pending
    的 case:{patient_id}:pending_session 對齊；療程正式跑完時仍會隨 meta 一起提前清除）。
    session_id 缺失時拒絕儲存（回傳 ok: false），個人化功能 fallback 為固定常數。
    """
    try:
        await get_therapist_id_from_ws_token(websocket.app.state.redis, token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    r = websocket.app.state.redis
    if session_id:
        # 同一個 session_id 在 2 小時內可能被同一位病患重複沿用（見 /session/pending）。
        # 若上一輪校正完成後療程沒有正式跑完（沒被 /session/start 之後的流程清掉），
        # 這裡殘留的舊校正資料會讓 /session/{id}/status 誤判成「已校正」、跳過
        # calibrating 中間態。新連線代表要重新校正一次，先清掉舊值。
        await r.delete(f"session:{session_id}:calibration")

    # 從這裡到收到最終校正資料為止，這條連線會一直卡在下面的 receive_json()——
    # 掛著沒斷代表 Unity 正在跑校正流程，供 /session/{id}/status 回報「校正進行中」
    # 給治療師網頁（見 ws_registry.py）。
    ws_registry.mark_calibrating(session_id)
    try:
        data = await websocket.receive_json()
        if data.get("type") != "calibration":
            await websocket.close(code=1008)
            return

        if not session_id:
            print("[WS/Calibration] WARNING: session_id 缺失，校正資料不儲存")
            await websocket.send_json({"ok": False, "error": "missing session_id"})
            return

        await r.set(f"session:{session_id}:calibration", json.dumps(data), ex=7200)
        await websocket.send_json({"ok": True, "session_id": session_id})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS/Calibration] {e}")
    finally:
        ws_registry.unmark_calibrating(session_id)
