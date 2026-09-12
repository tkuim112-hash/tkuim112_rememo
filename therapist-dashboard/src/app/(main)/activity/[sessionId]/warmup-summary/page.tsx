"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

// 暖身狀態總覽目前整頁都是前端寫死的佔位資料——後端還沒有記錄暖身活動的
// 逐項偵測數據（關節角度、平滑度、左右對稱性、耗時、跳過/手動標記次數）。
// 等後端補上這份資料的 API 後，這裡改成 fetch 實際紀錄，元件結構不用大改。
const WARMUP_STEPS = [
  {
    label: "手臂平舉 5 秒",
    image: "/image/手臂5s平舉.png",
    status: "完成" as const,
    jointAngle: 78,
    jointAngleDesc: "手臂舉起的角度大致到位，再多撐開一點會更標準。",
    smoothness: 78,
    smoothnessDesc: "雙手舉起的高度幾乎一致，左右施力平均。",
    symmetry: 78,
    duration: "45 秒",
  },
  {
    label: "舉手過肩 5 秒",
    image: null,
    status: "完成" as const,
    jointAngle: 82,
    jointAngleDesc: "手臂舉超過肩線，角度達標。",
    smoothness: 85,
    smoothnessDesc: "上舉過程速度平穩，沒有明顯停頓。",
    symmetry: 74,
    duration: "40 秒",
  },
  {
    label: "左右轉頭 5 秒",
    image: null,
    status: "完成" as const,
    jointAngle: 70,
    jointAngleDesc: "轉頭幅度略保守，可以再多轉一點。",
    smoothness: 88,
    smoothnessDesc: "左右轉動節奏一致。",
    symmetry: 60,
    duration: "38 秒",
  },
  {
    label: "深呼吸 5 秒",
    image: null,
    status: "跳過" as const,
    jointAngle: 0,
    jointAngleDesc: "",
    smoothness: 0,
    smoothnessDesc: "",
    symmetry: 0,
    duration: "—",
  },
  {
    label: "輕拍雙肩 5 秒",
    image: null,
    status: "手動標記" as const,
    jointAngle: 91,
    jointAngleDesc: "雙手準確拍到肩膀位置。",
    smoothness: 95,
    smoothnessDesc: "拍動節奏穩定、左右一致。",
    symmetry: 88,
    duration: "42 秒",
  },
];

const SUMMARY = {
  jointAngle: 78,
  symmetry: 66,
  symmetryNote: "偏左",
  smoothness: 90,
  avgDuration: "30 秒",
  skippedCount: 0,
  manualCount: 0,
};

const STATUS_COLOR: Record<string, string> = { 完成: "#34c759", 跳過: "#888888", 手動標記: "#5b8ac5" };
const STATUS_BG: Record<string, string> = { 完成: "#e3f6e9", 跳過: "#eee", 手動標記: "#eaf1fb" };

type PoseCard = {
  kind: "pose";
  label: string;
  image: string | null;
  status: "完成" | "跳過" | "手動標記";
  jointAngle: number;
  jointAngleDesc: string;
  smoothness: number;
  smoothnessDesc: string;
  symmetry: number;
  duration: string;
};
type SummaryCard = { kind: "summary" };
type Card = PoseCard | SummaryCard;

const CARDS: Card[] = [
  { kind: "summary" as const },
  ...WARMUP_STEPS.map((s) => ({ kind: "pose" as const, ...s })),
];

// ── 半圓弧形量表（總體平均卡片用），270 度弧、缺口朝下 ──────────────────
function Gauge({ pct, color = "#7EA872", size = 150 }: { pct: number; color?: string; size?: number }) {
  const clamped = Math.max(0, Math.min(100, pct));
  const r = size * 0.36;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * r;
  const arcFraction = 0.75; // 270 度 / 360 度
  const trackLen = circumference * arcFraction;
  const progressLen = trackLen * (clamped / 100);
  const strokeWidth = size * 0.1;
  const knobAngleDeg = 135 + 270 * (clamped / 100);
  const knobRad = (knobAngleDeg * Math.PI) / 180;
  const kx = cx + r * Math.cos(knobRad);
  const ky = cy + r * Math.sin(knobRad);

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-full h-full">
      <g transform={`rotate(-225 ${cx} ${cy})`}>
        <circle
          cx={cx} cy={cy} r={r} fill="none" stroke="#eef1e4" strokeWidth={strokeWidth}
          strokeDasharray={`${trackLen} ${circumference}`} strokeLinecap="round"
        />
        <circle
          cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth={strokeWidth}
          strokeDasharray={`${progressLen} ${circumference}`} strokeLinecap="round"
        />
      </g>
      <circle cx={kx} cy={ky} r={strokeWidth * 0.55} fill="#fff" stroke={color} strokeWidth={3} />
      <text
        x={cx} y={cy + size * 0.02} textAnchor="middle" dominantBaseline="middle"
        fontSize={size * 0.19} fontWeight={700} fill="#1a1a2e"
      >
        {clamped}%
      </text>
    </svg>
  );
}

function GaugeCard({ title, pct, note }: { title: string; pct: number; note?: string }) {
  return (
    <div className="bg-white rounded-2xl px-6 py-5 flex flex-col items-center gap-2">
      <h3 className="text-[18px] font-bold text-[#1a1a1a] self-start">{title}</h3>
      <div className="w-[150px] h-[150px]">
        <Gauge pct={pct} />
      </div>
      {note && <span className="text-[16px] text-[#888]">{note}</span>}
    </div>
  );
}

