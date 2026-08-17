"""
心得環節（三回合結束後）模板化生成。

2026-08-17起心得環節內容以固定模板為主（跟第三回合本身的 closing.txt／
_generate_closing 不同，那個是回合本身的內容，不在這支檔案的範圍內，見
orchestrator.py _start_round3_closing 說明）：

長者答完回合3的收尾問題後，build_closing_invitation(topics, round3_response,
emotion, llm_service) 直接產生開場邀請語三段內容：
  - scene_text（承接語，一般分類再接系統整合肯定）／thanks_text（結尾感謝語）
    ：依長者在回合3的回答做分類（classify_closing_response），從
    CLOSING_RECEIVING_PHRASES／SYSTEM_COMPLAINT_RECEIVING_PHRASES／
    build_closing_affirmation／CLOSING_TAIL_VARIANTS 挑對應模板組出——三段
    分開回傳是因為 scene_text／thanks_text 前端會播放各自獨立的預錄音檔，
    不能合併成一個欄位；多數分類用純規則判斷，只有「是不是在抱怨系統/AI
    本身」需要叫LLM（見 _detect_system_complaint 說明），不是每次都會叫LLM
  - question：固定的「回想整場聊下來，你有什麼想跟我分享的呢？」

長者回答這句問題後，答案只記錄不再另外生成收尾訊息，療程直接結束（見
app/routers/session.py session_closing 說明）。
"""
import random

# ── 主題分類 ──────────────────────────────
HARDSHIP_TOPICS = {"奮鬥經歷", "軍旅", "哀傷之事"}

WARM_TOPICS = {
    "童年經歷", "讀書求學", "家庭", "感情", "工作",
    "興趣", "專長", "印象最深刻的地方", "休閒", "節慶",
    "人生目標", "自我成就感", "生命中特殊的事件",
}


# ── 心得環節開場：邀請長者先分享 ──────────────────────────────
async def build_closing_invitation(
    topics: list[str],
    round3_response: str = "",
    emotion: str = "",
    llm_service=None,
) -> dict:
    """
    心得環節開場：收尾語先呼應長者剛才在回合3的回答，感謝語接在收尾語下面，
    問題接在最後邀請長者分享。Returns: {"scene_text": str, "thanks_text": str,
    "question": str}——三段分開回傳，scene_text/thanks_text 會是各自獨立的
    音檔（前端各自播放，不用後端合成語音，見下方第三次稽核）。

    round3_response/emotion/llm_service: 回合3那句收尾問題（「想到...你現在
    心裡是什麼感覺呢？」，見 orchestrator.py _start_round3_closing）長者的
    回答與情緒。scene_text/thanks_text 沿用跟 build_closing_message 同一套
    規則式分類（classify_closing_response 的 positive/thin_or_silent/
    emotional/system_complaint 四類），從 CLOSING_RECEIVING_PHRASES／
    SYSTEM_COMPLAINT_RECEIVING_PHRASES／CLOSING_TAIL_VARIANTS 挑對應模板——
    不用另外設計一套新的判斷邏輯，也維持這個檔案「多數分類用純規則、只有
    系統抱怨判斷需要叫LLM」的既有設計（見 classify_closing_response 說明）。
    round3_response 或 llm_service 缺值（例如呼叫端還沒接上、或本來就沒有
    這段內容）時，退回不呼應任何內容的中性收尾語、感謝語留空，不強求一定
    要能分類。

    2026-08-17：原本問「你現在心裡是什麼感覺呢？」，跟回合3收尾問的「現在
    感受或正向回憶」性質重複——長者才剛答過一次感覺，馬上又被問一次同性質的
    問題，而且收尾語是固定死的一句，跟長者剛才實際說了什麼完全無關。改成
    收尾語呼應回合3的回答，問題本身也把切入點從「當下感覺」換成「回顧整場」，
    跟回合3那句不撞。

    2026-08-17（第二次）：收尾語原本只接承接語，使用者要求改成「承接語＋
    系統整合肯定＋感謝語」完整一段，直接重用 build_closing_message 的分類/
    組裝邏輯，不重寫一份。

    2026-08-17（第三次）：使用者說感謝語會是另一段獨立的音檔（前端播放
    預錄好的音檔，不需要後端動態合成語音），不能跟承接語/系統整合肯定合併
    在同一個欄位裡。build_closing_message 改回傳 (receiving_text, thanks_text)
    tuple，這裡拆成 scene_text／thanks_text 兩個獨立欄位。
    """
    if round3_response and llm_service is not None:
        scene_text, thanks_text = await build_closing_message(
            round3_response, emotion, topics, llm_service,
        )
    else:
        scene_text, thanks_text = "今天聊了這麼多。", ""
    return {
        "scene_text": scene_text,
        "thanks_text": thanks_text,
        "question": "回想整場聊下來，你有什麼想跟我分享的呢？",
    }


