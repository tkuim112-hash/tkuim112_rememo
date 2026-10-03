"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { SessionTrendPoint } from "@/lib/types";
import { EMOTION_COLORS } from "@/lib/emotionSignals";

type Mode = "warmup" | "reminisce";
type FilterType = "month" | "week" | "custom";

function toDateStr(d: Date): string {
  return d.toISOString().slice(0, 10);
}

// 週檢視用：算出某個日期所在那一週的週一日期（ISO 週，週一為一週起始）。
function mondayOf(dateStr: string): string {
  const d = new Date(dateStr);
  const day = d.getDay(); // 0=週日...6=週六
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  return toDateStr(d);
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr);
  d.setDate(d.getDate() + days);
  return toDateStr(d);
}

type SeriesSpec = {
  key: keyof SessionTrendPoint;
  name: string;
  color: string;
  unit: "%" | "分" | "秒";
  decimals?: number;
};

function fmt(v: number, unit: SeriesSpec["unit"], decimals = 0): string {
  // API 理論上已經保證是 number，這裡再 Number() 一次只是防呆——上一次就是因為
  // API 漏轉型、這裡直接信任型別標註去呼叫 .toFixed() 才整頁炸掉。
  const s = Number(v).toFixed(decimals);
  if (unit === "分") return `${s} 分`;
  if (unit === "秒") return `${s} 秒`;
  return `${s}%`;
}

// 依篩選類型（近 N 個月 / 週檢視 / 自訂區間 / 全部）算出要顯示的場次區間，用
// 「最新一次活動的日期」當基準往前推，不是瀏覽器當下日期——個案可能好一陣子
// 沒來，用瀏覽器日期當基準會讓「近 1 個月」篩不到任何資料。
function filterPoints(
  all: SessionTrendPoint[],
  filterType: FilterType,
  months: number,
  customFrom: string,
  customTo: string,
  weekStart: string
): SessionTrendPoint[] {
  if (all.length === 0) return [];
  if (filterType === "week") {
    const weekEnd = addDays(weekStart, 6);
    return all.filter((p) => p.date >= weekStart && p.date <= weekEnd);
  }
  if (filterType === "custom") {
    if (!customFrom || !customTo) return all;
    return all.filter((p) => p.date >= customFrom && p.date <= customTo);
  }
  if (months >= 999) return all;
  const lastDate = new Date(all[all.length - 1].date);
  const cutoff = new Date(lastDate);
  cutoff.setMonth(cutoff.getMonth() - months);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  const windowed = all.filter((p) => p.date >= cutoffStr);
  return windowed.length > 0 ? windowed : all.slice(-1);
}

function deltaBadge(values: number[], unit: SeriesSpec["unit"], decimals: number, lowerIsBetter: boolean) {
  if (values.length < 2) return { text: "尚無上一次資料可比較", cls: "text-[#aaa]" };
  const last = values[values.length - 1];
  const prev = values[values.length - 2];
  const d = last - prev;
  const flat = Math.abs(d) < (decimals ? 0.05 : 0.5);
  if (flat) return { text: "與上次差不多", cls: "text-[#aaa]" };
  const improved = lowerIsBetter ? d < 0 : d > 0;
  const dtxt = Math.abs(d).toFixed(decimals);
  const unitTxt = unit === "%" ? "%" : unit === "分" ? " 分" : " 秒";
  return {
    text: `比上次${improved ? "進步" : "退步"} ${dtxt}${unitTxt}`,
    cls: improved ? "text-[#1a9e52]" : "text-[#c1121f]",
  };
}

function describeMetric(values: number[], unit: SeriesSpec["unit"], decimals: number, lowerIsBetter: boolean) {
  const first = values[0];
  const last = values[values.length - 1];
  const diff = last - first;
  const threshold = decimals ? 0.3 : 1;
  const flat = Math.abs(diff) < threshold;
  const dirWord = flat ? "持平" : diff > 0 ? "上升" : "下降";
  const good = flat ? null : lowerIsBetter ? diff < 0 : diff > 0;
  return {
    text: `${fmt(first, unit, decimals)} ${dirWord}至 ${fmt(last, unit, decimals)}`,
    good,
  };
}

function StatCard({ label, value, unit, delta }: { label: string; value: number | null; unit: string; delta: { text: string; cls: string } | null }) {
  return (
    <div className="bg-white rounded-xl px-5 py-4 flex flex-col gap-1.5">
      <p className="text-[13px] text-[#888]">{label}（最近一次）</p>
      <p className="text-[22px] font-bold text-[#1a1a1a] tabular-nums">
        {value != null ? value : "—"}
        <span className="text-[13px] font-semibold text-[#aaa] ml-1">{unit}</span>
      </p>
      {delta && <p className={`text-[12px] font-semibold tabular-nums ${delta.cls}`}>{delta.text}</p>}
    </div>
  );
}

