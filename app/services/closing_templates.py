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
# 兜不起來的風險。查無對應（例如下面 round3_response 缺值時的通用保底句
# 「今天聊了這麼多。」，這句不在錄音清單裡）就回傳空list，呼叫端維持現況
# ——心得環節本來就不會另外即時TTS，查無key就是沒有語音，跟現在的行為
# 一致，不是新增的缺陷。


def _audio_keys(text: str) -> list[str]:
    key = lookup_audio_key(text)
    return [key] if key else []

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
        scene_text, scene_audio_keys, thanks_text, thanks_audio_keys = await build_closing_message(
            round3_response, emotion, topics, llm_service,
        )
    else:
        scene_text, scene_audio_keys, thanks_text, thanks_audio_keys = "今天聊了這麼多。", [], "", []
    question = "回想整場聊下來，你有什麼想跟我分享的呢？"
    return {
        "scene_text": scene_text,
        # scene_text 可能是「承接語＋系統整合肯定」兩句拼接（見
        # build_closing_message），對應前端要接續播放的兩個音檔，所以是
        # list 不是單一 key；system_complaint分類時只有承接語一句，list只有
        # 一個元素；round3_response/llm_service缺值的中性保底句不在錄音
        # 清單裡，list是空的，跟現況一樣不播語音，不是新增的缺陷。
        "scene_audio_keys": scene_audio_keys,
        "thanks_text": thanks_text,
        "thanks_audio_keys": thanks_audio_keys,
        "question": question,
        "question_audio_keys": _audio_keys(question),
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

    2026-08-17稽核（實測後補）：原本直接叫模型「選一類或回NONE」，是整段
    一次性下結論的判斷模式——跟 orchestrator.py 好幾處稽核筆記記錄過的同一種
    本地量化基底模型不穩定模式一樣（見 _detect_covered_w／_check_w_answered
    等函式說明：「整體判斷小模型會穩定漏判/誤判，改逐項列證據才穩定」）。
    實測案例：長者說「我覺得我彷彿回到了那個時候」——單純在描述沉浸在回憶
    裡的正向感受，被誤判成「想找真人」，接了「不好意思，沒能讓你覺得像在跟
    真人聊天」這種完全答非所問的道歉語。改成先要求引用長者原話裡的具體證據
    再分類，且核對引用的證據是不是真的出現在長者原話裡（judgment_evidence_
    unsupported 同一套防線）——沒有證據支持，或證據是編造的，一律當NONE，
    不採信分類結果。
    """
    prompt = (
        f"長者剛才回答「現在心裡是什麼感覺」這個問題，他說：「{text}」\n\n"
        "請先判斷這句話裡有沒有實際出現「抱怨/不滿這套陪伴系統或AI本身」的"
        "具體字詞或語氣——不是長者在描述回憶內容、也不是單純敘述自己的感受"
        "（即使提到「像/彷彿/感覺」這類詞，只要語意上是在講回憶或情緒本身，"
        "不算）。如果真的有，逐字引用長者原話裡的證據；如果沒有，證據欄位"
        "就寫「找不到」，不要為了湊出證據硬找不相關的字詞。\n"
        "分類請依上面找到的證據，分成以下四類，選最貼近的一種：\n"
        "一般不耐煩：對這個過程/流程感到不耐煩、厭煩、想結束\n"
        "質疑AI不信任：質疑AI聽不懂他、AI不是真的懂他、不相信AI\n"
        "想找真人：明確表示想找真人聊、覺得AI不是人、想要真人陪伴\n"
        "覺得沒意義沒用：覺得這整件事沒有用、沒意義、沒幫助\n"
        "如果證據欄位是「找不到」，分類一律回NONE。\n"
        "輸出格式（兩行都要輸出）：\n"
        "證據：（逐字引用長者原話裡的具體字詞，或「找不到」）\n"
        "分類：（一般不耐煩／質疑AI不信任／想找真人／覺得沒意義沒用／NONE"
        "其中一個，不要其他文字）"
    )
    raw = (await llm_service.ask(prompt, temperature=0)).strip()
    evidence = ""
    classification = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("證據："):
            evidence = line[len("證據："):].strip()
        elif line.startswith("分類："):
            classification = line[len("分類："):].strip()
    if not classification:
        # 模型沒照格式輸出兩行時，退回舊版寬鬆比對，直接看整段輸出裡有沒有
        # 剛好等於某個分類key的內容——比完全解析失敗、直接當NONE安全一點。
        classification = raw
    if classification not in SYSTEM_COMPLAINT_RECEIVING_PHRASES:
        return None
    # 證據核對：引用的內容必須真的出現在長者原話裡，跟 response_guard.py
    # 的 judgment_evidence_unsupported 同一套防線，防止模型分類選對格式、
    # 但證據本身是編造的（先講出正確證據、卻選錯分類，或反過來，都是這個
    # 本地模型已知會犯的錯誤模式）。
    if not evidence or evidence == "找不到" or evidence not in text:
        return None
    return classification


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


def build_closing_affirmation(topics: list[str]) -> tuple[str, str | None]:
    """
    長者分享完之後，系統接住並放大，依這場實際聊過的主題
    決定用「撐過來」還是「美好時光」的收尾語氣，每個版本隨機挑一句，
    避免每次都聽到一模一樣的收尾。不含結尾感謝語——那是獨立的一段
    （CLOSING_TAIL_VARIANTS），呼叫端各自處理，見 build_closing_message。

    2026-08-17（第二次）：感謝語原本直接接在核心肯定語後面回傳同一個字串，
    使用者說感謝語會是另一段獨立的音檔，需要拆成獨立欄位——這裡改成只回
    核心肯定語本身，感謝語交給呼叫端另外處理。

    2026-08-18：回傳改成 (text, audio_key) tuple——audio_key 是這句在
    audio_bank.py SHARING_TEXT_KEYS 裡對應的預錄音檔 key（sharing_affirm_
    hard_1~4／sharing_affirm_warm_1~4 之一），直接對隨機選中的那句文字查表，
    保證 key 跟文字是同一句，不用另外維護一份 index 對照。
    """
    has_hardship_content = any(t in HARDSHIP_TOPICS for t in topics)
    text = random.choice(HARDSHIP_CORE_VARIANTS if has_hardship_content else WARM_CORE_VARIANTS)
    return text, lookup_audio_key(text)


async def build_closing_message(
    text: str, emotion: str, topics: list[str], llm_service,
) -> tuple[str, list[str], str, list[str]]:
    """
    長者回答完開場邀請語後，組出「承接語（含系統整合肯定，若適用）」與
    「結尾感謝語」兩段，分開回傳。Returns: (receiving_text,
    receiving_audio_keys, thanks_text, thanks_audio_keys)。

    抱怨/不滿系統或AI本身時（system_complaint）：receiving_text 只是抱怨
    對應的承接語，不接 build_closing_affirmation 那段「撐過來／美好時光」
    核心語——那段是呼應這場療程實際聊過的內容的肯定語，長者剛才是在抱怨
    系統本身，接這段語意兜不起來；thanks_text 兩種分類都照樣接。

    2026-08-17（第二次）：原本回傳合併成一個字串的收尾訊息，使用者要求
    感謝語要拆成獨立欄位（會是另一個音檔，見 build_closing_invitation
    呼叫處），改成回傳 tuple，兩段內容分開、由呼叫端自行決定怎麼組裝/顯示。

    2026-08-18：receiving_audio_keys 是前端要依序播放的音檔 key 列表——
    system_complaint 分類只有一個key（純抱怨承接語）；其餘分類是「承接語
    ＋系統整合肯定」兩句拼接成一個 receiving_text 字串，對應兩個要接續播放
    的音檔，所以是list不是單一key。receiving_text 本身故意不在承接語跟
    肯定語中間加分隔符（維持原本的行為），前端播放兩個音檔本身自然有間隔，
    不需要文字上的分隔符。
    """
    category, subtype = await classify_closing_response(text, emotion, llm_service)
    thanks_text = random.choice(CLOSING_TAIL_VARIANTS)
    thanks_audio_keys = _audio_keys(thanks_text)
    if category == "system_complaint":
        receiving_text = random.choice(SYSTEM_COMPLAINT_RECEIVING_PHRASES[subtype])
        return receiving_text, _audio_keys(receiving_text), thanks_text, thanks_audio_keys
    ack_text = random.choice(CLOSING_RECEIVING_PHRASES[category])
    affirmation_text, affirmation_key = build_closing_affirmation(topics)
    receiving_text = ack_text + affirmation_text
    receiving_audio_keys = _audio_keys(ack_text) + ([affirmation_key] if affirmation_key else [])
    return receiving_text, receiving_audio_keys, thanks_text, thanks_audio_keys
