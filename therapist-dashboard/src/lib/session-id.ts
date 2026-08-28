import sql from "@/lib/db";

// 2026-08-27稽核：這個路由參數有兩種來源——從個案頁「歷次活動」清單點進來的是
// PostgreSQL 內部整數 sessions.id；從「活動觀察頁面」（治療師端即時療程）結束時
// 帶過來的是 Python 後端用的 UUID session_uuid（見 activity/[sessionId] 的
// LiveSessionView）。原本只有 /api/sessions/[id]/route.ts 處理了這個判斷，
// /api/sessions/[id]/rounds/route.ts 一直是直接 parseInt(id)——UUID 字串（例如
// "29e7a882-97ff-4bd0-bc01-ae7f2bb57827"）對 parseInt() 只會取到開頭數字
// （變成 29），查到完全不相關（或根本不存在）的 session_id，導致治療師網頁
// 從活動觀察頁結束後看歷史逐字稿時整頁是空的，卻沒有任何錯誤訊息。抽成共用
// function，兩支路由都要用同一套判斷，不要再各自維護一份、悄悄失去同步。
export function sessionIdWhereClause(id: string, tableAlias = "") {
  const col = tableAlias ? `${tableAlias}.` : "";
  return /^\d+$/.test(id)
    ? sql`${sql.unsafe(col)}id = ${parseInt(id)}`
    : sql`${sql.unsafe(col)}session_uuid = ${id}`;
}
