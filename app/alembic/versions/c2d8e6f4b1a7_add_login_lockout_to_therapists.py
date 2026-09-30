"""add login lockout to therapists

Revision ID: c2d8e6f4b1a7
Revises: b7f3c9e1a8d4
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2d8e6f4b1a7'
down_revision: Union[str, Sequence[str], None] = 'b7f3c9e1a8d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """2026-09-30 稽核發現治療師後台真正在用的登入頁（therapist-dashboard
    api/auth/login/route.ts）完全沒有登入失敗次數限制，可以無限次嘗試猜密碼——
    Unity 端的登入（app/routers/auth.py）早就有這組保護（Redis 版，5 次鎖 15
    分鐘），只是 Next.js 那邊沒有 Redis 可用，一直沒補上。這裡在 Postgres 加
    兩個欄位做一樣的事。
    """
    op.execute(
        "ALTER TABLE therapists ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE therapists ADD COLUMN locked_until TIMESTAMP"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE therapists DROP COLUMN IF EXISTS locked_until")
    op.execute("ALTER TABLE therapists DROP COLUMN IF EXISTS failed_login_count")
