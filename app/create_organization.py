"""
人工開通新機構用的內部工具，取代已關閉的公開註冊頁（api/auth/register 已停用，
見 therapist-dashboard 那邊的說明）。任何人都能透過公開表單自建機構、自封管理者
這件事被視為信任模型的漏洞後，機構起始帳號改成只能由平台方（也就是執行這支
腳本的人）手動開通。

建立機構＋第一位治療師（自動設為機構管理者），密碼是誰都不知道的隨機值，
執行成功後會呼叫前端既有的 /api/auth/forgot-password 寄一封驗證信，讓對方自己
設定密碼——跟機構成員管理頁「新增治療師」（見 routers/organization.py
create_member）用的是同一套機制，這裡只是多了「順便建立機構」這一步。

用法（在 docker 裡執行，這樣才能吃到跟 app 服務一樣的 DATABASE_URL）：
    docker exec tku-care-app-1 python create_organization.py \\
        --org "長青日照中心" --name "王雅婷" --email "yating.wang@example.org"

寄信失敗不會讓帳號建立跟著失敗（帳號已經建好了沒有回頭路），失敗時腳本會
提示之後可以請對方自己在登入頁按「忘記密碼」重新觸發寄信。
"""
import argparse
import asyncio
import secrets
import sys

import bcrypt
import httpx
from sqlalchemy import select

from db.models import Organization, Therapist
from db.session import AsyncSessionLocal

# app 容器跟 frontend 容器在同一個 docker-compose network，service 名稱可以直接當 host 用。
_FRONTEND_URL = "http://frontend:3000"


async def _create_organization(org_name: str, admin_name: str, admin_email: str) -> int:
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(Therapist.id).where(Therapist.email == admin_email))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"錯誤：{admin_email} 已經是治療師帳號了，不能重複建立", file=sys.stderr)
            sys.exit(1)

        # 誰都不知道的隨機密碼，帳號要靠寄出去的驗證信自己設定才能用——理由
        # 同 routers/organization.py create_member，這裡不重複解釋。
        placeholder_password = secrets.token_urlsafe(32)
        hashed = bcrypt.hashpw(placeholder_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        org = Organization(name=org_name, email=admin_email, password=hashed)
        db.add(org)
        await db.flush()  # 拿到 org.id，還沒 commit

        admin = Therapist(
            organization_id=org.id,
            name=admin_name,
            email=admin_email,
            password=hashed,
            is_org_admin=True,
        )
        db.add(admin)
        await db.commit()
        return org.id


async def _send_setup_email(email: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_FRONTEND_URL}/api/auth/forgot-password", json={"email": email, "mode": "setup"}
            )
        return resp.status_code == 200
    except httpx.HTTPError as e:
        print(f"寄信請求失敗：{e}", file=sys.stderr)
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="人工開通新機構（取代已關閉的公開註冊頁）")
    parser.add_argument("--org", required=True, help="機構名稱")
    parser.add_argument("--name", required=True, help="第一位管理者姓名")
    parser.add_argument("--email", required=True, help="第一位管理者的電子信箱")
    args = parser.parse_args()

    org_id = await _create_organization(args.org, args.name, args.email)
    print(f"已建立機構「{args.org}」（id={org_id}），管理者：{args.name} <{args.email}>")

    print("寄送設定密碼的驗證信...")
    if await _send_setup_email(args.email):
        print(f"已寄出，請通知 {args.email} 檢查信箱、完成驗證後設定密碼")
    else:
        print("寄信失敗，但帳號已經建立成功。", file=sys.stderr)
        print(f"可以請對方直接到登入頁按「忘記密碼」，用 {args.email} 重新觸發寄信。", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
