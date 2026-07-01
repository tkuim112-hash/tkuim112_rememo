"use client";

import { use, useState, useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import type { Session, SessionRound, Case } from "@/lib/types";

const ROUND_LABELS = ["一", "二", "三"];

export default function RoundDetailPage({
  params,
}: {
  params: Promise<{ sessionId: string; roundNumber: string }>;
}) {
  const { sessionId, roundNumber } = use(params);
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [caseData, setCaseData] = useState<Case | null>(null);
  const [rounds, setRounds] = useState<SessionRound[]>([]);

  const currentRoundNum = parseInt(roundNumber);

  useEffect(() => {
    Promise.all([
      fetch(`/api/sessions/${sessionId}`).then((res) => res.ok ? res.json() : null),
      fetch(`/api/sessions/${sessionId}/rounds`).then((res) => res.ok ? res.json() : []),
    ]).then(async ([sessionData, roundsData]) => {
      if (sessionData) {
        setSession(sessionData);
        setRounds((roundsData as SessionRound[]).filter((r) => r.type === "回合"));
        const caseRes = await fetch(`/api/cases/${sessionData.caseId}`);
        if (caseRes.ok) setCaseData(await caseRes.json());
      }
    });
  }, [sessionId]);

  const currentRound = rounds.find((r) => r.roundNumber === currentRoundNum) ?? null;

  if (!session || !caseData || !currentRound) return null;

  return (
    <div className="min-h-screen bg-[#f5e6d3] px-8 py-6 flex flex-col gap-4">

      {/* 麵包屑 */}
      <nav className="flex items-center gap-2 text-[14px]">
        <Link
          href={`/activity/${sessionId}`}
          className="text-[#888] hover:text-[#1a1a1a] transition-colors"
        >
          歷史療程
        </Link>
        <span className="text-[#888]">›</span>
        <span className="text-[#1a1a1a]">第 {session.sessionNumber} 次的療程</span>
      </nav>

      {/* 標頭卡片：姓名 + 回合標籤 */}
      <div className="bg-white rounded-2xl px-6 py-4 flex items-center justify-between">
        <div className="flex flex-col gap-0.5">
          <h1 className="text-[22px] font-bold text-[#1a1a1a]">{caseData.name}</h1>
          <p className="text-[14px] text-[#888]">
            第 {session.sessionNumber} 次療程 {session.date}
          </p>
        </div>
        <div className="flex gap-2">
          {rounds.map((r, idx) => (
            <button
              key={r.id}
              type="button"
              onClick={() =>
                router.push(`/activity/${sessionId}/round/${r.roundNumber}`)
              }
              className={`px-5 py-2 rounded-xl text-[15px] font-medium border transition-colors ${
                r.roundNumber === currentRoundNum
                  ? "bg-[#c08252] text-white border-[#c08252]"
                  : "bg-white text-[#1a1a1a] border-[#d0d0d0] hover:bg-[#f5f5f5]"
              }`}
            >
              回合{ROUND_LABELS[idx]}
            </button>
          ))}
        </div>
      </div>

      {/* 主要內容：場景圖 + 問答紀錄 */}
      <div className="flex gap-4 flex-1 min-h-0">

        {/* 左欄：場景圖片 */}
        <div className="flex-none w-[600px] h-[600px] bg-white rounded-2xl overflow-hidden">
          {currentRound.sceneImage ? (
            <Image
              src={currentRound.sceneImage}
              alt={currentRound.sceneName}
              width={600}
              height={600}
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-[#888] text-[16px]">
              {currentRound.sceneName}
            </div>
          )}
        </div>

        {/* 右欄：問答紀錄 */}
        <div className="flex-1 bg-white rounded-2xl px-6 py-4 flex flex-col gap-4 overflow-y-auto">
          <h2 className="text-[21px] font-bold text-[#1a1a1a]">問答紀錄</h2>

          {(currentRound.exchanges ?? []).map((ex) => (
            <div key={ex.questionNumber} className="flex flex-col gap-1.5">
              <p className="text-[14px] text-[#888]">第 {ex.questionNumber} 次提問</p>
              <p className="text-[16px] text-[#1a1a1a]">{ex.question}</p>
              {ex.answer != null ? (
                <div className="bg-[#f5e6d3] rounded-xl px-4 py-2.5 text-[16px] text-[#1a1a1a]">
                  {ex.answer}
                </div>
              ) : (
                <p className="text-[15px] text-[#c08252]">未偵測到回答・已跳過</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
