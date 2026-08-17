import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;

  let sessions;
  try {
    sessions = await sql`
      SELECT
        s.id,
        s.patient_id,
        s.date,
        s.mode,
        s.total_score,
        s.story_summary,
        s.emotional_status,
        s.status,
        (SELECT COUNT(*)::int FROM sessions s2
          WHERE s2.patient_id = s.patient_id AND s2.id <= s.id) AS session_number,
        (SELECT COUNT(*)::int FROM rounds r WHERE r.session_id = s.id AND (r.type IS NULL OR r.type != '心得')) AS rounds_count,
        (SELECT ROUND(AVG(r.response_time)::numeric, 1)
          FROM rounds r WHERE r.session_id = s.id AND (r.type IS NULL OR r.type != '心得')) AS avg_response_time
      FROM sessions s
      JOIN patients p ON p.id = s.patient_id
      WHERE s.patient_id = ${parseInt(id)}
        AND p.organization_id = ${session.organizationId}
      ORDER BY s.id DESC
    `;
  } catch (err) {
    console.error(`[api/cases/${id}/sessions] 查詢失敗:`, err);
    return NextResponse.json({ error: "活動紀錄查詢失敗" }, { status: 500 });
  }

  await logAccess({
    therapistId: session.therapistId,
    patientId: parseInt(id),
    action: "view_patient_sessions",
    resource: `patients:${id}`,
    req,
  });

  return NextResponse.json(sessions.map(s => ({
    id: s.id.toString(),
    caseId: id,
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
  })));
}
