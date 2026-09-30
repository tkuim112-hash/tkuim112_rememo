"""add is_org_admin and last_login_at to therapists

Revision ID: e5a9c14d7f32
Revises: d8b3c5f27a91
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a9c14d7f32'
down_revision: Union[str, Sequence[str], None] = 'd8b3c5f27a91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """機構成員管理：is_org_admin 讓機構管理者能新增/移除同機構的其他治療師帳號
    （見 routers/organization.py），last_login_at 供成員列表顯示「尚未登入」提醒
    （管理者建立帳號後要記得把密碼告知對方）。

    註冊流程（api/auth/register/route.ts）目前每次都會建立一筆新機構，所以在這個
    欄位加上去之前，每個機構最多只有一位治療師——也就是該機構的建立者，直接把
    既有帳號都設成管理者即可，不會多授權給任何人。
    """
    op.execute(
        "ALTER TABLE therapists ADD COLUMN is_org_admin BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE therapists ADD COLUMN last_login_at TIMESTAMP"
    )
    op.execute("UPDATE therapists SET is_org_admin = true")


def downgrade() -> None:
    op.execute("ALTER TABLE therapists DROP COLUMN IF EXISTS last_login_at")
    op.execute("ALTER TABLE therapists DROP COLUMN IF EXISTS is_org_admin")
