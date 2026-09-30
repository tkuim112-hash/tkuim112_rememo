import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

// 新增備註：author_id 固定是目前登入的治療師（稽核用，記錄誰真的建立了
// 這筆備註），但顯示用的署名（author_name）可自由填寫——備註常常是記錄
// 「家屬轉述」的內容（例如電話裡女兒說的觀察），署名應該是「家屬（女兒）」
// 這種來源說明，不一定是輸入這筆備註的工作人員本人。內容故意不分類別
// （電話訪談/協調聯繫/直接照護），理由見「追蹤與備註」分頁的說明。
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const patientId = parseInt(id);
  const { content, authorName } = await req.json();

  if (!content || !(content as string).trim()) {
    return NextResponse.json({ error: "請填寫備註內容" }, { status: 400 });
  }

  const [patient] = await sql`
    SELECT id FROM patients WHERE id = ${patientId} AND organization_id = ${session.organizationId}
  `;
  if (!patient) return NextResponse.json({ error: "找不到個案" }, { status: 404 });

  const [loginTherapist] = await sql`SELECT name FROM therapists WHERE id = ${session.therapistId}`;
  const finalAuthorName =
    typeof authorName === "string" && authorName.trim() ? authorName.trim() : loginTherapist?.name ?? "";

  const [note] = await sql`
    INSERT INTO patient_notes (patient_id, author_id, author_name, content)
    VALUES (${patientId}, ${session.therapistId}, ${finalAuthorName}, ${(content as string).trim()})
    RETURNING id, content, TO_CHAR(created_at, 'YYYY/MM/DD') AS created_at_str
  `;

  await logAccess({
    therapistId: session.therapistId,
    patientId,
    action: "create_patient_note",
    resource: `patients:${id}:notes:${note.id}`,
    req,
  });

  return NextResponse.json(
    {
      id: (note.id as number).toString(),
      content: note.content,
      authorName: finalAuthorName,
      createdAt: note.created_at_str,
    },
    { status: 201 }
  );
}
