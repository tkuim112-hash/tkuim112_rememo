import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import sql from "@/lib/db";

const MAX_ATTEMPTS = 10;

export async function POST(req: NextRequest) {
  const { email, code, password } = await req.json();

  if (!email || !code || !password) {
    return NextResponse.json({ error: "資料不完整" }, { status: 400 });
  }

  if (password.length < 8 || !/[a-z]/.test(password) || !/[A-Z]/.test(password)) {
    return NextResponse.json({ error: "密碼至少需要 8 個字元，且包含大小寫字母" }, { status: 400 });
  }

  // 這裡原本只檢查「這個信箱有沒有一筆還沒過期的驗證碼紀錄」，完全沒比對
  // verification_code——等於任何人只要知道對方信箱，觸發一次 forgot-password
  // （不需要真的收得到信，DB 那筆紀錄在寄信「之前」就已經寫入）就能跳過驗證碼
  // 直接改密碼，是嚴重的帳號接管漏洞（2026-09-30 稽核發現）。一定要連同
  // verification_code 一起比對，缺一不可。
  const [record] = await sql`
    SELECT id, verification_code, attempts FROM password_reset_codes
    WHERE email = ${email} AND expires_at > NOW()
  `;

  if (!record) {
    return NextResponse.json({ error: "驗證碼錯誤或已過期，請重新進行驗證" }, { status: 400 });
  }

  // 這支才是真正改密碼的地方，攻擊者可以跳過 verify-code 直接對這支硬猜
  // 6 碼驗證碼，所以這裡也要獨立擋次數，不能只靠 verify-code 那邊的限制。
  if (record.attempts >= MAX_ATTEMPTS) {
    return NextResponse.json({ error: "嘗試次數過多，請重新發送驗證碼" }, { status: 429 });
  }

  if (record.verification_code !== code) {
    await sql`UPDATE password_reset_codes SET attempts = attempts + 1 WHERE id = ${record.id}`;
    return NextResponse.json({ error: "驗證碼錯誤或已過期，請重新進行驗證" }, { status: 400 });
  }

  const hashedPassword = await bcrypt.hash(password, 10);
  await sql`UPDATE therapists SET password = ${hashedPassword} WHERE email = ${email}`;
  await sql`DELETE FROM password_reset_codes WHERE email = ${email}`;

  return NextResponse.json({ ok: true });
}
