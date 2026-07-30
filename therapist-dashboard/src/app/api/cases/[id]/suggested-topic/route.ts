import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;

  const [best] = await sql`
    SELECT
      s.topic,
      COUNT(*) FILTER (WHERE r.emotion = '適當')::float / COUNT(*) AS calm_rate,
      AVG(r.response_time) AS avg_response_time
    FROM sessions s
    JOIN patients p ON p.id = s.patient_id
    JOIN rounds r ON r.session_id = s.id
    WHERE s.patient_id = ${parseInt(id)}
      AND p.organization_id = ${session.organizationId}
      AND s.topic IS NOT NULL
      AND (r.type IS NULL OR r.type != '心得')
      AND r.emotion IS NOT NULL AND r.emotion != ''
    GROUP BY s.id, s.topic
    ORDER BY calm_rate DESC, avg_response_time ASC NULLS LAST
    LIMIT 1
  `;

  return NextResponse.json({ topic: best?.topic ?? null });
}
