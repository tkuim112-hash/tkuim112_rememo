import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";
import { sessionIdWhereClause } from "@/lib/session-id";

// 這支路由的 id 參數有兩種來源：從個案頁「歷次活動」清單點進來的是 PostgreSQL
// 內部整數 sessions.id；從「活動觀察頁面」（治療師端即時療程）結束時帶過來的
// 是 Python 後端用的 UUID session_uuid（見 LiveSessionView.tsx 的 sessionId）。
// 判斷邏輯見 @/lib/session-id.ts——2026-08-27稽核：/rounds 這支路由當初沒有
// 一併套用同一套判斷，一直是直接 parseInt(id)，UUID 字串查到不相關的
// session_id，導致治療師網頁從活動觀察頁結束後看歷史逐字稿整頁是空的，已經
// 抽成共用 function 讓兩支路由用同一份，不再各自維護。

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const {
    totalScore, emotionalStatus, notes, status,
    scoreParticipation, scoreAttention, scoreEndurance, scoreEmotion, scoreInteraction,
  } = await req.json();

  // endurance_reason／interaction_reason 是後端自動評分時附帶的觸發原因
  // （例如持續力1分是「擅自離開」還是「情緒極度低落」），只在對應分數沒變
  // 時保留——治療師手動把分數改成別的值，代表這是人工判斷，原本自動算出
  // 的原因已經不適用，要清空，不然畫面會顯示一個跟新分數對不上的舊原因
  // （2026-09-06 稽核：這是這兩欄新增時就要處理的既有風險，不是事後才發現）。
  const [updated] = await sql`
    UPDATE sessions SET
      total_score          = COALESCE(${totalScore ?? null}, total_score),
      emotional_status     = COALESCE(${emotionalStatus ?? null}, emotional_status),
      therapist_note       = ${notes ?? null},
      status                = COALESCE(${status ?? null}, status),
      score_participation  = COALESCE(${scoreParticipation ?? null}, score_participation),
      score_attention      = COALESCE(${scoreAttention ?? null}, score_attention),
      score_endurance      = COALESCE(${scoreEndurance ?? null}, score_endurance),
      endurance_reason     = CASE
        WHEN ${scoreEndurance ?? null}::int IS NOT NULL
             AND ${scoreEndurance ?? null}::int IS DISTINCT FROM score_endurance
        THEN NULL ELSE endurance_reason END,
      score_emotion        = COALESCE(${scoreEmotion ?? null}, score_emotion),
      score_interaction    = COALESCE(${scoreInteraction ?? null}, score_interaction),
      interaction_reason   = CASE
        WHEN ${scoreInteraction ?? null}::int IS NOT NULL
             AND ${scoreInteraction ?? null}::int IS DISTINCT FROM score_interaction
        THEN NULL ELSE interaction_reason END
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
      s.endurance_reason,
      s.score_emotion,
      s.score_interaction,
      s.interaction_reason,
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
    enduranceReason: s.endurance_reason ?? null,
    scoreEmotion: s.score_emotion ?? null,
    scoreInteraction: s.score_interaction ?? null,
    interactionReason: s.interaction_reason ?? null,
    therapistNote: s.therapist_note ?? "",
  });
}
