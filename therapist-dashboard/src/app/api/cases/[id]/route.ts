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

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const [p] = await sql`
    SELECT
      p.id, p.name, p.birth_year, p.hometown, p.occupation,
      p.family, p.preferences, p.taboo_words,
      (SELECT COUNT(*)::int FROM sessions s WHERE s.patient_id = p.id) AS total_sessions,
      (SELECT TO_CHAR(MAX(s.date), 'YYYY/MM/DD') FROM sessions s WHERE s.patient_id = p.id) AS last_session
    FROM patients p
    WHERE p.id = ${parseInt(id)} AND p.organization_id = ${session.organizationId}
  `;

  if (!p) return NextResponse.json({ error: "找不到個案" }, { status: 404 });

  return NextResponse.json(mapPatient(p));
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const { birthYear, birthPlace, career, family, hobbies, tabooTopics } = await req.json();

  const tabooStr = Array.isArray(tabooTopics) ? tabooTopics.join("、") : (tabooTopics ?? "");

  await sql`
    UPDATE patients SET
      birth_year = ${birthYear ? parseInt(birthYear) : 0},
      hometown   = ${birthPlace ?? ""},
      occupation = ${career ?? ""},
      family     = ${family ?? ""},
      preferences = ${hobbies ?? ""},
      taboo_words = ${tabooStr}
    WHERE id = ${parseInt(id)} AND organization_id = ${session.organizationId}
  `;

  const [p] = await sql`
    SELECT
      p.id, p.name, p.birth_year, p.hometown, p.occupation,
      p.family, p.preferences, p.taboo_words,
      (SELECT COUNT(*)::int FROM sessions s WHERE s.patient_id = p.id) AS total_sessions,
      (SELECT TO_CHAR(MAX(s.date), 'YYYY/MM/DD') FROM sessions s WHERE s.patient_id = p.id) AS last_session
    FROM patients p WHERE p.id = ${parseInt(id)}
  `;

  return NextResponse.json(mapPatient(p));
}

export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  await sql`DELETE FROM patients WHERE id = ${parseInt(id)} AND organization_id = ${session.organizationId}`;

  return NextResponse.json({ ok: true });
}
