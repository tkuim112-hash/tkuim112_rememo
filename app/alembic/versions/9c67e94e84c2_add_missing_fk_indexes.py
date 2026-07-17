"""add indexes on heavily-queried FK columns

Revision ID: 9c67e94e84c2
Revises: 8f24a753cc9f
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c67e94e84c2'
down_revision: Union[str, Sequence[str], None] = '8f24a753cc9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Postgres 不會自動幫一般外鍵欄位建索引（只有 PK/UNIQUE 會）。
    這三個欄位是治療師後台歷史紀錄/AI建議查詢常用的 WHERE/JOIN 條件，
    資料量還小時感覺不到差異，先補上避免以後變成全表掃描。
    rounds.session_id 已經被 rounds_session_round_uniq (session_id, round_number)
    這個複合唯一索引的最左前綴覆蓋，不用重複建。"""
    op.execute("CREATE INDEX IF NOT EXISTS idx_sessions_patient_id ON sessions(patient_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_patients_organization_id ON patients(organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_round_exchanges_round_id ON round_exchanges(round_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_round_exchanges_round_id")
    op.execute("DROP INDEX IF EXISTS idx_patients_organization_id")
    op.execute("DROP INDEX IF EXISTS idx_sessions_patient_id")
