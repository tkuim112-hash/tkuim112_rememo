import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

const ALLOWED_PRIORITIES = new Set(["一般", "高優先"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function mapTodo(t: Record<string, unknown>) {
  return {
    id: (t.id as number).toString(),
    content: t.content,
    isDone: t.is_done,
    priority: t.priority,
    dueDate: t.due_date_str ?? null,
  };
}

// 待追蹤事項可以單純勾選完成，也可以編輯內容/優先度/到期日——用同一支
// PATCH，依 body 帶了哪些欄位決定要更新什麼，避免勾選跟編輯各開一支路由。
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; todoId: string }> }
) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id, todoId } = await params;
  const patientId = parseInt(id);
  const body = await req.json();

  const updates: Record<string, unknown> = {};
  if ("isDone" in body) updates.is_done = !!body.isDone;
  if ("content" in body) {
    const content = typeof body.content === "string" ? body.content.trim() : "";
    if (!content) return NextResponse.json({ error: "請填寫待追蹤事項內容" }, { status: 400 });
    updates.content = content;
  }
  if ("priority" in body) {
    updates.priority = ALLOWED_PRIORITIES.has(body.priority) ? body.priority : "一般";
  }
  if ("dueDate" in body) {
    updates.due_date = typeof body.dueDate === "string" && DATE_RE.test(body.dueDate) ? body.dueDate : null;
  }

  if (Object.keys(updates).length === 0) {
    return NextResponse.json({ error: "沒有可更新的欄位" }, { status: 400 });
  }

  const [updated] = await sql`
    UPDATE patient_todos SET ${sql(updates)}
    WHERE id = ${parseInt(todoId)}
      AND patient_id = ${patientId}
      AND patient_id IN (SELECT id FROM patients WHERE organization_id = ${session.organizationId})
    RETURNING id, content, is_done, priority, TO_CHAR(due_date, 'YYYY/MM/DD') AS due_date_str
  `;

  if (!updated) return NextResponse.json({ error: "找不到待追蹤事項" }, { status: 404 });

  await logAccess({
    therapistId: session.therapistId,
    patientId,
    action: "update_patient_todo",
    resource: `patients:${id}:todos:${todoId}`,
    req,
  });

  return NextResponse.json(mapTodo(updated));
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; todoId: string }> }
) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id, todoId } = await params;
  const patientId = parseInt(id);

  const [deleted] = await sql`
    DELETE FROM patient_todos
    WHERE id = ${parseInt(todoId)}
      AND patient_id = ${patientId}
      AND patient_id IN (SELECT id FROM patients WHERE organization_id = ${session.organizationId})
    RETURNING id
  `;

  if (!deleted) return NextResponse.json({ error: "找不到待追蹤事項" }, { status: 404 });

  await logAccess({
    therapistId: session.therapistId,
    patientId,
    action: "delete_patient_todo",
    resource: `patients:${id}:todos:${todoId}`,
    req,
  });

  return NextResponse.json({ ok: true });
}
