"""add topic column to sessions

Revision ID: 8f24a753cc9f
Revises: 8e5328a789a3
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f24a753cc9f'
down_revision: Union[str, Sequence[str], None] = '8e5328a789a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """存下每場療程實際使用的今日主題，讓「AI 建議」能根據過去療程的反應
    （rounds.emotion / response_time）挑出歷史表現最好的主題，而不是寫死的字串。"""
    op.execute("ALTER TABLE sessions ADD COLUMN topic TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS topic")
