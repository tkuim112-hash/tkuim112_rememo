import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

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
      total_score          = ${totalScore ?? null},
      emotional_status     = ${emotionalStatus ?? null},
      therapist_note       = ${notes ?? null},
      status                = COALESCE(${status ?? null}, status),
      score_participation  = ${scoreParticipation ?? null},
      score_attention      = ${scoreAttention ?? null},
      score_endurance      = ${scoreEndurance ?? null},
      score_emotion        = ${scoreEmotion ?? null},
      score_interaction    = ${scoreInteraction ?? null}
    WHERE id = ${parseInt(id)}
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
    WHERE s.id = ${parseInt(id)}
  `;

  if (!s) return NextResponse.json({ error: "找不到療程" }, { status: 404 });

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
