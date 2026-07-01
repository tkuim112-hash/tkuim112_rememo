import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import sql from "@/lib/db";
import { createSession } from "@/lib/session";

export async function POST(req: NextRequest) {
  const { email, password } = await req.json();

  if (!email || !password) {
    return NextResponse.json({ error: "請填寫所有欄位" }, { status: 400 });
  }

  const [therapist] = await sql`
    SELECT id, organization_id, password
    FROM therapists
    WHERE email = ${email}
  `;

  if (!therapist || !(await bcrypt.compare(password, therapist.password))) {
    return NextResponse.json({ error: "電子信箱或密碼錯誤" }, { status: 401 });
  }

  await createSession(therapist.id, therapist.organization_id);
  return NextResponse.json({ ok: true });
}
