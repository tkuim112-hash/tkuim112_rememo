"""add author_name to patient_notes

Revision ID: a3d9f1c5e7b2
Revises: f4a7c2e9b3d5
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d9f1c5e7b2'
down_revision: Union[str, Sequence[str], None] = 'f4a7c2e9b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """備註的「作者」欄位原本直接顯示 author_id 對應的治療師姓名，但實務上
    備註常常是記錄「家屬轉述」的內容（例如電話裡女兒說的觀察），署名應該是
    「家屬（女兒）」這種來源說明，不是實際登入輸入這筆備註的工作人員本人。
    author_id 保留不動，繼續記錄真正登入建立這筆備註的帳號（稽核用途）；
    另外新增 author_name 讓使用者自由填寫顯示用的署名，預設帶登入者姓名、
    但可以改掉。
    """
    op.execute("ALTER TABLE patient_notes ADD COLUMN author_name TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE patient_notes DROP COLUMN IF EXISTS author_name")
