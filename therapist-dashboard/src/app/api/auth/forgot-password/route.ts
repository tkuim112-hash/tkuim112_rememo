import { NextRequest, NextResponse } from "next/server";
import { Resend } from "resend";
import sql from "@/lib/db";

const resend = new Resend(process.env.RESEND_API_KEY);

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

  try {
    await resend.emails.send({
      from: "onboarding@resend.dev",
      to: email,
      subject: "Rememo 密碼重設驗證碼",
      html: `<p>您的驗證碼為：<strong style="font-size:24px">${code}</strong></p><p>此驗證碼將於 10 分鐘後失效。</p>`,
    });
  } catch {
    return NextResponse.json({ error: "驗證碼寄送失敗，請稍後再試" }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
