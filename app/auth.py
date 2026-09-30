"""
JWT 簽發與驗證，供 Unity 治療師登入、後續請求驗證身分用。
"""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.deps import get_db
from db.models import Therapist

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
    db: AsyncSession = Depends(get_db),
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
            therapist_id = int(therapist_id)
            # cookie 這顆 JWT 本身沒有像 Unity token 的 ver 可以撤銷，帳號被機構
            # 管理者移除（見 routers/organization.py remove_member）或自己刪除帳號
            # 後，cookie 在瀏覽器裡最長還能再撐 7 天——這裡現查一次 DB 確認帳號
            # 還存在，讓「移除後立即失去存取權限」對瀏覽器登入的治療師也成立。
            exists = (
                await db.execute(select(Therapist.id).where(Therapist.id == therapist_id))
            ).scalar_one_or_none()
            if exists is None:
                raise HTTPException(status_code=401, detail="帳號已不存在，請重新登入")
            return therapist_id

    raise HTTPException(status_code=401, detail="請先登入")


async def get_current_therapist(
    therapist_id: int = Depends(get_current_therapist_id),
    db: AsyncSession = Depends(get_db),
) -> Therapist:
    """回傳目前登入治療師的完整資料列（機構、是否為管理者等）。

    is_org_admin 一律從 DB 現查，不放進 JWT/cookie claim——管理者權限被收回時
    （見 routers/organization.py 的移除治療師）要立即生效，不能讓舊 token
    在過期前繼續被當成管理者用。"""
    therapist = (
        await db.execute(select(Therapist).where(Therapist.id == therapist_id))
    ).scalar_one_or_none()
    if therapist is None:
        raise HTTPException(status_code=401, detail="找不到使用者，請重新登入")
    return therapist


async def require_org_admin(
    therapist: Therapist = Depends(get_current_therapist),
) -> Therapist:
    """FastAPI dependency：只有機構管理者能呼叫（見 routers/organization.py）。"""
    if therapist.organization_id is None or not therapist.is_org_admin:
        raise HTTPException(status_code=403, detail="需要機構管理者權限")
    return therapist


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
