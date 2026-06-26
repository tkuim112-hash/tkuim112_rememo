"use client";


import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { mockActiveSession } from "@/lib/mock-data";


const CRITERIA = [
 { name: "參與度",  options: ["干擾", "被動需提醒", "鼓勵下可配合", "主動參與"] },
 { name: "注意力",  options: ["表現差", "需不斷提醒", "經提醒可配合", "表現佳"] },
 { name: "持續力",  options: ["擅自離開", "需不斷提醒", "經提醒可配合", "表現佳"] },
 { name: "情緒狀況", options: ["低落", "焦躁", "亢奮", "適當"] },
 { name: "互動頻率", options: ["無互動", "僅指令回應", "需引導互動", "主動互動"] },
];


const DEFAULT_SCORES = [2, 3, 2, 3, 3];


export default function SessionEndPage() {
 const router = useRouter();
 const session = mockActiveSession;

 const [scores, setScores] = useState<number[]>(DEFAULT_SCORES);
 const [notes, setNotes] = useState("");
 const [isEditing, setIsEditing] = useState(false);
 const containerRef = useRef<HTMLDivElement>(null);

 useEffect(() => {
   const isDesktop = window.matchMedia("(pointer: fine)").matches;
   if (isDesktop) {
     document.body.classList.add("overflow-hidden");
     containerRef.current?.classList.add("fixed", "inset-0", "overflow-hidden");
     return () => {
       document.body.classList.remove("overflow-hidden");
       containerRef.current?.classList.remove("fixed", "inset-0", "overflow-hidden");
     };
   }
 }, []);

 const total = scores.reduce((sum, s) => sum + (s + 1), 0);
 const maxScore = CRITERIA.length * 4;

 function handleSelect(rowIdx: number, colIdx: number) {
   if (!isEditing) return;
   setScores((prev) => prev.map((s, i) => (i === rowIdx ? colIdx : s)));
 }

 return (
   <div ref={containerRef} className="min-h-screen bg-[#f5e6d3] px-4 md:px-[8%] lg:px-[10%] 2xl:px-[12%] pt-3 md:pt-8 xl:pt-16 2xl:pt-20 pb-3 md:pb-5 lg:pb-6 flex flex-col gap-1.5 md:gap-2">

     {/* 頁首：標題 + 編輯按鈕 */}
     <div className="flex items-start justify-between">
       <div className="flex flex-col gap-0.5 md:gap-1">
         <h1 className="text-[20px] md:text-[26px] lg:text-[28px] xl:text-[32px] 2xl:text-[38px] font-bold text-[#1a1a1a] ml-1 md:ml-2">療程結束 填寫觀察量表</h1>
         <p className="text-[12px] md:text-[13px] lg:text-[15px] 2xl:text-[17px] text-[#888] ml-1 md:ml-2">
           {session.caseName} 第 {6} 次療程 2024.12.17
         </p>
       </div>
       <button
         type="button"
         onClick={() => setIsEditing((v) => !v)}
         className="bg-white border border-[#d0d0d0] rounded-xl px-3 py-1.5 md:px-5 md:py-2 lg:px-6 lg:py-2.5 2xl:px-7 2xl:py-3 text-[12px] md:text-[14px] lg:text-[15px] 2xl:text-[17px] font-medium text-[#1a1a1a] hover:bg-[#f5f5f5] transition-colors mt-[1%] md:mt-[1.5%]"
       >
         {isEditing ? "檢視" : "編輯"}
       </button>
     </div>

     {/* 觀察量表格 */}
     <div className="bg-white rounded-xl md:rounded-2xl overflow-hidden border border-[#e8e8e8] shrink-0">
       <table className="w-full border-collapse">
         <thead>
           <tr>
             <th className="w-[70px] md:w-[110px] lg:w-[140px] xl:w-[160px] 2xl:w-[190px] border-b border-r border-[#e8e8e8]" />
             {[1, 2, 3, 4].map((score) => (
               <th key={score} className="border-b border-r border-[#e8e8e8] last:border-r-0 py-2 md:py-3 lg:py-4 2xl:py-5">
                 <span className="bg-[#b8d0f0] text-[#1a1a1a] text-[12px] md:text-[14px] lg:text-[15px] xl:text-[16px] 2xl:text-[18px] font-semibold rounded-lg md:rounded-xl px-2 py-1 md:px-4 md:py-1.5 lg:px-5 lg:py-2 xl:px-6 2xl:px-7">
                   {score}分
                 </span>
               </th>
             ))}
           </tr>
         </thead>
         <tbody>
           {CRITERIA.map((row, rowIdx) => (
             <tr key={row.name} className="border-b border-[#e8e8e8] last:border-b-0">
               <td className="border-r border-[#e8e8e8] px-2 py-2 md:px-4 md:py-2.5 lg:px-5 lg:py-2.5 xl:px-6 xl:py-3 2xl:px-8 2xl:py-4 text-[12px] md:text-[13px] lg:text-[14px] xl:text-[15px] 2xl:text-[17px] font-medium text-[#1a1a1a]">
                 {row.name}
               </td>
               {row.options.map((option, colIdx) => {
                 const selected = scores[rowIdx] === colIdx;
                 return (
                   <td
                     key={colIdx}
                     onClick={() => handleSelect(rowIdx, colIdx)}
                     className={`border-r border-[#e8e8e8] last:border-r-0 px-1 py-2 md:px-2 md:py-2.5 lg:px-3 lg:py-2.5 xl:px-4 xl:py-3 2xl:px-5 2xl:py-4 text-[11px] md:text-[12px] lg:text-[13px] xl:text-[14px] 2xl:text-[16px] text-center transition-colors ${
                       selected
                         ? "bg-[#e6f4ee] text-[#1a1a1a] font-medium"
                         : isEditing
                         ? "text-[#555] cursor-pointer hover:bg-[#f5f5f5]"
                         : "text-[#555]"
                     }`}
                   >
                     {option}{selected ? " ✓" : ""}
                   </td>
                 );
               })}
             </tr>
           ))}
         </tbody>
       </table>
     </div>

     {/* 今日總分 */}
     <p className="text-[15px] md:text-[18px] lg:text-[20px] xl:text-[22px] 2xl:text-[26px] font-semibold text-[#1a1a1a]">
       今日總分 <span className="text-[24px] md:text-[28px] lg:text-[32px] xl:text-[36px] 2xl:text-[42px] font-bold">{total}</span>
       <span className="text-[#888] text-[13px] md:text-[16px] lg:text-[18px] xl:text-[20px] 2xl:text-[24px]"> /{maxScore}</span>
     </p>

     {/* 治療師觀察備註 */}
     <div className="flex flex-col gap-1 md:gap-1.5 lg:gap-2">
       <h2 className="text-[14px] md:text-[15px] lg:text-[16px] xl:text-[18px] 2xl:text-[20px] font-semibold text-[#1a1a1a]">治療師觀察備註</h2>
       <textarea
         value={notes}
         onChange={(e) => setNotes(e.target.value)}
         placeholder="今天對廟口場景反應特別好，主動提到阿明......"
         className="w-full h-[70px] md:h-[90px] lg:h-[110px] xl:h-[140px] 2xl:h-[160px] bg-white border border-[#e0e0e0] rounded-xl px-3 py-2 md:px-4 md:py-3 lg:px-5 lg:py-3 xl:py-4 2xl:px-6 2xl:py-5 text-[13px] md:text-[14px] lg:text-[15px] 2xl:text-[16px] text-[#1a1a1a] placeholder:text-[#bbb] outline-none resize-none focus:border-[#5b8ac5] transition-colors"
       />
     </div>

     {/* 底部按鈕 */}
     <div className="flex gap-2 md:gap-3 mt-2">
       <button
         type="button"
         onClick={() => router.push("/dashboard")}
         className="bg-[#2b7fff] text-white rounded-xl px-4 py-2 md:px-5 md:py-2.5 lg:px-6 lg:py-3 xl:px-7 xl:py-[14px] 2xl:px-8 2xl:py-4 text-[12px] md:text-[13px] lg:text-[14px] xl:text-[14.5px] 2xl:text-[16px] font-medium hover:bg-[#1a6eee] transition-colors"
       >
         儲存並完成
       </button>
       <button
         type="button"
         onClick={() => router.push("/dashboard")}
         className="bg-white border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-4 py-2 md:px-5 md:py-2.5 lg:px-6 lg:py-3 xl:px-7 xl:py-[14px] 2xl:px-8 2xl:py-4 text-[12px] md:text-[13px] lg:text-[14px] xl:text-[14.5px] 2xl:text-[16px] font-medium hover:bg-[#f5f5f5] transition-colors"
       >
         稍後填寫
       </button>
     </div>
   </div>
 );
}
