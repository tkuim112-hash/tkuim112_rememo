"use client";


import { useState, Fragment, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import type { ActiveSession } from "@/lib/types";
import { API_BASE } from "@/lib/api";
import { EMOTION_COLORS, DIMENSION_COLORS, groupSignalsByDimension, reasonCaption } from "@/lib/emotionSignals";
import { EmotionBar } from "@/components/EmotionBar";


type View = "scene" | "response";


export function LiveSessionView({ sessionId, caseId }: { sessionId: string; caseId?: string }) {
 const [session, setSession] = useState<ActiveSession>({
   sessionId,
   caseId: "",
   caseName: "",
   currentRound: 1,
   totalRounds: 3,
   status: "running",
   currentScene: "",
   elderResponse: "",
   emotionState: "",
   responseTime: "—",
   aiSuggestions: [],
   tabooTopics: [],
   reviewStatus: "",
   elderResponseDraft: "",
   engagementPct: 0,
   happinessPct: 0,
   agitationPct: 0,
   signalCodes: [],
 });
 const [currentRound, setCurrentRound] = useState(1);
 const [view, setView] = useState<View>("scene");


 const router = useRouter();
 const [showConfirm, setShowConfirm] = useState(false);

 // 治療師正在編輯、還沒送出的審核文字（一開始是空字串，等偵測到新的待審核
 // 內容才會被填入，見下面那個 useEffect）。
 const [draftText, setDraftText] = useState("");
 const [confirming, setConfirming] = useState(false);
 const [confirmError, setConfirmError] = useState(false);
 // 用來記住「上一次看到的 reviewStatus」，這樣才能只在它從空字串「變成」
 // pending 的那一瞬間做事（帶入草稿文字、自動切到回應分頁），而不是每 2 秒
 // polling 都重複做一次、把治療師正在打的字蓋掉。
 const prevReviewStatusRef = useRef<ActiveSession["reviewStatus"]>("");

 useEffect(() => {
   if (session.reviewStatus !== "" && prevReviewStatusRef.current === "") {
     setDraftText(session.elderResponseDraft);
     setConfirmError(false);
     setView("response");
   }
   prevReviewStatusRef.current = session.reviewStatus;
 }, [session.reviewStatus, session.elderResponseDraft]);


 useEffect(() => {
   if (window.innerWidth >= 1024) {
     document.body.classList.add("overflow-hidden");
     return () => document.body.classList.remove("overflow-hidden");
   }
 }, []);

 // 從 API 取得個案資料（caseName、tabooTopics）
 useEffect(() => {
   const fetchCase = async (id: string) => {
     const c = await fetch(`/api/cases/${id}`).then((r) => r.ok ? r.json() : null);
     if (!c) return;
     setSession((prev) => ({
       ...prev,
       caseId: id,
       caseName: c.name ?? "",
       tabooTopics: c.tabooTopics ?? [],
     }));
   };

   if (caseId) {
     fetchCase(caseId).catch(() => {});
   } else {
     fetch(`/api/sessions/${sessionId}`)
       .then((r) => r.ok ? r.json() : null)
       .then((s) => { if (s?.caseId) fetchCase(s.caseId); })
       .catch(() => {});
   }
 }, [sessionId, caseId]);

 // 從後端 polling 情緒、反應時間與場景資訊。Unity 端是每 2 秒送一次感測
 // 資料（見 KinectSensorSender.cs sendInterval），原本這裡也用 2 秒
 // polling、跟來源同頻，但兩邊沒有對齊時脈，最壞情況 polling 剛好卡在
 // Unity 更新前一刻，要等下一輪才追上，等於白白多等快 2 秒、判斷依據百分比
 // 感覺卡卡的。改成 500ms polling，來源資料還是 2 秒才變一次，但能把「沒
 // 對齊」造成的最差延遲收斂到 0.5 秒內。
 useEffect(() => {
   let cancelled = false;
   let timer: ReturnType<typeof setTimeout>;

   const poll = async () => {
     try {
       const res = await fetch(`${API_BASE}/session/${sessionId}/metrics`, {
         credentials: "include",
       });
       if (res.ok) {
         const data = await res.json();
         // 長者答完心得、後端算完評估分數後 session:{id}:meta 會被清掉
         // （見 app/routers/session.py session_metrics 的 session_completed 說明），
         // 不用等治療師自己按「結束活動」，直接自動跳轉到結束頁面。
         if (data.session_completed) {
           router.push(`/activity/${sessionId}/end?from=live`);
           return;
         }
         setSession((s) => ({
           ...s,
           emotionState: data.emotion ?? s.emotionState,
           responseTime: data.response_time ?? s.responseTime,
           currentScene: data.current_scene ?? s.currentScene,
           elderResponse: data.elder_response ?? s.elderResponse,
           currentRound: data.current_round ?? s.currentRound,
           totalRounds: data.total_rounds ?? s.totalRounds,
           reviewStatus: data.review_status ?? s.reviewStatus,
           elderResponseDraft: data.elder_response_draft ?? s.elderResponseDraft,
           engagementPct: data.engagement_pct ?? s.engagementPct,
           happinessPct: data.happiness_pct ?? s.happinessPct,
           agitationPct: data.agitation_pct ?? s.agitationPct,
           signalCodes: data.signal_codes ?? s.signalCodes,
         }));
         if (data.current_round) setCurrentRound(data.current_round);
       }
     } catch {
       // 網路暫時中斷時保留上次數值，不中斷顯示
     } finally {
       // 用 setTimeout 串行而不是 setInterval，避免請求偶爾變慢時同時有
       // 多個 in-flight request 疊加、彼此搶著更新畫面。
       if (!cancelled) timer = setTimeout(poll, 500);
     }
   };

   poll();
   return () => {
     cancelled = true;
     clearTimeout(timer);
   };
 }, [sessionId]);


 const sendControl = (action: string) =>
   fetch(`${API_BASE}/session/${sessionId}/control`, {
     method: "POST",
     headers: { "Content-Type": "application/json" },
     credentials: "include",
     body: JSON.stringify({ action }),
   }).catch(() => {});

 const handleReplay = () => sendControl("replay_audio");
 const handleSkip = () => sendControl("skip_scene");
 const handlePause = () => {
   setSession((s) => ({ ...s, status: "paused" }));
   sendControl("pause");
 };
 const handleResume = () => {
   setSession((s) => ({ ...s, status: "running" }));
   sendControl("resume");
 };
 const handleEnd = async () => {
   await sendControl("end");
   router.push(`/activity/${sessionId}/end?from=live`);
 };

 // 「確認並送回長者畫面」按鈕——回合1-3跟心得回合是兩支不同的後端API，
 // 用 reviewStatus 判斷現在是哪一種，打對應的那支。判斷式要同時涵蓋
 // "pending_*"（第一次確認前）跟 "awaiting_*_submit"（確認過、長者還沒
 // 按送出、治療師正在重新編輯）兩種階段，不然重新編輯時 isClosing 判斷
 // 錯誤，會打錯 API（2026-09-06 稽核：兩種回合的 awaiting 狀態改成分開
 // 命名就是為了這裡能分辨）。
 const handleConfirmResponse = async () => {
   const isClosing =
     session.reviewStatus === "pending_closing" ||
     session.reviewStatus === "awaiting_closing_submit";
   setConfirming(true);
   setConfirmError(false);
   try {
     const url = isClosing
       ? `${API_BASE}/session/${sessionId}/closing/confirm_response`
       : `${API_BASE}/session/${sessionId}/confirm_response`;
     const res = await fetch(url, {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       credentials: "include",
       body: JSON.stringify({ elder_response: draftText }),
     });
     if (!res.ok) throw new Error("confirm_response failed");
     // 樂觀更新：不用等下一次 polling，按下去畫面就先反映結果。2026-09-06
     // 改版後兩種回合都一樣——治療師確認只是把文字送給長者看，真正的
     // 生成/評估要等長者自己按下「送出故事」才觸發（見 app/routers/
     // session.py session_confirm_response／session_closing_confirm_
     // response 說明），這裡故意不清空 reviewStatus，讓編輯框繼續開著，
     // 長者按送出前治療師都能重新編輯、再送一次（後端 pending_review／
     // pending_closing_review 支援重複確認覆蓋，見該端點）。真正結束的
     // 訊號等長者按送出後，下一次 polling 到 metrics 的 review_status
     // 變空字串時才會反映出來。
     setSession((s) => ({
       ...s,
       reviewStatus: isClosing ? "awaiting_closing_submit" : "awaiting_round_submit",
       elderResponse: draftText,
     }));
   } catch {
     // 這支失敗代表長者會一直卡在「等待治療師確認中」，不能像 sendControl
     // 那樣靜默吞掉，要讓治療師看到錯誤、可以重試。
     setConfirmError(true);
   } finally {
     setConfirming(false);
   }
 };

 const signalsByDimension = groupSignalsByDimension(session.signalCodes);

 return (
   <div className="min-h-screen lg:h-screen lg:overflow-hidden bg-[#f5e6d3] px-4 md:px-6 lg:px-8 xl:px-14 pt-3 md:pt-[3vh] lg:pt-[3vh] xl:pt-[4vh] 2xl:pt-[9vh] pb-4 flex flex-col gap-2 md:gap-2 lg:gap-4 xl:gap-5" >


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
                 {session.reviewStatus !== "" && (
                   <span className="inline-block w-2 h-2 rounded-full bg-[#fb2c36] ml-1.5 align-middle" />
                 )}
               </button>
             </h2>
           </div>
           {view === "response" && session.reviewStatus !== "" ? (
             <div className="bg-white rounded-xl p-3 md:p-3 lg:p-5 xl:p-6 h-[170px] md:h-[210px] lg:h-[270px] xl:h-[320px] flex flex-col gap-2 lg:gap-3">
               <p className="shrink-0 text-[14px] md:text-[16px] lg:text-[18px] xl:text-[20px] text-[#7a4a28] font-medium">
                 {session.reviewStatus === "awaiting_round_submit" ||
                 session.reviewStatus === "awaiting_closing_submit"
                   ? "已送給長者確認，長者按下送出前都還能再次編輯後重新送出"
                   : "長者剛講完話，確認或編輯後送回長者畫面"}
               </p>
               <textarea
                 value={draftText}
                 onChange={(e) => setDraftText(e.target.value)}
                 className="flex-1 min-h-0 w-full text-[14px] md:text-[16px] lg:text-[20px] text-black leading-relaxed border border-[#e5e7eb] rounded-lg p-2 resize-none focus:outline-none focus:border-[#7a4a28]"
               />
               {confirmError && (
                 <p className="shrink-0 text-[12px] md:text-[13px] text-[#fb2c36]">送出失敗，請重試</p>
               )}
               <button
                 type="button"
                 onClick={handleConfirmResponse}
                 disabled={confirming || draftText.trim() === ""}
                 className="shrink-0 self-end bg-[#7a4a28] text-white rounded-lg py-2 px-4 text-[13px] md:text-[15px] lg:text-[16px] font-medium hover:bg-[#5f3a1f] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
               >
                 {confirming ? "送出中…" : "確認並送回長者畫面"}
               </button>
             </div>
           ) : (
             <div className="bg-white rounded-xl p-3 md:p-3 lg:p-5 xl:p-6 h-[170px] md:h-[210px] lg:h-[270px] xl:h-[320px] overflow-y-auto">
               <p className="text-[14px] md:text-[16px] lg:text-[20px] text-black leading-relaxed">
                 {view === "scene" ? session.currentScene : session.elderResponse}
               </p>
             </div>
           )}
         </div>


         {/* 情緒判斷依據（原本「操作建議」的外框位置/樣式不動，只換內容）。改回自然高度
             不撐滿——訊號標籤是空的時候（療程剛開始、還沒收到Kinect資料）內容會比右欄
             短很多，強行拉長對齊底部反而會留下一大塊空白，比高度沒對齊更難看。 */}
         <div className="bg-[#f9fafb] rounded-xl p-3 md:p-3 lg:p-6 xl:p-8 flex flex-col gap-2 md:gap-2 lg:gap-4 xl:gap-5">
           <h3 className="text-[15px] md:text-[18px] lg:text-[20px] font-medium text-[#0a0a0a]">
             Kinect 情緒判斷為{" "}
             <span
               className="text-[19px] md:text-[22px] lg:text-[26px] font-bold"
               style={{ color: session.emotionState ? EMOTION_COLORS[session.emotionState] : undefined }}
             >
               {session.emotionState || "—"}
             </span>
           </h3>
           <p className="text-[12px] text-[#9aa1ab]">{reasonCaption(session.happinessPct, session.agitationPct)}</p>
           <div className="flex flex-col sm:flex-row gap-3 lg:gap-6 xl:gap-8">
             <EmotionBar label="投入度" pct={session.engagementPct} color={DIMENSION_COLORS.engagement} codes={signalsByDimension.engagement} dimension="engagement" />
             <EmotionBar label="臉部表情（正負向）" pct={session.happinessPct} color={DIMENSION_COLORS.happiness} codes={signalsByDimension.happiness} dimension="happiness" />
             <EmotionBar label="肢體與語調（激動程度）" pct={session.agitationPct} color={DIMENSION_COLORS.agitation} codes={signalsByDimension.agitation} dimension="agitation" />
           </div>
           <p className="text-[11px] md:text-[12px] text-[#888] leading-relaxed">
             Kinect 依臉部表情、姿勢與聲音特徵綜合判斷，僅供參考，仍以治療師實際觀察為主。
           </p>
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
                 style={{ color: session.emotionState ? EMOTION_COLORS[session.emotionState] : undefined }}
               >
                 {session.emotionState || "—"}
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


         {/* 活動控制 */}
         <div className="flex flex-col gap-2 lg:gap-3 xl:gap-4">
           <h3 className="text-[14px] md:text-[17px] lg:text-[20px] font-medium text-[#0a0a0a]">活動控制</h3>
           <div className="grid grid-cols-2 gap-2 lg:gap-3 xl:gap-4">
             <button
               type="button"
               onClick={handleReplay}
               className="bg-white border border-[#d1d5dc] rounded-xl py-2.5 lg:py-4 xl:py-5 text-[12px] md:text-[14px] lg:text-[18px] font-medium text-[#0a0a0a] hover:bg-[#f5f5f5] transition-colors"
             >
               重播語音
             </button>
             <button
               type="button"
               onClick={handleSkip}
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
             結束活動
           </button>
         </div>
       </div>
     </div>


     {/* 防呆確認視窗 */}
     {showConfirm && (
       <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
         <div className="bg-white rounded-2xl p-7 md:p-9 xl:p-10 flex flex-col gap-5 w-[300px] md:w-[400px] xl:w-[480px] shadow-xl">
           <div className="flex flex-col gap-2">
             <h2 className="text-[20px] md:text-[22px] font-bold text-[#1a1a1a]">確定要結束活動？</h2>
             <p className="text-[13px] md:text-[15px] text-[#888]">結束後將到結束量表，本次活動紀錄將會儲存。</p>
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
               onClick={handleEnd}
               className="flex-1 bg-[#fb2c36] text-white rounded-xl py-3 xl:py-4 text-[14px] md:text-[16px] font-medium hover:bg-[#e0252e] transition-colors"
             >
               結束活動
             </button>
           </div>
         </div>
       </div>
     )}
   </div>
 );
}


