import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";
import { sessionIdWhereClause } from "@/lib/session-id";

// 暖身狀態總覽頁用：查這場療程真正抽到的暖身動作卡跟評估結果（見
// app/routers/session.py session_warmup_card_result，Unity 卡片完成/跳過
// 時寫進 warmup_card_results 表）。舊 session（這個功能上線前跑過暖身的）
// 查不到任何列，回傳空陣列，前端顯示空狀態。
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;

  const rows = await sql`
    SELECT
      w.card_key, w.card_order, w.status,
      w.joint_angle_pct, w.smoothness_pct, w.symmetry_pct, w.duration_seconds,
      s.patient_id
    FROM warmup_card_results w
    JOIN sessions s ON s.id = w.session_id
    JOIN patients p ON p.id = s.patient_id
    WHERE ${sessionIdWhereClause(id, "s")}
      AND p.organization_id = ${session.organizationId}
    ORDER BY w.card_order ASC
  `;

  if (rows.length > 0) {
    await logAccess({
      therapistId: session.therapistId,
      patientId: rows[0].patient_id ?? null,
      action: "view_warmup_summary",
      resource: `sessions:${id}`,
      req,
    });
  }

  const cards = rows.map((r) => ({
    cardKey: r.card_key as string,
    cardOrder: r.card_order as number,
    status: r.status as "completed" | "skipped" | "manual",
    jointAnglePct: r.joint_angle_pct as number | null,
    smoothnessPct: r.smoothness_pct as number | null,
    symmetryPct: r.symmetry_pct as number | null,
    durationSeconds: r.duration_seconds as number | null,
  }));

  // 聚合值：只拿有真的做過動作（非跳過）的卡片算平均，跳過的卡片沒有指標
  // 可以平均，混進去會拉低分母卻沒有對應的分子。
  const measured = cards.filter((c) => c.status !== "skipped");
  const avg = (values: (number | null)[]) => {
    const nums = values.filter((v): v is number => v != null);
    return nums.length > 0 ? Math.round(nums.reduce((a, b) => a + b, 0) / nums.length) : null;
  };
  const avgDurationSeconds = avg(measured.map((c) => c.durationSeconds));

  const summary = {
    jointAnglePct: avg(measured.map((c) => c.jointAnglePct)),
    smoothnessPct: avg(measured.map((c) => c.smoothnessPct)),
    symmetryPct: avg(measured.map((c) => c.symmetryPct)),
    avgDurationSeconds,
    skippedCount: cards.filter((c) => c.status === "skipped").length,
    manualCount: cards.filter((c) => c.status === "manual").length,
  };

  return NextResponse.json({ cards, summary });
}
