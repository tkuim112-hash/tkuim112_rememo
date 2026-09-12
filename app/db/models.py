"""
SQLAlchemy ORM models，對應 database/m6_db_schema.sql 的表。
"""
from datetime import datetime, date
from sqlalchemy import String, Integer, Text, Date, DateTime, Float, ForeignKey, func
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
    avatar: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


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
    # 持續力同一個分數可能是「擅自離開」或「情緒極度低落」兩種完全不同的
    # 原因觸發（見 session.py _score_persistence），存下實際原因供前端挑選
    # 正確的文字標籤，不是固定寫死對應分數的單一敘述。
    endurance_reason: Mapped[str | None] = mapped_column(Text)
    score_emotion: Mapped[int | None] = mapped_column(Integer)
    score_interaction: Mapped[int | None] = mapped_column(Integer)
    # 互動頻率同一個分數可能是「完全沒開口，只有動作」或「只回極短的指令式
    # 回答」兩種原因（見 session.py _score_interaction），理由同 endurance_reason。
    interaction_reason: Mapped[str | None] = mapped_column(Text)
    total_score: Mapped[int | None] = mapped_column(Integer)
    emotional_status: Mapped[str | None] = mapped_column(Text)
    therapist_note: Mapped[str | None] = mapped_column(Text)
    story_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="in_progress")
    topic: Mapped[str | None] = mapped_column(Text)


class TherapyRound(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sessions.id", ondelete="CASCADE")
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    response_time: Mapped[float | None] = mapped_column(Float)
    emotion: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    generated_scene: Mapped[str | None] = mapped_column(Text)
    patient_response: Mapped[str | None] = mapped_column(Text)
    scene_image: Mapped[str | None] = mapped_column(Text)
    # 這回合長者發言的一句話重點摘要（LLM 生成，見 session.py
    # _generate_round_summary），給歷史療程列表快速瀏覽用，避免把長者
    # 原話整段堆在畫面上。
    summary: Mapped[str | None] = mapped_column(Text)
    # 情緒判斷依據（見 app/routers/session.py _finalize_round_signals）：
    # engagement/happiness/agitation_pct 是三維 EMA 分數換算成 0-100%，
    # signal_codes 是 JSON 字串陣列（訊號代碼，中文文案在前端
    # therapist-dashboard/src/lib/emotionSignals.ts），供治療師端顯示
    # 「AI 為什麼這樣判斷」。
    engagement_pct: Mapped[int | None] = mapped_column(Integer)
    happiness_pct: Mapped[int | None] = mapped_column(Integer)
    agitation_pct: Mapped[int | None] = mapped_column(Integer)
    signal_codes: Mapped[str | None] = mapped_column(Text)


class WarmupCardResult(Base):
    """暖身活動每張動作卡的評估結果（見 app/routers/session.py
    warmup_card_result）：關節角度/動作到位程度/畫圓穩定度、平滑度
    （Log Dimensionless Jerk）、左右對稱性（Symmetry Index）都是 Unity
    端在卡片進行期間逐幀取樣、卡片完成/跳過那一刻換算成 0-100 送過來的，
    這裡只負責存最終結果，不做任何計算。"""
    __tablename__ = "warmup_card_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sessions.id", ondelete="CASCADE")
    )
    card_key: Mapped[str] = mapped_column(Text, nullable=False)
    card_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # 'completed'（Kinect 自動偵測完成）/'skipped'（治療師跳過）/
    # 'manual'（治療師手動標記完成）。skipped 的話下面四個指標欄位都是 null。
    status: Mapped[str] = mapped_column(Text, nullable=False)
    joint_angle_pct: Mapped[int | None] = mapped_column(Integer)
    smoothness_pct: Mapped[int | None] = mapped_column(Integer)
    symmetry_pct: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class RoundExchange(Base):
    __tablename__ = "round_exchanges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("rounds.id", ondelete="CASCADE")
    )
    question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    # 'pre_image'：生圖前的引導問題（見 orchestrator.py 的 pre_image_q1/pre_image_q2）；
    # 其餘一律 NULL，代表圖片生成後才問的一般問題。只有第一回合才會有 pre_image。
    stage: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """存取稽核紀錄：誰（therapist_id）、什麼時候（created_at）、看了哪個病患的資料（patient_id）。"""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    therapist_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("therapists.id", ondelete="SET NULL")
    )
    patient_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    verification_code: Mapped[str] = mapped_column(String(6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)