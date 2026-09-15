"""
心得環節（三回合結束後）模板化生成。

2026-08-17起心得環節內容以固定模板為主（跟第三回合本身的 closing.txt／
_generate_closing 不同，那個是回合本身的內容，不在這支檔案的範圍內，見
orchestrator.py _start_round3_closing 說明）：

長者答完回合3的收尾問題後，build_closing_invitation() 直接產生開場邀請語
兩段內容：
  - thanks_text：結尾感謝語，從 CLOSING_TAIL_VARIANTS 隨機挑一句
  - question：固定的「回想整場聊下來，你有什麼想跟我分享的呢？」

長者回答這句問題後，答案只記錄不再另外生成收尾訊息，療程直接結束（見
app/routers/session.py session_closing 說明）。

2026-09-08：原本這裡還有第三段「承接語」（先呼應長者回合3回答的分類、
再接系統整合肯定），跟感謝語、問題一起接成三段顯示。但心得畫面
（ShareScene AIBubble）的背景圖 st.png 是畫死 4 條格線的靜態圖，文字框
沒有行數上限，三段拼起來常常超過 4 行，超出的行沒有格線可以對齊，畫面
上就變成「前幾行有底線、最後一行沒有」的跑版（使用者回報的截圖）。
使用者決定拿掉承接語，只留感謝語＋問題，不修背景圖也不重接動態畫線
——原本用來挑承接語內容的 classify_closing_response／
build_closing_affirmation／build_closing_message 那整套規則分類（連同
CLOSING_RECEIVING_PHRASES／SYSTEM_COMPLAINT_RECEIVING_PHRASES／
HARDSHIP_CORE_VARIANTS／WARM_CORE_VARIANTS）現在完全沒有呼叫端在用，
已一併移除；對應的 sharing_ack_*／sharing_complaint_*／sharing_affirm_*
預錄音檔 key 也已從 audio_bank.py 拿掉（音檔本身留在 Unity
StreamingAssets/Audio，之後確定不會再用到可以整批砍掉）。
"""
import random

from services.audio_bank import lookup_audio_key

# 2026-08-18：心得環節這批固定句全部併入 audio_bank.py 的預錄音檔對照表
# （SHARING_TEXT_KEYS），跟其餘 fixed_/q1_inv_/... 用同一套「前端依 key 播放
# 內建音檔、後端不再即時TTS」機制——這份檔案本來的註解說前端已經在播預錄
# 音檔了，但實際查過 Unity 專案（GameController.cs／ShareController.cs）
# 後發現前端目前完全沒有這個機制，是規劃、不是現況，這次才真的接上。
#
# 這裡選字串用的仍然是 random.choice，不是自己維護一份「index -> key」
# 對照——選完之後直接拿選中的文字去 lookup_audio_key() 查，是同一套資料
# 來源（audio_bank.py 裡逐句核對過的 SHARING_TEXT_KEYS），不會有文字/key
# 兜不起來的風險。


def _audio_keys(text: str) -> list[str]:
    key = lookup_audio_key(text)
    return [key] if key else []

CLOSING_TAIL_VARIANTS = [
    "謝謝你今天願意跟我分享這麼多，希望這些美好的時光，能常常陪著你、讓你覺得溫暖。",
    "謝謝你今天陪我聊了這麼多，希望這份溫暖能一直留在你心裡。",
    "很謝謝你把這些故事說給我聽，希望你隨時想起來，都能感覺到溫暖。",
]


# ── 心得環節開場：邀請長者先分享 ──────────────────────────────
async def build_closing_invitation() -> dict:
    """
    心得環節開場：感謝語接在問題前面，邀請長者分享。Returns:
    {"thanks_text": str, "thanks_audio_keys": list[str], "question": str,
    "question_audio_keys": list[str]}。
    """
    thanks_text = random.choice(CLOSING_TAIL_VARIANTS)
    question = "回想整場聊下來，你有什麼想跟我分享的呢？"
    return {
        "thanks_text": thanks_text,
        "thanks_audio_keys": _audio_keys(thanks_text),
        "question": question,
        "question_audio_keys": _audio_keys(question),
    }
