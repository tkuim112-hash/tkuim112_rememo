import Link from "next/link";

// 公開自助註冊已關閉（見 api/auth/register/route.ts 的說明），這裡改成靜態頁面，
// 引導有需要開通機構帳號的人去聯絡系統管理者，而不是留一個表單讓任何人都能
// 自建機構、自封管理者。
export default function RegisterPage() {
  return (
    <div className="min-h-screen bg-[#f5e6d3] flex items-center justify-center px-4">
      <div className="bg-white rounded-2xl shadow-lg p-10 w-full max-w-[520px] flex flex-col items-center gap-6 text-center">
        <div className="w-20 h-20 rounded-full bg-[#fde8e4] flex items-center justify-center">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="9" cy="7" r="4" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            <line x1="15" y1="8" x2="21" y2="14" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            <line x1="21" y1="8" x2="15" y2="14" stroke="#e05c3a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>

        <div className="flex flex-col items-center gap-2">
          <h1 className="text-[26px] font-semibold text-[#1a1a1a]">暫不開放自助註冊</h1>
          <p className="text-[15px] text-[#888] leading-relaxed">
            機構帳號目前改由系統管理者手動開通，請聯絡系統管理者為您的機構建立帳號，
            建立完成後您會收到一封驗證信，自行設定密碼即可開始使用。
          </p>
        </div>

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
