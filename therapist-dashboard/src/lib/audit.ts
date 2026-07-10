import "server-only";
import { NextRequest } from "next/server";
import sql from "@/lib/db";

// 存取稽核紀錄：誰（therapistId）、什麼時候（created_at）、看了哪個病患的資料（patientId）。
// 寫入失敗只印警告，不讓稽核本身變成主功能的單點故障。
export async function logAccess(params: {
  therapistId: number;
  patientId?: number | null;
  action: string;
  resource?: string;
  req?: NextRequest;
}) {
  const { therapistId, patientId = null, action, resource = null, req } = params;
  const ipAddress = req?.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? null;

  try {
    await sql`
      INSERT INTO audit_logs (therapist_id, patient_id, action, resource, ip_address)
      VALUES (${therapistId}, ${patientId}, ${action}, ${resource}, ${ipAddress})
    `;
  } catch (e) {
    console.warn("[AuditLog] 寫入失敗（不影響主要功能）:", e);
  }
}
