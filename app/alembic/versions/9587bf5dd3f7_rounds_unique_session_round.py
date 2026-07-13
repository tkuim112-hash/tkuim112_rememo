"""rounds unique (session_id, round_number)

Revision ID: 9587bf5dd3f7
Revises: 040f34f838f8
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9587bf5dd3f7'
down_revision: Union[str, Sequence[str], None] = '040f34f838f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """防止併發請求對同一回合建立重複的 rounds 列（_get_or_create_round 先前是
    SELECT 再 INSERT，非原子操作）。session_id 為 NULL 時 Postgres 視為互不相等，
    不受此約束影響。"""
    op.execute("""
        ALTER TABLE rounds
        ADD CONSTRAINT rounds_session_round_uniq UNIQUE (session_id, round_number)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE rounds DROP CONSTRAINT IF EXISTS rounds_session_round_uniq")
