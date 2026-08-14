"use client";

import Link from "next/link";
import { use, useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import type { Case } from "@/lib/types";
import { API_BASE } from "@/lib/api";

type DeviceStatus = "connected" | "unstable" | "disconnected";

function IconRefresh({ color }: { color: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="shrink-0">
      <path d="M23 4v6h-6" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconSun({ color }: { color: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="shrink-0">
      <circle cx="12" cy="12" r="5" stroke={color} strokeWidth="2" />
      {[
        ["12","1","12","3"], ["12","21","12","23"],
        ["4.22","4.22","5.64","5.64"], ["18.36","18.36","19.78","19.78"],
        ["1","12","3","12"], ["21","12","23","12"],
        ["4.22","19.78","5.64","18.36"], ["18.36","5.64","19.78","4.22"],
      ].map(([x1, y1, x2, y2], i) => (
        <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth="2" strokeLinecap="round" />
      ))}
    </svg>
  );
}

function IconPower({ color }: { color: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="shrink-0">
      <path d="M18.36 6.64a9 9 0 1 1-12.73 0" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="12" y1="2" x2="12" y2="12" stroke={color} strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SuggestionCard({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="bg-white rounded-2xl px-5 py-4 flex items-center gap-3">
      {icon}
      <p className="text-[14px] text-[#1a1a1a]">{text}</p>
    </div>
  );
}

export default function StartSessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: caseId } = use(params);
  const router = useRouter();
  const [scene, setScene] = useState("");
  // 目前沒有真正的 Kinect 連線檢查機制，先預設為已連線，避免擋住啟動活動按鈕；
  // 「使用真實訊號」開關打開後，這個值會改由下方 polling Unity 校正狀態的 effect 覆蓋。
  const [status, setStatus] = useState<DeviceStatus>("connected");
  const [useRealSignal, setUseRealSignal] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [caseData, setCaseData] = useState<Case | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [suggestedTopic, setSuggestedTopic] = useState<string | null>(null);
  const [suggestionLoading, setSuggestionLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/cases/${caseId}`)
      .then((r) => r.json())
      .then((data) => setCaseData(data))
      .catch(() => {});

    fetch(`/api/cases/${caseId}/suggested-topic`)
      .then((r) => r.ok ? r.json() : { topic: null })
      .then((data) => setSuggestedTopic(data.topic ?? null))
      .catch(() => setSuggestedTopic(null))
      .finally(() => setSuggestionLoading(false));

    // 跟 Unity 共用同一組 session_id（見 KinectCalibrationManager 校正前的換取邏輯），
    // 這樣 Unity 回報的校正完成狀態才能對應到這個病患這次要開的活動。
    fetch(`${API_BASE}/session/pending?patient_id=${encodeURIComponent(caseId)}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => setSessionId(data?.session_id ?? null))
      .catch(() => setSessionId(null));
  }, [caseId]);

  // 開發者「使用真實訊號」開關：每 2 秒問後端 Unity 是否已完成校正，
  // 完成就自動切到「連線正常」解鎖啟動活動；開關關閉時維持原本手動三顆按鈕的 demo 行為。
  useEffect(() => {
    if (!useRealSignal || !sessionId) return;

    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/session/${sessionId}/status`, { credentials: "include" });
        if (!res.ok || cancelled) return;
        const data = await res.json();
        setStatus(data.calibrated ? "connected" : "disconnected");
      } catch {
        // 忽略單次輪詢失敗，2 秒後重試
      }
    };
    poll();
    const interval = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [useRealSignal, sessionId]);

  if (!caseData) return null;

  const nextSession = caseData.totalSessions + 1;

  const handleStart = async () => {
    setIsStarting(true);
    setStartError("");
    try {
      const newSessionId = sessionId || crypto.randomUUID();
      const topic = scene.trim() || suggestedTopic || "";
      const res = await fetch(
        `${API_BASE}/session/start?user_id=${encodeURIComponent(caseId)}&session_id=${encodeURIComponent(newSessionId)}&topic=${encodeURIComponent(topic)}`,
        { method: "POST", credentials: "include" }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "啟動活動失敗，請確認長者端裝置與後端服務狀態");
      }
      router.push(`/activity/${newSessionId}?caseId=${caseId}&live=1`);
    } catch (e) {
      setStartError(e instanceof Error ? e.message : "啟動活動失敗，請稍後再試");
      setIsStarting(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#f5e6d3] px-12 pt-6 pb-36 flex flex-col">
      {/* 返回 */}
      <Link
        href={`/cases/${caseId}`}
        className="flex items-center gap-1.5 text-[#1a1a1a] text-[15px] font-medium mb-5 self-start"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path d="M19 12H5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {caseData.name}
      </Link>

      <div className="max-w-[680px] w-full mx-auto flex flex-col gap-5">
        {/* 標題 */}
        <div className="flex flex-col gap-1">
          <h1 className="text-[28px] font-bold text-[#1a1a1a] mt-[0.8%]">開始活動</h1>
        </div>

        {/* 長者 */}
        <div className="flex flex-col gap-2">
          <h2 className="text-[15px] font-semibold text-[#1a1a1a]">長者</h2>
          <div className="bg-white rounded-2xl px-5 py-4 flex items-center gap-4">
            {caseData.avatar ? (
              <img src={caseData.avatar} alt={caseData.name} className="w-12 h-12 rounded-full object-cover shrink-0" />
            ) : (
              <div
                className="w-12 h-12 rounded-full flex items-center justify-center text-[18px] font-medium text-[#666] shrink-0"
                style={{ backgroundColor: caseData.avatarColor }}
              >
                {caseData.surname}
              </div>
            )}
            <div>
              <p className="text-[17px] font-medium text-[#1a1a1a]">{caseData.name}</p>
              <p className="text-[13px] text-[#888]">第 {nextSession} 次活動</p>
            </div>
          </div>
        </div>

        {/* 起始場景 */}
        <div className="flex flex-col gap-2">
          <h2 className="text-[15px] font-semibold text-[#1a1a1a]">起始場景</h2>
          <div className="bg-white rounded-2xl px-5 py-4">
            <p className="text-[14px] text-[#888]">
              {suggestionLoading
                ? "AI 建議：載入中…"
                : suggestedTopic
                ? `AI 建議：${suggestedTopic}（上次反應最佳）`
                : "尚無歷史活動資料，暫無 AI 建議，可手動輸入場景描述"}
            </p>
          </div>
          <p className="text-[13px] text-[#888] mt-0.5">或手動輸入場景描述</p>
          <textarea
            value={scene}
            onChange={(e) => setScene(e.target.value)}
            placeholder="輸入自訂場景..."
            rows={4}
            className="bg-white rounded-2xl px-5 py-4 text-[14px] text-[#1a1a1a] placeholder:text-[#1a1a1a]/30 outline-none resize-none border border-transparent focus:border-[#d0d0d0] transition-colors"
          />
        </div>

        {/* 裝置狀態：連線正常 */}
        {status === "connected" && (
          <div className="bg-[#e8f7ef] rounded-2xl px-5 py-4 flex items-center justify-between border border-[#b8e8ce]">
            <div className="flex items-center gap-3">
              <span className="w-3 h-3 rounded-full bg-[#2e9e5b] shrink-0" />
              <div>
                <p className="text-[15px] font-semibold text-[#2e9e5b]">Kinect 連線正常</p>
                <p className="text-[13px] text-[#2e9e5b]/80">骨架偵測就緒，可以開始活動</p>
              </div>
            </div>
          </div>
        )}

        {/* 裝置狀態：骨架不穩定 */}
        {status === "unstable" && (
          <>
            <div className="bg-[#fff8ee] rounded-2xl px-5 py-4 flex items-center justify-between border border-[#f5d999]">
              <div className="flex items-center gap-3">
                <span className="w-3 h-3 rounded-full bg-[#e09540] shrink-0" />
                <div>
                  <p className="text-[15px] font-semibold text-[#c07a20]">骨架偵測不穩定</p>
                  <p className="text-[13px] text-[#c07a20]/80">可能影響手勢辨識，建議重新校正後再開始</p>
                </div>
              </div>
              <button
                type="button"
                className="bg-[#e09540] text-white text-[14px] font-medium rounded-full px-4 py-2 whitespace-nowrap"
              >
                重新校正
              </button>
            </div>
            <div className="flex flex-col gap-2">
              <SuggestionCard
                icon={<IconRefresh color="#e09540" />}
                text="重新偵測骨架（讓長者移動一下再試）"
              />
              <SuggestionCard
                icon={<IconSun color="#e09540" />}
                text="調整環境光線後重試"
              />
            </div>
          </>
        )}

        {/* 裝置狀態：未連線 */}
        {status === "disconnected" && (
          <>
            <div className="bg-[#fff0f0] rounded-2xl px-5 py-4 flex items-center justify-between border border-[#ffb3b3]">
              <div className="flex items-center gap-3">
                <span className="w-3 h-3 rounded-full bg-[#e05c3a] shrink-0" />
                <div>
                  <p className="text-[15px] font-semibold text-[#c03a20]">Kinect 裝置未連線</p>
                  <p className="text-[13px] text-[#c03a20]/80">請檢查 USB 連接，或重新啟動裝置</p>
                </div>
              </div>
              <button
                type="button"
                className="bg-[#e05c3a] text-white text-[14px] font-medium rounded-full px-4 py-2 whitespace-nowrap"
              >
                重新連線
              </button>
            </div>
            <div className="flex flex-col gap-2">
              <SuggestionCard
                icon={<IconRefresh color="#e09540" />}
                text="重新初始化 Kinect 連線"
              />
              <SuggestionCard
                icon={<IconPower color="#e05c3a" />}
                text="完整重啟裝置服務"
              />
            </div>
          </>
        )}

        {/* Demo 狀態切換（原型用；開啟「使用真實訊號」後改由 Unity 校正狀態驅動，此處僅供檢視、停用點擊） */}
        <div className="flex flex-col items-center gap-1.5 pt-1">
          <div className="flex gap-2 self-center">
            {(["connected", "unstable", "disconnected"] as DeviceStatus[]).map((s) => (
              <button
                key={s}
                type="button"
                disabled={useRealSignal}
                onClick={() => setStatus(s)}
                className={`text-[11px] px-3 py-1 rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                  status === s
                    ? "bg-[#1a1a1a] text-white border-[#1a1a1a]"
                    : "bg-white/60 text-[#888] border-[#d0d0d0]"
                }`}
              >
                {{ connected: "連線正常", unstable: "不穩定", disconnected: "未連線" }[s]}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setUseRealSignal((v) => !v)}
            className={`text-[11px] px-3 py-1 rounded-full border transition-colors ${
              useRealSignal
                ? "bg-[#5b8ac5] text-white border-[#5b8ac5]"
                : "bg-white/60 text-[#888] border-[#d0d0d0]"
            }`}
          >
            {useRealSignal ? "使用真實訊號（開發者）：開" : "使用真實訊號（開發者）：關"}
          </button>
        </div>
      </div>

      {/* 固定底部按鈕 */}
      <div className="fixed bottom-0 left-0 right-0 px-5 pb-6 pt-3 bg-[#f5e6d3]">
        <div className="max-w-[680px] w-full mx-auto flex flex-col gap-2">
          {startError && (
            <p className="text-[13px] text-[#e05c3a] bg-[#fff0f0] border border-[#ffb3b3] rounded-xl px-4 py-2.5">
              {startError}
            </p>
          )}
          <div className="flex gap-3">
          {status === "connected" && (
            <button
              type="button"
              onClick={handleStart}
              disabled={isStarting}
              className="flex-1 bg-[#5b8ac5] text-white text-[17px] font-semibold rounded-2xl py-4 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {isStarting ? "啟動中…" : "啟動活動"}
            </button>
          )}
          {status === "unstable" && (
            <button
              type="button"
              disabled
              className="flex-1 bg-[#d0d0d0] text-[#999] text-[17px] font-semibold rounded-2xl py-4 cursor-not-allowed"
            >
              啟動活動（需解決不穩定問題）
            </button>
          )}
          {status === "disconnected" && (
            <button
              type="button"
              disabled
              className="flex-1 bg-[#d0d0d0] text-[#999] text-[17px] font-semibold rounded-2xl py-4 cursor-not-allowed"
            >
              啟動活動（需先解決連線問題）
            </button>
          )}
          <Link
            href={`/cases/${caseId}`}
            className="bg-white text-[#1a1a1a] text-[17px] font-semibold rounded-2xl px-6 py-4 flex items-center justify-center"
          >
            取消
          </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
