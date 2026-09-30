import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

function mapTodo(t: Record<string, unknown>) {
  return {
    id: (t.id as number).toString(),
    content: t.content,
    isDone: t.is_done,
    priority: t.priority,
    dueDate: t.due_date_str ?? null,
  };
}

function mapNote(n: Record<string, unknown>) {
  return {
    id: (n.id as number).toString(),
    content: n.content,
    authorName: n.author_name ?? "（未署名）",
    createdAt: n.created_at_str,
  };
}

// 「基本資料」分頁的 /api/cases/[id] 沒有回傳照顧者跟待追蹤/備註資料，
// 這幾個只有「追蹤與備註」分頁會用到，獨立成自己的 route 避免每次載入
// 個案詳情頁都多查這幾張表。
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const patientId = parseInt(id);

  const [patient] = await sql`
    SELECT caregiver_name, caregiver_relationship, caregiver_phone
    FROM patients
    WHERE id = ${patientId} AND organization_id = ${session.organizationId}
  `;
  if (!patient) return NextResponse.json({ error: "找不到個案" }, { status: 404 });

  const todos = await sql`
    SELECT id, content, is_done, priority, TO_CHAR(due_date, 'YYYY/MM/DD') AS due_date_str
    FROM patient_todos
    WHERE patient_id = ${patientId}
    ORDER BY is_done ASC, (priority = '高優先') DESC, due_date ASC NULLS LAST, created_at ASC
  `;

  const notes = await sql`
    SELECT n.id, n.content, COALESCE(n.author_name, t.name) AS author_name,
      TO_CHAR(n.created_at, 'YYYY/MM/DD') AS created_at_str
    FROM patient_notes n
    LEFT JOIN therapists t ON t.id = n.author_id
    WHERE n.patient_id = ${patientId}
    ORDER BY n.created_at DESC
  `;

  await logAccess({
    therapistId: session.therapistId,
    patientId,
    action: "view_patient_tracking",
    resource: `patients:${id}:tracking`,
    req,
  });

  return NextResponse.json({
    caregiverName: patient.caregiver_name ?? "",
    caregiverRelationship: patient.caregiver_relationship ?? "",
    caregiverPhone: patient.caregiver_phone ?? "",
    todos: todos.map(mapTodo),
    notes: notes.map(mapNote),
  });
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const patientId = parseInt(id);
  const { caregiverName, caregiverRelationship, caregiverPhone } = await req.json();

  const [updated] = await sql`
    UPDATE patients SET
      caregiver_name         = ${caregiverName ?? ""},
      caregiver_relationship = ${caregiverRelationship ?? ""},
      caregiver_phone        = ${caregiverPhone ?? ""}
    WHERE id = ${patientId} AND organization_id = ${session.organizationId}
    RETURNING id
  `;

  if (!updated) return NextResponse.json({ error: "找不到個案" }, { status: 404 });

  await logAccess({
    therapistId: session.therapistId,
    patientId,
    action: "update_patient_caregiver",
    resource: `patients:${id}:tracking`,
    req,
  });

  return NextResponse.json({
    caregiverName: caregiverName ?? "",
    caregiverRelationship: caregiverRelationship ?? "",
    caregiverPhone: caregiverPhone ?? "",
  });
}
