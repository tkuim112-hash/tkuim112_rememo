"use client";

import Link from "next/link";
import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import type { Case } from "@/lib/types";

const AVATAR_COLORS = ["#d4e4f7", "#f7e4d4", "#e4f7d4", "#f7d4e4", "#e4d4f7", "#f7f0d4"];

export default function NewCasePage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [birthYear, setBirthYear] = useState("");
  const [birthPlace, setBirthPlace] = useState("");
  const [career, setCareer] = useState("");
  const [family, setFamily] = useState("");
  const [hobbies, setHobbies] = useState("");
  const [tabooTopics, setTabooTopics] = useState("");
  const [showUploadModal, setShowUploadModal] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const inputClass =
    "w-full bg-[#f5f5f5] rounded-xl px-4 py-2 text-[15px] text-[#1a1a1a] placeholder:text-[#1a1a1a]/30 outline-none focus:bg-[#efefef] transition-colors";

  return (
    <div className="min-h-screen bg-[#f5e6d3] px-12 py-5 flex flex-col gap-3">

      {/* 麵包屑導覽 */}
      <nav className="flex items-center gap-2 text-[14px] ml-[25%]">
        <Link href="/dashboard" className="text-[#888] hover:text-[#1a1a1a] transition-colors">
          個案列表
        </Link>
        <span className="text-[#888]">›</span>
        <span className="text-[#1a1a1a]">新增個案</span>
      </nav>

      {/* 頁面標題 */}
      <h1 className="text-[32px] font-semibold text-[#1a1a1a] ml-[25%]">新增長者個案</h1>

      {/* 表單白卡 */}
      <div className="bg-white rounded-2xl px-10 py-6 flex flex-col gap-3 w-[50%] ml-[25%]">

        {/* 區塊標題 */}
        <h2 className="text-[20px] font-semibold text-[#1a1a1a]">基本資料</h2>

        {/* 頭貼 + 姓名 */}
        <div className="flex items-end gap-6">
          <div className="flex flex-col gap-2">
            <label className="text-[14px] font-medium text-[#1a1a1a]">頭貼</label>
            <div onClick={() => setShowUploadModal(true)} className="w-[68px] h-[68px] border-2 border-dashed border-[#d0d0d0] rounded-xl flex items-center justify-center cursor-pointer hover:border-[#aaa] transition-colors bg-[#fafafa]">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="#bbb" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <polyline points="17 8 12 3 7 8" stroke="#bbb" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <line x1="12" y1="3" x2="12" y2="15" stroke="#bbb" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
          </div>
          <div className="flex flex-col gap-2 flex-1">
            <label className="text-[14px] font-medium text-[#1a1a1a]">姓名</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputClass}
            />
          </div>
        </div>

        {/* 出生年 + 出生地 */}
        <div className="grid grid-cols-2 gap-4">
          <div className="flex flex-col gap-2">
            <label className="text-[14px] font-medium text-[#1a1a1a]">出生年</label>
            <input
              type="text"
              value={birthYear}
              onChange={(e) => setBirthYear(e.target.value)}
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-[14px] font-medium text-[#1a1a1a]">出生地</label>
            <input
              type="text"
              value={birthPlace}
              onChange={(e) => setBirthPlace(e.target.value)}
              className={inputClass}
            />
          </div>
        </div>

        {/* 主要職業經歷 */}
        <div className="flex flex-col gap-2">
          <label className="text-[14px] font-medium text-[#1a1a1a]">主要職業經歷</label>
          <input
            type="text"
            value={career}
            onChange={(e) => setCareer(e.target.value)}
            className={inputClass}
          />
        </div>

        {/* 家人姓名關係 */}
        <div className="flex flex-col gap-2">
          <label className="text-[14px] font-medium text-[#1a1a1a]">家人姓名關係</label>
          <input
            type="text"
            value={family}
            onChange={(e) => setFamily(e.target.value)}
            className={inputClass}
          />
        </div>

        {/* 喜好 */}
        <div className="flex flex-col gap-2">
          <label className="text-[14px] font-medium text-[#1a1a1a]">喜好（食物、地點、節慶）</label>
          <input
            type="text"
            value={hobbies}
            onChange={(e) => setHobbies(e.target.value)}
            className={inputClass}
          />
        </div>

        {/* 禁忌話題 */}
        <div className="flex flex-col gap-2">
          <label className="text-[14px] font-medium text-[#1a1a1a]">禁忌話題（不可提及）</label>
          <input
            type="text"
            value={tabooTopics}
            onChange={(e) => setTabooTopics(e.target.value)}
            className={inputClass}
          />
        </div>

        {/* 按鈕列 */}
        <div className="flex items-center gap-3 pt-1">
          <button
            type="button"
            onClick={() => {
              const existing: Case[] = JSON.parse(localStorage.getItem("rememo_cases") ?? "[]");
              const newCase: Case = {
                id: Date.now().toString(),
                name,
                surname: name.charAt(0),
                age: birthYear ? new Date().getFullYear() - parseInt(birthYear) : 0,
                gender: "male",
                avatarColor: AVATAR_COLORS[existing.length % AVATAR_COLORS.length],
                lastSession: "尚未開始",
                totalSessions: 0,
                tabooTopics: tabooTopics ? tabooTopics.split("、").map(s => s.trim()).filter(Boolean) : [],
                notes: [career, family, hobbies].filter(Boolean).join("；"),
                birthYear,
                birthPlace,
                career,
                family,
                hobbies,
                mode: "輕度模式",
              };
              localStorage.setItem("rememo_cases", JSON.stringify([...existing, newCase]));
              router.push("/dashboard");
            }}
            className="bg-[#5b8ac5] text-white rounded-xl px-7 py-2 text-[15px] font-medium hover:bg-[#3a6aa0] transition-colors"
          >
            儲存個案
          </button>
          <Link
            href="/dashboard"
            className="border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-7 py-2 text-[15px] font-medium hover:bg-[#f5f5f5] transition-colors"
          >
            取消
          </Link>
        </div>
      </div>
      {/* 上傳圖片 Modal */}
      {showUploadModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-10 w-[420px] flex flex-col items-center gap-6">

            {/* 圓形虛線上傳區 */}
            <div className="w-44 h-44 rounded-full border-2 border-dashed border-[#ccc] bg-[#f5f5f5] flex items-center justify-center">
              <svg width="44" height="44" viewBox="0 0 24 24" fill="none">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="#aaa" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                <polyline points="17 8 12 3 7 8" stroke="#aaa" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                <line x1="12" y1="3" x2="12" y2="15" stroke="#aaa" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>

            {/* 提示文字 */}
            <p className="text-[13px] text-[#888] text-center">建議上傳正方形圖片</p>

            {/* 隱藏的檔案選擇 input */}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
            />

            {/* 選擇圖片按鈕 */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="w-full bg-[#5b8ac5] text-white rounded-xl py-3.5 text-[15px] font-medium hover:bg-[#3a6aa0] transition-colors"
            >
              選擇圖片
            </button>

            {/* 取消按鈕 */}
            <button
              type="button"
              onClick={() => setShowUploadModal(false)}
              className="w-full border border-[#d0d0d0] text-[#1a1a1a] rounded-xl py-3.5 text-[15px] font-medium hover:bg-[#f5f5f5] transition-colors"
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
