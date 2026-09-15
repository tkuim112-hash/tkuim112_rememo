"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { WARMUP_CARDS } from "@/lib/warmupCards";
import type { WarmupCardResult, WarmupSummary } from "@/lib/types";

const STATUS_LABEL: Record<string, "完成" | "跳過" | "手動標記"> = {
  completed: "完成",
  skipped: "跳過",
  manual: "手動標記",
};
const STATUS_COLOR: Record<string, string> = { 完成: "#34c759", 跳過: "#888888", 手動標記: "#5b8ac5" };
const STATUS_BG: Record<string, string> = { 完成: "#e3f6e9", 跳過: "#eee", 手動標記: "#eaf1fb" };

type PoseCard = { kind: "pose"; result: WarmupCardResult };
type SummaryCard = { kind: "summary" };
type Card = PoseCard | SummaryCard;

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  return `${Math.round(seconds)} 秒`;
}

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

function GaugeCard({ title, pct, note }: { title: string; pct: number | null; note?: string }) {
  return (
    <div className="bg-white rounded-2xl px-6 py-5 flex flex-col items-center gap-2">
      <h3 className="text-[18px] font-bold text-[#1a1a1a] self-start">{title}</h3>
      <div className="w-[150px] h-[150px]">
        {pct != null ? <Gauge pct={pct} /> : (
          <div className="w-full h-full flex items-center justify-center text-[14px] text-[#bbb]">無資料</div>
        )}
      </div>
      {note && <span className="text-[16px] text-[#888]">{note}</span>}
    </div>
  );
}

function MetricRow({ title, pct }: { title: string; pct: number | null }) {
  return (
    <div className="bg-white rounded-2xl px-6 py-5 flex items-center justify-between gap-6">
      <h3 className="text-[19px] font-bold text-[#1a1a1a]">{title}</h3>
      <div className="flex flex-col items-end gap-1.5 shrink-0 w-[150px]">
        <span className="text-[22px] font-bold text-[#1a1a1a]">{pct != null ? `${pct}%` : "—"}</span>
        <div className="w-full h-2 bg-[#f0ead9] rounded-full overflow-hidden">
          <div className="h-full bg-[#7EA872] rounded-full" style={{ width: `${pct ?? 0}%` }} />
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

  const [loading, setLoading] = useState(true);
  const [results, setResults] = useState<WarmupCardResult[]>([]);
  const [summary, setSummary] = useState<WarmupSummary | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    fetch(`/api/sessions/${sessionId}/warmup-summary`)
      .then((r) => r.json())
      .then((data) => {
        setResults(data.cards ?? []);
        setSummary(data.summary ?? null);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sessionId]);

  const CARDS: Card[] = [{ kind: "summary" }, ...results.map((result) => ({ kind: "pose" as const, result }))];
  const n = CARDS.length;

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

  if (loading) {
    return <div className="min-h-screen bg-[#f5e6d3] flex items-center justify-center text-[#888]">載入中…</div>;
  }

  if (results.length === 0) {
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
          歷史活動
        </Link>
        <div className="flex-1 flex items-center justify-center text-[18px] text-[#888]">
          此次活動沒有暖身評估資料
        </div>
      </div>
    );
  }

  const activeCardInfo = active.kind === "pose" ? WARMUP_CARDS[active.result.cardKey] : undefined;
  const activeStatusLabel = active.kind === "pose" ? STATUS_LABEL[active.result.status] : undefined;

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
        歷史活動
      </Link>

      <div className="flex gap-8 items-start flex-wrap">
        {/* 左側：目前選到的卡片 */}
        <div
          className="bg-[#fdf7ee] rounded-[28px] px-8 pt-7 pb-8 w-[420px] aspect-square shrink-0 flex flex-col gap-5"
        >
          <p className="text-[15px] font-bold text-[#c96b3c]">共 {results.length} 個動作</p>

          <div className="relative w-full aspect-square rounded-2xl bg-white flex items-center justify-center">
            {active.kind === "summary" ? (
              <span className="text-[36px] font-bold text-[#2b1d13]">總體平均</span>
            ) : (
              <div className="flex flex-col items-center gap-4 px-4">
                <div className="w-[280px] h-[280px] flex items-center justify-center">
                  {activeCardInfo ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={activeCardInfo.image} alt={activeCardInfo.label} className="max-w-full max-h-full object-contain" />
                  ) : (
                    <span className="text-[12px] text-[#bbb] text-center">{active.result.cardKey}</span>
                  )}
                </div>
              </div>
            )}
          </div>

          {active.kind === "pose" && activeStatusLabel && (
            <span
              className="self-center text-[15px] font-bold px-5 py-1.5 rounded-full"
              style={{
                color: STATUS_COLOR[activeStatusLabel],
                backgroundColor: STATUS_BG[activeStatusLabel],
              }}
            >
              {activeStatusLabel}
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
              <MetricRow
                title={activeCardInfo?.metricOneLabel ?? "關節角度達成率"}
                pct={active.result.jointAnglePct}
              />
              <MetricRow title="動作平滑度" pct={active.result.smoothnessPct} />
              <MetricRow title="左右對稱性" pct={active.result.symmetryPct} />
              <div className="bg-white rounded-2xl px-6 py-5 flex items-center justify-between">
                <span className="text-[18px] font-bold text-[#1a1a1a]">此動作耗時</span>
                <span className="text-[20px] font-bold text-[#1a1a1a]">{formatDuration(active.result.durationSeconds)}</span>
              </div>
            </>
          ) : (
            <div className="grid grid-cols-2 gap-5">
              <GaugeCard title="關節角度達成率" pct={summary?.jointAnglePct ?? null} />
              <GaugeCard title="左右對稱性" pct={summary?.symmetryPct ?? null} />
              <GaugeCard title="動作平滑度" pct={summary?.smoothnessPct ?? null} />
              <div className="flex flex-col gap-4">
                <StatRow label="平均耗時" value={formatDuration(summary?.avgDurationSeconds ?? null)} />
                <StatRow label="跳過次數" value={`${summary?.skippedCount ?? 0} 次`} />
                <StatRow label="手動標記次數" value={`${summary?.manualCount ?? 0} 次`} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
