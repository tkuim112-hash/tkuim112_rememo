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

// 前端自己的公開網址，組信裡「前往設定密碼」連結用——沒有現成的
// NEXT_PUBLIC_SITE_URL 可用，比照 lib/session.ts COOKIE_DOMAIN 的做法：
// 正式環境用真正的網域，本機開發沒有這個網域就退回 localhost。
const SITE_URL = process.env.NODE_ENV === "production" ? "https://re-memo.com" : "http://localhost:3000";

function buildCodeEmail(code: string, email: string, isSetup: boolean): string {
  // 文案跟連結依情境分兩種：isSetup=true 是機構管理者新增治療師帳號、或
  // create_organization.py 開通新機構時觸發的（見 (main)/members/page.tsx、
  // app/create_organization.py）——對方是全新帳號，從沒設過密碼，用「重設
  // 密碼」的措辭、又要他自己手動打開忘記密碼頁面重新輸入一次信箱，會讓人
  // 覺得莫名其妙又麻煩。isSetup=false 才是使用者自己在登入頁按「忘記密碼」
  // 觸發的真實重設流程，繼續用原本的中性措辭即可。
  const heading = isSetup ? "啟用您的 Rememo 帳號" : "Rememo 帳號驗證";
  const intro = isSetup
    ? "您好，機構管理者已經為您建立帳號，請使用以下驗證碼設定您的登入密碼："
    : "您好，請使用以下驗證碼完成信箱驗證，驗證後即可設定登入密碼：";
  const setupLink = `${SITE_URL}/verify-email?email=${encodeURIComponent(email)}&mode=setup`;

  return `
    <div style="background-color:#f5e6d3; padding:40px 16px; font-family:'PingFang TC','Microsoft JhengHei',Arial,sans-serif;">
      <div style="max-width:480px; margin:0 auto; background-color:#ffffff; border-radius:16px; padding:40px 32px; text-align:center;">
        <h1 style="margin:0 0 8px; font-size:22px; color:#1a1a1a;">${heading}</h1>
        <p style="margin:0 0 24px; font-size:15px; color:#888888; line-height:1.6;">
          ${intro}
        </p>
        <div style="background-color:#fdf1e6; border:2px solid #e09540; border-radius:12px; padding:16px; margin:0 0 24px;">
          <span style="font-size:32px; font-weight:600; letter-spacing:8px; color:#1a1a1a;">${code}</span>
        </div>
        ${
          isSetup
            ? `<a href="${setupLink}" style="display:inline-block; background-color:#1a1a1a; color:#ffffff; text-decoration:none; border-radius:12px; padding:14px 32px; font-size:15px; font-weight:500; margin:0 0 24px;">前往設定密碼</a>`
            : ""
        }
        <p style="margin:0 0 24px; font-size:14px; color:#888888;">
          此驗證碼將於 <strong style="color:#1a1a1a;">${isSetup ? "3 天" : "10 分鐘"}後</strong> 失效，請盡快完成驗證。
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
  const { email, mode } = await req.json();
  const isSetup = mode === "setup";

  if (!email) {
    return NextResponse.json({ error: "請填寫電子信箱" }, { status: 400 });
  }

  const [therapist] = await sql`SELECT id FROM therapists WHERE email = ${email}`;
  if (!therapist) {
    // 不透露 email 是否存在，一律回傳 ok
    return NextResponse.json({ ok: true });
  }

  const code = Math.floor(100000 + Math.random() * 900000).toString();
  // 自助「忘記密碼」的人是當下坐在電腦前等信，10 分鐘沒問題；但 isSetup（管理者
  // 新增治療師、或 create_organization.py 開通新機構）觸發的信，收件人可能是幾小時
  // 甚至幾天後才點開信箱檢查，10 分鐘完全不合理——尤其一次幫多位治療師開通帳號時，
  // 太短的效期只會讓大部分人都得重新觸發一次。
  const expiresAt = new Date(Date.now() + (isSetup ? 3 * 24 * 60 * 60 * 1000 : 10 * 60 * 1000));

  await sql`DELETE FROM password_reset_codes WHERE email = ${email}`;
  await sql`
    INSERT INTO password_reset_codes (email, verification_code, expires_at)
    VALUES (${email}, ${code}, ${expiresAt})
  `;

  try {
    const result = await getResend().emails.send({
      from: "Rememo <noreply@re-memo.com>",
      to: email,
      subject: isSetup ? "啟用您的 Rememo 帳號" : "Rememo 帳號驗證碼",
      html: buildCodeEmail(code, email, isSetup),
    });
    // Resend SDK 對 API 層級的錯誤（例如網域未驗證、from 位址不合法）不一定會
    // throw，而是回在 result.error 裡——只 catch 沒檢查這個欄位的話，
    // 這類錯誤會被完全吃掉、前端誤以為寄信成功。
    if (result.error) {
      console.error("[ForgotPassword] Resend 回傳錯誤:", result.error);
      return NextResponse.json({ error: "驗證碼寄送失敗，請稍後再試" }, { status: 500 });
    }
  } catch (e) {
    console.error("[ForgotPassword] 寄信例外:", e);
    return NextResponse.json({ error: "驗證碼寄送失敗，請稍後再試" }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
