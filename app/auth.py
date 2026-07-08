"""
JWT 簽發與驗證，供 Unity 治療師登入、後續請求驗證身分用。
"""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

_bearer_scheme = HTTPBearer(auto_error=False)


def _token_version_key(therapist_id: int) -> str:
    return f"auth:token_version:{therapist_id}"


async def get_token_version(redis, therapist_id: int) -> int:
    value = await redis.get(_token_version_key(therapist_id))
    return int(value) if value else 0


async def revoke_therapist_tokens(redis, therapist_id: int) -> None:
    """讓這個治療師目前所有已簽發的 token 立即失效（例如裝置遺失時使用），
    不用等 jwt_expire_minutes 自然過期。"""
    await redis.incr(_token_version_key(therapist_id))


async def create_access_token(redis, therapist_id: int, organization_id: int | None) -> str:
    token_version = await get_token_version(redis, therapist_id)
    payload = {
        "sub": str(therapist_id),
        "organization_id": organization_id,
        "ver": token_version,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="登入已過期或無效，請重新登入") from e


def _decode_dashboard_session(token: str) -> dict:
    try:
        return jwt.decode(token, settings.session_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="登入已過期或無效，請重新登入") from e


async def get_current_therapist_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> int:
    """FastAPI dependency：驗證呼叫者是已登入的治療師，回傳 therapist_id。

    /session、/sensor 有些端點同時被兩種呼叫者用，這裡分開處理：
    - Unity：帶 `Authorization: Bearer <JWT>`（本檔案簽發，可被 revoke_therapist_tokens 立即撤銷）
    - 治療師後台瀏覽器：帶 `rememo_session` cookie（Next.js 用 SESSION_SECRET 簽的另一組 JWT）
    """
    if credentials is not None:
        payload = decode_access_token(credentials.credentials)
        therapist_id = int(payload["sub"])

        redis = request.app.state.redis
        current_version = await get_token_version(redis, therapist_id)
        if payload.get("ver", 0) != current_version:
            raise HTTPException(status_code=401, detail="登入已被撤銷，請重新登入")

        return therapist_id

    cookie_token = request.cookies.get("rememo_session")
    if cookie_token:
        payload = _decode_dashboard_session(cookie_token)
        therapist_id = payload.get("therapistId")
        if therapist_id is not None:
            return int(therapist_id)

    raise HTTPException(status_code=401, detail="請先登入")


async def get_therapist_id_from_ws_token(redis, token: str) -> int:
    """WebSocket 版本的驗證：連線網址帶 ?token=<JWT>（WS handshake 無法像一般
    HTTP header 那樣通用地帶 Authorization，query string 是各家 WS client 都支援的做法）。"""
    if not token:
        raise HTTPException(status_code=401, detail="請先登入")
    payload = decode_access_token(token)
    therapist_id = int(payload["sub"])
    current_version = await get_token_version(redis, therapist_id)
    if payload.get("ver", 0) != current_version:
        raise HTTPException(status_code=401, detail="登入已被撤銷，請重新登入")
    return therapist_id
