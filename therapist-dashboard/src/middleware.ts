import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// 這個 app 完全沒有用到 Next.js Server Actions（沒有任何 "use server"、
// useActionState、表單 action={}，見 2026-08-21 稽核）。但 log 裡持續出現
// 「Failed to find Server Action "x"」「Invariant: Expected RSC response,
// got text/plain」這組錯誤，規律到不像正常使用者操作，比較像是外部掃描
// 程式在對這個公開網域探測 Server Action 端點。帶 Next-Action header 的
// 請求對這個 app 來說一律不合法，直接擋掉，不讓它走到 RSC action 解析
// 那一層產生噪音、也少一點被進一步探測的機會。
export function middleware(request: NextRequest) {
  if (request.headers.has("Next-Action")) {
    return new NextResponse(null, { status: 400 });
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    // 排除靜態資源與 favicon，這些請求不可能是 Server Action 呼叫，
    // 沒必要每次都進 middleware 檢查一次。
    "/((?!_next/static|_next/image|favicon.ico).*)",
  ],
};
