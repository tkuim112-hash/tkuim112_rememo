"use client";

import { useState } from "react";
import Link from "next/link";

export default function RegisterPage() {
  const [institution, setInstitution] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [success, setSuccess] = useState(false);

  if (success) {
    return (
      <div className="min-h-screen bg-[#f5e6d3] flex items-center justify-center px-4">
        <div className="bg-white rounded-2xl shadow-lg px-16 py-14 w-full max-w-[520px] flex flex-col items-center gap-8">

          {/* 勾勾圖示 */}
          <div className="w-24 h-24 rounded-full bg-[#fde8e4] flex items-center justify-center">
            <div className="w-16 h-16 rounded-full bg-[#e05c3a] flex items-center justify-center">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
                <path d="M20 6L9 17l-5-5" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
          </div>

          {/* 標題與說明 */}
          <div className="flex flex-col items-center gap-2 text-center">
            <h1 className="text-[30px] font-semibold text-[#1a1a1a]">註冊成功</h1>
            <p className="text-[16px] text-[#888]">您的帳號已成功建立，現在可以開始使用 Rememo</p>
          </div>

          {/* 前往登入按鈕 */}
          <Link
            href="/login"
            className="w-full bg-[#1a1a1a] text-white rounded-xl py-4 font-medium hover:bg-[#333] transition-colors text-[16px] text-center"
          >
            前往登入
          </Link>
        </div>
      </div>
    );
  }

  return (
    // 整頁容器：暖色背景，置中
    <div className="min-h-screen bg-[#f5e6d3] flex items-center justify-center px-4 py-10">

      {/* 白色卡片 */}
      <div className="bg-white rounded-2xl shadow-lg px-16 py-6 w-full max-w-[760px] flex flex-col items-center gap-3">

        {/* 人物加號圖示 */}
        <div className="w-20 h-20 rounded-full bg-[#fde8e4] flex items-center justify-center">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <circle cx="9" cy="7" r="4" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <line x1="19" y1="8" x2="19" y2="14" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <line x1="16" y1="11" x2="22" y2="11" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </div>

        {/* 標題 */}
        <h1 className="text-[28px] font-semibold text-[#1a1a1a]">建立帳號</h1>

        {/* 表單 */}
        <form
          className="w-full flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setSuccess(true);
          }}
        >
          {/* 機構名稱 */}
          <div className="flex flex-col gap-2">
            <label className="font-medium text-[#1a1a1a] text-[15px]">機構名稱</label>
            <input
              type="text"
              value={institution}
              onChange={(e) => setInstitution(e.target.value)}
              placeholder="照護機構名稱"
              className="w-full border border-[#e0e0e0] rounded-xl px-4 py-2 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px]"
            />
          </div>

          {/* 使用者名稱 */}
          <div className="flex flex-col gap-2">
            <label className="font-medium text-[#1a1a1a] text-[15px]">使用者名稱</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="使用者名稱"
              className="w-full border border-[#e0e0e0] rounded-xl px-4 py-2 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px]"
            />
          </div>

          {/* 電子信箱 */}
          <div className="flex flex-col gap-2">
            <label className="font-medium text-[#1a1a1a] text-[15px]">電子信箱</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="therapist@hospital.com.tw"
              className="w-full border border-[#e0e0e0] rounded-xl px-4 py-2 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px]"
            />
          </div>

          {/* 密碼 */}
          <div className="flex flex-col gap-2">
            <label className="font-medium text-[#1a1a1a] text-[15px]">密碼</label>
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="請輸入至少 8 個字元"
                className="w-full border border-[#e0e0e0] rounded-xl px-4 py-2 pr-12 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px]"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-4 top-1/2 -translate-y-1/2 text-[#888] hover:text-[#1a1a1a] transition-colors"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                  {showPassword ? (
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  ) : (
                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24M1 1l22 22" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  )}
                </svg>
              </button>
            </div>
          </div>

          {/* 再次確認密碼 */}
          <div className="flex flex-col gap-2">
            <label className="font-medium text-[#1a1a1a] text-[15px]">再次確認密碼</label>
            <div className="relative">
              <input
                type={showConfirm ? "text" : "password"}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="請再次輸入密碼"
                className="w-full border border-[#e0e0e0] rounded-xl px-4 py-2 pr-12 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px]"
              />
              <button
                type="button"
                onClick={() => setShowConfirm(!showConfirm)}
                className="absolute right-4 top-1/2 -translate-y-1/2 text-[#888] hover:text-[#1a1a1a] transition-colors"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                  {showConfirm ? (
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  ) : (
                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24M1 1l22 22" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  )}
                </svg>
              </button>
            </div>
          </div>

          {/* 下一步按鈕 */}
          <button
            type="submit"
            className="w-full bg-[#1a1a1a] text-white rounded-xl py-3 font-medium hover:bg-[#333] transition-colors text-[17px] mt-2"
          >
            下一步
          </button>
        </form>

        {/* 已有帳號 */}
        <p className="text-[17px] text-[#888]">
          已有帳號？{" "}
          <Link href="/login" className="text-[#e05c3a] font-medium hover:text-[#c04a2c] transition-colors">
            登入
          </Link>
        </p>
      </div>
    </div>
  );
}
