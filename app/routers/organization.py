import json
import secrets
import time

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_admin, revoke_therapist_tokens
from db.deps import get_db
from db.models import Therapist

router = APIRouter(prefix="/organization", tags=["organization"])


class MemberOut(BaseModel):
    id: int
    name: str
    email: str
    specialization: str | None = None
    is_org_admin: bool
    is_self: bool
    never_logged_in: bool
    is_in_session: bool


class MemberListResponse(BaseModel):
    members: list[MemberOut]


class CreateMemberRequest(BaseModel):
    name: str
    email: str
    specialization: str | None = None


class CreateMemberResponse(BaseModel):
    id: int


class UpdateMemberAdminRequest(BaseModel):
    is_org_admin: bool


async def _locked_admin_count(db: AsyncSession, organization_id: int) -> int:
    """鎖住機構目前所有管理者的資料列再回傳人數，供「降級前確認不是最後一位」
    用。用 count(*) 查詢的話，兩個管理者幾乎同時互降對方（或同一個人連點兩下）
    時，兩個 request 可能都在對方 commit 前讀到「還有兩位」而一起放行，兩個都
    降級成功後機構就會變成 0 個管理者、沒有人能再修復。FOR UPDATE 鎖住這些列，
    讓第二個 request 得等第一個 commit 完、重新看到最新人數才能繼續判斷。"""
    rows = (
        await db.execute(
            select(Therapist.id)
            .where(
                Therapist.organization_id == organization_id,
                Therapist.is_org_admin.is_(True),
            )
            .with_for_update()
        )
    ).scalars().all()
    return len(rows)


_STALE_SESSION_MS = 2 * 60 * 60 * 1000  # 跟 /session/pending 的 2 小時 TTL 一致


async def _ids_in_session(request: Request) -> set[str]:
    """目前有進行中療程的 therapist_id 集合（見 session.py _init_session_meta），
    只當作「這個人現在忙碌中」的顯示用途，這裡掃到的所有機構的 session 都只在
    伺服器端比對、絕不會回傳給前端，不會有 active-patients 那種跨機構外洩問題。

    _init_session_meta 寫入這個 key 時沒有設 TTL，只有療程正常跑完（見
    _compute_and_save_assessment）才會清掉——中途中斷（瀏覽器關掉、Unity
    當機、測試到一半沒跑完）就會永遠留著，2026-09-30 稽核發現真的有這種
    留了好幾個月的舊資料，會讓對應的治療師被永遠標成「活動中」跟事實不符。
    這裡用 start_at 過濾掉超過合理療程時長的舊資料，不讓陳年垃圾資料
    誤導畫面；真正清掉這些殘留 key 是另一件事，這裡只負責不被它們騙。
    """
    r = request.app.state.redis
    now_ms = time.time() * 1000
    ids: set[str] = set()
    async for key in r.scan_iter(match="session:*:meta"):
        raw = await r.get(key)
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            continue
        start_at = meta.get("start_at")
        if isinstance(start_at, (int, float)) and now_ms - start_at > _STALE_SESSION_MS:
            continue
        tid = meta.get("therapist_id")
        if tid:
            ids.add(str(tid))
    return ids


@router.get("/members", response_model=MemberListResponse, summary="列出同機構的治療師成員（僅管理者）")
async def list_members(
    request: Request,
    admin: Therapist = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Therapist)
        .where(Therapist.organization_id == admin.organization_id)
        .order_by(Therapist.id)
    )
    members = result.scalars().all()
    busy_ids = await _ids_in_session(request)

    return MemberListResponse(
        members=[
            MemberOut(
                id=m.id,
                name=m.name,
                email=m.email,
                specialization=m.specialization,
                is_org_admin=m.is_org_admin,
                is_self=m.id == admin.id,
                never_logged_in=m.last_login_at is None,
                is_in_session=str(m.id) in busy_ids,
            )
            for m in members
        ]
    )


