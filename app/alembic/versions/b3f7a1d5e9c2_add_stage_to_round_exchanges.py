"""add stage column to round_exchanges

Revision ID: b3f7a1d5e9c2
Revises: 9c67e94e84c2
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f7a1d5e9c2'
down_revision: Union[str, Sequence[str], None] = '9c67e94e84c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """標記每筆 round_exchanges 是不是生圖前問的引導問題（見 orchestrator.py
    的 pre_image_q1/pre_image_q2），讓歷史療程檢視頁能區分第一回合的問題
    是生圖前還是生圖後問的。"""
    op.execute("ALTER TABLE round_exchanges ADD COLUMN stage TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE round_exchanges DROP COLUMN IF EXISTS stage")
