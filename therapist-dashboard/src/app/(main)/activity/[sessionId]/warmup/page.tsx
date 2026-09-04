"use client";

import { use, useState, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { API_BASE } from "@/lib/api";

// 每張暖身動作卡的圖片跟顯示文字都存在前端本機（public/image/warmup/）。
// 後端只會在 Unity 換卡時回報一個 card_key 字串說「現在要顯示哪一張」
// （見 app/routers/session.py session_warmup_progress），不會傳圖片本身。
// key 要跟 Unity WarmupCardController.ActionCard.cardKey 保持一致（見
// Unity/Rememo/Rememo/Assets/Scenes/WarmupGameScene.unity 的 cardPool），
// 之後在 Unity 那邊新增/調整暖身卡時，這個表要跟著同步更新。
const WARMUP_CARDS: Record<string, { label: string; image: string }> = {
  arm_raise: { label: "手臂平舉 5 秒", image: "/image/warmup/arm_raise.png" },
  leg_kick: { label: "踢腿 3 次", image: "/image/warmup/leg_kick.png" },
  march_in_place: { label: "原地踏步 5 次", image: "/image/warmup/march_in_place.png" },
  touch_knees: { label: "手摸膝蓋 5 次", image: "/image/warmup/touch_knees.png" },
  arm_circle: { label: "手臂旋轉 5 次", image: "/image/warmup/arm_circle.png" },
  waist_twist: { label: "扭腰 5 次", image: "/image/warmup/waist_twist.png" },
  chest_expand: { label: "擴胸 5 次", image: "/image/warmup/chest_expand.png" },
};

export default function WarmupPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const caseId = searchParams.get("caseId") ?? "";

  const [caseName, setCaseName] = useState("使用者名稱");
  // 目前這張卡的 card_key／卡片序號／總卡數，全部由後端 polling 回來，
  // 不再是前端自己模擬的假資料。cardIndex 是 1-based（跟 Unity 回報的一致）。
  const [cardKey, setCardKey] = useState<string | null>(null);
  const [cardIndex, setCardIndex] = useState(0);
  const [totalCards, setTotalCards] = useState(0);

  useEffect(() => {
    if (!caseId) return;
    fetch(`/api/cases/${caseId}`)
      .then((r) => r.json())
      .then((data) => setCaseName(data.name ?? "使用者名稱"))
      .catch(() => {});
  }, [caseId]);

  // 每秒從後端 polling 目前暖身動作卡。暖身每張卡通常十幾秒就換過，比主活動
  // LiveSessionView 的 2 秒 metrics polling 間隔更短，避免治療師端明顯慢半拍。
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/session/${sessionId}/warmup_progress`, {
          credentials: "include",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.card_key) setCardKey(data.card_key);
        setCardIndex(data.card_index ?? 0);
        setTotalCards(data.total_cards ?? 0);
      } catch {
        // 網路暫時中斷時保留上次數值，不中斷顯示
      }
    };

    poll();
    const timer = setInterval(poll, 1000);
    return () => clearInterval(timer);
  }, [sessionId]);

  const currentCard = cardKey ? WARMUP_CARDS[cardKey] : undefined;

  function goToActivity() {
    router.push(`/activity/${sessionId}?caseId=${caseId}&live=1`);
  }

  return (
    <div className="min-h-screen bg-[#f5e6d3] flex flex-col">
      {/* 標題列 */}
      <div className="px-10 pt-8 pb-6">
        <h1 className="text-[38px] font-bold text-[#3d2b1f]">{caseName}</h1>
        <p className="text-[20px] text-[#7d6a56] mt-1">暖身活動</p>
      </div>

      {/* 內容區 */}
      <div className="h-[50vh] bg-white mx-0 px-10 py-10 flex items-start gap-12">
        {/* 左側：動作示範 */}
        <div className="flex flex-col items-center gap-6 w-[320px] shrink-0 ml-[4%]">
          <div className="w-full aspect-square rounded-2xl bg-[#f5f5f5] flex items-center justify-center">
            {currentCard ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={currentCard.image} alt={currentCard.label} className="max-w-full max-h-full object-contain" />
            ) : (
              <span className="text-[13px] text-[#bbb]">等待長者端開始暖身動作…</span>
            )}
          </div>
        </div>

        {/* 分隔線 */}
        <div className="w-[2px] bg-[#e5e5e5] self-stretch" />

        {/* 右側：目前動作與控制 */}
        <div className="flex-1 flex flex-col gap-6 pt-2">
          <div>
            <p className="text-[15px] text-[#888]">目前動作</p>
            <p className="text-[22px] font-bold text-[#1a1a1a] mt-1">{currentCard?.label ?? "—"}</p>
          </div>

          {/* 進度條 */}
          <div className="flex items-center gap-2">
            {Array.from({ length: totalCards }, (_, i) => (
              <span
                key={i}
                className="h-[3px] w-16 rounded-full"
                style={{ backgroundColor: i < cardIndex ? "#e09540" : "#d9d9d9" }}
              />
            ))}
          </div>

          {/* 控制按鈕：暖身動作的推進完全由 Unity 端偵測長者的 Kinect 動作決定，
              目前沒有反向管道能從治療師網頁推進/跳過長者端的卡片，所以「手動
              標記完成」「跳過此動作」先移除，避免顯示成能操控長者端、實際上
              點了沒有作用的按鈕。要補上這個功能需要另外接一條治療師網頁→
              Unity 的控制訊號（可參考 /session/{id}/control 讓 Unity 監聽
              WarmupGameScene 版本的控制指令）。 */}
          <div className="flex gap-3 mt-[20vh]">
            <button
              type="button"
              onClick={goToActivity}
              className="bg-[#1a1a1a] text-white rounded-xl px-5 py-3 text-[15px] font-medium hover:bg-[#333] transition-colors"
            >
              進入活動
            </button>
          </div>
        </div>
      </div>

      {/* 底部裝飾條，跟頂部標題列同色呼應 */}
      <div className="h-10 bg-[#f5e6d3]" />
    </div>
  );
}