# ── 長者回應分類 ──────────────────────────────
# 短到視為「沒有實質內容」的字數門檻，「還好」「嗯」「沒有」這類單薄回應
# 都落在這個門檻內，跟完全沉默（空字串）一起歸類成 thin_or_silent。
_THIN_RESPONSE_MAX_LEN = 4

# 2026-08-17 使用者實測案例：長者答「很溫馨」（3個字），純用字數門檻判斷會
# 被歸進 thin_or_silent，接了「沒關係，能陪你聊今天這些，我也很開心」這種
# 像在安慰長者「沒關係」的話——但「很溫馨」本身語意完整、清楚表達正向感受，
# 不是「還好」「嗯」這種真的沒說什麼的語助詞，答非所問。這裡列出常見的
# 短版正向情緒詞，命中就不當 thin_or_silent 處理，直接算 positive；跟
# _GENERIC_ACK_PATTERNS 同一種取捨，只抓明確訊號，不窮舉所有可能的正向詞。
_SHORT_POSITIVE_WORDS = (
    "溫馨", "開心", "幸福", "感動", "珍惜", "懷念", "難忘", "感恩",
    "知足", "滿足", "欣慰", "快樂", "甜蜜", "美好", "溫暖", "感激", "感謝",
)

CLOSING_RECEIVING_PHRASES = {
    "positive": [
        "聽你這樣說，我也覺得很溫暖。",
        "能感覺到你很珍惜這些回憶呢。",
    ],
    "thin_or_silent": [
        "沒關係，能陪你聊今天這些，我也很開心。",
    ],
    "emotional": [
        "這些回憶對你來說真的很重要，謝謝你願意跟我分享。",
    ],
}

# 長者在心得環節不是在回答「現在心裡是什麼感覺」，而是在抱怨/不滿這套系統或
# AI本身時用的承接語，依抱怨的方向分四類。這一類不像 thin_or_silent/emotional
# 能用簡單規則判斷，需要叫LLM判斷語意（見 _detect_system_complaint）。
SYSTEM_COMPLAINT_RECEIVING_PHRASES = {
    "一般不耐煩": [
        "不好意思，讓你覺得不耐煩了。",
    ],
    "質疑AI不信任": [
        "抱歉，我沒辦法像真人一樣理解你，這是我的限制。",
    ],
    "想找真人": [
        "不好意思，沒能讓你覺得像在跟真人聊天。",
    ],
    "覺得沒意義沒用": [
        "抱歉讓你覺得這個沒有幫助，這個方式不一定適合每個人。",
    ],
}


async def _detect_system_complaint(text: str, llm_service) -> str | None:
    """
    用LLM判斷長者這句話是不是在抱怨/不滿這套系統或AI本身，而不是在回答
    「現在心裡是什麼感覺」這個問題本身。跟 thin_or_silent/emotional 那兩類
    不同，「是不是在抱怨系統」是語意/語氣判斷，關鍵字規則容易誤判或漏掉
    長者各種說法（跟 orchestrator.py _detect_emotional_trigger 用LLM判斷
    情緒訊號同一個理由），所以這裡才需要叫LLM，其餘分類不用。

    回傳 SYSTEM_COMPLAINT_RECEIVING_PHRASES 的其中一個 key，或 None（不是
    在抱怨系統）。
    """
    prompt = (
        f"長者剛才回答「現在心裡是什麼感覺」這個問題，他說：「{text}」\n\n"
        "請判斷這句話是不是在抱怨/不滿這套陪伴系統或AI本身，而不是在回答"
        "問題本身的內容，分成以下四類，選最貼近的一種：\n"
        "一般不耐煩：對這個過程/流程感到不耐煩、厭煩、想結束\n"
        "質疑AI不信任：質疑AI聽不懂他、AI不是真的懂他、不相信AI\n"
        "想找真人：想找真人聊、覺得AI不是人、想要真人陪伴\n"
        "覺得沒意義沒用：覺得這整件事沒有用、沒意義、沒幫助\n"
        "如果都不是（長者是在正常回答自己的感受或回憶），回NONE。\n"
        "只回「一般不耐煩」「質疑AI不信任」「想找真人」「覺得沒意義沒用」"
        "其中一個詞，或「NONE」，不要其他文字。"
    )
    raw = (await llm_service.ask(prompt, temperature=0)).strip()
    return raw if raw in SYSTEM_COMPLAINT_RECEIVING_PHRASES else None


