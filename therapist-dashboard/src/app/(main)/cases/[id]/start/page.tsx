"use client";

import Link from "next/link";
import { use, useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { mockCases } from "@/lib/mock-data";
import type { Case } from "@/lib/types";

type DeviceStatus = "connected" | "unstable" | "disconnected";

const AI_SUGGESTION = "AI 建議：台中紡織廠黃昏（上次反應最佳）";

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
  const [status, setStatus] = useState<DeviceStatus>("unstable");
  const [localCase, setLocalCase] = useState<Case | null>(null);

  const mockCase = mockCases.find((c) => c.id === caseId) ?? null;

  useEffect(() => {
    if (!mockCase) {
      const stored: Case[] = JSON.parse(localStorage.getItem("rememo_cases") ?? "[]");
      const found = stored.find((c) => c.id === caseId);
      if (found) setLocalCase(found);
    }
  }, [caseId, mockCase]);

  const caseData = mockCase ?? localCase;
  if (!caseData) return null;

  const nextSession = caseData.totalSessions + 1;

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
          <h1 className="text-[28px] font-bold text-[#1a1a1a] mt-[0.8%]">開始療程</h1>
          <p className="text-[13px] text-[#888]">確認設定後，系統將啟動 AI 者編故事</p>
        </div>

        {/* 長者 */}
        <div className="flex flex-col gap-2">
          <h2 className="text-[15px] font-semibold text-[#1a1a1a]">長者</h2>
          <div className="bg-white rounded-2xl px-5 py-4 flex items-center gap-4">
            <div
              className="w-12 h-12 rounded-full flex items-center justify-center text-[18px] font-medium text-[#666] shrink-0"
              style={{ backgroundColor: caseData.avatarColor }}
            >
              {caseData.surname}
            </div>
            <div>
              <p className="text-[17px] font-medium text-[#1a1a1a]">{caseData.name}</p>
              <p className="text-[13px] text-[#888]">第 {nextSession} 次療程</p>
            </div>
          </div>
        </div>

        {/* 起始場景 */}
        <div className="flex flex-col gap-2">
          <h2 className="text-[15px] font-semibold text-[#1a1a1a]">起始場景</h2>
          <div className="bg-white rounded-2xl px-5 py-4">
            <p className="text-[14px] text-[#888]">{AI_SUGGESTION}</p>
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
                <p className="text-[13px] text-[#2e9e5b]/80">骨架偵測就緒，可以開始療程</p>
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

        {/* Demo 狀態切換（原型用） */}
        <div className="flex gap-2 self-center pt-1">
          {(["connected", "unstable", "disconnected"] as DeviceStatus[]).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStatus(s)}
              className={`text-[11px] px-3 py-1 rounded-full border transition-colors ${
                status === s
                  ? "bg-[#1a1a1a] text-white border-[#1a1a1a]"
                  : "bg-white/60 text-[#888] border-[#d0d0d0]"
              }`}
            >
              {{ connected: "連線正常", unstable: "不穩定", disconnected: "未連線" }[s]}
            </button>
          ))}
        </div>
      </div>

      {/* 固定底部按鈕 */}
      <div className="fixed bottom-0 left-0 right-0 px-5 pb-6 pt-3 bg-[#f5e6d3]">
        <div className="max-w-[680px] w-full mx-auto flex gap-3">
          {status === "connected" && (
            <button
              type="button"
              onClick={() => router.push("/activity/s-live-1")}
              className="flex-1 bg-[#5b8ac5] text-white text-[17px] font-semibold rounded-2xl py-4"
            >
              啟動療程
            </button>
          )}
          {status === "unstable" && (
            <button
              type="button"
              disabled
              className="flex-1 bg-[#d0d0d0] text-[#999] text-[17px] font-semibold rounded-2xl py-4 cursor-not-allowed"
            >
              啟動療程（需解決不穩定問題）
            </button>
          )}
          {status === "disconnected" && (
            <button
              type="button"
              disabled
              className="flex-1 bg-[#d0d0d0] text-[#999] text-[17px] font-semibold rounded-2xl py-4 cursor-not-allowed"
            >
              啟動療程（需先解決連線問題）
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
  );
}
