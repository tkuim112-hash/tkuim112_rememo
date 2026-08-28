"""round_exchanges unique (round_id, question_number)

Revision ID: f1a3c9e7b2d4
Revises: d4f8e2a6c1b9
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a3c9e7b2d4'
down_revision: Union[str, Sequence[str], None] = 'd4f8e2a6c1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """防止併發請求（例如前端逾時重試）對同一(round, 題號)建立重複的
    round_exchanges 列——session.py _save_round_exchange 原本是 SELECT
    再 INSERT，非原子操作，兩個幾乎同時抵達的請求可能都通過「查無此題號」
    的檢查、各自插入一列，讓 _fill_round_exchange_answer 之後查到多筆
    （2026-08 稽核時在正式資料裡也真的抓到一組重複：round_id=718,
    question_number=2）。跟 rounds(session_id, round_number) 那條唯一約束
    （見 9587bf5dd3f7）同一套修法：先清掉既有重複（保留 id 較大、也就是
    較新的一列——兩列都沒有 answer 時不會遺失任何資料），再加上唯一約束，
    後續改用 ON CONFLICT DO NOTHING 走真正原子的 upsert。
    """
    op.execute("""
        DELETE FROM round_exchanges a
        USING round_exchanges b
        WHERE a.round_id = b.round_id
          AND a.question_number = b.question_number
          AND a.id < b.id
    """)
    op.execute("""
        ALTER TABLE round_exchanges
        ADD CONSTRAINT round_exchanges_round_question_uniq UNIQUE (round_id, question_number)
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE round_exchanges DROP CONSTRAINT IF EXISTS round_exchanges_round_question_uniq"
    )
