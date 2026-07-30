"""add status column to sessions

Revision ID: 8e5328a789a3
Revises: 9587bf5dd3f7
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e5328a789a3'
down_revision: Union[str, Sequence[str], None] = '9587bf5dd3f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """讓「儲存並完成」跟「稍後填寫」寫入不同狀態，療程列表才能分辨是否已完成評分。"""
    op.execute("""
        ALTER TABLE sessions
        ADD COLUMN status TEXT NOT NULL DEFAULT 'in_progress'
    """)
    op.execute("""
        ALTER TABLE sessions
        ADD CONSTRAINT sessions_status_check
        CHECK (status IN ('scheduled', 'in_progress', 'completed'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_status_check")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS status")
