import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import sql from "@/lib/db";
import { createSession } from "@/lib/session";

// 比照 app/routers/auth.py 給 Unity 登入用的 Redis 版鎖定邏輯（5 次鎖 15 分鐘）——
// 這裡是治療師後台真正在用的登入頁，原本完全沒有失敗次數限制，可以無限次猜密碼，
// 2026-09-30 稽核發現後補上。Next.js 這邊沒有 Redis 可用，改用 therapists 表上的
// failed_login_count／locked_until 兩個欄位在 Postgres 做一樣的事。
const MAX_FAILED_LOGIN_ATTEMPTS = 5;
const LOGIN_LOCKOUT_MINUTES = 15;

export async function POST(req: NextRequest) {
  const { email, password } = await req.json();

  if (!email || !password) {
    return NextResponse.json({ error: "請填寫所有欄位" }, { status: 400 });
  }

  const [therapist] = await sql`
    SELECT id, organization_id, password, failed_login_count, locked_until
    FROM therapists
    WHERE email = ${email}
  `;

  if (therapist?.locked_until && new Date(therapist.locked_until) > new Date()) {
    return NextResponse.json(
      { error: `登入失敗次數過多，請 ${LOGIN_LOCKOUT_MINUTES} 分鐘後再試` },
      { status: 429 }
    );
  }

  const passwordOk = therapist && (await bcrypt.compare(password, therapist.password));

  if (!passwordOk) {
    if (therapist) {
      const nextCount = therapist.failed_login_count + 1;
      if (nextCount >= MAX_FAILED_LOGIN_ATTEMPTS) {
        const lockedUntil = new Date(Date.now() + LOGIN_LOCKOUT_MINUTES * 60 * 1000);
        await sql`
          UPDATE therapists
          SET failed_login_count = 0, locked_until = ${lockedUntil}
          WHERE id = ${therapist.id}
        `;
      } else {
        await sql`UPDATE therapists SET failed_login_count = ${nextCount} WHERE id = ${therapist.id}`;
      }
    }
    return NextResponse.json({ error: "電子信箱或密碼錯誤" }, { status: 401 });
  }

  // 供機構成員管理頁判斷「尚未登入」用（見 (main)/members/page.tsx），
  // 順便清掉失敗次數計數——登入成功代表這是本人，不該繼續累計。
  await sql`
    UPDATE therapists
    SET last_login_at = now(), failed_login_count = 0, locked_until = NULL
    WHERE id = ${therapist.id}
  `;

  await createSession(therapist.id, therapist.organization_id);
  return NextResponse.json({ ok: true });
}
