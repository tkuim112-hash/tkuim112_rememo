import { SIGNAL_TEXT, type SignalCategory } from "@/lib/emotionSignals";

const ICONS: Record<SignalCategory, React.ReactNode> = {
  face: (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none">
      <circle cx="10" cy="10" r="7.2" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="7.3" cy="8.5" r="0.9" fill="currentColor" />
      <circle cx="12.7" cy="8.5" r="0.9" fill="currentColor" />
      <path d="M6.8 12c.9 1.1 2 1.7 3.2 1.7s2.3-.6 3.2-1.7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  ),
  eye: (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none">
      <path d="M2 10c2.2-3.5 5.2-5.3 8-5.3s5.8 1.8 8 5.3c-2.2 3.5-5.2 5.3-8 5.3S4.2 13.5 2 10Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx="10" cy="10" r="2.2" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  ),
  body: (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none">
      <circle cx="8" cy="4.5" r="2.2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M4 17c.5-3.8 2.3-6.2 5.5-7l4.5 2.4M9.5 10l3 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  speaker: (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none">
      <path d="M3 8h2.6L9 5.3v9.4L5.6 12H3V8Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M12.2 7.2a4 4 0 0 1 0 5.6M14.6 5a7.4 7.4 0 0 1 0 10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  ),
};

// 情緒判斷依據的訊號標籤（icon + 中文文案），即時畫面／歷史回合／回合詳情
// 三個頁面共用。code 對應不到 SIGNAL_TEXT（例如後端加了新代碼但前端還沒補
// 文案）時直接不渲染，不顯示壞掉的空白 chip。
export function SignalChip({ code, color }: { code: string; color: string }) {
  const def = SIGNAL_TEXT[code];
  if (!def) return null;
  return (
    <span className="inline-flex items-center gap-1.5 bg-white border border-[#e5e7eb] rounded-full px-3 py-1.5 text-[13px] text-[#0a0a0a]">
      <span style={{ color }} className="inline-flex shrink-0">{ICONS[def.category]}</span>
      {def.text}
    </span>
  );
}
