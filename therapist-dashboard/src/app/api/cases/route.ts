import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";

const AVATAR_COLORS = ["#d4e4f7", "#f7e4d4", "#e4f7d4", "#f7d4e4", "#e4d4f7", "#f7f0d4"];

function mapPatient(p: Record<string, unknown>) {
  const id = p.id as number;
  return {
    id: id.toString(),
    name: p.name,
    surname: (p.name as string).charAt(0),
    birthYear: p.birth_year?.toString() ?? "",
    birthPlace: p.hometown ?? "",
    career: p.occupation ?? "",
    family: p.family ?? "",
    hobbies: p.preferences ?? "",
    tabooTopics: p.taboo_words ? (p.taboo_words as string).split("、").filter(Boolean) : [],
    avatarColor: AVATAR_COLORS[id % AVATAR_COLORS.length],
    totalSessions: p.total_sessions ?? 0,
    lastSession: p.last_session ?? "尚未開始",
    isActive: true,
    age: p.birth_year ? new Date().getFullYear() - (p.birth_year as number) : 0,
    gender: "unknown",
    mode: "輕度模式",
    notes: [p.occupation, p.family, p.preferences].filter(Boolean).join("；"),
  };
}

export async function GET() {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const cases = await sql`
    SELECT
      p.id, p.name, p.birth_year, p.hometown, p.occupation,
      p.family, p.preferences, p.taboo_words,
      (SELECT COUNT(*)::int FROM sessions s WHERE s.patient_id = p.id) AS total_sessions,
      (SELECT TO_CHAR(MAX(s.date), 'YYYY/MM/DD') FROM sessions s WHERE s.patient_id = p.id) AS last_session
    FROM patients p
    WHERE p.organization_id = ${session.organizationId}
    ORDER BY p.id DESC
  `;

  return NextResponse.json(cases.map(mapPatient));
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { name, birthYear, birthPlace, career, family, hobbies, tabooTopics } = await req.json();

  if (!name) {
    return NextResponse.json({ error: "請填寫姓名" }, { status: 400 });
  }

  const tabooStr = Array.isArray(tabooTopics) ? tabooTopics.join("、") : (tabooTopics ?? "");

  const [newCase] = await sql`
    INSERT INTO patients (organization_id, name, birth_year, hometown, occupation, family, preferences, taboo_words)
    VALUES (
      ${session.organizationId},
      ${name},
      ${birthYear ? parseInt(birthYear) : 0},
      ${birthPlace ?? ""},
      ${career ?? ""},
      ${family ?? ""},
      ${hobbies ?? ""},
      ${tabooStr}
    )
    RETURNING id
  `;

  return NextResponse.json({ id: newCase.id.toString() }, { status: 201 });
}
