"use client";

import { use, useState, useEffect } from "react";
import Link from "next/link";

import { useRouter } from "next/navigation";
import type { Session, SessionRound, Case } from "@/lib/types";
import { API_BASE as AI_BASE } from "@/lib/api";
import { EMOTION_COLORS, DIMENSION_COLORS, groupSignalsByDimension, reasonCaption } from "@/lib/emotionSignals";
import { EmotionBar } from "@/components/EmotionBar";

const ROUND_LABELS = ["一", "二", "三"];

function ImageWithFallback({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="w-full h-full flex items-center justify-center text-[#888] text-[16px]">
        圖片載入失敗
      </div>
    );
  }
  return (
    <img src={src} alt={alt} className="w-full h-full object-cover" onError={() => setFailed(true)} />
  );
}

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
        setRounds((roundsData as SessionRound[]).filter((r) => r.type !== "心得"));
        const caseRes = await fetch(`/api/cases/${sessionData.caseId}`);
        if (caseRes.ok) setCaseData(await caseRes.json());
      }
    });
  }, [sessionId]);

  const currentRound = rounds.find((r) => r.roundNumber === currentRoundNum) ?? null;
  // 只有第一回合真的會生圖，第二、三回合設計上不生圖：這兩個回合直接固定
  // 顯示第一回合的場景圖片，不用檢查自己有沒有圖。
  const displaySceneImage =
    currentRoundNum === 1
      ? currentRound?.sceneImage
      : rounds.find((r) => r.roundNumber === 1)?.sceneImage;

  if (!session || !caseData || !currentRound) return null;

  const signalsByDimension = groupSignalsByDimension(currentRound.signalCodes ?? []);

  return (
    <div className="min-h-screen bg-[#f5e6d3] px-8 py-6 flex flex-col gap-4">

      {/* 麵包屑 */}
      <nav className="flex items-center gap-2 text-[14px]">
        <Link
          href={`/activity/${sessionId}`}
          className="text-[#888] hover:text-[#1a1a1a] transition-colors"
        >
          歷史活動
        </Link>
        <span className="text-[#888]">›</span>
        <span className="text-[#1a1a1a]">第 {session.sessionNumber} 次的活動</span>
      </nav>

      {/* 標頭卡片：姓名 + 回合標籤 */}
      <div className="bg-white rounded-2xl px-6 py-4 flex items-center justify-between">
        <div className="flex flex-col gap-0.5">
          <h1 className="text-[22px] font-bold text-[#1a1a1a]">{caseData.name}</h1>
          <p className="text-[14px] text-[#888]">
            第 {session.sessionNumber} 次活動 {session.date}
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

      {/* Kinect 情緒判斷依據（整個回合的彙整結果，不細到每一題） */}
      {currentRound.engagementPct != null && currentRound.happinessPct != null && currentRound.agitationPct != null && (
        <div className="bg-white rounded-2xl px-6 py-5 flex items-start gap-8 flex-wrap">
          <div className="flex flex-col gap-0.5 min-w-[170px]">
            <span className="text-[13px] text-[#888]">本回合 Kinect 情緒判斷</span>
            <span className="text-[22px] font-bold" style={{ color: EMOTION_COLORS[currentRound.emotion] ?? "#888" }}>
              {currentRound.emotion}
            </span>
            <p className="text-[12px] text-[#9aa1ab]">{reasonCaption(currentRound.happinessPct, currentRound.agitationPct)}</p>
          </div>
          <div className="flex gap-6 flex-1 flex-wrap min-w-[300px]">
            <EmotionBar label="專注度" pct={currentRound.engagementPct} color={DIMENSION_COLORS.engagement} codes={signalsByDimension.engagement} dimension="engagement" />
            <EmotionBar label="表情訊號" pct={currentRound.happinessPct} color={DIMENSION_COLORS.happiness} codes={signalsByDimension.happiness} dimension="happiness" />
            <EmotionBar label="肢體與語調訊號" pct={currentRound.agitationPct} color={DIMENSION_COLORS.agitation} codes={signalsByDimension.agitation} dimension="agitation" />
          </div>
        </div>
      )}

      {/* 主要內容：場景圖 + 問答紀錄 */}
      <div className="flex gap-4 flex-1 min-h-0">

        {/* 左欄：場景圖片 */}
        <div className="flex-none w-[600px] h-[600px] xl:w-[700px] xl:h-[700px] bg-white rounded-2xl overflow-hidden">
          {displaySceneImage ? (
            <ImageWithFallback src={`${AI_BASE}${displaySceneImage}`} alt={currentRound.sceneName} />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-[#888] text-[16px]">
              尚無場景圖片
            </div>
          )}
        </div>

        {/* 右欄：問答紀錄 */}
        <div className="flex-1 bg-white rounded-2xl px-6 py-4 flex flex-col gap-4 overflow-y-auto">
          <h2 className="text-[21px] font-bold text-[#1a1a1a]">問答紀錄</h2>

          {(currentRound.exchanges ?? []).map((ex) => (
            <div key={ex.questionNumber} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <p className="text-[14px] text-[#888]">第 {ex.questionNumber} 次提問</p>
                {currentRoundNum === 1 && (
                  <span
                    className={`text-[12px] font-medium rounded-full px-2.5 py-0.5 ${
                      ex.stage === "pre_image"
                        ? "bg-[#ddeeff] text-[#5b8ac5]"
                        : "bg-[#f0e6d8] text-[#c08252]"
                    }`}
                  >
                    {ex.stage === "pre_image" ? "生圖前" : "生圖後"}
                  </span>
                )}
              </div>
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
