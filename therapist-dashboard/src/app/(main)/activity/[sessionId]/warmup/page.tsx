"use client";

import { use, useState, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

// 暖身動作清單目前是前端寫死的佔位資料——動作名稱/秒數、以及每個動作對應的
// 示範圖，之後都要改成後端提供（見下方 IconPose 留空的地方）。等後端有
// 對應 API 後，這裡改成 fetch 動作清單，示範圖改吃後端回傳的圖片網址。
const WARMUP_STEPS = [
  { label: "手臂平舉 5 秒" },
  { label: "原地踏步 5 秒" },
  { label: "手臂旋轉3次" },
  { label: "扭腰5秒" },
  { label: "踢腿3次" },
  { label: "擴胸" }
];

function IconEdit() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" className="shrink-0">
      <path d="M12 20h9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function WarmupPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const caseId = searchParams.get("caseId") ?? "";

  const [caseName, setCaseName] = useState("使用者名稱");
  const [stepIndex, setStepIndex] = useState(0);
  // 動作示範圖：之後後端會透過某個 API／WebSocket 把目前這個動作的示範圖
  // 網址傳過來，這裡拿到值再放進 <img>。先用一張本機測試圖佔位確認版面，
  // 之後接上後端資料時要換成依 currentStep 動態切換。
  const [poseImage] = useState<string | null>("/image/手臂5s平舉.png");

  useEffect(() => {
    if (!caseId) return;
    fetch(`/api/cases/${caseId}`)
      .then((r) => r.json())
      .then((data) => setCaseName(data.name ?? "使用者名稱"))
      .catch(() => {});
  }, [caseId]);

  const currentStep = WARMUP_STEPS[Math.min(stepIndex, WARMUP_STEPS.length - 1)];

  function goToActivity() {
    router.push(`/activity/${sessionId}?caseId=${caseId}&live=1`);
  }

  function markDone() {
    setStepIndex((i) => Math.min(i + 1, WARMUP_STEPS.length - 1));
  }

  function skipStep() {
    setStepIndex((i) => Math.min(i + 1, WARMUP_STEPS.length - 1));
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
            {poseImage ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={poseImage} alt={currentStep.label} className="max-w-full max-h-full object-contain" />
            ) : (
              <span className="text-[13px] text-[#bbb]">動作示範圖放置位置</span>
            )}
          </div>
        </div>

        {/* 分隔線 */}
        <div className="w-[2px] bg-[#e5e5e5] self-stretch" />

        {/* 右側：目前動作與控制 */}
        <div className="flex-1 flex flex-col gap-6 pt-2">
          <div>
            <p className="text-[15px] text-[#888]">目前動作</p>
            <p className="text-[22px] font-bold text-[#1a1a1a] mt-1">{currentStep.label}</p>
          </div>

          {/* 進度條 */}
          <div className="flex items-center gap-2">
            {WARMUP_STEPS.map((step, i) => (
              <span
                key={step.label}
                className="h-[3px] w-16 rounded-full"
                style={{ backgroundColor: i <= stepIndex ? "#e09540" : "#d9d9d9" }}
              />
            ))}
          </div>

          {/* 控制按鈕 */}
          <div className="flex gap-3 mt-[20vh]">
            <button
              type="button"
              onClick={markDone}
              className="flex items-center gap-2 border border-[#d0d0d0] rounded-xl px-5 py-3 text-[15px] font-medium text-[#1a1a1a] hover:bg-[#f5f5f5] transition-colors"
            >
              <IconEdit /> 手動標記完成
            </button>
            <button
              type="button"
              onClick={skipStep}
              className="bg-[#b9cde8] text-[#2c4a73] rounded-xl px-5 py-3 text-[15px] font-medium hover:bg-[#a9c0de] transition-colors"
            >
              跳過此動作
            </button>
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
