"use client"; // 這是客戶端元件（有互動狀態需要在瀏覽器執行）

import { useState } from "react"; // 引入 React 狀態管理 hook
import Link from "next/link"; // 引入 Next.js 的路由連結元件


// 本地圖片路徑（存放在 public/image/ 資料夾）
const imgLogo   = "/image/logo_Text.png";   // REMEMO 商標圖示
const imgPhoto1 = "/image/photo1_1.png"; // 拍立得照片 1
const imgPhoto2 = "/image/photo2_1.png"; // 拍立得照片 2
const imgPhoto3 = "/image/photo3.png"; // 拍立得照片 3
const imgStar   = "/image/star.png";   // 裝飾星星圖案
const imgHeart  = "/image/heart.png";  // 裝飾愛心圖案


export default function LoginPage() {
  const [email, setEmail] = useState("");           // 電子郵件輸入狀態
  const [password, setPassword] = useState("");     // 密碼輸入狀態
  const [showPassword, setShowPassword] = useState(false); // 控制密碼是否顯示

  return (
    // 整頁容器：暖色背景，左右兩欄排列
    <div className="h-screen bg-[#f5e6d3] flex overflow-hidden">
      <div className="flex flex-1 ml-[-28px] h-full">

      {/* 左側裝飾區：手機隱藏，平板（768px）以上才顯示 */}
      <div className="hidden md:flex flex-col flex-1 relative overflow-hidden">

        {/* Logo 在左上角 */}
        <div className="absolute top-8 left-15">
          <img src={imgLogo} alt="REMEMO" style={{ height: "clamp(36px, 4vw, 52px)", objectFit: "contain" }} />
        </div>

        {/* 照片群組 + 標語：全部固定在同一個置中容器內 */}
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="flex flex-col items-center gap-8">

            <div className="relative w-[480px] h-[560px]">

              <img src={imgStar} alt="" className="absolute"
                style={{ left: "-30px", top: "110px", width: "70px" }} />

              
              <img src={imgPhoto3} alt="運動" className="absolute"
                style={{ left: "240px", top: "215px", width: "215px", transform: "rotate(14.07deg)", objectFit: "cover", zIndex: 0 }} />

              <img src={imgPhoto1} alt="讀書照" className="absolute"
                style={{ left: "130px", top: "5px", width: "240px", transform: "rotate(-1.24deg)", objectFit: "cover", zIndex: 1 }} />

              <img src={imgPhoto2} alt="家族照" className="absolute"
                style={{ left: "30px", top: "195px", width: "245px", transform: "rotate(-4.47deg)", objectFit: "cover", zIndex: 2 }} />

             
              <img src={imgHeart} alt="" className="absolute"
                style={{ right: "-145px", bottom: "30px", width: "160px" }} />

            </div>

            <p className="font-hand font-bold text-[#262625] leading-tight whitespace-nowrap text-[55px] -mt-22">
              Your Story, Piece by Piece
            </p>

          </div>
        </div>
      </div>

      {/* 右側登入卡片區：不同螢幕尺寸有不同寬度 */}
      {/* md:420px → lg:520px → xl:600px */}
      <div className="flex items-center justify-center w-full md:w-[390px] lg:w-[450px] xl:w-[510px] shrink-0 px-6 py-10 md:px-8 lg:px-10 lg:py-12 mr-[3%]">

        {/* 白色登入卡片 */}
        <div className="w-full bg-white rounded-2xl shadow-xl p-7 md:p-8 lg:p-10 flex flex-col gap-6 lg:gap-8">

          {/* 標題區塊 */}
          <div className="flex flex-col gap-1">
            {/* 主標題：字體大小隨視窗縮放 */}
            <h1
              className="font-medium text-[#1a1a1a] leading-tight tracking-tight"
              style={{ fontSize: "clamp(26px, 3vw, 38px)" }}
            >
              歡迎回來
            </h1>
            {/* 副標題說明文字 */}
            <p className="text-[#666] text-[15px] lg:text-[18px]">請輸入您的帳號資訊</p>
          </div>

          {/* 登入表單 */}
          <form
            className="flex flex-col gap-4 lg:gap-5"
            onSubmit={(e) => {
              e.preventDefault(); // 防止表單預設提交行為（頁面重整）
              window.location.href = "/dashboard"; // 登入成功後跳轉到儀表板
            }}
          >
            {/* 電子郵件欄位 */}
            <div className="flex flex-col gap-1.5 md:gap-2">
              <label className="font-medium text-[#1a1a1a] text-[15px] lg:text-[17px]">
                電子郵件
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)} // 即時更新 email 狀態
                placeholder="your@email.com"
                className="border border-[#e0e0e0] rounded-xl px-4 py-3 lg:py-3.5 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px] lg:text-[16px]"
              />
            </div>

            {/* 密碼欄位 */}
            <div className="flex flex-col gap-1.5 md:gap-2">
              <label className="font-medium text-[#1a1a1a] text-[15px] lg:text-[17px]">
                密碼
              </label>
              <div className="relative"> {/* relative 讓「顯示/隱藏」按鈕能絕對定位在右側 */}
                <input
                  type={showPassword ? "text" : "password"} // 切換明文/密碼顯示
                  value={password}
                  onChange={(e) => setPassword(e.target.value)} // 即時更新 password 狀態
                  placeholder="••••••••"
                  className="w-full border border-[#e0e0e0] rounded-xl px-4 py-3 lg:py-3.5 pr-12 text-[#1a1a1a] placeholder:text-[#1a1a1a]/40 outline-none focus:border-[#1a1a1a] transition-colors text-[15px] lg:text-[16px]"
                />
                {/* 顯示/隱藏密碼切換按鈕 */}
                <button
                  type="button" // 防止觸發表單提交
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-4 top-1/2 -translate-y-1/2 text-[#666] hover:text-[#1a1a1a] transition-colors text-sm"
                >
                  {showPassword ? "隱藏" : "顯示"}
                </button>
              </div>
            </div>

            {/* 忘記密碼連結（靠右對齊） */}
            <div className="flex justify-end">
              <Link
                href="/forgot-password"
                className="text-[#666] hover:text-[#1a1a1a] transition-colors text-[14px] lg:text-[16px]"
              >
                忘記密碼？
              </Link>
            </div>

            {/* 登入按鈕 */}
            <button
              type="submit"
              className="bg-[#1a1a1a] text-white rounded-xl py-3.5 lg:py-4 font-medium hover:bg-[#333] transition-colors text-[16px] lg:text-[18px]"
            >
              登入
            </button>
          </form>

          {/* 底部：跳轉至註冊頁的連結 */}
          <p className="text-center text-[#666] text-[15px] lg:text-[17px]">
            還沒有帳號？{" "}
            <Link href="/register" className="font-medium text-[#1a1a1a] hover:underline">
              立即註冊
            </Link>
          </p>
        </div>
      </div>
      </div>
    </div>
  );
}