function MetricRow({ title, desc, pct }: { title: string; desc?: string; pct: number }) {
  return (
    <div className="bg-white rounded-2xl px-6 py-5 flex items-center justify-between gap-6">
      <div>
        <h3 className="text-[19px] font-bold text-[#1a1a1a]">{title}</h3>
        {desc && <p className="text-[14px] text-[#666] mt-1">{desc}</p>}
      </div>
      <div className="flex flex-col items-end gap-1.5 shrink-0 w-[150px]">
        <span className="text-[22px] font-bold text-[#1a1a1a]">{pct}%</span>
        <div className="w-full h-2 bg-[#f0ead9] rounded-full overflow-hidden">
          <div className="h-full bg-[#7EA872] rounded-full" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white rounded-2xl px-6 py-5 flex items-center justify-between flex-1">
      <span className="text-[16px] font-bold text-[#1a1a1a]">{label}</span>
      <span className="text-[20px] font-bold text-[#1a1a1a]">{value}</span>
    </div>
  );
}

export default function WarmupSummaryPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);

  useEffect(() => {
    // 目前這頁沒有顯示個案姓名，先保留串接位置，等版面需要再加。
  }, [sessionId]);

  const n = CARDS.length;

  const [activeIndex, setActiveIndex] = useState(0);

  function goTo(i: number) {
    setActiveIndex(((i % n) + n) % n);
  }
  function prev() {
    goTo(activeIndex - 1);
  }
  function next() {
    goTo(activeIndex + 1);
  }
  const active = CARDS[activeIndex];

  return (
    <div className="min-h-screen bg-[#f5e6d3] px-12 py-8 flex flex-col gap-6">
      <Link
        href={`/activity/${sessionId}`}
        className="flex items-center gap-2 text-black text-[17px] font-medium hover:text-black/70 transition-colors self-start"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path d="M19 12H5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        歷史療程
      </Link>

      <div className="flex gap-8 items-start flex-wrap">
        {/* 左側：目前選到的卡片 */}
        <div
          className="bg-[#fdf7ee] rounded-[28px] px-8 pt-7 pb-8 w-[420px] aspect-square shrink-0 flex flex-col gap-5"
        >
          <p className="text-[15px] font-bold text-[#c96b3c]">共 {WARMUP_STEPS.length} 個動作</p>

          <div className="relative w-full aspect-square rounded-2xl bg-white flex items-center justify-center">
            {active.kind === "summary" ? (
              <span className="text-[36px] font-bold text-[#2b1d13]">總體平均</span>
            ) : (
              <div className="flex flex-col items-center gap-4 px-4">
                <div className="w-[280px] h-[280px] flex items-center justify-center">
                  {active.image ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={active.image} alt={active.label} className="max-w-full max-h-full object-contain" />
                  ) : (
                    <span className="text-[12px] text-[#bbb] text-center">動作示範圖由後端提供</span>
                  )}
                </div>
              </div>
            )}
          </div>

          {active.kind === "pose" && (
            <span
              className="self-center text-[15px] font-bold px-5 py-1.5 rounded-full"
              style={{
                color: STATUS_COLOR[active.status],
                backgroundColor: STATUS_BG[active.status],
              }}
            >
              {active.status}
            </span>
          )}

          {/* 箭頭 + 分頁點 */}
          <div className="flex items-center justify-center gap-6 mt-auto">
            <button
              type="button"
              onClick={prev}
              aria-label="上一張"
              className="w-11 h-11 rounded-full bg-white shadow-sm flex items-center justify-center text-[#2b1d13] hover:bg-[#f5efe4] transition-colors"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M15 18l-6-6 6-6" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <div className="flex items-center gap-1.5">
              {CARDS.map((_, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => goTo(i)}
                  aria-label={`切換到第 ${i + 1} 張卡片`}
                  className="rounded-full transition-all duration-300"
                  style={{
                    width: i === activeIndex ? 20 : 6,
                    height: 6,
                    backgroundColor: i === activeIndex ? "#c96b3c" : "#e3d5c0",
                  }}
                />
              ))}
            </div>
            <button
              type="button"
              onClick={next}
              aria-label="下一張"
              className="w-11 h-11 rounded-full bg-white shadow-sm flex items-center justify-center text-[#2b1d13] hover:bg-[#f5efe4] transition-colors"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M9 18l6-6-6-6" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
        </div>

        {/* 右側：指標卡片，依目前是動作卡還是總體平均卡切換排版 */}
        <div className="flex-1 min-w-[420px] flex flex-col gap-5">
          {active.kind === "pose" ? (
            <>
              <MetricRow title="關節角度達成率" desc={active.jointAngleDesc} pct={active.jointAngle} />
              <MetricRow title="動作平滑度" desc={active.smoothnessDesc} pct={active.smoothness} />
              <MetricRow title="左右對稱性" pct={active.symmetry} />
              <div className="bg-white rounded-2xl px-6 py-5 flex items-center justify-between">
                <span className="text-[18px] font-bold text-[#1a1a1a]">此動作耗時</span>
                <span className="text-[20px] font-bold text-[#1a1a1a]">{active.duration}</span>
              </div>
            </>
          ) : (
            <div className="grid grid-cols-2 gap-5">
              <GaugeCard title="關節角度達成率" pct={SUMMARY.jointAngle} />
              <GaugeCard title="左右對稱性" pct={SUMMARY.symmetry} note={SUMMARY.symmetryNote} />
              <GaugeCard title="動作平滑度" pct={SUMMARY.smoothness} />
              <div className="flex flex-col gap-4">
                <StatRow label="平均耗時" value={SUMMARY.avgDuration} />
                <StatRow label="跳過次數" value={`${SUMMARY.skippedCount} 次`} />
                <StatRow label="手動標記次數" value={`${SUMMARY.manualCount} 次`} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
