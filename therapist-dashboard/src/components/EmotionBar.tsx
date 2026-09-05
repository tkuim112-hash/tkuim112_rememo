import { tierLabel, type Dimension } from "@/lib/emotionSignals";
import { SignalChip } from "./SignalChip";

// 情緒判斷依據的量表列（專注度／表情訊號／肢體與語調訊號共用），即時畫面／歷史回合／
// 回合詳情三個頁面都用同一個，避免各自刻一份樣式不一致。codes 是這個量表
// 對應到的訊號標籤（見 emotionSignals.ts 的 groupSignalsByDimension），直接
// 掛在量表下面，讓「為什麼這個量表這麼高/低」看得出因果關係。dimension 決定
// tierLabel 用哪一組措辭（專注度用偏高/偏低這種程度詞；表情訊號量的是方向,
// 用偏正向/偏負向；肢體與語調量的是活動量,用波動明顯/平穩），不要三條量表
// 共用同一組「偏高/偏低」。
export function EmotionBar({
  label, pct, color, codes = [], dimension = "engagement",
}: { label: string; pct: number; color: string; codes?: string[]; dimension?: Dimension }) {
  return (
    <div className="flex-1 min-w-[180px]">
      <div className="flex justify-between items-baseline">
        <span className="text-[14px] text-[#0a0a0a]">{label}</span>
        <span className="text-[13px] font-medium" style={{ color }}>{tierLabel(pct, dimension)}</span>
      </div>
      <div className="h-2 bg-[#e5e7eb] rounded-full mt-1.5 overflow-hidden">
        <div className="h-2 rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      {codes.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {codes.map((code) => (
            <SignalChip key={code} code={code} color={color} />
          ))}
        </div>
      )}
    </div>
  );
}
