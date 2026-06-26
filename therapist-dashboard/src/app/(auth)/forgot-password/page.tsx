"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const router = useRouter();

  return (
    // 整頁容器：暖色背景，置中
    <div className="min-h-screen bg-[#f5e6d3] flex items-center justify-center px-4">

      {/* 白色卡片 */}
      <div className="bg-white rounded-2xl shadow-lg p-10 w-full max-w-[520px] flex flex-col items-center gap-6">

        {/* 鎖頭圖示 */}
        <img src="/image/lock_qus.png" alt="忘記密碼" className="w-24 h-24 object-contain" />

        {/* 標題與說明 */}
        <div className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-[28px] font-semibold text-[#1a1a1a]">忘記密碼</h1>
          <p className="text-[15px] text-[#888]">輸入您的電子信箱，我們將寄重設驗證碼到您的信箱</p>
        </div>

        {/* 表單 */}
        <form
          className="w-full flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            router.push("/verify-email");
          }}
        >
          {/* 電子信箱欄位 */}
          <div className="flex flex-col gap-2">
            <label className="font-medium text-[#1a1a1a] text-[15px]">電子信箱</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="therapist@hospital.com.tw"
              className="border border-[#e0e0e0] rounded-xl px-4 py-3.5 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px]"
            />
          </div>

          {/* 繼續按鈕 */}
          <button
            type="submit"
            className="bg-[#1a1a1a] text-white rounded-xl py-4 font-medium hover:bg-[#333] transition-colors text-[16px]"
          >
            繼續
          </button>
        </form>

        {/* 返回登入 */}
        <Link
          href="/login"
          className="text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors text-[15px] font-medium"
        >
          返回登入
        </Link>
      </div>
    </div>
  );
}
