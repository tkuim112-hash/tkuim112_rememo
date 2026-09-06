"""add score reason columns to sessions

Revision ID: ce0a3f17ce56
Revises: a2c6e9f14b7d
Create Date: 2026-09-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce0a3f17ce56'
down_revision: Union[str, Sequence[str], None] = 'a2c6e9f14b7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """持續力(score_endurance)、互動頻率(score_interaction)這兩個分數各自有
    兩種完全不同的觸發原因（例如持續力1分可能是「擅自離開」也可能是「情緒
    極度低落」），但治療師端只有分數、沒有原因，固定寫死的文字標籤只能反映
    其中一種原因，另一種情況會顯示錯誤的描述（2026-09-06 稽核）。新增這兩欄
    存觸發原因，供前端依原因挑選正確的文字標籤，而不是依分數寫死。"""
    op.execute("ALTER TABLE sessions ADD COLUMN endurance_reason TEXT")
    op.execute("ALTER TABLE sessions ADD COLUMN interaction_reason TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS endurance_reason")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS interaction_reason")
