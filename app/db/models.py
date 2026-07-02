"""
SQLAlchemy ORM models，對應 database/m6_db_schema.sql 的表。
"""
from datetime import datetime, date
from sqlalchemy import String, Integer, Text, Date, DateTime, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from db.session import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    contact_phone: Mapped[str | None] = mapped_column(Text)
    password: Mapped[str] = mapped_column(Text, nullable=False)


class Therapist(Base):
    __tablename__ = "therapists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    specialization: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password: Mapped[str] = mapped_column(Text, nullable=False)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    birth_year: Mapped[int] = mapped_column(Integer, nullable=False)
    hometown: Mapped[str | None] = mapped_column(Text)
    occupation: Mapped[str] = mapped_column(Text, nullable=False)
    family: Mapped[str | None] = mapped_column(Text)
    preferences: Mapped[str | None] = mapped_column(Text)
    taboo_words: Mapped[str | None] = mapped_column(Text)
    scene_weights: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TherapySession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_uuid: Mapped[str | None] = mapped_column(Text, unique=True)
    patient_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE")
    )
    therapist_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("therapists.id", ondelete="SET NULL")
    )
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    start_scene: Mapped[str | None] = mapped_column(Text)
    score_participation: Mapped[int | None] = mapped_column(Integer)
    score_attention: Mapped[int | None] = mapped_column(Integer)
    score_endurance: Mapped[int | None] = mapped_column(Integer)
    score_emotion: Mapped[int | None] = mapped_column(Integer)
    score_interaction: Mapped[int | None] = mapped_column(Integer)
    total_score: Mapped[int | None] = mapped_column(Integer)
    therapist_note: Mapped[str | None] = mapped_column(Text)
    story_summary: Mapped[str | None] = mapped_column(Text)


class TherapyRound(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sessions.id", ondelete="CASCADE")
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    response_time: Mapped[float | None] = mapped_column(Float)
    emotion: Mapped[str | None] = mapped_column(Text)
    generated_scene: Mapped[str | None] = mapped_column(Text)
    patient_response: Mapped[str | None] = mapped_column(Text)


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    verification_code: Mapped[str] = mapped_column(String(6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)