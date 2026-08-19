"""add summary column to rounds

Revision ID: d4f8e2a6c1b9
Revises: c7d4f2a8b1e6
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f8e2a6c1b9'
down_revision: Union[str, Sequence[str], None] = 'c7d4f2a8b1e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """存每個回合長者發言的一句話重點摘要（LLM 生成），讓歷史療程列表能顯示
    摘要而不是把長者原話整段堆在畫面上（見 app/routers/session.py
    _generate_round_summary）。"""
    op.execute("ALTER TABLE rounds ADD COLUMN summary TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE rounds DROP COLUMN IF EXISTS summary")
