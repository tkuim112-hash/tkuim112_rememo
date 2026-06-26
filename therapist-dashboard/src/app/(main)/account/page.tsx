"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { mockTherapist } from "@/lib/mock-data";

export default function AccountPage() {
  const [therapistName, setTherapistName] = useState(mockTherapist.name);
  const [isEditingName, setIsEditingName] = useState(false);
  const [institution, setInstitution] = useState(mockTherapist.institution);
  const [email, setEmail] = useState(mockTherapist.email);

  useEffect(() => {
    setTherapistName(localStorage.getItem("rememo_name") ?? mockTherapist.name);
    setInstitution(localStorage.getItem("rememo_institution") ?? mockTherapist.institution);
    setEmail(localStorage.getItem("rememo_email") ?? mockTherapist.email);
  }, []);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  const inputClass =
    "w-full bg-white border border-[#e0e0e0] rounded-xl px-4 py-2 text-[15px] text-[#1a1a1a] placeholder:text-[#1a1a1a]/30 outline-none focus:border-[#1a1a1a] transition-colors";

  return (
    <div className="min-h-screen bg-[#f5e6d3] px-12 py-5 flex flex-col gap-4">

      {/* 返回 + 標題 */}
      <div className="flex items-center gap-3">
        <Link href="/dashboard" className="flex items-center gap-1.5 text-[#1a1a1a] hover:text-[#555] transition-colors">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <path d="M19 12H5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <path d="M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <span className="text-[15px] font-medium">個案列表</span>
        </Link>
      </div>

      {/* 內容區：置中 */}
      <div className="ml-[25%] w-[50%] flex flex-col gap-3">

        {/* 使用者資訊 */}
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            {isEditingName ? (
              <input
                autoFocus
                type="text"
                value={therapistName}
                onChange={(e) => setTherapistName(e.target.value)}
                onBlur={() => setIsEditingName(false)}
                onKeyDown={(e) => { if (e.key === "Enter") setIsEditingName(false); }}
                className="text-[26px] font-bold text-[#1a1a1a] ml-[1.7%] bg-transparent border-b-2 border-[#1a1a1a] outline-none w-[180px]"
              />
            ) : (
              <span className="text-[26px] font-bold text-[#1a1a1a] ml-[1.7%]">{therapistName}</span>
            )}
            <button type="button" onClick={() => setIsEditingName(true)} className="text-[#888] hover:text-[#1a1a1a] transition-colors ml-[0.6%]">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>
          <p className="text-[14px] text-[#888] ml-[2%]">{mockTherapist.email}</p>
        </div>

        {/* 修改資料 */}
        <div className="bg-white rounded-2xl px-8 py-4 flex flex-col gap-3">
          <h2 className="text-[21px] font-semibold text-[#1a1a1a]">修改資料</h2>

          <div className="flex flex-col gap-2">
            <label className="text-[15px] font-medium text-[#1a1a1a]">機構名稱</label>
            <input
              type="text"
              value={institution}
              onChange={(e) => setInstitution(e.target.value)}
              className={inputClass}
            />
          </div>

          <div className="flex flex-col gap-2">
            <label className="text-[15px] font-medium text-[#1a1a1a]">電子信箱</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputClass}
            />
          </div>

          <button
            type="button"
            onClick={() => {
              localStorage.setItem("rememo_name", therapistName);
              localStorage.setItem("rememo_institution", institution);
              localStorage.setItem("rememo_email", email);
            }}
            className="self-start bg-[#5b8ac5] text-white rounded-xl px-6 py-2.5 text-[14px] font-medium hover:bg-[#3a6aa0] transition-colors"
          >
            儲存變更
          </button>
        </div>

        {/* 修改密碼 */}
        <div className="bg-white rounded-2xl px-8 py-4 flex flex-col gap-3">
          <h2 className="text-[21px] font-semibold text-[#1a1a1a]">修改密碼</h2>

          <div className="flex flex-col gap-2">
            <label className="text-[15px] font-medium text-[#1a1a1a]">目前密碼</label>
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className={inputClass}
            />
          </div>

          <div className="flex flex-col gap-2">
            <label className="text-[15px] font-medium text-[#1a1a1a]">新密碼</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className={inputClass}
            />
          </div>

          <button
            type="button"
            className="self-start bg-[#5b8ac5] text-white rounded-xl px-6 py-2.5 text-[14px] font-medium hover:bg-[#3a6aa0] transition-colors"
          >
            更新密碼
          </button>
        </div>

        {/* 刪除帳號 */}
        <button
          type="button"
          onClick={() => setShowDeleteDialog(true)}
          className="self-start text-[#e05c3a] text-[14px] font-medium hover:text-[#c04a2c] transition-colors ml-[2%] mt-[2%]"
        >
          刪除帳號
        </button>
      </div>

      {/* 刪除帳號確認 Dialog */}
      {showDeleteDialog && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
          onClick={() => setShowDeleteDialog(false)}
        >
          <div
            className="bg-white rounded-2xl px-8 py-6 w-[360px] flex flex-col gap-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex flex-col gap-1">
              <h2 className="text-[18px] font-semibold text-[#1a1a1a]">確定要刪除帳號嗎？</h2>
              <p className="text-[14px] text-[#888]">此操作無法復原，所有資料將永久刪除。</p>
            </div>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => setShowDeleteDialog(false)}
                className="px-5 py-2 rounded-xl text-[14px] font-medium text-[#1a1a1a] bg-[#f0f0f0] hover:bg-[#e0e0e0] transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowDeleteDialog(false);
                  window.location.href = "/login";
                }}
                className="px-5 py-2 rounded-xl text-[14px] font-medium text-white bg-[#e05c3a] hover:bg-[#c04a2c] transition-colors"
              >
                確認刪除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
