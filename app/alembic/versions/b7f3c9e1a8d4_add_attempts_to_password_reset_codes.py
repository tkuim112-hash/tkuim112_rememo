"""add attempts to password_reset_codes

Revision ID: b7f3c9e1a8d4
Revises: e5a9c14d7f32
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f3c9e1a8d4'
down_revision: Union[str, Sequence[str], None] = 'e5a9c14d7f32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """2026-09-30 稽核發現 reset-password/route.ts 完全沒比對 verification_code，
    只看信箱有沒有一筆還沒過期的紀錄就放行改密碼——任何人知道對方信箱就能接管
    帳號。修掉那個漏洞之後，verify-code／reset-password 才真的會去比對 6 碼
    驗證碼，但兩支都還沒擋暴力猜（100 萬種組合），這裡加欄位記錄猜錯次數，
    超過上限就要求重新寄送驗證碼，不能無限次嘗試。
    """
    op.execute(
        "ALTER TABLE password_reset_codes ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE password_reset_codes DROP COLUMN IF EXISTS attempts")