// 手刻 SVG 折線圖：跟專案裡其他視覺化（Gauge）走同一套做法，沒有另外引入
// 圖表套件。單一指標的折線每個點都直接標數字（不用 hover），多指標疊在同一
// 張圖時改成只標線尾最新一次的數字，避免擠成一團——這個取捨是跟治療師來回
// 討論過的：平板沒有 hover，逐點全標在多線圖上會互相重疊看不清楚。
function LineChart({
  points,
  series,
  yMin,
  yMax,
  ticks,
}: {
  points: SessionTrendPoint[];
  series: SeriesSpec[];
  yMin: number;
  yMax: number;
  ticks: number[];
}) {
  const router = useRouter();
  const multi = series.length > 1;
  // 多指標圖的線尾數字是往右外側標（見下面 endLabels），右邊要多留一點空間
  // 才不會被裁掉；單指標圖數字是置中標在點正上方，不用額外留白。
  const H = 220, padL = 38, padR = multi ? 50 : 16, padT = 26, padB = 34;
  const n = points.length;
  // 每個點都要如實標出日期跟數字，不抽稀疏——改成圖表寬度跟著場次數一起變寬
  // （每個點固定留 56px 空間），場次一多圖表自然變寬，用左右滑動看完整內容，
  // 而不是把全部點硬塞進固定寬度、擠到只能挑著標籤顯示。
  const minPointSpacing = 56;
  const W = n <= 1 ? 640 : Math.max(640, padL + padR + (n - 1) * minPointSpacing);
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const xAt = (i: number) => (n <= 1 ? padL : padL + (i * plotW) / (n - 1));
  const yAt = (v: number) => padT + (1 - (v - yMin) / (yMax - yMin)) * plotH;
  const labelEvery = 1;

  const seriesPts = series.map((s) => {
    const raw = points.map((p, i) => {
      const v = p[s.key];
      return typeof v === "number" ? ({ x: xAt(i), y: yAt(v), v } as const) : null;
    });
    return { spec: s, pts: raw };
  });

  // 多指標：只標線尾最新一次的數字，彼此太近時往上錯開避免重疊。
  const endLabels = multi
    ? (() => {
        const info = seriesPts
          .map(({ spec, pts }) => {
            const last = [...pts].reverse().find((p) => p != null);
            return last ? { x: last.x, y: last.y, color: spec.color, text: fmt(last.v, spec.unit, spec.decimals ?? 0) } : null;
          })
          .filter((x): x is NonNullable<typeof x> => x != null)
          .sort((a, b) => a.y - b.y);
        for (let i = 1; i < info.length; i++) {
          if (info[i].y - info[i - 1].y < 13) info[i].y = info[i - 1].y + 13;
        }
        return info;
      })()
    : [];

  return (
    <div className="overflow-x-auto overflow-y-hidden -mx-1 px-1">
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block" style={{ minWidth: W }}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={padL} x2={W - padR} y1={yAt(t)} y2={yAt(t)} stroke="#eee" strokeWidth={1} />
          <text x={padL - 8} y={yAt(t) + 3} textAnchor="end" fontSize={10} fill="#aaa">
            {t}
          </text>
        </g>
      ))}

      {points.map((p, i) => {
        if (i % labelEvery !== 0 && i !== n - 1) return null;
        const x = xAt(i);
        return (
          <g
            key={p.id}
            className="cursor-pointer"
            onClick={() => router.push(`/activity/${p.id}`)}
          >
            <rect x={x - 24} y={H - 32} width={48} height={30} fill="transparent" />
            <text x={x} y={H - 20} textAnchor="middle" fontSize={10} fill="#aaa">
              {p.dateDisplay.replace(/^\d+\//, "")}
            </text>
            <text x={x} y={H - 8} textAnchor="middle" fontSize={9} fill="#aaa" opacity={0.75}>
              {`(第${p.sessionNumber}次)`}
            </text>
          </g>
        );
      })}

      {seriesPts.map(({ spec, pts }) => {
        const segments: string[] = [];
        let current = "";
        pts.forEach((pt) => {
          if (!pt) {
            if (current) segments.push(current);
            current = "";
            return;
          }
          current += current ? ` L${pt.x.toFixed(1)} ${pt.y.toFixed(1)}` : `M${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`;
        });
        if (current) segments.push(current);
        return (
          <g key={spec.key}>
            {segments.map((d, i) => (
              <path key={i} d={d} fill="none" stroke={spec.color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
            ))}
            {pts.map((pt, i) =>
              pt ? <circle key={i} cx={pt.x} cy={pt.y} r={i === n - 1 ? 4.5 : 3.5} fill={spec.color} stroke="#fff" strokeWidth={1.4} /> : null
            )}
            {!multi &&
              pts.map((pt, i) => {
                if (!pt) return null;
                if (i % labelEvery !== 0 && i !== n - 1) return null;
                return (
                  <text key={`t${i}`} x={pt.x} y={pt.y - 9} textAnchor="middle" fontSize={9.5} fontWeight={700} fill={spec.color}>
                    {fmt(pt.v, spec.unit, spec.decimals ?? 0)}
                  </text>
                );
              })}
          </g>
        );
      })}

      {endLabels.map((info, i) => (
        <text key={i} x={info.x + 8} y={info.y + 3} fontSize={11} fontWeight={700} fill={info.color}>
          {info.text}
        </text>
      ))}
    </svg>
    </div>
  );
}

function ChartCard({ title, unitLabel, caption, children }: { title: string; unitLabel: string; caption?: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl px-5 py-4 flex flex-col">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <h3 className="text-[15px] font-bold text-[#1a1a1a]">{title}</h3>
        <span className="text-[12px] text-[#aaa]">{unitLabel}</span>
      </div>
      <div className="mt-1">{children}</div>
      {caption && <p className="text-[11px] text-[#aaa] leading-relaxed mt-2">{caption}</p>}
    </div>
  );
}

function Legend({ series }: { series: SeriesSpec[] }) {
  if (series.length <= 1) return null;
  return (
    <div className="flex gap-3.5 flex-wrap mb-1">
      {series.map((s) => (
        <span key={s.key} className="flex items-center gap-1.5 text-[12px] text-[#888]">
          <span className="w-[9px] h-[9px] rounded-full shrink-0" style={{ backgroundColor: s.color }} />
          {s.name}
        </span>
      ))}
    </div>
  );
}

// 情緒是系統既有的分類標籤（適當/亢奮/焦躁/低落），這 4 個本來沒有天然的
// 連續數值關係，但跟治療師確認過，希望畫成跟其他指標一致的折線圖、方便一起
// 掃視走勢，所以 Y 軸改用「需要關注的程度」排固定的 4 列（由上到下：適當→
// 亢奮→焦躁→低落），用列的位置代表類別，不是真的量出來的程度分數——折線本身
// 只是視覺上串連每次活動，顏色跟著各自的類別走（跟 EMOTION_COLORS、歷次活動
// 頁籤的彩色標籤共用同一套顏色)。
const EMOTION_ROWS = ["適當", "亢奮", "焦躁", "低落"] as const;

function EmotionTimeline({ points }: { points: SessionTrendPoint[] }) {
  const router = useRouter();
  const n = points.length;
  const H = 180, padL = 52, padR = 16, padT = 16, padB = 34;
  const minPointSpacing = 56;
  const W = n <= 1 ? 640 : Math.max(640, padL + padR + (n - 1) * minPointSpacing);
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const xAt = (i: number) => (n <= 1 ? padL : padL + (i * plotW) / (n - 1));
  const rowY = (status: string) => {
    const idx = EMOTION_ROWS.indexOf(status as (typeof EMOTION_ROWS)[number]);
    if (idx === -1) return padT + plotH / 2;
    return padT + (idx / (EMOTION_ROWS.length - 1)) * plotH;
  };

  const pts = points.map((p, i) =>
    p.emotionalStatus ? { x: xAt(i), y: rowY(p.emotionalStatus), color: EMOTION_COLORS[p.emotionalStatus] ?? "#999" } : null
  );
  const segments: string[] = [];
  let current = "";
  pts.forEach((pt) => {
    if (!pt) {
      if (current) segments.push(current);
      current = "";
      return;
    }
    current += current ? ` L${pt.x.toFixed(1)} ${pt.y.toFixed(1)}` : `M${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`;
  });
  if (current) segments.push(current);

  return (
    <div className="overflow-x-auto overflow-y-hidden -mx-1 px-1">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block" style={{ minWidth: W }}>
        {EMOTION_ROWS.map((status) => {
          const y = rowY(status);
          return (
            <g key={status}>
              <line x1={padL} x2={W - padR} y1={y} y2={y} stroke="#eee" strokeWidth={1} />
              <text x={padL - 8} y={y + 3} textAnchor="end" fontSize={10} fontWeight={700} fill={EMOTION_COLORS[status]}>
                {status}
              </text>
            </g>
          );
        })}

        {points.map((p, i) => {
          const x = xAt(i);
          return (
            <g key={p.id} className="cursor-pointer" onClick={() => router.push(`/activity/${p.id}`)}>
              <rect x={x - 24} y={H - 32} width={48} height={30} fill="transparent" />
              <text x={x} y={H - 20} textAnchor="middle" fontSize={10} fill="#aaa">
                {p.dateDisplay.replace(/^\d+\//, "")}
              </text>
              <text x={x} y={H - 8} textAnchor="middle" fontSize={9} fill="#aaa" opacity={0.75}>
                {`(第${p.sessionNumber}次)`}
              </text>
            </g>
          );
        })}

        {segments.map((d, i) => (
          <path key={i} d={d} fill="none" stroke="#c3c2b7" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
        ))}
        {pts.map((pt, i) =>
          pt ? <circle key={i} cx={pt.x} cy={pt.y} r={i === n - 1 ? 5 : 4} fill={pt.color} stroke="#fff" strokeWidth={1.4} /> : null
        )}
      </svg>
    </div>
  );
}

function EmotionStatCard({ allPoints }: { allPoints: SessionTrendPoint[] }) {
  const statuses = allPoints.map((p) => p.emotionalStatus).filter((v): v is string => v != null);
  const last = statuses[statuses.length - 1] ?? null;
  const prev = statuses[statuses.length - 2] ?? null;
  const color = last ? (EMOTION_COLORS[last] ?? "#888") : "#ccc";
  return (
    <div className="bg-white rounded-xl px-5 py-4 flex flex-col gap-1.5">
      <p className="text-[13px] text-[#888]">情緒（最近一次）</p>
      <p className="text-[22px] font-bold" style={{ color }}>
        {last ?? "—"}
      </p>
      {prev ? (
        <p className="text-[12px] font-semibold text-[#aaa]">
          {last === prev ? `與上次相同（${prev}）` : `較上次：${prev} → ${last}`}
        </p>
      ) : (
        <p className="text-[12px] font-semibold text-[#aaa]">尚無上一次資料可比較</p>
      )}
    </div>
  );
}

export default function TrendsTab({ caseId }: { caseId: string }) {
  const [allPoints, setAllPoints] = useState<SessionTrendPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [mode, setMode] = useState<Mode>("warmup");
  const [filterType, setFilterType] = useState<FilterType>("week");
  const [months, setMonths] = useState(999);
  const [customOpen, setCustomOpen] = useState(false);
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [weekStart, setWeekStart] = useState("");

  useEffect(() => {
    fetch(`/api/cases/${caseId}/trends`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`趨勢資料查詢失敗（${r.status}）`);
        return r.json();
      })
      .then((data: SessionTrendPoint[]) => {
        setAllPoints(Array.isArray(data) ? data : []);
        setError(false);
      })
      .catch((err) => {
        console.error(err);
        setAllPoints([]);
        setError(true);
      })
      .finally(() => setLoading(false));
  }, [caseId]);

  const latestDate = allPoints[allPoints.length - 1]?.date ?? "";
  const earliestDate = allPoints[0]?.date ?? "";
  const customFromValue = customFrom || latestDate;
  const customToValue = customTo || latestDate;
  const weekStartValue = weekStart || (latestDate ? mondayOf(latestDate) : "");

  const points = useMemo(
    () => filterPoints(allPoints, filterType, months, customFromValue, customToValue, weekStartValue),
    [allPoints, filterType, months, customFromValue, customToValue, weekStartValue]
  );

  if (loading) {
    return <div className="bg-white rounded-xl px-6 py-10 text-center text-[#888] text-[15px]">載入中…</div>;
  }
  if (error) {
    return <div className="bg-white rounded-xl px-6 py-10 text-center text-[#e05c3a] text-[15px]">趨勢資料載入失敗，請重新整理頁面再試一次</div>;
  }
  if (allPoints.length === 0) {
    return <div className="bg-white rounded-xl px-6 py-10 text-center text-[#888] text-[15px]">尚無已完成的活動紀錄，累積幾次活動後即可查看趨勢</div>;
  }

  const warmupSeries: SeriesSpec[] = [
    { key: "jointAnglePct", name: "關節角度達成率", color: "#c96b3c", unit: "%" },
    { key: "smoothnessPct", name: "動作平滑度", color: "#2a78d6", unit: "%" },
    { key: "symmetryPct", name: "左右對稱性", color: "#1baf7a", unit: "%" },
  ];
  const durationSeries: SeriesSpec = { key: "avgDurationSeconds", name: "平均耗時", color: "#c96b3c", unit: "秒", decimals: 1 };
  const scoreSeries: SeriesSpec = { key: "score", name: "評估量表總分", color: "#5b8ac5", unit: "分" };
  const responseSeries: SeriesSpec = { key: "avgResponseTime", name: "平均回應時間", color: "#6a5acd", unit: "秒", decimals: 1 };

  const rangeLabel =
    filterType === "week"
      ? `${weekStartValue} ~ ${addDays(weekStartValue, 6)}（${points.length} 次）`
      : filterType === "custom"
        ? `${customFromValue} ~ ${customToValue}（${points.length} 次）`
        : months >= 999
          ? `全部 ${allPoints.length} 次`
          : `近 ${months} 個月（${points.length} 次）`;

  let insight: { title: string; tag: { text: string; cls: string }; body: string } | null = null;
  if (points.length < 2) {
    insight = null;
  } else if (mode === "warmup") {
    const metrics = [
      describeMetric(points.map((p) => p.jointAnglePct).filter((v): v is number => typeof v === "number"), "%", 0, false),
      describeMetric(points.map((p) => p.smoothnessPct).filter((v): v is number => typeof v === "number"), "%", 0, false),
      describeMetric(points.map((p) => p.symmetryPct).filter((v): v is number => typeof v === "number"), "%", 0, false),
      describeMetric(points.map((p) => p.avgDurationSeconds).filter((v): v is number => typeof v === "number"), "秒", 1, true),
    ];
    const good = metrics.filter((m) => m.good === true).length;
    const bad = metrics.filter((m) => m.good === false).length;
    insight = {
      title: "暖身活動趨勢小結",
      tag: bad === 0 && good > 0 ? { text: "穩定進步", cls: "bg-[#e3f6ea] text-[#1a9e52]" } : good === 0 && bad > 0 ? { text: "建議留意", cls: "bg-[#fdeee0] text-[#b0661f]" } : { text: "變化不一", cls: "bg-[#fdeee0] text-[#b0661f]" },
      body: `${rangeLabel}的資料中，關節活動度由${metrics[0].text}、平滑度由${metrics[1].text}、對稱性由${metrics[2].text}、平均動作時長由${metrics[3].text}。${
        bad === 0 && good > 0 ? "整體呈穩定進步，建議維持目前的訓練強度與互動方式。" : good === 0 && bad > 0 ? "整體呈下滑趨勢，建議輔導員近期多加留意並適度調整活動安排。" : "各項指標變化方向不一致，建議搭配下方圖表個別觀察，留意退步的項目。"
      }`,
    };
  } else {
    const statuses = points.map((p) => p.emotionalStatus).filter((v): v is string => v != null);
    const emotionFirst = statuses[0] ?? null;
    const emotionLast = statuses[statuses.length - 1] ?? null;
    // 情緒是分類標籤，沒有「上升/下降」這種連續數值的方向，只用「最新一次是
    // 不是焦躁/低落」當作要不要提醒留意的判斷依據，不跟分數/回應時間用同一套
    // describeMetric 邏輯硬套。
    const emotionConcerning = emotionLast === "焦躁" || emotionLast === "低落";
    const emotionText = emotionFirst && emotionLast
      ? emotionFirst === emotionLast
        ? `情緒多維持在「${emotionLast}」`
        : `情緒從「${emotionFirst}」變化到「${emotionLast}」`
      : "情緒資料不足";

    const metrics = [
      describeMetric(points.map((p) => p.score).filter((v): v is number => typeof v === "number"), "分", 0, false),
      describeMetric(points.map((p) => p.avgResponseTime).filter((v): v is number => typeof v === "number"), "秒", 1, true),
    ];
    const good = metrics.filter((m) => m.good === true).length + (emotionLast ? (emotionConcerning ? 0 : 1) : 0);
    const bad = metrics.filter((m) => m.good === false).length + (emotionLast ? (emotionConcerning ? 1 : 0) : 0);
    insight = {
      title: "懷舊活動趨勢小結",
      tag: bad === 0 && good > 0 ? { text: "穩定進步", cls: "bg-[#e3f6ea] text-[#1a9e52]" } : good === 0 && bad > 0 ? { text: "建議留意", cls: "bg-[#fdeee0] text-[#b0661f]" } : { text: "變化不一", cls: "bg-[#fdeee0] text-[#b0661f]" },
      body: `${rangeLabel}的資料中，評估量表總分由${metrics[0].text}、${emotionText}、平均回應時間由${metrics[1].text}。${
        bad === 0 && good > 0 ? "整體呈穩定進步，建議維持目前的訓練強度與互動方式。" : good === 0 && bad > 0 ? "整體呈下滑趨勢，建議輔導員近期多加留意並適度調整活動安排。" : "各項指標變化方向不一致，建議搭配下方圖表個別觀察，留意退步的項目。"
      }`,
    };
  }

  const lastTwo = (key: keyof SessionTrendPoint) =>
    allPoints.map((p) => p[key]).filter((v): v is number => typeof v === "number");

  return (
    <div className="flex flex-col gap-3">
      {/* 暖身／懷舊切換 */}
      <div className="flex flex-col gap-2">
        <div className="inline-flex bg-white border border-[#e0e0e0] rounded-full p-[3px] gap-0.5 self-start">
          {(["warmup", "reminisce"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`rounded-full px-4 py-1.5 text-[13px] font-semibold transition-colors ${
                mode === m ? "bg-[#1a1a1a] text-white" : "text-[#888] hover:text-[#1a1a1a]"
              }`}
            >
              {m === "warmup" ? "暖身活動" : "懷舊活動"}
            </button>
          ))}
        </div>
        <p className="text-[13px] text-[#888] leading-relaxed">
          {mode === "warmup"
            ? "追蹤長者多次暖身動作的關節活動度、平滑度與對稱性變化，協助判斷訓練成效與是否需要調整動作難度。"
            : "追蹤長者多次懷舊對話活動的評估分數與情緒表現，協助掌握長期狀態變化。"}
        </p>
      </div>

      {/* 時間範圍篩選 */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => {
            setFilterType("week");
            if (!weekStart) setWeekStart(weekStartValue);
          }}
          className={`rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold border transition-colors ${
            filterType === "week" ? "bg-[#5b8ac5] border-[#5b8ac5] text-white" : "bg-white border-[#e0e0e0] text-[#888] hover:text-[#1a1a1a]"
          }`}
        >
          週檢視
        </button>
        {[
          { label: "近 1 個月", m: 1 },
          { label: "近 3 個月", m: 3 },
        ].map((opt) => (
          <button
            key={opt.m}
            type="button"
            onClick={() => {
              setFilterType("month");
              setMonths(opt.m);
            }}
            className={`rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold border transition-colors ${
              filterType === "month" && months === opt.m ? "bg-[#5b8ac5] border-[#5b8ac5] text-white" : "bg-white border-[#e0e0e0] text-[#888] hover:text-[#1a1a1a]"
            }`}
          >
            {opt.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setCustomOpen((v) => !v)}
          className={`rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold border transition-colors ${
            filterType === "custom" ? "bg-[#5b8ac5] border-[#5b8ac5] text-white" : "bg-white border-[#e0e0e0] text-[#888] hover:text-[#1a1a1a]"
          }`}
        >
          自訂區間 ▾
        </button>
        <button
          type="button"
          onClick={() => {
            setFilterType("month");
            setMonths(999);
          }}
          className={`rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold border transition-colors ${
            filterType === "month" && months >= 999 ? "bg-[#5b8ac5] border-[#5b8ac5] text-white" : "bg-white border-[#e0e0e0] text-[#888] hover:text-[#1a1a1a]"
          }`}
        >
          全部
        </button>
      </div>

      {filterType === "week" && (
        <div className="flex items-center gap-3 pt-1 pb-2 text-[13px] text-[#1a1a1a]">
          <button
            type="button"
            onClick={() => setWeekStart(addDays(weekStartValue, -7))}
            disabled={!!earliestDate && weekStartValue <= mondayOf(earliestDate)}
            className="rounded-full px-3 py-1 text-[12.5px] font-semibold border border-[#e0e0e0] bg-white text-[#888] hover:text-[#1a1a1a] transition-colors disabled:opacity-40 disabled:hover:text-[#888]"
          >
            ‹ 上一週
          </button>
          <span className="font-semibold tabular-nums">
            {weekStartValue} ~ {addDays(weekStartValue, 6)}
          </span>
          <button
            type="button"
            onClick={() => setWeekStart(addDays(weekStartValue, 7))}
            disabled={!!latestDate && addDays(weekStartValue, 7) > latestDate}
            className="rounded-full px-3 py-1 text-[12.5px] font-semibold border border-[#e0e0e0] bg-white text-[#888] hover:text-[#1a1a1a] transition-colors disabled:opacity-40 disabled:hover:text-[#888]"
          >
            下一週 ›
          </button>
        </div>
      )}

      {customOpen && (
        <div className="flex items-center gap-2 flex-wrap pt-2 pb-3 border-b border-dashed border-[#e0e0e0] text-[12.5px] text-[#888]">
          <label htmlFor="trendFrom" className="font-semibold">從</label>
          <input
            id="trendFrom"
            type="date"
            value={customFromValue}
            min={allPoints[0]?.date}
            max={allPoints[allPoints.length - 1]?.date}
            onChange={(e) => setCustomFrom(e.target.value)}
            className="bg-[#f5f5f5] rounded-lg px-2.5 py-1 text-[12.5px] text-[#1a1a1a] outline-none focus:bg-[#efefef] transition-colors"
          />
          <label htmlFor="trendTo" className="font-semibold">到</label>
          <input
            id="trendTo"
            type="date"
            value={customToValue}
            min={allPoints[0]?.date}
            max={allPoints[allPoints.length - 1]?.date}
            onChange={(e) => setCustomTo(e.target.value)}
            className="bg-[#f5f5f5] rounded-lg px-2.5 py-1 text-[12.5px] text-[#1a1a1a] outline-none focus:bg-[#efefef] transition-colors"
          />
          <button
            type="button"
            onClick={() => setFilterType("custom")}
            className="bg-white border border-[#e0e0e0] rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold text-[#888] hover:text-[#1a1a1a] transition-colors"
          >
            套用
          </button>
        </div>
      )}

      {/* 統計卡片：永遠比「最新一次」跟「上一次」，不受篩選器影響 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
        {mode === "warmup" ? (
          <>
            {warmupSeries.map((s) => {
              const vals = lastTwo(s.key);
              return (
                <StatCard
                  key={s.key}
                  label={s.name}
                  value={vals.length > 0 ? vals[vals.length - 1] : null}
                  unit="%"
                  delta={vals.length > 0 ? deltaBadge(vals, "%", 0, false) : null}
                />
              );
            })}
            {(() => {
              const vals = lastTwo("avgDurationSeconds");
              return (
                <StatCard
                  label="平均耗時"
                  value={vals.length > 0 ? Number(vals[vals.length - 1].toFixed(1)) : null}
                  unit="秒"
                  delta={vals.length > 0 ? deltaBadge(vals, "秒", 1, true) : null}
                />
              );
            })()}
          </>
        ) : (
          <>
            {(() => {
              const vals = lastTwo("score");
              return <StatCard label="評估量表總分" value={vals.length > 0 ? vals[vals.length - 1] : null} unit="/ 20 分" delta={vals.length > 0 ? deltaBadge(vals, "分", 0, false) : null} />;
            })()}
            <EmotionStatCard allPoints={allPoints} />
            {(() => {
              const vals = lastTwo("avgResponseTime");
              return (
                <StatCard
                  label="平均回應時間"
                  value={vals.length > 0 ? Number(vals[vals.length - 1].toFixed(1)) : null}
                  unit="秒"
                  delta={vals.length > 0 ? deltaBadge(vals, "秒", 1, true) : null}
                />
              );
            })()}
          </>
        )}
      </div>

      {/* 趨勢小結 */}
      {insight ? (
        <div className="bg-[#eef4ff] border-l-[3px] border-[#5b8ac5] rounded-xl px-4 py-3 text-[13px] text-[#2d4a6e] leading-relaxed">
          <div className="flex items-center gap-1.5 text-[12.5px] font-bold text-[#2a78d6] mb-1">
            📝 {insight.title}
            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] font-bold ml-1 ${insight.tag.cls}`}>{insight.tag.text}</span>
          </div>
          <div>{insight.body}</div>
        </div>
      ) : (
        <div className="bg-[#eef4ff] border-l-[3px] border-[#5b8ac5] rounded-xl px-4 py-3 text-[13px] text-[#2d4a6e]">
          這個區間內只有 {points.length} 次紀錄，至少需要 2 次以上才能看出變化方向，請擴大篩選區間。
        </div>
      )}

      {/* 點日期可跳轉的使用提示 */}
      <div className="flex items-center gap-1.5 flex-wrap bg-[#eef6ff] border border-[#c7ddf7] rounded-xl px-3.5 py-2.5 text-[13px] text-[#2d5a8f]">
        💡 <b className="text-[#2a78d6]">小提醒：</b>點擊圖表或下方數據表中的日期，可直接前往該次活動的完整紀錄。
      </div>

      {/* 圖表 */}
      <div className="flex flex-col gap-2.5">
        {mode === "warmup" ? (
          <>
            <ChartCard title="動作品質指標" unitLabel="百分比 (%)" caption="動作數值可能因當天體力、睡眠或情緒狀況而有正常波動，建議觀察多次後的整體走勢，而非單次高低；每次的確切數字可參考下方數據表。">
              <Legend series={warmupSeries} />
              <LineChart points={points} series={warmupSeries} yMin={0} yMax={100} ticks={[0, 50, 100]} />
            </ChartCard>
            <ChartCard title={durationSeries.name} unitLabel="秒">
              {(() => {
                const yMax = Math.max(16, ...points.map((p) => p.avgDurationSeconds ?? 0)) + 2;
                return <LineChart points={points} series={[durationSeries]} yMin={0} yMax={yMax} ticks={[0, Math.round(yMax / 2), Math.round(yMax)]} />;
              })()}
            </ChartCard>
          </>
        ) : (
          <>
            <ChartCard title={scoreSeries.name} unitLabel="分 / 20">
              <LineChart points={points} series={[scoreSeries]} yMin={0} yMax={20} ticks={[0, 10, 20]} />
            </ChartCard>
            <ChartCard
              title="情緒"
              unitLabel="適當／亢奮／焦躁／低落"
              caption="⚠️ 此分類由系統依臉部表情與肢體語調訊號自動推估，可能受當日狀態、光線、鏡頭角度等因素影響，請搭配輔導員現場觀察與備註綜合判斷，單次結果不代表長者情緒真的惡化。"
            >
              <EmotionTimeline points={points} />
            </ChartCard>
            <ChartCard title={responseSeries.name} unitLabel="秒">
              {(() => {
                const yMax = Math.max(8, ...points.map((p) => p.avgResponseTime ?? 0)) + 2;
                return <LineChart points={points} series={[responseSeries]} yMin={0} yMax={yMax} ticks={[0, Math.round(yMax / 2), Math.round(yMax)]} />;
              })()}
            </ChartCard>
          </>
        )}
      </div>

      {/* 數據表 */}
      <div className="bg-white rounded-xl px-5 py-4 overflow-x-auto">
        <p className="text-[12px] text-[#aaa] mb-2">
          {mode === "warmup" ? "暖身活動數據表" : "懷舊活動數據表"}（點日期可前往該次完整活動紀錄；可另存／複製，供交接或紀錄引用）
        </p>
        <table className="w-full border-collapse text-[12.5px] min-w-[480px]">
          <thead>
            <tr className="text-[#888] font-semibold text-[11.5px]">
              <th className="text-left py-1.5 px-2.5">次數</th>
              <th className="text-left py-1.5 px-2.5">日期</th>
              {mode === "warmup" ? (
                <>
                  <th className="text-right py-1.5 px-2.5">關節角度達成率 (%)</th>
                  <th className="text-right py-1.5 px-2.5">動作平滑度 (%)</th>
                  <th className="text-right py-1.5 px-2.5">左右對稱性 (%)</th>
                  <th className="text-right py-1.5 px-2.5">平均耗時 (秒)</th>
                </>
              ) : (
                <>
                  <th className="text-right py-1.5 px-2.5">評估量表總分 (/20)</th>
                  <th className="text-right py-1.5 px-2.5">情緒</th>
                  <th className="text-right py-1.5 px-2.5">平均回應時間 (秒)</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.id} className="border-b border-[#f0f0f0]">
                <td className="py-1.5 px-2.5 text-[#1a1a1a]">第 {p.sessionNumber} 次</td>
                <td className="py-1.5 px-2.5">
                  <Link href={`/activity/${p.id}`} className="font-semibold text-[#2a78d6] hover:underline">
                    {p.dateDisplay}
                  </Link>
                </td>
                {mode === "warmup" ? (
                  <>
                    <td className="py-1.5 px-2.5 text-right tabular-nums">{p.jointAnglePct ?? "—"}</td>
                    <td className="py-1.5 px-2.5 text-right tabular-nums">{p.smoothnessPct ?? "—"}</td>
                    <td className="py-1.5 px-2.5 text-right tabular-nums">{p.symmetryPct ?? "—"}</td>
                    <td className="py-1.5 px-2.5 text-right tabular-nums">{p.avgDurationSeconds != null ? p.avgDurationSeconds.toFixed(1) : "—"}</td>
                  </>
                ) : (
                  <>
                    <td className="py-1.5 px-2.5 text-right tabular-nums">{p.score ?? "—"}</td>
                    <td className="py-1.5 px-2.5 text-right font-semibold" style={{ color: p.emotionalStatus ? (EMOTION_COLORS[p.emotionalStatus] ?? "#888") : "#aaa" }}>
                      {p.emotionalStatus ?? "—"}
                    </td>
                    <td className="py-1.5 px-2.5 text-right tabular-nums">{p.avgResponseTime != null ? p.avgResponseTime.toFixed(1) : "—"}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
