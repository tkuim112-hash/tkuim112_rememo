import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    // 這個 app 大多是即時狀態頁面（活動中徽章、療程進行中狀態等），
    // 關掉 App Router 的 client-side 路由快取，避免切頁面再切回來時
    // 顯示定格在離開當下的舊資料，而不是重新 fetch。
    staleTimes: {
      dynamic: 0,
      static: 0,
    },
  },
};

export default nextConfig;
