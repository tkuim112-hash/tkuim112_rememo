"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { mockCases, mockTherapist } from "@/lib/mock-data";
import type { Case } from "@/lib/types";

export default function DashboardPage() {
  const [cases, setCases] = useState<Case[]>([]);
  const [search, setSearch] = useState("");
  const [displayName, setDisplayName] = useState(mockTherapist.name);
  const [displayInstitution, setDisplayInstitution] = useState(mockTherapist.institution);

  useEffect(() => {
    setDisplayName(localStorage.getItem("rememo_name") ?? mockTherapist.name);
    setDisplayInstitution(localStorage.getItem("rememo_institution") ?? mockTherapist.institution);
  }, []);

  useEffect(() => {
    const stored: Case[] = JSON.parse(localStorage.getItem("rememo_cases") ?? "[]");
    const deleted: string[] = JSON.parse(localStorage.getItem("rememo_deleted") ?? "[]");
    const deletedSet = new Set(deleted);
    const mockIds = new Set(mockCases.map((c) => c.id));
    const localOnly = stored.filter((c) => !mockIds.has(c.id) && !deletedSet.has(c.id));
    const activeMock = mockCases.filter((c) => !deletedSet.has(c.id));
    setCases([...activeMock, ...localOnly]);
  }, []);

  const filtered = cases.filter((c) => c.name.includes(search));
  const isEmpty = cases.length === 0;

  return (
    <div className="min-h-screen bg-[#f5e6d3] p-8 flex flex-col gap-6">

      {/* 頂部導覽列 */}
      <nav className="bg-white rounded-2xl shadow-sm px-8 py-5 flex items-center justify-between">
        <span className="text-[25px] font-medium text-[#1a1a1a] tracking-tight">
          {displayInstitution}　{displayName}
        </span>
        <div className="flex items-center gap-4">
          <Link
            href="/account"
            className="border border-[#1a1a1a] rounded-xl px-5 py-2.5 text-[15px] font-medium text-[#1a1a1a] hover:bg-[#f5f5f5] transition-colors"
          >
            帳號設定
          </Link>
          <Link
            href="/login"
            className="border border-[#1a1a1a] rounded-xl px-5 py-2.5 text-[15px] font-medium text-[#1a1a1a] hover:bg-[#f5f5f5] transition-colors flex items-center gap-2"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <polyline points="16 17 21 12 16 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <line x1="21" y1="12" x2="9" y2="12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            登出
          </Link>
        </div>
      </nav>

      {/* 個案列表區塊 */}
      <div className="flex flex-col gap-4">

        {/* 標題列 */}
        <div className="flex items-center justify-between">
          <div className="flex flex-col gap-0.5 ml-[0.2%]">
            <h1 className="text-[29px] font-semibold text-[#1a1a1a]">個案列表</h1>
            <p className="text-[13px] text-[#888] ml-[0.2%]">共 {cases.length} 位長者</p>
          </div>
          <Link
            href="/cases/new"
            className="bg-[#E8F3FF] text-[#5B8AC5] rounded-xl px-5 py-3 text-[15px] font-medium hover:bg-[#D6E9FF] transition-colors flex items-center gap-2 mr-[0.8%]"
          >
            <span className="text-lg leading-none">+</span> 新增個案
          </Link>
        </div>

        {/* 搜尋列 */}
        <div className="relative">
          <svg
            width="18" height="18" viewBox="0 0 24 24" fill="none"
            className="absolute left-4 top-1/2 -translate-y-1/2 text-[#888]"
          >
            <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜尋個案姓名..."
            className="w-full bg-white border border-[#e0e0e0] rounded-xl pl-12 pr-4 py-4 text-[15px] text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors"
          />
        </div>

        {/* 內容區：空狀態或個案列表 */}
        {isEmpty ? (
          /* 無個案初始畫面 */
          <div className="bg-white rounded-2xl flex flex-col items-center justify-center gap-4 py-32">
            <div className="w-20 h-20 rounded-full bg-[#fde8e4] flex items-center justify-center">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
                <line x1="12" y1="5" x2="12" y2="19" stroke="#e05c3a" strokeWidth="2.5" strokeLinecap="round"/>
                <line x1="5" y1="12" x2="19" y2="12" stroke="#e05c3a" strokeWidth="2.5" strokeLinecap="round"/>
              </svg>
            </div>
            <p className="text-[20px] font-semibold text-[#1a1a1a]">尚無個案資料</p>
            <p className="text-[14px] text-[#888]">點擊右上角「個案新建」按鈕建立第一個個案</p>
          </div>
        ) : (
          /* 個案列表 */
          <div className="flex flex-col gap-3">
            {filtered.length === 0 ? (
              <div className="bg-white rounded-2xl flex flex-col items-center justify-center gap-4 py-24">
                <p className="text-[16px] text-[#888]">找不到符合「{search}」的個案</p>
              </div>
            ) : (
              filtered.map((c) => (
                <div
                  key={c.id}
                  className="bg-white rounded-xl shadow-sm px-6 py-5 flex items-center justify-between"
                >
                  <div className="flex items-center gap-4">
                    <div
                      className="w-14 h-14 rounded-full flex items-center justify-center text-[18px] font-medium text-[#666] shrink-0"
                      style={{ backgroundColor: c.avatarColor }}
                    >
                      {c.surname}
                    </div>
                    <div>
                      <p className="text-[19px] font-medium text-[#1a1a1a] leading-tight">{c.name}</p>
                      <p className="text-[14px] text-[#888] mt-0.5">最近療程：{c.lastSession}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    {/* 活動中標籤，只有 isActive 為 true 時顯示 */}
                    {c.isActive && (
                      <span className="bg-[#d4f5e2] text-[#2e9e5b] text-[15px] font-medium rounded-full px-[17px] py-[6.2px]">
                        活動中
                      </span>
                    )}
                    <Link
                      href={`/cases/${c.id}`}
                      className="text-[18px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors"
                    >
                      查看 ›
                    </Link>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
