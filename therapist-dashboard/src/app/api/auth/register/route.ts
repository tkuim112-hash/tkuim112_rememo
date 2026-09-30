import { NextResponse } from "next/server";

// 公開自助註冊已關閉：任何人只要填表單就能自建機構、自封管理者，機構之間雖然
// 彼此隔離、不會外洩別人的資料，但完全沒有驗證「這個人是不是真的有資格代表
// 這個機構」，被視為信任模型上的疑慮。新機構起始帳號改成只能由平台方用
// app/create_organization.py 手動開通（同一套「寄驗證信讓對方自己設密碼」的
// 機制，只是多了建立機構那一步），不再開放公開表單自助建立。
export async function POST() {
  return NextResponse.json(
    { error: "本平台暫不開放自助註冊，請洽系統管理者為您的機構開通帳號" },
    { status: 403 }
  );
}
