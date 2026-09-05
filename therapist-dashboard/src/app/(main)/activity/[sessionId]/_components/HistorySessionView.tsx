"use client"; // 標記為 Client Component，允許使用 useState / useEffect


import { useState } from "react"; // React 狀態管理
import Link from "next/link"; // Next.js 的路由連結元件
import type { Session, SessionRound, Case } from "@/lib/types"; // 引入型別定義
import { EMOTION_COLORS, DIMENSION_COLORS, groupSignalsByDimension, reasonCaption } from "@/lib/emotionSignals";
import { EmotionBar } from "@/components/EmotionBar";


// 歷史活動報告元件，接收活動資料、個案資料、各回合紀錄
export function HistorySessionView({
 session,   // 該次活動的完整資料
 caseData,  // 個案基本資料（姓名等）
 rounds,    // 各回合的紀錄列表
}: {
 session: Session;
 caseData: Case;
 rounds: SessionRound[];
}) {
 const [showModal, setShowModal] = useState(false);
 const [expandedRoundId, setExpandedRoundId] = useState<string | null>(null);
 const heartRound = rounds.find((r) => r.type === "心得");


 return (
   // 整頁背景：米色，左右內距 48px，上下 32px，垂直排列，間距 24px
   <div className="relative min-h-screen bg-[#f5e6d3] px-12 py-8 flex flex-col gap-6">


 

     {/* ── 頁首：返回箭頭 + 個案資料文字 ── */}
     <Link
       href={`/cases/${session.caseId}`}
       className="flex items-center gap-2 text-[#5b8ac5] text-[17px] font-medium hover:text-[#3a6aa0] transition-colors self-start"
     >
       <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
         <path d="M19 12H5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
         <path d="M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
       </svg>
       個案資料
     </Link>


     {/* ── 四格統計卡片 ── */}
     {/* 活動次數與日期，顯示在白框上方 */}
     <p className="text-[18px] text-[#555] ml-[8.5%]"> {caseData.name}/第 {session.sessionNumber} 次活動 {session.date}</p>
     <div className="grid grid-cols-4 gap-8 w-[84.5%] ml-[8.5%]"> {/* 四欄等寬格線，寬度 75%，向右偏移 20% */}


       {/* 卡片一：總分 */}
       <div className="bg-white rounded-xl px-2 py-3 flex flex-col items-center gap-1">
         <span className="text-[34px] font-bold text-[#1a1a1a] leading-none mt-5">{session.score}</span> {/* 得分數字 */}
         <span className="text-[17px] text-[#888] mt-1">/{session.totalScore} 總分</span> {/* 滿分說明 */}
         <Link href={`/activity/${session.id}/end?from=history`} className="text-[15px] text-[#5b8ac5] mt-1 hover:text-[#3a6aa0]">詳情</Link>
       </div>


       {/* 卡片二：完成回合數 */}
       <div className="bg-white rounded-xl px-4 py-3 flex flex-col items-center gap-1">
         <span className="text-[34px] font-bold text-[#1a1a1a] leading-none mt-7">{session.rounds}</span> {/* 回合數 */}
         <span className="text-[17px] text-[#888] mt-1">回合完成</span>
       </div>


       {/* 卡片三：整體情緒 */}
       <div className="bg-white rounded-xl px-4 py-3 flex flex-col items-center gap-1">
         <span className="text-[34px] font-bold text-[#1a1a1a] leading-none mt-7">{session.overallEmotion}</span> {/* 整體情緒文字 */}
         <span className="text-[17px] text-[#888] mt-1">整體情緒</span>
       </div>


       {/* 卡片四：平均反應時間 */}
       <div className="bg-white rounded-xl px-4 py-3 flex flex-col items-center gap-1">
         <span className="text-[34px] font-bold text-[#1a1a1a] leading-none mt-7">{session.averageResponseTime}</span> {/* 反應時間（含單位） */}
         <span className="text-[17px] text-[#888] mt-1">平均反應</span>
       </div>
     </div>


     {/* ── 今日故事摘要 ── */}
     <div className="flex flex-col gap-3">
       <h2 className="text-[20px] font-bold text-[#1a1a1a] ml-[8.5%] mt-[-10px]">今日故事摘要</h2> {/* 區塊標題 */}


       {/* 淡藍底 + 左側藍色邊框的引言框 */}
       <div className="bg-[#eef4ff] border-l-4 border-[#5b8ac5] rounded-r-xl px-6 py-5 w-[84.5%] ml-[8.5%] mt-[-5px]">
         <p className="text-[16px] text-[#1a1a1a] leading-relaxed">{session.storySummary}</p> {/* 故事摘要文字 */}
       </div>
     </div>


     {/* ── 各回合紀錄列表 ── */}
     <div className="flex flex-col gap-3 w-[84.5%] ml-[8.5%] mt-[-10px]">
       <h2 className="text-[20px] font-bold text-[#1a1a1a]">各回合紀錄</h2> {/* 區塊標題 */}


       {/* 逐筆渲染每個回合卡片 */}
       {rounds.map((round) => {
         const dotColor = EMOTION_COLORS[round.emotion] ?? "#888";
         const hasReasoning = round.engagementPct != null && round.happinessPct != null && round.agitationPct != null;
         const expanded = expandedRoundId === round.id;
         const signalsByDimension = groupSignalsByDimension(round.signalCodes ?? []);
         return (
           // 白色圓角卡片，左右對齊內容
           <div key={round.id} className="bg-white rounded-xl overflow-hidden">
           <div className="px-6 py-5 flex items-center justify-between">


             {/* 左側：類型標籤 + 秒數 + 場景名稱 + 長者回應 */}
             <div className="flex items-center gap-6">


               {/* 類型（心得 / 回合N）+ 時間（秒） */}
               <div className="flex flex-col min-w-[60px]  ">
                 <span className="text-[13px] text-[#888]">
                   {round.type === "心得" ? "心得" : `回合 ${round.roundNumber}`} {/* 心得卡顯示「心得」，其他顯示「回合 N」 */}
                 </span>
                 <span className="text-[16px] font-bold text-[#1a1a1a]">{round.duration} 秒</span> {/* 該回合持續時間 */}
               </div>


               {/* 場景名稱（小字）+ 長者回應內容（大字） */}
               <div className="flex flex-col gap-0.5">
                 <span className="text-[13px] text-[#888]">{round.sceneName}</span> {/* 場景名稱 */}
                 <span className="text-[16px] text-[#1a1a1a] whitespace-pre-line">
                   {round.summary || round.content} {/* 優先顯示 LLM 生成的一句話摘要，沒有才 fallback 顯示長者原話（例如舊資料還沒補摘要） */}
                 </span>
               </div>
             </div>


             {/* 右側：情緒指示燈 + 判斷依據展開按鈕 + 查看按鈕 */}
             <div className="flex items-center gap-5">


               {/* 彩色圓點 + 情緒文字 */}
               <span className="text-[14px] font-medium flex items-center gap-1.5" style={{ color: dotColor }}>
                 <span className="w-2 h-2 rounded-full inline-block" style={{ backgroundColor: dotColor }} /> {/* 情緒顏色圓點 */}
                 {round.emotion}
               </span>

               {hasReasoning && (
                 <button
                   type="button"
                   onClick={() => setExpandedRoundId(expanded ? null : round.id)}
                   className="flex items-center gap-1 bg-[#f9fafb] border border-[#e5e7eb] rounded-full px-3 py-1.5 text-[13px] text-[#555] hover:bg-[#f0f0f0] transition-colors"
                 >
                   判斷依據
                   <svg
                     viewBox="0 0 20 20" width="14" height="14" fill="none"
                     style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .15s" }}
                   >
                     <path d="M5 7.5l5 5 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                   </svg>
                 </button>
               )}

               {round.type === "心得" ? (
                 <button
                   onClick={() => setShowModal(true)}
                   className="text-[14px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors"
                 >
                   查看 ›
                 </button>
               ) : (
                 <Link
                   href={`/activity/${session.id}/round/${round.roundNumber}`}
                   className="text-[14px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors"
                 >
                   查看 ›
                 </Link>
               )}
             </div>
           </div>

           {expanded && hasReasoning && (
             <div className="bg-[#f9fafb] px-6 py-5 border-t border-[#eee] flex flex-col gap-3">
               <p className="text-[12px] text-[#9aa1ab]">{reasonCaption(round.happinessPct!, round.agitationPct!)}</p>
               <div className="flex gap-8 flex-wrap">
                 <EmotionBar label="專注度" pct={round.engagementPct!} color={DIMENSION_COLORS.engagement} codes={signalsByDimension.engagement} dimension="engagement" />
                 <EmotionBar label="表情訊號" pct={round.happinessPct!} color={DIMENSION_COLORS.happiness} codes={signalsByDimension.happiness} dimension="happiness" />
                 <EmotionBar label="肢體與語調訊號" pct={round.agitationPct!} color={DIMENSION_COLORS.agitation} codes={signalsByDimension.agitation} dimension="agitation" />
               </div>
             </div>
           )}
           </div>
         );
       })}
     </div>


     {/* ── 心得彈窗 ── */}
     {showModal && (
       // 半透明遮罩，點擊關閉
       <div
         className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
         onClick={() => setShowModal(false)}
       >
         {/* 白色彈窗，點擊內部不關閉 */}
         <div
           className="bg-white rounded-2xl px-8 py-7 w-[680px] flex flex-col gap-4 relative"
           onClick={(e) => e.stopPropagation()}
         >
           {/* 關閉按鈕 */}
           <button
             onClick={() => setShowModal(false)}
             className="absolute top-5 right-5 text-[#888] hover:text-[#1a1a1a] transition-colors"
           >
             <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
               <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
             </svg>
           </button>


           {/* 標籤：姓名·第N次活動 */}
           <div className="inline-flex">
             <span className="bg-[#ddeeff] text-[#5b8ac5] text-[13px] font-medium rounded-full px-4 py-1">
               {caseData.name}·第 {session.sessionNumber} 次活動
             </span>
           </div>


           {/* 標題與日期 */}
           <div className="flex flex-col gap-1">
             <h2 className="text-[24px] font-bold text-[#1a1a1a]">長者心得紀錄</h2>
             <p className="text-[14px] text-[#888]">{session.date}</p>
           </div>


           {/* 長者心得內容（唯讀顯示） */}
           <div className="w-full min-h-[220px] bg-[#f9f9f9] border border-[#e0e0e0] rounded-xl px-5 py-4 text-[15px] text-[#1a1a1a] leading-relaxed">
             {heartRound?.content ?? "—"}
           </div>
         </div>
       </div>
     )}
   </div>
 );
}


