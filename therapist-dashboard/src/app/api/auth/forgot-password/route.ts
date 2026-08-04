import { NextRequest, NextResponse } from "next/server";
import { Resend } from "resend";
import sql from "@/lib/db";

// 延後到真的要寄信才建立 client：build 階段（next build 收集 route 資訊時）
// 不一定拿得到 runtime 的 RESEND_API_KEY，在 module scope 建構會直接讓 build 失敗。
let resend: Resend | null = null;
function getResend(): Resend {
  if (!resend) resend = new Resend(process.env.RESEND_API_KEY);
  return resend;
}

function buildResetCodeEmail(code: string): string {
  return `
    <div style="background-color:#f5e6d3; padding:40px 16px; font-family:'PingFang TC','Microsoft JhengHei',Arial,sans-serif;">
      <div style="max-width:480px; margin:0 auto; background-color:#ffffff; border-radius:16px; padding:40px 32px; text-align:center;">
        <h1 style="margin:0 0 8px; font-size:22px; color:#1a1a1a;">Rememo 密碼重設</h1>
        <p style="margin:0 0 24px; font-size:15px; color:#888888; line-height:1.6;">
          您好，我們收到您重設密碼的請求。<br/>請使用以下驗證碼完成後續步驟：
        </p>
        <div style="background-color:#fdf1e6; border:2px solid #e09540; border-radius:12px; padding:16px; margin:0 0 24px;">
          <span style="font-size:32px; font-weight:600; letter-spacing:8px; color:#1a1a1a;">${code}</span>
        </div>
        <p style="margin:0 0 24px; font-size:14px; color:#888888;">
          此驗證碼將於 <strong style="color:#1a1a1a;">10 分鐘後</strong> 失效，請盡快完成驗證。
        </p>
        <hr style="border:none; border-top:1px solid #eeeeee; margin:24px 0;" />
        <p style="margin:0; font-size:13px; color:#aaaaaa; line-height:1.6;">
          如果這不是您本人的操作，請忽略此信件，您的帳號密碼不會被變更。<br/>
          此信件由系統自動發送，請勿直接回覆。
        </p>
      </div>
    </div>
  `;
}

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
    await getResend().emails.send({
      from: "Rememo <noreply@re-memo.com>",
      to: email,
      subject: "Rememo 密碼重設驗證碼",
      html: buildResetCodeEmail(code),
    });
  } catch {
    return NextResponse.json({ error: "驗證碼寄送失敗，請稍後再試" }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
