"""add caregiver fields and patient_todos/patient_notes tables

Revision ID: f4a7c2e9b3d5
Revises: c2d8e6f4b1a7
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a7c2e9b3d5'
down_revision: Union[str, Sequence[str], None] = 'c2d8e6f4b1a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """個案詳情頁新增「追蹤與備註」分頁：主要照顧者聯絡資訊（單純參考用的
    姓名/關係/電話，不是既有 family 那個「緊急聯絡人」自由文字欄位，兩者
    用途不同不合併）、可勾選完成的待追蹤事項清單、跟不分類別的備註時間軸。

    待追蹤事項故意不分類別、備註也故意不分「電話訪談/協調聯繫/直接照護」
    類型——目標客戶機構目前用 Word 手動記錄，介面操作摩擦力不能比 Word
    打字還高，多一個強制分類欄位就會讓機構不想換過來用。

    同時補上 sessions(patient_id, date DESC) 複合索引：個案列表的「近期
    情緒趨勢」判斷（每位長者查最近 3 筆 emotional_status）跟這裡的個案
    詳情頁一樣是用 patient_id 過濾、依 date 排序，原本只有 patient_id
    單欄索引，長者、場次數一多會全表掃描再排序。
    """
    op.execute("ALTER TABLE patients ADD COLUMN caregiver_name TEXT")
    op.execute("ALTER TABLE patients ADD COLUMN caregiver_relationship TEXT")
    op.execute("ALTER TABLE patients ADD COLUMN caregiver_phone TEXT")

    op.execute(
        """
        CREATE TABLE patient_todos (
            id SERIAL PRIMARY KEY,
            patient_id INTEGER REFERENCES patients(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            is_done BOOLEAN NOT NULL DEFAULT false,
            priority TEXT NOT NULL DEFAULT '一般',
            due_date DATE,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_patient_todos_patient_id ON patient_todos(patient_id)")

    op.execute(
        """
        CREATE TABLE patient_notes (
            id SERIAL PRIMARY KEY,
            patient_id INTEGER REFERENCES patients(id) ON DELETE CASCADE,
            author_id INTEGER REFERENCES therapists(id) ON DELETE SET NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_patient_notes_patient_id ON patient_notes(patient_id)")

    op.execute("CREATE INDEX IF NOT EXISTS idx_sessions_patient_id_date ON sessions(patient_id, date DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_sessions_patient_id_date")
    op.execute("DROP TABLE IF EXISTS patient_notes")
    op.execute("DROP TABLE IF EXISTS patient_todos")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS caregiver_phone")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS caregiver_relationship")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS caregiver_name")
