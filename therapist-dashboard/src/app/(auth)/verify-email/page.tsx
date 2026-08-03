"use client";

import { useState, useRef, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

const OTP_LENGTH = 6; // 驗證碼位數

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <VerifyEmailForm />
    </Suspense>
  );
}

function VerifyEmailForm() {
  const [otp, setOtp] = useState<string[]>(Array(OTP_LENGTH).fill("")); // 每格一個字元
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [resent, setResent] = useState(false);
  const inputs = useRef<(HTMLInputElement | null)[]>([]); // 各格 input 的 ref
  const router = useRouter();
  const searchParams = useSearchParams();
  const email = searchParams.get("email") ?? "";

  async function handleVerify() {
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/verify-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code: otp.join("") }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "驗證失敗，請稍後再試");
        return;
      }
      router.push(`/reset-password?email=${encodeURIComponent(email)}`);
    } catch {
      setError("網路連線失敗，請稍後再試");
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    setError("");
    setResent(false);
    try {
      const res = await fetch("/api/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error ?? "重新發送失敗，請稍後再試");
        return;
      }
      setResent(true);
    } catch {
      setError("網路連線失敗，請稍後再試");
    }
  }

  // 處理每格輸入
  function handleChange(value: string, index: number) {
    if (!/^\d?$/.test(value)) return; // 只允許數字
    const next = [...otp];
    next[index] = value;
    setOtp(next);
    if (value && index < OTP_LENGTH - 1) {
      inputs.current[index + 1]?.focus(); // 輸入後自動跳下一格
    }
  }

  // 處理退格鍵
  function handleKeyDown(e: React.KeyboardEvent, index: number) {
    if (e.key === "Backspace" && !otp[index] && index > 0) {
      inputs.current[index - 1]?.focus(); // 空格退格跳前一格
    }
  }

  // 處理貼上整串驗證碼
  function handlePaste(e: React.ClipboardEvent) {
    const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, OTP_LENGTH);
    if (!pasted) return;
    const next = [...otp];
    pasted.split("").forEach((char, i) => { next[i] = char; });
    setOtp(next);
    inputs.current[Math.min(pasted.length, OTP_LENGTH - 1)]?.focus();
    e.preventDefault();
  }

  return (
    // 整頁容器：暖色背景，置中
    <div className="min-h-screen bg-[#f5e6d3] flex items-center justify-center px-4">

      {/* 白色卡片 */}
      <div className="bg-white rounded-2xl shadow-lg p-10 w-full max-w-[520px] flex flex-col items-center gap-6">

        {/* 信封圖示 */}
        <div className="w-20 h-20 rounded-full bg-[#fde8e4] flex items-center justify-center">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
            <path
              d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"
              stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            />
            <polyline
              points="22,6 12,13 2,6"
              stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            />
          </svg>
        </div>

        {/* 標題與說明 */}
        <div className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-[28px] font-semibold text-[#1a1a1a]">驗證您的電子信箱</h1>
          <p className="text-[15px] text-[#888]">我們已將驗證碼發送至</p>
          <p className="text-[15px] text-[#1a1a1a] font-medium">{email}</p>
        </div>

        {/* 6 格 OTP 輸入 */}
        <div className="flex gap-3" onPaste={handlePaste}>
          {otp.map((digit, i) => (
            <input
              key={i}
              ref={(el) => { inputs.current[i] = el; }}
              type="text"
              inputMode="numeric"
              maxLength={1}
              value={digit}
              onChange={(e) => handleChange(e.target.value, i)}
              onKeyDown={(e) => handleKeyDown(e, i)}
              className="w-12 h-14 text-center text-[22px] font-medium text-[#1a1a1a] border border-[#e0e0e0] rounded-xl outline-none focus:border-[#1a1a1a] transition-colors"
            />
          ))}
        </div>

        {error && <p className="text-[14px] text-[#e05c3a]">{error}</p>}

        {/* 繼續按鈕 */}
        <button
          type="button"
          onClick={handleVerify}
          disabled={loading || otp.some((d) => !d)}
          className="w-full bg-[#1a1a1a] text-white rounded-xl py-4 font-medium hover:bg-[#333] transition-colors text-[16px] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? "驗證中..." : "繼續"}
        </button>

        {/* 重新發送 */}
        <div className="flex flex-col items-center gap-1">
          <p className="text-[14px] text-[#888]">
            {resent ? "驗證碼已重新發送" : "沒有收到驗證碼？"}
          </p>
          <button
            type="button"
            onClick={handleResend}
            className="text-[14px] font-medium text-[#e05c3a] hover:text-[#c04a2c] transition-colors"
          >
            點此重新發送
          </button>
        </div>

        {/* 返回 */}
        <Link
          href="/forgot-password"
          className="text-[14px] text-[#888] hover:text-[#1a1a1a] transition-colors flex items-center gap-1"
        >
          ← 返回
        </Link>
      </div>
    </div>
  );
}
