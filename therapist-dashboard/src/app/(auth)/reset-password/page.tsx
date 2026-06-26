"use client";

import { useState } from "react";
import Link from "next/link";

export default function ResetPasswordPage() {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [success, setSuccess] = useState(false); // 控制是否顯示成功畫面

  // 成功畫面
  if (success) {
    return (
      <div className="min-h-screen bg-[#f5e6d3] flex items-center justify-center px-4">
        <div className="bg-white rounded-2xl shadow-lg p-18 w-full max-w-[520px] flex flex-col items-center gap-11">

          {/* 勾勾圖示 */}
          <div className="w-[124px] h-[124px] rounded-full bg-[#fde8e4] flex items-center justify-center">
            <div className="w-[86px] h-[86px] rounded-full bg-[#e05c3a] flex items-center justify-center">
              <svg width="60" height="60" viewBox="0 0 24 24" fill="none">
                <path d="M20 6L9 17l-5-5" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
          </div>

          {/* 標題與說明 */}
          <div className="flex flex-col items-center gap-2 text-center">
            <h1 className="text-[32px] font-semibold text-[#1a1a1a]">密碼更新成功</h1>
            <p className="text-[17px] text-[#888]">您現在可以使用新密碼登入您的帳號</p>
          </div>

          {/* 返回登入按鈕 */}
          <Link
            href="/login"
            className="w-full bg-[#1a1a1a] text-white rounded-xl py-4 font-medium hover:bg-[#333] transition-colors text-[16px] text-center"
          >
            返回登入
          </Link>
        </div>
      </div>
    );
  }

  return (
    // 整頁容器：暖色背景，置中
    <div className="min-h-screen bg-[#f5e6d3] flex items-center justify-center px-4">

      {/* 白色卡片 */}
      <div className="bg-white rounded-2xl shadow-lg p-10 w-full max-w-[520px] flex flex-col items-center gap-6">

        {/* 鎖頭圖示（開鎖狀態） */}
        <div className="w-20 h-20 rounded-full bg-[#fde8e4] flex items-center justify-center">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="11" width="18" height="11" rx="2" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <path d="M7 11V7a5 5 0 0 1 9.9-1" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </div>

        {/* 標題與說明 */}
        <div className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-[28px] font-semibold text-[#1a1a1a]">重設您的密碼</h1>
          <p className="text-[15px] text-[#888]">請在下方輸入新密碼以變更您的密碼</p>
        </div>

        {/* 表單 */}
        <form
          className="w-full flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            setSuccess(true); // 提交後顯示成功畫面
          }}
        >
          {/* 新密碼 */}
          <div className="flex flex-col gap-2">
            <label className="font-medium text-[#1a1a1a] text-[15px]">新密碼</label>
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="請輸入至少 8 個字元"
                className="w-full border border-[#e0e0e0] rounded-xl px-4 py-3.5 pr-12 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px]"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-4 top-1/2 -translate-y-1/2 text-[#888] hover:text-[#1a1a1a] transition-colors"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                  {showPassword ? (
                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24M1 1l22 22" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  ) : (
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  )}
                </svg>
              </button>
            </div>
          </div>

          {/* 確認新密碼 */}
          <div className="flex flex-col gap-2">
            <label className="font-medium text-[#1a1a1a] text-[15px]">確認新密碼</label>
            <div className="relative">
              <input
                type={showConfirm ? "text" : "password"}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="請再次輸入密碼"
                className="w-full border border-[#e0e0e0] rounded-xl px-4 py-3.5 pr-12 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px]"
              />
              <button
                type="button"
                onClick={() => setShowConfirm(!showConfirm)}
                className="absolute right-4 top-1/2 -translate-y-1/2 text-[#888] hover:text-[#1a1a1a] transition-colors"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                  {showConfirm ? (
                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24M1 1l22 22" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  ) : (
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  )}
                </svg>
              </button>
            </div>
          </div>

          {/* 重設密碼按鈕 */}
          <button
            type="submit"
            className="w-full bg-[#1a1a1a] text-white rounded-xl py-4 font-medium hover:bg-[#333] transition-colors text-[16px] mt-2"
          >
            重設密碼
          </button>
        </form>

        {/* 返回登入 */}
        <Link
          href="/login"
          className="text-[14px] text-[#888] hover:text-[#1a1a1a] transition-colors"
        >
          ← 返回登入
        </Link>
      </div>
    </div>
  );
}
