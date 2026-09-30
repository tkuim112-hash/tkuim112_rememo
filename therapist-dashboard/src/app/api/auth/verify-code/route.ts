import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";

const MAX_ATTEMPTS = 10;

export async function POST(req: NextRequest) {
  const { email, code } = await req.json();

  if (!email || !code) {
    return NextResponse.json({ error: "資料不完整" }, { status: 400 });
  }

  const [record] = await sql`
    SELECT id, verification_code, attempts FROM password_reset_codes
    WHERE email = ${email} AND expires_at > NOW()
  `;

  if (!record) {
    return NextResponse.json({ error: "驗證碼錯誤或已過期" }, { status: 400 });
  }

  // 6 碼驗證碼只有 100 萬種組合，沒有這道限制的話可以無限次嘗試硬猜——
  // 尤其 setup 模式效期長達 3 天，攻擊窗口很大，一定要擋（2026-09-30 稽核）。
  if (record.attempts >= MAX_ATTEMPTS) {
    return NextResponse.json({ error: "嘗試次數過多，請重新發送驗證碼" }, { status: 429 });
  }

  if (record.verification_code !== code) {
    await sql`UPDATE password_reset_codes SET attempts = attempts + 1 WHERE id = ${record.id}`;
    return NextResponse.json({ error: "驗證碼錯誤或已過期" }, { status: 400 });
  }

  return NextResponse.json({ ok: true });
}
