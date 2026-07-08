import bcrypt
import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, get_current_therapist_id, revoke_therapist_tokens
from db.deps import get_db
from db.models import Therapist

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60  # 連續失敗達上限後鎖定 15 分鐘


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    therapist_id: int
    name: str
    # 0 代表沒有掛任何機構；organizations.id 是 SERIAL（從 1 開始），不會撞號。
    organization_id: int = 0


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except ValueError:
        # stored_hash 不是合法的 bcrypt hash（例如舊資料/手動塞入的測試資料）
        return False


@router.post("/login", response_model=LoginResponse, summary="治療師登入（Unity 用，回傳 JWT）")
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    r = request.app.state.redis
    fail_key = f"login_fail:{body.email}"

    fail_count = int(await r.get(fail_key) or 0)
    if fail_count >= MAX_FAILED_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"登入失敗次數過多，請 {LOGIN_LOCKOUT_SECONDS // 60} 分鐘後再試",
        )

    result = await db.execute(select(Therapist).where(Therapist.email == body.email))
    therapist = result.scalar_one_or_none()

    # bcrypt 比對是 CPU-bound 同步呼叫，丟到 thread pool 執行，避免卡住整個事件迴圈
    # （同一個 process 裡還有 WebSocket STT 等即時流量在跑）。
    password_ok = therapist is not None and await anyio.to_thread.run_sync(
        _verify_password, body.password, therapist.password
    )

    if not password_ok:
        pipe = r.pipeline(transaction=False)
        pipe.incr(fail_key)
        pipe.expire(fail_key, LOGIN_LOCKOUT_SECONDS)
        await pipe.execute()
        raise HTTPException(status_code=401, detail="電子信箱或密碼錯誤")

    await r.delete(fail_key)

    token = await create_access_token(r, therapist.id, therapist.organization_id)
    return LoginResponse(
        token=token,
        therapist_id=therapist.id,
        name=therapist.name,
        organization_id=therapist.organization_id or 0,
    )


@router.post(
    "/revoke",
    summary="登出所有裝置（例如裝置遺失時使用，讓目前所有已簽發的 token 立即失效）",
)
async def revoke_all_devices(
    request: Request,
    therapist_id: int = Depends(get_current_therapist_id),
):
    await revoke_therapist_tokens(request.app.state.redis, therapist_id)
    return {"ok": True}
