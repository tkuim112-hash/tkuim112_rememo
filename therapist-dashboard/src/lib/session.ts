import "server-only";
import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";

const encodedKey = new TextEncoder().encode(process.env.SESSION_SECRET);

export interface SessionPayload {
  therapistId: number;
  organizationId: number;
  expiresAt: Date;
  [key: string]: unknown;
}

export async function encrypt(payload: SessionPayload) {
  return new SignJWT(payload)
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("7d")
    .sign(encodedKey);
}

export async function decrypt(session: string | undefined = "") {
  try {
    const { payload } = await jwtVerify(session, encodedKey, {
      algorithms: ["HS256"],
    });
    return payload as unknown as SessionPayload;
  } catch {
    return null;
  }
}

// 前端 re-memo.com、後端 api.re-memo.com 是兩個子網域，cookie 預設是 host-only
// （只綁在設定當下的網域），呼叫後端 API 時不會帶上——要明確指定 domain 才能讓
// 前後端共用同一顆登入 cookie。本機開發沒有這個網域，不能設，否則 cookie 整個失效。
const COOKIE_DOMAIN = process.env.NODE_ENV === "production" ? ".re-memo.com" : undefined;

export async function createSession(therapistId: number, organizationId: number) {
  const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
  const session = await encrypt({ therapistId, organizationId, expiresAt });
  const cookieStore = await cookies();

  cookieStore.set("rememo_session", session, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    expires: expiresAt,
    sameSite: "lax",
    path: "/",
    domain: COOKIE_DOMAIN,
  });
}

export async function deleteSession() {
  const cookieStore = await cookies();
  cookieStore.delete({ name: "rememo_session", path: "/", domain: COOKIE_DOMAIN });
}

export async function getSession() {
  const cookieStore = await cookies();
  const session = cookieStore.get("rememo_session")?.value;
  return decrypt(session);
}
