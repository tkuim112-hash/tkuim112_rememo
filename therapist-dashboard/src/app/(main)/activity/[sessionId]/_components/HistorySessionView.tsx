"use client"; // 標記為 Client Component，允許使用 useState / useEffect


import { useState } from "react"; // React 狀態管理
import Link from "next/link"; // Next.js 的路由連結元件
import Image from "next/image"; // Next.js 圖片元件，自動處理解析度最佳化
import type { Session, SessionRound, Case } from "@/lib/types"; // 引入型別定義


// 情緒文字對應的顏色（適當 → 綠色，亢奮 → 紅色，焦躁 → 黃色，低落 → 灰色）
const EMOTION_DOT: Record<string, string> = {
 適當: "#34c759",
 亢奮: "#fb2c36",
 焦躁: "#f0c52c",
 低落: "#888888",
};


// 歷史療程報告元件，接收療程資料、個案資料、各回合紀錄
export function HistorySessionView({
 session,   // 該次療程的完整資料
 caseData,  // 個案基本資料（姓名等）
 rounds,    // 各回合的紀錄列表
}: {
 session: Session;
 caseData: Case;
 rounds: SessionRound[];
}) {
 const [showModal, setShowModal] = useState(false); // 控制心得彈窗是否開啟


 return (
   // 整頁背景：米色，左右內距 48px，上下 32px，垂直排列，間距 24px
   <div className="relative min-h-screen bg-[#f5e6d3] px-12 py-8 flex flex-col gap-6">


     {/* 故事卡按鈕：絕對定位，可自由調整 top/right */}
     <button className="absolute top-[7.8%] right-[9.8%] bg-[#f0c840] text-[#1a1a1a] rounded-xl px-5 py-2 text-[18px] font-medium flex items-center gap-2 hover:bg-[#e0b830] transition-colors">
       <Image src="/image/Polaroid camera.png" alt="故事卡" width={24} height={25} />
       故事卡
     </button>


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
     {/* 療程次數與日期，顯示在白框上方 */}
     <p className="text-[18px] text-[#555] ml-[8.5%]"> {caseData.name}/第 {session.sessionNumber} 次療程 {session.date}</p>
     <div className="grid grid-cols-4 gap-8 w-[84.5%] ml-[8.5%]"> {/* 四欄等寬格線，寬度 75%，向右偏移 20% */}


       {/* 卡片一：總分 */}
       <div className="bg-white rounded-xl px-2 py-3 flex flex-col items-center gap-1">
         <span className="text-[34px] font-bold text-[#1a1a1a] leading-none mt-5">{session.score}</span> {/* 得分數字 */}
         <span className="text-[17px] text-[#888] mt-1">/{session.totalScore} 總分</span> {/* 滿分說明 */}
         <Link href={`/activity/${session.id}/end`} className="text-[15px] text-[#5b8ac5] mt-1 hover:text-[#3a6aa0]">詳情</Link>
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
       {rounds.map((round, idx) => {
         const dotColor = EMOTION_DOT[round.emotion] ?? "#888"; // 取情緒對應顏色，找不到則用灰色
         return (
           // 白色圓角卡片，左右對齊內容
           <div key={round.id} className="bg-white rounded-xl px-6 py-5 flex items-center justify-between">


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
                 <span className="text-[16px] text-[#1a1a1a]">{round.content}</span> {/* 長者的回應或行動 */}
               </div>
             </div>


             {/* 右側：情緒指示燈 + 查看按鈕（僅第一筆） */}
             <div className="flex items-center gap-5">


               {/* 彩色圓點 + 情緒文字 */}
               <span className="text-[14px] font-medium flex items-center gap-1.5" style={{ color: dotColor }}>
                 <span className="w-2 h-2 rounded-full inline-block" style={{ backgroundColor: dotColor }} /> {/* 情緒顏色圓點 */}
                 {round.emotion} {/* 情緒文字（適當、亢奮、焦躁、低落） */}
               </span>


               {/* 只有第一筆（心得欄）顯示查看按鈕，點擊開啟心得彈窗 */}
               {idx === 0 && (
                 <button
                   onClick={() => setShowModal(true)}
                   className="text-[14px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors"
                 >
                   查看 ›
                 </button>
               )}
             </div>
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


           {/* 標籤：姓名·第N次療程 */}
           <div className="inline-flex">
             <span className="bg-[#ddeeff] text-[#5b8ac5] text-[13px] font-medium rounded-full px-4 py-1">
               {caseData.name}·第 {session.sessionNumber} 次療程
             </span>
           </div>


           {/* 標題與日期 */}
           <div className="flex flex-col gap-1">
             <h2 className="text-[24px] font-bold text-[#1a1a1a]">長者心得紀錄</h2>
             <p className="text-[14px] text-[#888]">{session.date}</p>
           </div>


           {/* 長者心得內容（唯讀顯示） */}
           <div className="w-full min-h-[220px] bg-[#f9f9f9] border border-[#e0e0e0] rounded-xl px-5 py-4 text-[15px] text-[#1a1a1a] leading-relaxed">
             今天看到廟口的場景，我一下子就想起阿明了，我們那時候常常在廟埕旁邊坐著聊天。歌仔戲的聲音很好聽，跟以前聽的一樣，感覺心情很好，很久沒有這樣輕鬆了。
           </div>
         </div>
       </div>
     )}
   </div>
 );
}


