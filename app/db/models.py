"""
SQLAlchemy ORM models，對應 database/m6_db_schema.sql 的表。
"""
from datetime import datetime, date
from sqlalchemy import String, Integer, Text, Date, DateTime, Float, Boolean, ForeignKey, func
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
    # 機構管理者：能透過 /organization/members 新增、移除同機構的其他治療師帳號。
    is_org_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # 從未登入過就是 NULL，供成員列表顯示「尚未登入」提醒管理者告知對方帳密。
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 治療師後台登入（therapist-dashboard api/auth/login/route.ts）的失敗次數鎖定，
    # 比照 app/routers/auth.py 那組給 Unity 登入用的 Redis 版鎖定邏輯——Next.js
    # 那邊沒有 Redis 可用，改用這兩個欄位在 Postgres 做一樣的事。
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)


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
    # 主要照顧者聯絡資訊：純參考用（姓名/關係/電話），跟 family 那個舊有的
    # 「緊急聯絡人」自由文字欄位是兩回事，故意分開存，不互相取代。
    caregiver_name: Mapped[str | None] = mapped_column(Text)
    caregiver_relationship: Mapped[str | None] = mapped_column(Text)
    caregiver_phone: Mapped[str | None] = mapped_column(Text)
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


class PatientTodo(Base):
    """個案詳情頁「追蹤與備註」分頁的待追蹤事項：故意不分類別，單純文字＋
    勾選完成，比照目標客戶機構原本用 Word 手動記錄的操作習慣。"""
    __tablename__ = "patient_todos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE")
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    priority: Mapped[str] = mapped_column(Text, nullable=False, server_default="一般")
    due_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class PatientNote(Base):
    """個案詳情頁「追蹤與備註」分頁的備註時間軸：單一不分類別的自由文字，
    理由同 PatientTodo——強制選分類會讓介面比 Word 打字還麻煩。"""
    __tablename__ = "patient_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE")
    )
    author_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("therapists.id", ondelete="SET NULL")
    )
    # 顯示用的署名，使用者可自由填寫（例如「家屬（女兒）」轉述的內容），
    # 不一定等於 author_id 對應的實際登入帳號——author_id 只負責稽核追蹤
    # 誰真的登入建立了這筆備註。
    author_name: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
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
    # 猜錯次數：verify-code／reset-password 用來擋暴力猜 6 碼驗證碼
    # （100 萬種組合），見那兩支 route.ts 的說明。
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")