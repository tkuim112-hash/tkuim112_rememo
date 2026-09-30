import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession, deleteSession } from "@/lib/session";

export async function GET() {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const [therapist] = await sql`
    SELECT t.id, t.name, t.email, t.is_org_admin, o.name AS institution
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
    isOrgAdmin: therapist.is_org_admin,
  });
}

export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { name, institution, email } = await req.json();
  if (!name || !institution || !email) {
    return NextResponse.json({ error: "請填寫所有欄位" }, { status: 400 });
  }

  // 機構名稱現在是多位治療師共用的資料，只有管理者能改——不然任何一位
  // 成員在自己的帳號頁動這個欄位，會連帶把同機構其他人看到的機構名稱都改掉。
  // is_org_admin 現查 DB，不信任前端傳來的值。
  const [therapist] = await sql`SELECT is_org_admin FROM therapists WHERE id = ${session.therapistId}`;
  if (!therapist) return NextResponse.json({ error: "找不到使用者" }, { status: 404 });

  await sql`UPDATE therapists SET name = ${name}, email = ${email} WHERE id = ${session.therapistId}`;
  if (therapist.is_org_admin) {
    await sql`UPDATE organizations SET name = ${institution} WHERE id = ${session.organizationId}`;
  }

  return NextResponse.json({ ok: true });
}

export async function DELETE() {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  // 機構現在可能有多位治療師（見機構成員管理頁），如果刪帳號的人是機構唯一的
  // 管理者、機構底下還有其他人，刪掉自己會讓整個機構變成沒有人能再新增／
  // 移除成員或指派管理者的死局——這裡先擋下來，請他指派別人接手管理者再刪除。
  const [therapist] = await sql`SELECT is_org_admin FROM therapists WHERE id = ${session.therapistId}`;
  if (!therapist) return NextResponse.json({ error: "找不到使用者" }, { status: 404 });

  if (therapist.is_org_admin) {
    const [stats] = await sql`
      SELECT
        count(*) FILTER (WHERE id != ${session.therapistId}) AS other_members,
        count(*) FILTER (WHERE id != ${session.therapistId} AND is_org_admin) AS other_admins
      FROM therapists
      WHERE organization_id = ${session.organizationId}
    `;
    if (Number(stats.other_members) > 0 && Number(stats.other_admins) === 0) {
      return NextResponse.json(
        { error: "你是機構目前唯一的管理者，請先在機構成員管理頁指派其他管理者，再刪除帳號" },
        { status: 400 }
      );
    }
  }

  await sql`DELETE FROM therapists WHERE id = ${session.therapistId}`;
  await deleteSession();

  return NextResponse.json({ ok: true });
}
