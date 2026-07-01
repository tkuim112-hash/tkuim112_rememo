import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";

export async function POST(req: NextRequest) {
  const { email, code } = await req.json();

  if (!email || !code) {
    return NextResponse.json({ error: "資料不完整" }, { status: 400 });
  }

  const [record] = await sql`
    SELECT id FROM password_reset_codes
    WHERE email = ${email}
      AND verification_code = ${code}
      AND expires_at > NOW()
  `;

  if (!record) {
    return NextResponse.json({ error: "驗證碼錯誤或已過期" }, { status: 400 });
  }

  return NextResponse.json({ ok: true });
}
