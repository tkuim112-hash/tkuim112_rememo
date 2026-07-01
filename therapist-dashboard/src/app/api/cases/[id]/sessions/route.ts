import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;

  const sessions = await sql`
    SELECT
      s.id,
      s.patient_id,
      s.date,
      s.mode,
      s.total_score,
      s.story_summary,
      s.emotional_status,
      (SELECT COUNT(*)::int FROM sessions s2
        WHERE s2.patient_id = s.patient_id AND s2.id <= s.id) AS session_number,
      (SELECT COUNT(*)::int FROM rounds r WHERE r.session_id = s.id) AS rounds_count,
      (SELECT ROUND(AVG(r.response_time)::numeric, 1)
        FROM rounds r WHERE r.session_id = s.id) AS avg_response_time,
      (SELECT r.emotion FROM rounds r WHERE r.session_id = s.id
        GROUP BY r.emotion ORDER BY COUNT(*) DESC LIMIT 1) AS overall_emotion
    FROM sessions s
    WHERE s.patient_id = ${parseInt(id)}
    ORDER BY s.id DESC
  `;

  return NextResponse.json(sessions.map(s => ({
    id: s.id.toString(),
    caseId: id,
    date: s.date ? new Date(s.date).toLocaleDateString("zh-TW") : "",
    sessionNumber: s.session_number,
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
