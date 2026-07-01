"use client";

import Link from "next/link";
import { use, useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import type { Case, Session } from "@/lib/types";

const RATING_COLOR: Record<string, string> = {
  適當: "#4caf7d",
  亢奮: "#e09540",
  焦躁: "#e09540",
  低落: "#e05c3a",
};

const inputClass =
  "w-full bg-[#f5f5f5] rounded-xl px-4 py-2 text-[15px] text-[#1a1a1a] outline-none focus:bg-[#efefef] transition-colors";

function InfoCard({ label, value }: { label: string; value?: string }) {
  return (
    <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-1">
      <p className="text-[13px] text-[#aaa]">{label}</p>
      <p className="text-[15px] text-[#1a1a1a]">{value || "—"}</p>
    </div>
  );
}

export default function CaseDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [caseData, setCaseData] = useState<Case | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [tab, setTab] = useState<"info" | "history">("info");
  const [isEditing, setIsEditing] = useState(false);

  const [editBirthYear, setEditBirthYear] = useState("");
  const [editBirthPlace, setEditBirthPlace] = useState("");
  const [editCareer, setEditCareer] = useState("");
  const [editFamily, setEditFamily] = useState("");
  const [editHobbies, setEditHobbies] = useState("");
  const [editTaboo, setEditTaboo] = useState("");

  useEffect(() => {
    fetch(`/api/cases/${id}`)
      .then((r) => r.json())
      .then((data) => setCaseData(data))
      .catch(() => {});

    fetch(`/api/cases/${id}/sessions`)
      .then((r) => r.json())
      .then((data) => setSessions(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, [id]);

  function startEditing() {
    if (!caseData) return;
    setEditBirthYear(caseData.birthYear ?? "");
    setEditBirthPlace(caseData.birthPlace ?? "");
    setEditCareer(caseData.career ?? "");
    setEditFamily(caseData.family ?? "");
    setEditHobbies(caseData.hobbies ?? "");
    setEditTaboo(caseData.tabooTopics.join("、"));
    setIsEditing(true);
  }

  async function deleteCase() {
    await fetch(`/api/cases/${id}`, { method: "DELETE" });
    router.push("/dashboard");
  }

  async function saveEditing() {
    if (!caseData) return;
    const res = await fetch(`/api/cases/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: caseData.name,
        birthYear: editBirthYear,
        birthPlace: editBirthPlace,
        career: editCareer,
        family: editFamily,
        hobbies: editHobbies,
        tabooTopics: editTaboo,
      }),
    });
    if (res.ok) {
      const updated = await res.json();
      setCaseData(updated);
    }
    setIsEditing(false);
  }

  if (!caseData) return null;

  const recentSessions = sessions.slice(0, 2);

  return (
    <div className="min-h-screen bg-[#f5e6d3] px-12 py-6 flex flex-col gap-5">

      {/* 返回連結 */}
      <Link
        href="/dashboard"
        className="flex items-center gap-2 text-[#5b8ac5] text-[16px] font-medium hover:text-[#3a6aa0] transition-colors self-start ml-[2%] mt-[6]"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path d="M19 12H5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          <path d="M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
        個案列表
      </Link>

      {/* 主要內容區 */}
      <div className="ml-[20%] w-[60%] flex flex-col gap-5">

        {/* 個案標頭 */}
        <div className="flex items-center gap-4">
          <div
            className="w-14 h-14 rounded-full flex items-center justify-center text-[22px] font-medium text-[#666] shrink-0"
            style={{ backgroundColor: caseData.avatarColor }}
          >
            {caseData.surname}
          </div>
          <div className="flex flex-col gap-0.5">
            <h1 className="text-[26px] font-bold text-[#1a1a1a]">{caseData.name}</h1>
            <p className="text-[13px] text-[#888]">
              共 {caseData.totalSessions} 次療程
            </p>
          </div>

          <div className="ml-auto flex gap-2">
            {isEditing ? (
              <>
                <button
                  type="button"
                  onClick={saveEditing}
                  className="bg-[#5b8ac5] text-white rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#3a6aa0] transition-colors"
                >
                  儲存
                </button>
                <button
                  type="button"
                  onClick={() => setIsEditing(false)}
                  className="border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#f5f5f5] transition-colors"
                >
                  取消
                </button>
              </>
            ) : (
              <>
                {caseData.isActive && (
                  <Link
                    href={`/cases/${id}/start`}
                    className="bg-[#e09540] text-white rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#c07a20] transition-colors"
                  >
                    開始療程
                  </Link>
                )}
                <button
                  type="button"
                  onClick={startEditing}
                  className="bg-white border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#f5f5f5] transition-colors"
                >
                  編輯資料
                </button>
              </>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-6 border-b border-[#e0e0e0]">
          {(["info", "history"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`pb-2 text-[15px] font-medium transition-colors border-b-2 -mb-px ${
                tab === t
                  ? "border-[#5b8ac5] text-[#5b8ac5]"
                  : "border-transparent text-[#888] hover:text-[#1a1a1a]"
              }`}
            >
              {t === "info" ? "基本資料" : "歷次療程"}
            </button>
          ))}
        </div>

        {tab === "info" && (
          <div className="flex flex-col gap-3">
            {isEditing ? (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                    <p className="text-[13px] text-[#aaa]">出生年</p>
                    <input value={editBirthYear} onChange={(e) => setEditBirthYear(e.target.value)} placeholder="例：1938" className={inputClass} />
                  </div>
                  <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                    <p className="text-[13px] text-[#aaa]">出生地</p>
                    <input value={editBirthPlace} onChange={(e) => setEditBirthPlace(e.target.value)} placeholder="例：彰化縣" className={inputClass} />
                  </div>
                  <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                    <p className="text-[13px] text-[#aaa]">職業經歷</p>
                    <input value={editCareer} onChange={(e) => setEditCareer(e.target.value)} className={inputClass} />
                  </div>
                  <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                    <p className="text-[13px] text-[#aaa]">家人</p>
                    <input value={editFamily} onChange={(e) => setEditFamily(e.target.value)} className={inputClass} />
                  </div>
                </div>
                <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                  <p className="text-[13px] text-[#aaa]">興趣</p>
                  <input value={editHobbies} onChange={(e) => setEditHobbies(e.target.value)} className={inputClass} />
                </div>
                <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                  <p className="text-[13px] text-[#aaa]">禁忌話題（用「、」分隔）</p>
                  <input value={editTaboo} onChange={(e) => setEditTaboo(e.target.value)} placeholder="例：家人離世、戰爭細節" className={inputClass} />
                </div>
                <div className="flex justify-start mt-2">
                  <button
                    type="button"
                    onClick={deleteCase}
                    className="bg-[#fff0ed] border border-[#e05c3a] text-[#e05c3a] rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#fde0d8] transition-colors"
                  >
                    刪除個案
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <InfoCard
                    label="出生年"
                    value={caseData.birthYear ? `${caseData.birthYear} 年${caseData.birthPlace ? `（${caseData.birthPlace}）` : ""}` : undefined}
                  />
                  <InfoCard label="職業經歷" value={caseData.career} />
                  <InfoCard label="家人" value={caseData.family} />
                  <InfoCard label="興趣" value={caseData.hobbies} />
                </div>
                <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-1">
                  <p className="text-[13px] text-[#aaa]">禁忌話題</p>
                  <p className="text-[15px] text-[#1a1a1a]">
                    {caseData.tabooTopics.length > 0 ? caseData.tabooTopics.join("、") : "—"}
                  </p>
                </div>
              </>
            )}

            {/* 最近療程 */}
            {!isEditing && recentSessions.length > 0 && (
              <div className="flex flex-col gap-3 mt-2">
                <h2 className="text-[22px] font-bold text-[#e05c3a]">最近療程</h2>
                {recentSessions.map((s, idx) => {
                  const pct = s.score != null ? Math.round((s.score / (s.totalScore ?? 20)) * 100) : 0;
                  const color = RATING_COLOR[s.rating ?? ""] ?? "#888";
                  return (
                    <div key={s.id} className="bg-white rounded-xl px-6 py-4 flex items-center justify-between">
                      <div className="flex flex-col gap-1.5">
                        <div className="flex items-center gap-3">
                          <span className="text-[18px] font-bold text-[#1a1a1a]">第 {sessions.length - idx} 次</span>
                          <span className="text-[14px] text-[#888]">{s.date}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-[13px] text-[#888]">{s.rounds} 個回合</span>
                          <span className="text-[13px] text-[#888]">總分 {s.score ?? "—"}</span>
                          <div className="w-28 h-2 bg-[#eee] rounded-full overflow-hidden">
                            <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
                          </div>
                          <span className="text-[13px] font-medium" style={{ color }}>{s.rating}</span>
                        </div>
                      </div>
                      <Link href={`/activity/${s.id}`} className="text-[15px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors">
                        查看 ›
                      </Link>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {tab === "history" && (
          <div className="flex flex-col gap-3">
            {sessions.length === 0 ? (
              <div className="bg-white rounded-xl px-6 py-10 text-center text-[#888] text-[15px]">尚無療程記錄</div>
            ) : (
              sessions.map((s, idx) => {
                const pct = s.score != null ? s.score : 0;
                const color = RATING_COLOR[s.rating ?? ""] ?? "#888";
                return (
                  <div key={s.id} className="bg-white rounded-xl px-6 py-4 flex items-center justify-between">
                    <div className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-3">
                        <span className="text-[18px] font-bold text-[#1a1a1a]">第 {sessions.length - idx} 次</span>
                        <span className="text-[14px] text-[#888]">{s.date}</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-[13px] text-[#888]">{s.rounds} 個回合</span>
                        <span className="text-[13px] text-[#888]">總分 {s.score ?? "—"}</span>
                        <div className="w-28 h-2 bg-[#eee] rounded-full overflow-hidden">
                          <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
                        </div>
                        <span className="text-[13px] font-medium" style={{ color }}>{s.rating}</span>
                      </div>
                    </div>
                    <Link href={`/activity/${s.id}`} className="text-[15px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors">
                      查看 ›
                    </Link>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
}
