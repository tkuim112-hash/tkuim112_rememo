import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token
from db.deps import get_db
from db.models import Therapist

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    therapist_id: int
    name: str
    organization_id: int = 0


@router.post("/login", response_model=LoginResponse, summary="治療師登入（Unity 用，回傳 JWT）")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Therapist).where(Therapist.email == body.email))
    therapist = result.scalar_one_or_none()

    if therapist is None or not bcrypt.checkpw(
        body.password.encode("utf-8"), therapist.password.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="電子信箱或密碼錯誤")

    token = create_access_token(therapist.id, therapist.organization_id)
    return LoginResponse(
        token=token,
        therapist_id=therapist.id,
        name=therapist.name,
        organization_id=therapist.organization_id or 0,
    )
