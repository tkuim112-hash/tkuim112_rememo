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
  });
}

export async function deleteSession() {
  const cookieStore = await cookies();
  cookieStore.delete("rememo_session");
}

export async function getSession() {
  const cookieStore = await cookies();
  const session = cookieStore.get("rememo_session")?.value;
  return decrypt(session);
}
