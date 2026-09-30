// 近期情緒趨勢的「需留意」判斷，個案列表（api/cases/route.ts）跟個案詳情頁
// （api/cases/[id]/route.ts）共用同一份規則，避免兩邊各自實作出現不一致。
//
// 規則借用統計製程管制的 3 中取 2（Western Electric Rule 2 / Nelson Rule 5）：
// 最近 3 次場次中，只要有 2 次（不需連續）落在「焦躁」或「低落」就觸發，比單純
// 要求連續兩次更不容易漏掉中間穿插一次還好的情況。emotional_status 在 DB 層
// 沒有 enum/CHECK 限制，null 或非預期字串一律不算入焦躁/低落（防呆）。
const CONCERNING_EMOTIONS = new Set(["焦躁", "低落"]);

export function computeNeedsAttention(recentEmotions: unknown): boolean {
  if (!Array.isArray(recentEmotions)) return false;
  const concerningCount = recentEmotions.filter(
    (e) => typeof e === "string" && CONCERNING_EMOTIONS.has(e)
  ).length;
  return concerningCount >= 2;
}
