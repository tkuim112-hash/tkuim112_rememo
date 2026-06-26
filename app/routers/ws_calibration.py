import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/calibration")
async def ws_calibration(websocket: WebSocket, session_id: str = ""):
    """
    接收 Unity KinectCalibrationManager 送來的個人基準值（一次性）。

    URL: ws://localhost:8000/ws/calibration?session_id=xxx

    payload 格式（JSON）：
      {
        "type": "calibration",
        "duration": 15.0,
        "happyBaseline": 0.12,
        "lookingAwayBaseline": 0.05,
        "mouthMovedBaseline": 0.03,
        "pitchVarianceBaseline": 32.5,
        "jointKeys": [...],
        "jointX": [...],
        "jointY": [...],
        "jointZ": [...]
      }

    寫入 Redis key: session:{session_id}:calibration（無 TTL，療程結束時隨 meta 一起清除）
    session_id 缺失時拒絕儲存（回傳 ok: false），個人化功能 fallback 為固定常數。
    """
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        if data.get("type") != "calibration":
            await websocket.close(code=1008)
            return

        if not session_id:
            print("[WS/Calibration] WARNING: session_id 缺失，校正資料不儲存")
            await websocket.send_json({"ok": False, "error": "missing session_id"})
            return

        r = websocket.app.state.redis
        await r.set(f"session:{session_id}:calibration", json.dumps(data))
        await websocket.send_json({"ok": True, "session_id": session_id})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS/Calibration] {e}")
