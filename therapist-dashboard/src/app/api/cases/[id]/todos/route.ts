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

const ALLOWED_PRIORITIES = new Set(["一般", "高優先"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// 新增待追蹤事項：優先度只開放「一般」／「高優先」兩檔（不是任意分類），
// 到期日是選填的簡單日期（HTML date input 送出的 YYYY-MM-DD），格式不對
// 一律當沒填，不因為輸入怪字串就整筆新增失敗。
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const patientId = parseInt(id);
  const { content, priority, dueDate } = await req.json();

  if (!content || !(content as string).trim()) {
    return NextResponse.json({ error: "請填寫待追蹤事項內容" }, { status: 400 });
  }
  const finalPriority = ALLOWED_PRIORITIES.has(priority) ? priority : "一般";
  const finalDueDate = typeof dueDate === "string" && DATE_RE.test(dueDate) ? dueDate : null;

  const [patient] = await sql`
    SELECT id FROM patients WHERE id = ${patientId} AND organization_id = ${session.organizationId}
  `;
  if (!patient) return NextResponse.json({ error: "找不到個案" }, { status: 404 });

  const [todo] = await sql`
    INSERT INTO patient_todos (patient_id, content, priority, due_date)
    VALUES (${patientId}, ${(content as string).trim()}, ${finalPriority}, ${finalDueDate})
    RETURNING id, content, is_done, priority, TO_CHAR(due_date, 'YYYY/MM/DD') AS due_date_str
  `;

  await logAccess({
    therapistId: session.therapistId,
    patientId,
    action: "create_patient_todo",
    resource: `patients:${id}:todos:${todo.id}`,
    req,
  });

  return NextResponse.json(mapTodo(todo), { status: 201 });
}
