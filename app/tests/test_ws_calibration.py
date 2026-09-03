"""
重現並鎖住 ws_calibration.py 的舊校正資料殘留 bug：

病患第一輪校正成功後，若療程沒有正式跑完（session:{id}:calibration 只在
_compute_and_save_assessment 成功時才被清掉，見 session.py），2 小時內同一位
病患再次進站會沿用同一個 session_id（/session/pending 的 SETNX ex=7200）。
Unity 重新連上 /ws/calibration 準備跑新一輪校正時，若沒清掉舊資料，治療師網頁
poll /session/{id}/status 會看到 calibrated=true，直接跳過 calibrating 中間態
（session.py 的 calibrating = (not calibrated) and ...）。
"""
import json

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from routers.ws_calibration import router as ws_calibration_router


class FakeRedis:
    """夠用就好的假 Redis：get/set/delete/exists，外加記錄 TTL 供斷言用。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex
        return True

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.ttls.pop(key, None)
                removed += 1
        return removed

    async def exists(self, key):
        return 1 if key in self.store else 0


def _make_token(therapist_id: int = 1) -> str:
    # get_therapist_id_from_ws_token 只檢查 ver 是否跟 Redis 裡的 token_version
    # 一致；FakeRedis.get 對沒設過的 key 回 None → get_token_version 視為 0。
    payload = {"sub": str(therapist_id), "ver": 0}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


@pytest.fixture
def app_and_redis():
    app = FastAPI()
    app.include_router(ws_calibration_router)
    fake_redis = FakeRedis()
    app.state.redis = fake_redis
    return app, fake_redis


def test_reconnect_clears_stale_calibration_before_new_data_arrives(app_and_redis):
    app, fake_redis = app_and_redis
    session_id = "reused-session"
    key = f"session:{session_id}:calibration"

    # 模擬上一輪殘留的舊校正資料（那次療程中途被放棄，沒跑到會清 key 的
    # _compute_and_save_assessment）。
    fake_redis.store[key] = json.dumps({"pitchVarianceBaseline": 99.9})

    token = _make_token()
    client = TestClient(app)

    with client.websocket_connect(
        f"/ws/calibration?session_id={session_id}&token={token}"
    ) as ws:
        # accept() 之後、收到這次新的校正資料之前，舊 key 必須已經被清掉——
        # 不然此刻治療師網頁 poll /session/{id}/status 會誤判成 calibrated=true，
        # 跳過 calibrating 中間態。
        assert key not in fake_redis.store

        ws.send_json(
            {
                "type": "calibration",
                "duration": 15.0,
                "pitchVarianceBaseline": 12.3,
            }
        )
        response = ws.receive_json()

    assert response == {"ok": True, "session_id": session_id}
    assert key in fake_redis.store
    saved = json.loads(fake_redis.store[key])
    assert saved["pitchVarianceBaseline"] == 12.3
    # 新寫入必須帶 TTL，避免這次又中途放棄時再無限期殘留下去。
    assert fake_redis.ttls.get(key) == 7200


def test_first_time_calibration_still_gets_ttl(app_and_redis):
    """沒有舊資料殘留的正常路徑：確保這次修改沒有動到原本就沒問題的 first-time 流程。"""
    app, fake_redis = app_and_redis
    session_id = "brand-new-session"
    key = f"session:{session_id}:calibration"
    token = _make_token()
    client = TestClient(app)

    with client.websocket_connect(
        f"/ws/calibration?session_id={session_id}&token={token}"
    ) as ws:
        ws.send_json({"type": "calibration", "pitchVarianceBaseline": 5.0})
        response = ws.receive_json()

    assert response == {"ok": True, "session_id": session_id}
    assert fake_redis.ttls.get(key) == 7200
