import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import sql from "@/lib/db";

export async function POST(req: NextRequest) {
  const { email, password } = await req.json();

  if (!email || !password) {
    return NextResponse.json({ error: "資料不完整" }, { status: 400 });
  }

  if (password.length < 8 || !/[a-z]/.test(password) || !/[A-Z]/.test(password)) {
    return NextResponse.json({ error: "密碼至少需要 8 個字元，且包含大小寫字母" }, { status: 400 });
  }

  const [record] = await sql`
    SELECT id FROM password_reset_codes
    WHERE email = ${email} AND expires_at > NOW()
  `;

  if (!record) {
    return NextResponse.json({ error: "請重新進行驗證" }, { status: 400 });
  }

  const hashedPassword = await bcrypt.hash(password, 10);
  await sql`UPDATE therapists SET password = ${hashedPassword} WHERE email = ${email}`;
  await sql`DELETE FROM password_reset_codes WHERE email = ${email}`;

  return NextResponse.json({ ok: true });
}
