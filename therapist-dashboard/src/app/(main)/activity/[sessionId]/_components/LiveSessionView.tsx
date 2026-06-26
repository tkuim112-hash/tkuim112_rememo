"use client";


import { useState, Fragment, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { mockActiveSession } from "@/lib/mock-data";


const EMOTION_COLORS: Record<string, string> = {
 適當: "#34c759",
 亢奮: "#fb2c36",
 焦躁: "#f0c52c",
 低落: "#888888",
};


const MOCK_ELDER_RESPONSE =
 "我都去找我那個同事阿明，他很會唱歌，我們去廟口那邊坐......";


type View = "scene" | "response";


export function LiveSessionView() {
 const [session, setSession] = useState(mockActiveSession);
 const [currentRound, setCurrentRound] = useState(session.currentRound);
 const [view, setView] = useState<View>("scene");


 const router = useRouter();
 const [showConfirm, setShowConfirm] = useState(false);
 const containerRef = useRef<HTMLDivElement>(null);


 useEffect(() => {
   const isDesktop = window.matchMedia("(pointer: fine)").matches;
   if (isDesktop) {
     document.body.classList.add("overflow-hidden");
     containerRef.current?.classList.add("h-screen", "overflow-hidden");
     return () => {
       document.body.classList.remove("overflow-hidden");
       containerRef.current?.classList.remove("h-screen", "overflow-hidden");
     };
   }
 }, []);


 const handlePause = () => setSession((s) => ({ ...s, status: "paused" }));
 const handleResume = () => setSession((s) => ({ ...s, status: "running" }));


 return (
   <div ref={containerRef} className="min-h-screen bg-[#f5e6d3] px-4 md:px-6 lg:px-8 xl:px-14 pt-3 md:pt-[3vh] lg:pt-[3vh] xl:pt-[4vh] 2xl:pt-[9vh] pb-4 flex flex-col gap-2 md:gap-2 lg:gap-4 xl:gap-5">


     {/* 標題 + 回合追蹤 */}
     <div className="flex items-center gap-4 lg:gap-6 xl:gap-8 flex-wrap">
       <h1 className="text-[23px] md:text-[36px] lg:text-[50px] font-medium text-[#0a0a0a] leading-none shrink-0">
         {session.caseName}
       </h1>
       <div className="flex items-center gap-2 lg:gap-3 xl:gap-4">
       <span className="text-[14px] md:text-[18px] lg:text-[22px] font-medium text-[#0a0a0a] shrink-0">回合追蹤</span>
       <div className="flex items-center">
         {Array.from({ length: session.totalRounds }, (_, i) => i + 1).map((round, idx) => (
           <Fragment key={round}>
             {idx > 0 && (
               <div className="w-4 md:w-6 lg:w-8 xl:w-10 h-[2px] bg-[#c08252]" />
             )}
             <button
               type="button"
               onClick={() => setCurrentRound(round)}
               className={`w-8 h-8 md:w-11 md:h-11 lg:w-12 lg:h-12 xl:w-14 xl:h-14 rounded-full text-[11px] md:text-[14px] lg:text-[16px] font-medium transition-colors ${
                 round < currentRound
                   ? "bg-[#c08252] text-white"
                   : round === currentRound
                   ? "bg-[#7a4a28] text-white"
                   : "bg-[#e8d5c0] text-[#b8a090]"
               }`}
             >
               {round}
             </button>
             {round === currentRound && (
               <span className="ml-2 mr-1 text-[12px] md:text-[14px] lg:text-[16px] text-[#0a0a0a]">進行中</span>
             )}
           </Fragment>
         ))}
       </div>
       </div>
     </div>


     {/* 主要內容 */}
     <div className="flex flex-col sm:flex-row gap-3 sm:gap-4 lg:gap-6 xl:gap-8 flex-1">


       {/* 左欄 */}
       <div className="flex-1 flex flex-col gap-3 lg:gap-5 xl:gap-6">


         {/* 場景 / 回應切換 */}
         <div className="flex flex-col gap-2 lg:gap-3 xl:gap-4">
           <div>
             <h2 className="text-[16px] md:text-[22px] lg:text-[28px] font-medium text-[#0a0a0a]">
               <button
                 type="button"
                 onClick={() => setView("scene")}
                 className={view === "scene" ? "text-[#e09540]" : "text-[#888]"}
               >
                 長者端目前顯示的場景
               </button>
               <span className="text-[#888]"> / </span>
               <button
                 type="button"
                 onClick={() => setView("response")}
                 className={view === "response" ? "text-[#e09540]" : "text-[#888]"}
               >
                 長者的回應
               </button>
             </h2>
           </div>
           <div className="bg-white rounded-xl p-3 md:p-3 lg:p-5 xl:p-6 h-[90px] md:h-[100px] lg:h-[160px] xl:h-[200px] overflow-y-auto">
             <p className="text-[14px] md:text-[16px] lg:text-[20px] text-black leading-relaxed">
               {view === "scene" ? session.currentScene : MOCK_ELDER_RESPONSE}
             </p>
           </div>
         </div>


         {/* AI 建議 */}
         <div className="bg-[#f9fafb] rounded-xl p-3 md:p-3 lg:p-6 xl:p-8 flex flex-col gap-2 md:gap-2 lg:gap-4 xl:gap-5">
           <h3 className="text-[15px] md:text-[18px] lg:text-[20px] font-medium text-[#0a0a0a]">AI 建議追問語（參考用）</h3>
           <p className="text-[13px] md:text-[15px] lg:text-[17px] text-[#0a0a0a]">本回合可引導的方向：</p>
           <div className="flex flex-col gap-2 lg:gap-3 xl:gap-4">
             {session.aiSuggestions.map((s, i) => (
               <button
                 key={i}
                 type="button"
                 className="bg-white border border-[#e5e7eb] rounded-xl py-2 md:py-2 lg:py-4 xl:py-5 px-3 md:px-3 lg:px-5 xl:px-6 text-[13px] md:text-[14px] lg:text-[16px] font-medium text-[#0a0a0a] text-left hover:bg-[#f5f5f5] transition-colors"
               >
                 {s}
               </button>
             ))}
           </div>
         </div>
       </div>


       {/* 右欄 */}
       <div className="w-full sm:w-[220px] md:w-[290px] lg:w-[380px] xl:w-[460px] flex flex-col gap-3 lg:gap-5 xl:gap-6 sm:shrink-0 lg:-translate-y-[2.5%] xl:translate-y-[1.8%]">


         {/* 即時檢測回饋 */}
         <div>
           <h2 className="text-[16px] md:text-[22px] lg:text-[28px] font-medium text-[#0a0a0a] mb-3 lg:mb-4 xl:mb-5">即時檢測回饋</h2>
           <div className="flex gap-3 lg:gap-4 xl:gap-5">
             <div className="bg-white rounded-xl p-3 lg:p-5 xl:p-6 flex-1 flex flex-col gap-1 items-center justify-center">
               <span
                 className="text-[16px] md:text-[20px] lg:text-[24px] font-medium"
                 style={{ color: EMOTION_COLORS[session.emotionState] }}
               >
                 {session.emotionState}
               </span>
               <span className="text-[11px] md:text-[13px] lg:text-[14px] text-[#888]">情緒狀態</span>
             </div>
             <div className="bg-white rounded-xl p-3 lg:p-5 xl:p-6 flex-1 flex flex-col gap-1 items-center justify-center">
               <span className="text-[18px] md:text-[22px] lg:text-[28px] font-medium text-[#0a0a0a]">{session.responseTime}</span>
               <span className="text-[11px] md:text-[13px] lg:text-[14px] text-[#888]">反應時間</span>
             </div>
           </div>
         </div>


         {/* 禁忌話題 */}
         <div className="bg-[#fef2f2] border-[3px] border-[#ffa2a2] rounded-2xl p-3 md:p-3 lg:p-6 xl:p-8 flex flex-col gap-2 lg:gap-3 xl:gap-4">
           <h3 className="text-[14px] md:text-[17px] lg:text-[20px] font-medium text-[#0a0a0a]">禁忌話題提醒</h3>
           <div className="flex gap-3 lg:gap-4 xl:gap-5 flex-wrap">
             {session.tabooTopics.map((topic, i) => (
               <span key={i} className="text-[15px] md:text-[18px] lg:text-[22px] text-[#0a0a0a]">{topic}</span>
             ))}
           </div>
         </div>


         {/* 療程控制 */}
         <div className="flex flex-col gap-2 lg:gap-3 xl:gap-4">
           <h3 className="text-[14px] md:text-[17px] lg:text-[20px] font-medium text-[#0a0a0a]">療程控制</h3>
           <div className="grid grid-cols-2 gap-2 lg:gap-3 xl:gap-4">
             <button
               type="button"
               className="bg-white border border-[#d1d5dc] rounded-xl py-2.5 lg:py-4 xl:py-5 text-[12px] md:text-[14px] lg:text-[18px] font-medium text-[#0a0a0a] hover:bg-[#f5f5f5] transition-colors"
             >
               重播語音
             </button>
             <button
               type="button"
               className="bg-white border border-[#d1d5dc] rounded-xl py-2.5 lg:py-4 xl:py-5 text-[12px] md:text-[14px] lg:text-[18px] font-medium text-[#0a0a0a] hover:bg-[#f5f5f5] transition-colors"
             >
               跳過此場景
             </button>
             <button
               type="button"
               onClick={handlePause}
               disabled={session.status === "paused"}
               className="bg-white border border-[#d1d5dc] rounded-xl py-2.5 lg:py-4 xl:py-5 text-[12px] md:text-[14px] lg:text-[18px] font-medium text-[#0a0a0a] hover:bg-[#f5f5f5] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
             >
               暫停
             </button>
             <button
               type="button"
               onClick={handleResume}
               disabled={session.status === "running"}
               className="bg-white border border-[#d1d5dc] rounded-xl py-2.5 lg:py-4 xl:py-5 text-[12px] md:text-[14px] lg:text-[18px] font-medium text-[#0a0a0a] hover:bg-[#f5f5f5] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
             >
               繼續
             </button>
           </div>
           <button
             type="button"
             onClick={() => setShowConfirm(true)}
             className="bg-[#fb2c36] text-white rounded-xl py-2.5 lg:py-4 xl:py-5 text-[14px] md:text-[16px] lg:text-[18px] font-medium text-center hover:bg-[#e0252e] transition-colors"
           >
             結束療程
           </button>
         </div>
       </div>
     </div>


     {/* 防呆確認視窗 */}
     {showConfirm && (
       <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
         <div className="bg-white rounded-2xl p-7 md:p-9 xl:p-10 flex flex-col gap-5 w-[300px] md:w-[400px] xl:w-[480px] shadow-xl">
           <div className="flex flex-col gap-2">
             <h2 className="text-[20px] md:text-[22px] font-bold text-[#1a1a1a]">確定要結束療程？</h2>
             <p className="text-[13px] md:text-[15px] text-[#888]">結束後將到結束量表，本次療程紀錄將會儲存。</p>
           </div>
           <div className="flex gap-3">
             <button
               type="button"
               onClick={() => setShowConfirm(false)}
               className="flex-1 border border-[#d0d0d0] text-[#1a1a1a] rounded-xl py-3 xl:py-4 text-[14px] md:text-[16px] font-medium hover:bg-[#f5f5f5] transition-colors"
             >
               取消
             </button>
             <button
               type="button"
               onClick={() => router.push(`/activity/${session.sessionId}/end`)}
               className="flex-1 bg-[#fb2c36] text-white rounded-xl py-3 xl:py-4 text-[14px] md:text-[16px] font-medium hover:bg-[#e0252e] transition-colors"
             >
               結束療程
             </button>
           </div>
         </div>
       </div>
     )}
   </div>
 );
}


