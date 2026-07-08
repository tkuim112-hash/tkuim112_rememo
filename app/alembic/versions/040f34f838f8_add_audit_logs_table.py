"""add audit_logs table

Revision ID: 040f34f838f8
Revises: a1a906a2e072
Create Date: 2026-07-08 08:59:38.711001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '040f34f838f8'
down_revision: Union[str, Sequence[str], None] = 'a1a906a2e072'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            therapist_id INTEGER REFERENCES therapists(id) ON DELETE SET NULL,
            patient_id INTEGER REFERENCES patients(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            resource TEXT,
            ip_address TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_patient_id ON audit_logs(patient_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_therapist_id ON audit_logs(therapist_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_logs")
