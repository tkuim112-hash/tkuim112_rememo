import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import sql from "@/lib/db";
import { createSession } from "@/lib/session";

export async function POST(req: NextRequest) {
  const { username, institution, email, password } = await req.json();

  if (!username || !institution || !email || !password) {
    return NextResponse.json({ error: "請填寫所有欄位" }, { status: 400 });
  }

  if (password.length < 8 || !/[a-z]/.test(password) || !/[A-Z]/.test(password)) {
    return NextResponse.json({ error: "密碼至少需要 8 個字元，且包含大小寫字母" }, { status: 400 });
  }

  const [existing] = await sql`SELECT id FROM therapists WHERE email = ${email}`;
  if (existing) {
    return NextResponse.json({ error: "此 Email 已被註冊" }, { status: 409 });
  }

  const hashedPassword = await bcrypt.hash(password, 10);

  const [org] = await sql`
    INSERT INTO organizations (name, email, password)
    VALUES (${institution}, ${email}, ${hashedPassword})
    RETURNING id
  `;

  const [therapist] = await sql`
    INSERT INTO therapists (organization_id, name, email, password)
    VALUES (${org.id}, ${username}, ${email}, ${hashedPassword})
    RETURNING id
  `;

  await createSession(therapist.id, org.id);
  return NextResponse.json({ ok: true }, { status: 201 });
}
