import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";

export async function POST(req: NextRequest) {
  const { email } = await req.json();

  if (!email) {
    return NextResponse.json({ error: "請填寫電子信箱" }, { status: 400 });
  }

  const [therapist] = await sql`SELECT id FROM therapists WHERE email = ${email}`;
  if (!therapist) {
    // 不透露 email 是否存在，一律回傳 ok
    return NextResponse.json({ ok: true });
  }

  const code = Math.floor(100000 + Math.random() * 900000).toString();
  const expiresAt = new Date(Date.now() + 10 * 60 * 1000);

  await sql`DELETE FROM password_reset_codes WHERE email = ${email}`;
  await sql`
    INSERT INTO password_reset_codes (email, verification_code, expires_at)
    VALUES (${email}, ${code}, ${expiresAt})
  `;

  // TODO: 改成用 Resend 發送真實 email
  console.log(`[DEV] 驗證碼：${code}`);

  return NextResponse.json({ ok: true });
}
