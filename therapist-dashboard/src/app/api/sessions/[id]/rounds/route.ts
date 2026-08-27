import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";
import { sessionIdWhereClause } from "@/lib/session-id";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;

  const rows = await sql`
    SELECT
      r.id, r.round_number, r.type, r.response_time, r.emotion, r.generated_scene, r.patient_response, r.scene_image, r.summary,
      s.patient_id,
      re.id AS exchange_id, re.question_number, re.question, re.answer, re.stage
    FROM rounds r
    JOIN sessions s ON s.id = r.session_id
    JOIN patients p ON p.id = s.patient_id
    LEFT JOIN round_exchanges re ON re.round_id = r.id
    WHERE ${sessionIdWhereClause(id, "s")}
      AND p.organization_id = ${session.organizationId}
    ORDER BY r.round_number ASC, re.question_number ASC
  `;

  const roundMap = new Map<string, { round: typeof rows[0]; exchanges: typeof rows }>();
  for (const row of rows) {
    const key = row.id.toString();
    if (!roundMap.has(key)) roundMap.set(key, { round: row, exchanges: [] as unknown as typeof rows });
    if (row.exchange_id != null) roundMap.get(key)!.exchanges.push(row);
  }

  // rounds/round_exchanges 是逐字稿跟原始問答，是最敏感的內容，這裡一定要記錄稽核
  await logAccess({
    therapistId: session.therapistId,
    patientId: rows[0]?.patient_id ?? null,
    action: "view_session_transcript",
    resource: `sessions:${id}`,
    req,
  });

  return NextResponse.json(Array.from(roundMap.values()).map(({ round: r, exchanges }) => ({
    id: r.id.toString(),
    sessionId: id,
    roundNumber: r.round_number,
    type: (r.type as string) ?? "回合",
    duration: r.response_time ?? 0,
    sceneName: r.generated_scene ?? "",
    content: r.patient_response ?? "",
    // LLM 生成的一句話重點摘要（見 app/routers/session.py
    // _generate_round_summary），沒有的話（例如舊資料還沒補、或生成失敗）
    // 前端 fallback 顯示 content 原文。
    summary: r.summary ?? "",
    emotion: r.emotion ?? "—",
    sceneImage: r.scene_image ? (r.scene_image as string).replace("/media", "") : null,
    exchanges: exchanges.map(e => ({
      questionNumber: e.question_number,
      question: e.question,
      answer: e.answer ?? "",
      stage: e.stage ?? null,
    })),
  })));
}
