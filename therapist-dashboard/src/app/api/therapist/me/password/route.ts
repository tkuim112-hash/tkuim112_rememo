import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";

export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { currentPassword, newPassword } = await req.json();
  if (!currentPassword || !newPassword) {
    return NextResponse.json({ error: "請填寫所有欄位" }, { status: 400 });
  }

  const [therapist] = await sql`SELECT password FROM therapists WHERE id = ${session.therapistId}`;
  if (!therapist || !(await bcrypt.compare(currentPassword, therapist.password))) {
    return NextResponse.json({ error: "目前密碼錯誤" }, { status: 401 });
  }

  if (newPassword.length < 8 || !/[a-z]/.test(newPassword) || !/[A-Z]/.test(newPassword)) {
    return NextResponse.json({ error: "新密碼至少需要 8 個字元，且包含大小寫字母" }, { status: 400 });
  }

  const hashedPassword = await bcrypt.hash(newPassword, 10);
  await sql`UPDATE therapists SET password = ${hashedPassword} WHERE id = ${session.therapistId}`;

  return NextResponse.json({ ok: true });
}