async def classify_closing_response(
    text: str, emotion: str, llm_service,
) -> tuple[str, str | None]:
    """
    回傳 (category, complaint_subtype)。category 是 positive/thin_or_silent/
    emotional/system_complaint 其中之一；complaint_subtype 只有
    category=="system_complaint" 時才有值。emotional/thin_or_silent 用規則
    判斷、不叫LLM，只有排除掉這兩類之後才會叫LLM判斷是不是系統抱怨。

    2026-08-17：thin_or_silent 原本純用字數門檻判斷（<=_THIN_RESPONSE_MAX_LEN
    就算），沒考慮到「很溫馨」這類雖然短、但語意完整表達正向感受的回答會被
    誤歸進去，答非所問地接一句安慰語。加了 _SHORT_POSITIVE_WORDS 例外：短
    答案裡如果命中已知的正向情緒詞，直接算 positive，不當 thin_or_silent。
    """
    stripped = (text or "").strip()
    if emotion in ("sad", "angry"):
        return "emotional", None
    if not stripped:
        return "thin_or_silent", None
    if len(stripped) <= _THIN_RESPONSE_MAX_LEN:
        if any(w in stripped for w in _SHORT_POSITIVE_WORDS):
            return "positive", None
        return "thin_or_silent", None
    subtype = await _detect_system_complaint(stripped, llm_service)
    if subtype:
        return "system_complaint", subtype
    return "positive", None


# ── 系統整合肯定的收尾語變體 ──────────────────────────────
HARDSHIP_CORE_VARIANTS = [
    "你經歷了這麼多事，也都一一走過來、撐過來了，這是很不容易、很值得驕傲的一件事。",
    "這一路走來不容易，但你都撐過來了，這份堅強真的很讓人佩服。",
    "不管過程多辛苦，你都一步一步走過來了，這些都是你這一生的勳章。",
    "這些不容易的日子，你都好好地撐過來了，這份韌性很值得為自己驕傲。",
]

WARM_CORE_VARIANTS = [
    "你這一生有這麼多美好的時光可以回味，這些都是屬於你自己的、獨一無二的故事，真的很珍貴。",
    "這些美好的回憶，都是只屬於你的故事，很珍貴、很值得好好收藏。",
    "能擁有這麼多值得回味的時光，真的是很幸福的一件事。",
    "這一生留下這麼多溫暖的回憶，都是屬於你自己獨一無二的寶藏。",
]

CLOSING_TAIL_VARIANTS = [
    "謝謝你今天願意跟我分享這麼多，希望這些美好的時光，能常常陪著你、讓你覺得溫暖。",
    "謝謝你今天陪我聊了這麼多，希望這份溫暖能一直留在你心裡。",
    "很謝謝你把這些故事說給我聽，希望你隨時想起來，都能感覺到溫暖。",
]


def build_closing_affirmation(topics: list[str]) -> str:
    """
    長者分享完之後，系統接住並放大，依這場實際聊過的主題
    決定用「撐過來」還是「美好時光」的收尾語氣，每個版本隨機挑一句，
    避免每次都聽到一模一樣的收尾。不含結尾感謝語——那是獨立的一段
    （CLOSING_TAIL_VARIANTS），呼叫端各自處理，見 build_closing_message。

    2026-08-17（第二次）：感謝語原本直接接在核心肯定語後面回傳同一個字串，
    使用者說感謝語會是另一段獨立的音檔，需要拆成獨立欄位——這裡改成只回
    核心肯定語本身，感謝語交給呼叫端另外處理。
    """
    has_hardship_content = any(t in HARDSHIP_TOPICS for t in topics)
    return random.choice(HARDSHIP_CORE_VARIANTS if has_hardship_content else WARM_CORE_VARIANTS)


async def build_closing_message(
    text: str, emotion: str, topics: list[str], llm_service,
) -> tuple[str, str]:
    """
    長者回答完開場邀請語後，組出「承接語（含系統整合肯定，若適用）」與
    「結尾感謝語」兩段，分開回傳。Returns: (receiving_text, thanks_text)。

    抱怨/不滿系統或AI本身時（system_complaint）：receiving_text 只是抱怨
    對應的承接語，不接 build_closing_affirmation 那段「撐過來／美好時光」
    核心語——那段是呼應這場療程實際聊過的內容的肯定語，長者剛才是在抱怨
    系統本身，接這段語意兜不起來；thanks_text 兩種分類都照樣接。

    2026-08-17（第二次）：原本回傳合併成一個字串的收尾訊息，使用者要求
    感謝語要拆成獨立欄位（會是另一個音檔，見 build_closing_invitation
    呼叫處），改成回傳 tuple，兩段內容分開、由呼叫端自行決定怎麼組裝/顯示。
    """
    category, subtype = await classify_closing_response(text, emotion, llm_service)
    thanks_text = random.choice(CLOSING_TAIL_VARIANTS)
    if category == "system_complaint":
        receiving_text = random.choice(SYSTEM_COMPLAINT_RECEIVING_PHRASES[subtype])
        return receiving_text, thanks_text
    receiving_text = random.choice(CLOSING_RECEIVING_PHRASES[category]) + build_closing_affirmation(topics)
    return receiving_text, thanks_text
