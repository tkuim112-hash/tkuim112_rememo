"""add warmup_card_results table

Revision ID: d8b3c5f27a91
Revises: ce0a3f17ce56
Create Date: 2026-09-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8b3c5f27a91'
down_revision: Union[str, Sequence[str], None] = 'ce0a3f17ce56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """暖身活動評估指標：治療師端的暖身狀態總覽頁原本整頁是前端寫死的假
    資料，這張表存 Unity 端逐幀取樣、卡片完成/跳過時算出來的真實指標
    （關節角度/動作到位程度/畫圓穩定度、平滑度、左右對稱性），(session_id,
    card_order) 加 unique constraint 是為了讓重送用 ON CONFLICT DO UPDATE
    不會重複寫入（比照 rounds 的 (session_id, round_number) 模式）。"""
    op.execute(
        """
        CREATE TABLE warmup_card_results (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
            card_key TEXT NOT NULL,
            card_order INTEGER NOT NULL,
            status TEXT NOT NULL,
            joint_angle_pct INTEGER,
            smoothness_pct INTEGER,
            symmetry_pct INTEGER,
            duration_seconds DOUBLE PRECISION,
            created_at TIMESTAMP DEFAULT now(),
            UNIQUE (session_id, card_order)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS warmup_card_results")