@router.post("/members", response_model=CreateMemberResponse, status_code=201, summary="新增治療師帳號（僅管理者）")
async def create_member(
    body: CreateMemberRequest,
    admin: Therapist = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """直接把治療師掛到管理者自己的機構（admin.organization_id），不建立新機構
    ——這是修掉「每次註冊都新建機構、同機構永遠卡在一位治療師」這個 bug 的
    關鍵路徑：註冊頁只給第一次建立機構的管理者用，同機構之後累積的治療師都
    透過這支 API 加入。

    這裡不收也不指定密碼：帳號密碼是誰都不知道的隨機值，建立成功後前端會
    立即呼叫既有的 /api/auth/forgot-password（見 therapist-dashboard），
    讓新治療師收到驗證信、自己設定密碼——不會有初始密碼需要管理者用聊天
    軟體、簡訊等不安全管道轉達給對方的問題。
    """
    name = body.name.strip()
    email = body.email.strip()
    specialization = (body.specialization or "").strip() or None

    if not name or not email:
        raise HTTPException(status_code=400, detail="請填寫姓名與電子信箱")

    existing = (
        await db.execute(select(Therapist.id).where(Therapist.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="此 Email 已被註冊")

    placeholder_password = secrets.token_urlsafe(32)
    hashed = bcrypt.hashpw(placeholder_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    member = Therapist(
        organization_id=admin.organization_id,
        name=name,
        email=email,
        specialization=specialization,
        password=hashed,
        is_org_admin=False,
    )
    db.add(member)
    try:
        await db.commit()
    except IntegrityError:
        # 上面的 existing 檢查跟這裡的 commit 之間有空檔，兩個管理者（甚至同一個
        # 管理者連點兩次）幾乎同時新增同一組 email 時，其中一個會在這裡撞上
        # therapists.email 的 UNIQUE constraint——這裡補捕捉，讓使用者看到跟
        # 一般撞號一樣乾淨的 409，而不是未預期的 500。
        await db.rollback()
        raise HTTPException(status_code=409, detail="此 Email 已被註冊")
    await db.refresh(member)
    return CreateMemberResponse(id=member.id)


@router.delete("/members/{member_id}", status_code=204, summary="移除治療師帳號（僅管理者）")
async def remove_member(
    request: Request,
    member_id: int,
    admin: Therapist = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if member_id == admin.id:
        raise HTTPException(status_code=400, detail="無法移除自己的帳號，請至帳號設定刪除帳號")

    member = (
        await db.execute(select(Therapist).where(Therapist.id == member_id))
    ).scalar_one_or_none()
    if member is None or member.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="找不到這位成員")

    await db.delete(member)
    await db.commit()

    # 刪除資料列不會讓這個人手上已簽發的 Unity JWT 失效（get_current_therapist_id
    # 的 Bearer 分支只比對 Redis token_version，從不查 DB 確認帳號還存在），
    # 不補這一步的話，被移除的治療師在 token 自然過期（最長 jwt_expire_minutes）
    # 前仍能正常呼叫 /session、/patients 等 API，跟畫面上「移除後將立即失去
    # 存取權限」的說明不符。
    await revoke_therapist_tokens(request.app.state.redis, member_id)


@router.patch("/members/{member_id}", summary="設定或取消治療師的管理者權限（僅管理者，也可以用來取消自己）")
async def update_member_admin(
    member_id: int,
    body: UpdateMemberAdminRequest,
    admin: Therapist = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    # 這裡允許自己降級自己（前提是機構還有其他管理者，見下面的人數檢查）——
    # 如果整個擋死自我降級，這支 API 就只可能拿別人開刀，而呼叫者自己一定還
    # 是管理者，人數檢查會永遠 >= 2、變成永遠不會被觸發的死碼。允許自我降級
    # 才讓「機構至少需要一位管理者」這個保護真正有意義。
    if member_id == admin.id:
        member = admin
    else:
        member = (
            await db.execute(select(Therapist).where(Therapist.id == member_id))
        ).scalar_one_or_none()
        if member is None or member.organization_id != admin.organization_id:
            raise HTTPException(status_code=404, detail="找不到這位成員")

    if member.is_org_admin and not body.is_org_admin:
        # 降級前先確認機構還有其他管理者，不然整個機構會沒有任何人能再新增／
        # 移除成員或指派管理者，變成沒有人能修復的死局。
        if await _locked_admin_count(db, admin.organization_id) <= 1:
            raise HTTPException(status_code=400, detail="機構至少需要一位管理者，請先指派其他管理者後再取消")

    member.is_org_admin = body.is_org_admin
    await db.commit()
    return {"ok": True}
