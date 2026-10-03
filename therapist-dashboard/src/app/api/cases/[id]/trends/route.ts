import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

// 趨勢分析頁用：回傳這位個案所有已完成場次的暖身／懷舊指標，依場次時間由舊到
// 新排序（折線圖需要），每個欄位都是能直接拿去畫圖的數字。只納入 completed
// 的場次——進行中/排程中的場次分數可能是 null 或還沒跑完，混進趨勢線會變成
// 誤導的資料點。
//
// emotionalStatus 直接拿 sessions.emotional_status（適當/亢奮/焦躁/低落），
// 跟 /api/cases/[id]/sessions 的 rating/overallEmotion 是同一個欄位——情緒
// 本來就是系統既有的分類標籤，不是連續數值，不要自己另外算一個 0-100% 的
// 綜合分數出來，會跟其他頁面（歷次活動、個案列表）顯示的分類對不起來。
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const patientId = parseInt(id);

  let rows;
  try {
    rows = await sql`
      SELECT
        s.id,
        s.date,
        s.total_score,
        s.emotional_status,
        (SELECT COUNT(*)::int FROM sessions s2
          WHERE s2.patient_id = s.patient_id AND s2.id <= s.id) AS session_number,
        (SELECT ROUND(AVG(r.response_time)::numeric, 1)
          FROM rounds r WHERE r.session_id = s.id AND (r.type IS NULL OR r.type != '心得')) AS avg_response_time,
        (SELECT ROUND(AVG(w.joint_angle_pct)::numeric, 0)
          FROM warmup_card_results w WHERE w.session_id = s.id AND w.status != 'skipped') AS joint_angle_pct,
        (SELECT ROUND(AVG(w.smoothness_pct)::numeric, 0)
          FROM warmup_card_results w WHERE w.session_id = s.id AND w.status != 'skipped') AS smoothness_pct,
        (SELECT ROUND(AVG(w.symmetry_pct)::numeric, 0)
          FROM warmup_card_results w WHERE w.session_id = s.id AND w.status != 'skipped') AS symmetry_pct,
        (SELECT ROUND(AVG(w.duration_seconds)::numeric, 1)
          FROM warmup_card_results w WHERE w.session_id = s.id AND w.status != 'skipped') AS avg_duration_seconds
      FROM sessions s
      JOIN patients p ON p.id = s.patient_id
      WHERE s.patient_id = ${patientId}
        AND p.organization_id = ${session.organizationId}
        AND s.status = 'completed'
      ORDER BY s.id ASC
    `;
  } catch (err) {
    console.error(`[api/cases/${id}/trends] 查詢失敗:`, err);
    return NextResponse.json({ error: "趨勢資料查詢失敗" }, { status: 500 });
  }

  if (rows.length > 0) {
    await logAccess({
      therapistId: session.therapistId,
      patientId,
      action: "view_patient_trends",
      resource: `patients:${id}`,
      req,
    });
  }

  // postgres 套件對 ::numeric 轉型的欄位預設回傳字串（避免浮點誤差），不會自動
  // 變成 JS number——這裡每一個來自 ROUND(...)::numeric 的欄位都要手動 Number()
  // 轉換，漏轉的欄位傳到前端呼叫 .toFixed() 之類的數字方法就會整個炸掉（2026-10-04
  // 現場回報：Uncaught TypeError: e.toFixed is not a function，根因就是這裡）。
  const toNum = (v: unknown): number | null => (v == null ? null : Number(v));

  const points = rows.map((r) => ({
    id: r.id.toString(),
    date: r.date ? new Date(r.date).toISOString().slice(0, 10) : "",
    dateDisplay: r.date ? new Date(r.date).toLocaleDateString("zh-TW") : "",
    sessionNumber: r.session_number as number,
    score: toNum(r.total_score),
    emotionalStatus: (r.emotional_status as string | null) ?? null,
    avgResponseTime: toNum(r.avg_response_time),
    jointAnglePct: toNum(r.joint_angle_pct),
    smoothnessPct: toNum(r.smoothness_pct),
    symmetryPct: toNum(r.symmetry_pct),
    avgDurationSeconds: toNum(r.avg_duration_seconds),
  }));

  return NextResponse.json(points);
}
