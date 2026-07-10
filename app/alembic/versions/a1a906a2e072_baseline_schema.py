"""baseline schema

Revision ID: a1a906a2e072
Revises:
Create Date: 2026-07-08 07:27:13.587353

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1a906a2e072'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建立 database/m6_db_schema.sql 描述的完整表結構（給全新、空白的資料庫用）。

    這支 migration 是採用 Alembic 當下，把既有 schema 原封不動地收進版本控管的起點，
    之後任何欄位異動都應該用 `alembic revision --autogenerate` 產生新的 migration，
    不要再回頭改這支檔案。asyncpg 的 prepared statement 不支援一次執行多條指令，
    所以每個 CREATE TABLE 要分開呼叫 op.execute()。
    """
    op.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            address TEXT,
            contact_phone TEXT,
            password TEXT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS therapists (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            specialization TEXT,
            password TEXT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_codes (
            id SERIAL PRIMARY KEY,
            email TEXT NOT NULL,
            verification_code VARCHAR(6) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            birth_year INTEGER NOT NULL,
            hometown TEXT,
            occupation TEXT NOT NULL,
            family TEXT,
            preferences TEXT,
            taboo_words TEXT,
            scene_weights TEXT,
            avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id SERIAL PRIMARY KEY,
            session_uuid TEXT UNIQUE,
            patient_id INTEGER REFERENCES patients(id) ON DELETE CASCADE,
            therapist_id INTEGER REFERENCES therapists(id) ON DELETE SET NULL,
            organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            mode TEXT NOT NULL,
            start_scene TEXT,
            score_participation INTEGER,
            score_attention INTEGER,
            score_endurance INTEGER,
            score_emotion INTEGER,
            score_interaction INTEGER,
            total_score INTEGER,
            emotional_status TEXT,
            therapist_note TEXT,
            story_summary TEXT
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
            round_number INTEGER NOT NULL,
            response_time FLOAT,
            emotion TEXT,
            type TEXT,
            generated_scene TEXT,
            patient_response TEXT,
            scene_image TEXT
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS round_exchanges (
            id SERIAL PRIMARY KEY,
            round_id INTEGER REFERENCES rounds(id) ON DELETE CASCADE,
            question_number INTEGER NOT NULL,
            question TEXT,
            answer TEXT
        )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS round_exchanges")
    op.execute("DROP TABLE IF EXISTS rounds")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS patients")
    op.execute("DROP TABLE IF EXISTS password_reset_codes")
    op.execute("DROP TABLE IF EXISTS therapists")
    op.execute("DROP TABLE IF EXISTS organizations")
