"""
JWT 簽發與驗證，供 Unity 端治療師登入後續請求使用。
"""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

_bearer_scheme = HTTPBearer()


def create_access_token(therapist_id: int, organization_id: int | None) -> str:
    payload = {
        "sub": str(therapist_id),
        "organization_id": organization_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="登入已過期或無效，請重新登入") from e


async def get_current_therapist_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> int:
    """FastAPI dependency：供之後要保護的端點驗證 Unity 帶來的 Bearer token。"""
    payload = decode_access_token(credentials.credentials)
    return int(payload["sub"])
