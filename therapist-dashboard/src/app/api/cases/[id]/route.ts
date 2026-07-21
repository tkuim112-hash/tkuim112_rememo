import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";
import { getSession } from "@/lib/session";
import { logAccess } from "@/lib/audit";

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
    avatar: p.avatar ?? null,
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

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const [p] = await sql`
    SELECT
      p.id, p.name, p.birth_year, p.hometown, p.occupation,
      p.family, p.preferences, p.taboo_words, p.avatar,
      (SELECT COUNT(*)::int FROM sessions s WHERE s.patient_id = p.id) AS total_sessions,
      (SELECT TO_CHAR(MAX(s.date), 'YYYY/MM/DD') FROM sessions s WHERE s.patient_id = p.id) AS last_session
    FROM patients p
    WHERE p.id = ${parseInt(id)} AND p.organization_id = ${session.organizationId}
  `;

  if (!p) return NextResponse.json({ error: "找不到個案" }, { status: 404 });

  await logAccess({
    therapistId: session.therapistId,
    patientId: p.id,
    action: "view_patient",
    resource: `patients:${id}`,
    req,
  });

  return NextResponse.json(mapPatient(p));
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;
  const { birthYear, birthPlace, career, family, hobbies, tabooTopics, avatar } = await req.json();

  const tabooStr = Array.isArray(tabooTopics) ? tabooTopics.join("、") : (tabooTopics ?? "");

  const [updated] = await sql`
    UPDATE patients SET
      birth_year  = ${birthYear ? parseInt(birthYear) : 0},
      hometown    = ${birthPlace ?? ""},
      occupation  = ${career ?? ""},
      family      = ${family ?? ""},
      preferences = ${hobbies ?? ""},
      taboo_words = ${tabooStr},
      avatar      = ${avatar ?? null}
    WHERE id = ${parseInt(id)} AND organization_id = ${session.organizationId}
    RETURNING id
  `;

  if (!updated) {
    return NextResponse.json({ error: "找不到個案" }, { status: 404 });
  }

  const [p] = await sql`
    SELECT
      p.id, p.name, p.birth_year, p.hometown, p.occupation,
      p.family, p.preferences, p.taboo_words, p.avatar,
      (SELECT COUNT(*)::int FROM sessions s WHERE s.patient_id = p.id) AS total_sessions,
      (SELECT TO_CHAR(MAX(s.date), 'YYYY/MM/DD') FROM sessions s WHERE s.patient_id = p.id) AS last_session
    FROM patients p WHERE p.id = ${parseInt(id)}
  `;

  await logAccess({
    therapistId: session.therapistId,
    patientId: updated.id,
    action: "update_patient",
    resource: `patients:${id}`,
    req,
  });

  return NextResponse.json(mapPatient(p));
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "未登入" }, { status: 401 });

  const { id } = await params;

  // 用 RETURNING 確認真的刪到資料才寫稽核紀錄，避免 id 打錯或跨機構時
  // audit_logs 留下一筆從未發生過的「刪除成功」紀錄。
  const [deleted] = await sql`
    DELETE FROM patients
    WHERE id = ${parseInt(id)} AND organization_id = ${session.organizationId}
    RETURNING id
  `;

  if (!deleted) {
    return NextResponse.json({ error: "找不到個案" }, { status: 404 });
  }

  await logAccess({
    therapistId: session.therapistId,
    patientId: deleted.id,
    action: "delete_patient",
    resource: `patients:${id}`,
    req,
  });

  return NextResponse.json({ ok: true });
}
