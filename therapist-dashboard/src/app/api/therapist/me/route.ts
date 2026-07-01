import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession, deleteSession } from "@/lib/session";

export async function GET() {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const [therapist] = await sql`
    SELECT t.id, t.name, t.email, o.name AS institution
    FROM therapists t
    JOIN organizations o ON o.id = t.organization_id
    WHERE t.id = ${session.therapistId}
  `;

  if (!therapist) return NextResponse.json({ error: "找不到使用者" }, { status: 404 });

  return NextResponse.json({
    id: therapist.id,
    name: therapist.name,
    email: therapist.email,
    institution: therapist.institution,
  });
}

export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { name, institution, email } = await req.json();
  if (!name || !institution || !email) {
    return NextResponse.json({ error: "請填寫所有欄位" }, { status: 400 });
  }

  await sql`UPDATE therapists SET name = ${name}, email = ${email} WHERE id = ${session.therapistId}`;
  await sql`UPDATE organizations SET name = ${institution} WHERE id = ${session.organizationId}`;

  return NextResponse.json({ ok: true });
}

export async function DELETE() {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  await sql`DELETE FROM therapists WHERE id = ${session.therapistId}`;
  await deleteSession();

  return NextResponse.json({ ok: true });
}
