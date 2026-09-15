"""add emotion reasoning columns to rounds

Revision ID: a2c6e9f14b7d
Revises: f1a3c9e7b2d4
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2c6e9f14b7d'
down_revision: Union[str, Sequence[str], None] = 'f1a3c9e7b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """情緒判斷依據：三維 EMA 分數換算成 0-100%（engagement/happiness/
    agitation_pct）+ 訊號代碼 JSON 陣列（signal_codes），讓治療師端能看到
    AI 為什麼判斷這個回合是某個情緒，不只是看到標籤本身（見
    app/routers/session.py _finalize_round_signals）。"""
    op.execute("ALTER TABLE rounds ADD COLUMN engagement_pct INTEGER")
    op.execute("ALTER TABLE rounds ADD COLUMN happiness_pct INTEGER")
    op.execute("ALTER TABLE rounds ADD COLUMN agitation_pct INTEGER")
    op.execute("ALTER TABLE rounds ADD COLUMN signal_codes TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE rounds DROP COLUMN IF EXISTS engagement_pct")
    op.execute("ALTER TABLE rounds DROP COLUMN IF EXISTS happiness_pct")
    op.execute("ALTER TABLE rounds DROP COLUMN IF EXISTS agitation_pct")
    op.execute("ALTER TABLE rounds DROP COLUMN IF EXISTS signal_codes")
