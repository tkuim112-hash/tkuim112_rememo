import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

// 編輯備註：author_id 不變（還是原本登入建立這筆備註的帳號，稽核用），
// 只有顯示用的 author_name 跟內容可以改，理由同新增備註的說明。
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; noteId: string }> }
) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id, noteId } = await params;
  const patientId = parseInt(id);
  const { content, authorName } = await req.json();

  if (!content || !(content as string).trim()) {
    return NextResponse.json({ error: "請填寫備註內容" }, { status: 400 });
  }
  const finalAuthorName = typeof authorName === "string" ? authorName.trim() : "";

  const [updated] = await sql`
    UPDATE patient_notes SET
      content     = ${(content as string).trim()},
      author_name = ${finalAuthorName || null}
    WHERE id = ${parseInt(noteId)}
      AND patient_id = ${patientId}
      AND patient_id IN (SELECT id FROM patients WHERE organization_id = ${session.organizationId})
    RETURNING id, content, author_name, TO_CHAR(created_at, 'YYYY/MM/DD') AS created_at_str
  `;

  if (!updated) return NextResponse.json({ error: "找不到備註" }, { status: 404 });

  await logAccess({
    therapistId: session.therapistId,
    patientId,
    action: "update_patient_note",
    resource: `patients:${id}:notes:${noteId}`,
    req,
  });

  return NextResponse.json({
    id: (updated.id as number).toString(),
    content: updated.content,
    authorName: updated.author_name ?? "",
    createdAt: updated.created_at_str,
  });
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; noteId: string }> }
) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id, noteId } = await params;
  const patientId = parseInt(id);

  const [deleted] = await sql`
    DELETE FROM patient_notes
    WHERE id = ${parseInt(noteId)}
      AND patient_id = ${patientId}
      AND patient_id IN (SELECT id FROM patients WHERE organization_id = ${session.organizationId})
    RETURNING id
  `;

  if (!deleted) return NextResponse.json({ error: "找不到備註" }, { status: 404 });

  await logAccess({
    therapistId: session.therapistId,
    patientId,
    action: "delete_patient_note",
    resource: `patients:${id}:notes:${noteId}`,
    req,
  });

  return NextResponse.json({ ok: true });
}
