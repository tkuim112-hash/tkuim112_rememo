"""
心得環節（三回合結束後）模板化生成。

2026-08-17起心得環節內容以固定模板為主（跟第三回合本身的 closing.txt／
_generate_closing 不同，那個是回合本身的內容，不在這支檔案的範圍內，見
orchestrator.py _start_round3_closing 說明）——三步驟固定流程：

  1. build_closing_invitation(topics) → 開場邀請語，先讓長者自己說感覺
  2. 長者回答後，build_closing_message(text, emotion, topics, llm_service)
     依回答內容＋情緒分類（classify_closing_response），組出承接語＋收尾語：
       - 一般分類（positive/thin_or_silent/emotional）：CLOSING_RECEIVING_
         PHRASES 挑承接語，接 build_closing_affirmation(topics) ——依這場
         療程實際分類到的16大主題決定用「撐過來」還是「美好時光」的核心
         語氣，再接一句感謝的話
       - 抱怨/不滿系統或AI本身（system_complaint）：SYSTEM_COMPLAINT_
         RECEIVING_PHRASES 挑承接語，只接一句 CLOSING_TAIL_VARIANTS 感謝
         語，不接「撐過來／美好時光」那段——語意跟抱怨系統本身兜不起來
     多數分類用純規則判斷，只有「是不是在抱怨系統/AI本身」需要叫LLM（見
     _detect_system_complaint 說明），不是每次都會叫LLM

長者看完 build_closing_message 的訊息後療程直接結束，不用再回應。
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
def build_closing_invitation(topics: list[str]) -> dict:
    """
    心得環節開場，邀請長者自己分享感覺，系統不搶先下結論。Returns:
    {"scene_text": str, "question": str}——scene_text 留空，整句合併寫在
    question 一個欄位裡，不分兩段顯示（呼叫端仍要讀兩個 key，只是 scene_text
    固定是空字串）。

    2026-08-17：原本問「你現在心裡是什麼感覺呢？」，跟回合3收尾（closing.txt／
    _generate_closing，見 orchestrator.py _start_round3_closing）問的「現在
    感受或正向回憶」性質重複——長者才剛答過一次感覺，馬上又被問一次同性質的
    問題。改問「回想整場聊下來」，把切入點從「當下感覺」換成「回顧整場」，
    跟回合3那句不撞。
    """
    return {
        "scene_text": "",
        "question": "今天聊了這麼多，回想整場聊下來，你有什麼想跟我分享的呢？",
    }


# ── 長者回應分類 ──────────────────────────────
# 短到視為「沒有實質內容」的字數門檻，「還好」「嗯」「沒有」這類單薄回應
# 都落在這個門檻內，跟完全沉默（空字串）一起歸類成 thin_or_silent。
_THIN_RESPONSE_MAX_LEN = 4

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
    """
    stripped = (text or "").strip()
    if emotion in ("sad", "angry"):
        return "emotional", None
    if not stripped or len(stripped) <= _THIN_RESPONSE_MAX_LEN:
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
    避免每次都聽到一模一樣的收尾。
    """
    has_hardship_content = any(t in HARDSHIP_TOPICS for t in topics)
    core = random.choice(HARDSHIP_CORE_VARIANTS if has_hardship_content else WARM_CORE_VARIANTS)
    tail = random.choice(CLOSING_TAIL_VARIANTS)
    return f"{core}{tail}"


async def build_closing_message(
    text: str, emotion: str, topics: list[str], llm_service,
) -> str:
    """
    長者回答完開場邀請語後，組出完整的心得環節收尾訊息（承接語＋收尾語），
    session.py 只需要呼叫這一支。

    抱怨/不滿系統或AI本身時（system_complaint）：收尾語只接一句
    CLOSING_TAIL_VARIANTS 感謝語，不接 build_closing_affirmation 那段
    「撐過來／美好時光」核心語——那段是呼應這場療程實際聊過的內容的肯定語，
    長者剛才是在抱怨系統本身，接這段語意兜不起來，只留單純的感謝語收尾。
    """
    category, subtype = await classify_closing_response(text, emotion, llm_service)
    if category == "system_complaint":
        receiving = random.choice(SYSTEM_COMPLAINT_RECEIVING_PHRASES[subtype])
        return receiving + random.choice(CLOSING_TAIL_VARIANTS)
    receiving = random.choice(CLOSING_RECEIVING_PHRASES[category])
    return receiving + build_closing_affirmation(topics)
