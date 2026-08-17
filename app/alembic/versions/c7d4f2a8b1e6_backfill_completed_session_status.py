"""backfill status=completed for old sessions that already have a score

Revision ID: c7d4f2a8b1e6
Revises: b3f7a1d5e9c2
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d4f2a8b1e6'
down_revision: Union[str, Sequence[str], None] = 'b3f7a1d5e9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """8e5328a789a3 加 status 欄位時用 DEFAULT 'in_progress'，導致當時既有、
    早已做完的舊療程全部被灌成 in_progress（新完成的療程之後才由
    _compute_and_save_assessment 正確寫回 completed）。這裡把「已經有
    total_score（代表評分計算跑過）但 status 還卡在 in_progress」的舊資料
    一次補回 completed，讓治療師頁面「已完成 vs 進行中」的判斷恢復正確。"""
    op.execute("""
        UPDATE sessions
        SET status = 'completed'
        WHERE status = 'in_progress' AND total_score IS NOT NULL
    """)


def downgrade() -> None:
    """資料回補無法可靠復原（無法分辨哪些是這次回補、哪些本來就是
    completed），downgrade 不做任何事。"""
    pass
