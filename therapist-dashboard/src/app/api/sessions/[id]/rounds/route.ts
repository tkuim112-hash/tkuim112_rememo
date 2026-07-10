import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;

  const rows = await sql`
    SELECT
      r.id, r.round_number, r.type, r.response_time, r.emotion, r.generated_scene, r.patient_response, r.scene_image,
      re.id AS exchange_id, re.question_number, re.question, re.answer
    FROM rounds r
    LEFT JOIN round_exchanges re ON re.round_id = r.id
    WHERE r.session_id = ${parseInt(id)}
    ORDER BY r.round_number ASC, re.question_number ASC
  `;

  const roundMap = new Map<string, { round: typeof rows[0]; exchanges: typeof rows }>();
  for (const row of rows) {
    const key = row.id.toString();
    if (!roundMap.has(key)) roundMap.set(key, { round: row, exchanges: [] as unknown as typeof rows });
    if (row.exchange_id != null) roundMap.get(key)!.exchanges.push(row);
  }

  return NextResponse.json(Array.from(roundMap.values()).map(({ round: r, exchanges }) => ({
    id: r.id.toString(),
    sessionId: id,
    roundNumber: r.round_number,
    type: (r.type as string) ?? "回合",
    duration: r.response_time ?? 0,
    sceneName: r.generated_scene ?? "",
    content: r.patient_response ?? "",
    emotion: r.emotion ?? "—",
    sceneImage: r.scene_image ? (r.scene_image as string).replace("/media", "") : null,
    exchanges: exchanges.map(e => ({
      questionNumber: e.question_number,
      question: e.question,
      answer: e.answer ?? "",
    })),
  })));
}
