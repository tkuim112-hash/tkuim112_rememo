import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

// 這支路由的 id 參數有兩種來源：從個案頁「歷次活動」清單點進來的是 PostgreSQL
// 內部整數 sessions.id；從「活動觀察頁面」（治療師端即時療程）結束時帶過來的
// 是 Python 後端用的 UUID session_uuid（見 LiveSessionView.tsx 的 sessionId）。
// parseInt() 對 UUID 字串只會取到開頭數字（例如 "157fcc28-..." 變成 157），
// 查到完全不相關的 row，導致這條路徑一直悄悄查不到資料/存不進去卻沒有報錯。
// 兩種都要認得，用是否為純數字判斷要查哪個欄位；tableAlias 因為 PUT 的
// UPDATE 語句沒有下 alias、GET 的 SELECT 有下 "s"，兩邊欄位前綴不一樣。
function sessionIdWhereClause(id: string, tableAlias = "") {
  const col = tableAlias ? `${tableAlias}.` : "";
  return /^\d+$/.test(id)
    ? sql`${sql.unsafe(col)}id = ${parseInt(id)}`
    : sql`${sql.unsafe(col)}session_uuid = ${id}`;
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const {
    totalScore, emotionalStatus, notes, status,
    scoreParticipation, scoreAttention, scoreEndurance, scoreEmotion, scoreInteraction,
  } = await req.json();

  const [updated] = await sql`
    UPDATE sessions SET
      total_score          = COALESCE(${totalScore ?? null}, total_score),
      emotional_status     = COALESCE(${emotionalStatus ?? null}, emotional_status),
      therapist_note       = ${notes ?? null},
      status                = COALESCE(${status ?? null}, status),
      score_participation  = COALESCE(${scoreParticipation ?? null}, score_participation),
      score_attention      = COALESCE(${scoreAttention ?? null}, score_attention),
      score_endurance      = COALESCE(${scoreEndurance ?? null}, score_endurance),
      score_emotion        = COALESCE(${scoreEmotion ?? null}, score_emotion),
      score_interaction    = COALESCE(${scoreInteraction ?? null}, score_interaction)
    WHERE ${sessionIdWhereClause(id)}
      AND patient_id IN (SELECT id FROM patients WHERE organization_id = ${session.organizationId})
    RETURNING patient_id
  `;

  await logAccess({
    therapistId: session.therapistId,
    patientId: updated?.patient_id ?? null,
    action: "update_session_assessment",
    resource: `sessions:${id}`,
    req,
  });

  return NextResponse.json({ ok: true });
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;

  const [s] = await sql`
    SELECT
      s.id,
      s.patient_id,
      s.date,
      s.mode,
      s.total_score,
      s.story_summary,
      s.emotional_status,
      s.status,
      s.score_participation,
      s.score_attention,
      s.score_endurance,
      s.score_emotion,
      s.score_interaction,
      s.therapist_note,
      (SELECT COUNT(*)::int FROM sessions s2
        WHERE s2.patient_id = s.patient_id AND s2.id <= s.id) AS session_number,
      (SELECT COUNT(*)::int FROM rounds r WHERE r.session_id = s.id AND (r.type IS NULL OR r.type != '心得')) AS rounds_count,
      (SELECT ROUND(AVG(r.response_time)::numeric, 1)
        FROM rounds r WHERE r.session_id = s.id AND (r.type IS NULL OR r.type != '心得')) AS avg_response_time
    FROM sessions s
    JOIN patients p ON p.id = s.patient_id
    WHERE ${sessionIdWhereClause(id, "s")}
      AND p.organization_id = ${session.organizationId}
  `;

  if (!s) return NextResponse.json({ error: "找不到活動" }, { status: 404 });

  await logAccess({
    therapistId: session.therapistId,
    patientId: s.patient_id,
    action: "view_session",
    resource: `sessions:${id}`,
    req,
  });

  return NextResponse.json({
    id: s.id.toString(),
    caseId: s.patient_id?.toString() ?? "",
    date: s.date ? new Date(s.date).toLocaleDateString("zh-TW") : "",
    sessionNumber: s.session_number,
    status: s.status,
    rounds: s.rounds_count,
    score: s.total_score,
    totalScore: 20,
    averageResponseTime: s.avg_response_time != null ? `${s.avg_response_time} 秒` : "—",
    overallEmotion: s.emotional_status ?? "—",
    storySummary: s.story_summary ?? "",
    rating: s.emotional_status ?? "—",
    mode: s.mode,
    scoreParticipation: s.score_participation ?? null,
    scoreAttention: s.score_attention ?? null,
    scoreEndurance: s.score_endurance ?? null,
    scoreEmotion: s.score_emotion ?? null,
    scoreInteraction: s.score_interaction ?? null,
    therapistNote: s.therapist_note ?? "",
  });
}
