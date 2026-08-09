"""
內容驗證與重試模組 — app/safety/response_guard.py

防護對象：AI 生成的回應內容（scene_text / question）在送給長者聽之前，
是否符合 question_5w1h.txt 明文規定的格式/內容規則（是非題、太長、
把AI示意圖當成真實地點、把長者寫成站在畫面裡、精確地名時間、要求描述
畫面內容、已知用詞瑕疵等）。這些規則跟禁忌話題無關，是問題本身的品質/
格式要求；禁忌話題檢查（keyword_prescan／llm_topic_check）留在
taboo_checker.py，這裡透過 guarded_generate 一起呼叫。

架構：
  各條規則各自是一個同步、零延遲的 regex 檢查函式，guarded_generate
  依序呼叫，任一條沒過就把違規原因寫成 retry_feedback 交回生成函式
  重新生成，全部通過或重試次數用盡才回傳結果。
"""
import logging
import re

from safety.taboo_checker import keyword_prescan, llm_topic_check

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# AI 示意圖被當成長者真的去過/認得的地方
# ══════════════════════════════════════════════════════════════════════
#
# question_5w1h.txt 已經明文禁止這個模式（見該檔【提問規則】與【錯誤範例】），
# 但本地模型（rememo-llama3）指令遵循度較弱，實測仍會生成「你有沒有帶過團來
# 這裡參觀古蹟？」這類問題——把 AI 生成的示意圖當成長者本人真的去過、認得的
# 特定地方，長者根本不可能認得一張剛生成的示意圖，只會被迫困惑地附和一個不
# 存在的地方（虛構出一段假記憶）。純靠 prompt 文字說服弱模型不夠可靠，這裡
# 額外加一層事後偵測：只要問題裡同時出現「確認語氣詞」與「特定地點指示詞」，
# 就視為命中，交給 guarded_generate 重新生成。
#
# 判準刻意寬鬆（只要求兩類詞「同時出現」，不要求緊鄰），容許少量誤判——
# 誤判的代價只是多重試一次，遠比放過真正會讓長者困惑的問題划算，
# 跟 element_filter.py Layer 1 的粗篩設計取捨一致。
#
# 地點指示詞用 regex 而非固定詞表：「這個＋名詞」是開放式組合（這個廟口、
# 這個工廠、這個地方…無法窮舉），固定詞表只抓得到「這個地方」這種剛好列在
# 詞表裡的說法，抓不到「你對這個廟口熟不熟悉？」這種換了名詞的說法。
#
# 「確認語氣」除了固定詞表，也要抓「V過…嗎」這種經驗確認句型（例如「你帶過
# 團到這個城市嗎？」）——這種問法沒有用到「有沒有/是不是」等詞，但本質一樣
# 是要長者確認自己對這個特定地方有沒有實際經驗，一樣會讓長者困惑。只在句尾
# 是「嗎」時才觸發，避免誤傷「你以前住過這種三合院嗎」這種允許的泛稱問法
# （「這種」不會被下面的地點指示詞 regex 判定為特定地點，見 has_deictic）。
_CONFIRM_WORDS = ["有沒有", "是不是", "認不認得", "認得", "熟不熟悉", "熟悉", "是否"]
_EXPERIENTIAL_PAST_RE = re.compile(r"(去|來|帶|住|待|到)過")
_PLACE_DEICTIC_RE = re.compile(r"這裡|這地方|這是|這個[^，。！？?\s]{0,6}")


def treats_image_as_real_place(text: str) -> bool:
    """
    True 代表問題把 AI 示意畫面當成長者真的去過/認得的特定地方在問
    （例如「你有沒有來過這裡」「這是不是你以前工作的地方」「你對這個廟口
    熟不熟悉？」「你帶過團到這個城市嗎？」），需要重新生成。
    """
    if not text:
        return False
    has_confirm = any(w in text for w in _CONFIRM_WORDS) or (
        bool(_EXPERIENTIAL_PAST_RE.search(text)) and text.rstrip("？?").endswith("嗎")
    )
    has_deictic = bool(_PLACE_DEICTIC_RE.search(text))
    return has_confirm and has_deictic


# ══════════════════════════════════════════════════════════════════════
# 是非題（比 treats_image_as_real_place 更通用）
# ══════════════════════════════════════════════════════════════════════
#
# treats_image_as_real_place 只抓「確認語氣＋特定地點」的子情況。但實測發現
# 本地模型會生成「你有沒有來過這樣的熱帶島嶼？」這種問題——用「這樣的」把地點
# 講成泛稱（照 question_5w1h.txt 的規則本來是允許的用法），逃過地點指示詞的
# 判定，但整句話仍然是只能回答「有/沒有」的是非題，長者一樣沒辦法展開分享。
# 這裡直接補上更根本的通用檢查：question_5w1h.txt【提問規則】明文「絕對不用
# 是非題」，這條規則本身也該有事後防護，不能只靠 prompt 文字。
_ALTERNATIVE_CHOICE_RE = re.compile(r"還是")
_YESNO_MARKERS_RE = re.compile(
    r"嗎|有沒有|是不是|會不會|是否|對不對|好不好|可不可以|能不能|認不認得|熟不熟悉"
)


def is_yesno_question(text: str) -> bool:
    """
    True 代表這句問題只能回答「是/不是」「有/沒有」，是是非題，需要重新生成。
    「A還是B」這種二選一問句是 question_5w1h.txt 明文允許的例外，不算是非題。
    """
    if not text:
        return False
    if _ALTERNATIVE_CHOICE_RE.search(text):
        return False
    return bool(_YESNO_MARKERS_RE.search(text))


# ══════════════════════════════════════════════════════════════════════
# 場景文字把長者寫成親身站在畫面裡
# ══════════════════════════════════════════════════════════════════════
#
# question_5w1h.txt【承接語／場景文字規則】明文規定場景文字要用第三人稱描述
# 畫面本身，不能寫成長者正站在畫面裡，並附了範例：
#   不寫「你正站在這個海港，望著夕陽」，改寫「夕陽下的海港，漁船正陸續返航」
# 但實測本地模型仍會生成「你站在郵輪甲板上，腳下的海浪輕柔地拍打著船身」這種
# 場景文字，把長者寫成親身置身在一個他從沒去過、AI剛生成的畫面裡——這比單純
# 「問長者是否認得這個地方」更嚴重，是直接用陳述句「告訴」長者他人在畫面中，
# 等同替長者捏造一段不存在的親身經歷。
#
# 判準：場景文字裡只要出現「你」，就代表破壞了第三人稱敘述——檢查過
# question_5w1h.txt 裡所有場景文字／承接語範例，沒有一句用到「你」，這是
# 可以直接照抄的一致模式。注意「closing_text」（收尾語，例如「謝謝你今天的
# 分享」）不適用這條規則，收尾語本來就該直接對長者說話；這裡只檢查
# scene_text 這個 key，closing 的結果不會有 scene_text 這個 key，天然不受影響。
#
# 同一種違規還有一個變形：不用「你」，改用長者的本名當畫面裡動作的主詞
# （例如「張欣瑜小姐，請想像一下自己身處在...」）——一樣是把AI示意圖當成
# 長者本人真的在裡面的照片，只是用第三人稱名字代替第二人稱「你」，一樣要擋。
# 這對應 dpo/collect_data.py 的 elder_as_photo_subject 規則，2026-08 實測
# 發現這個變形完全沒被原本只查「你」的判準攔到，才補上長者姓名的檢查。
def scene_text_addresses_elder(scene_text: str, elder_name: str = "") -> bool:
    """True 代表場景文字（引導語／承接語）用了「你」或長者本名，把長者寫成親身站在畫面裡，需要重新生成。"""
    if not scene_text:
        return False
    if "你" in scene_text:
        return True
    return bool(elder_name) and elder_name in scene_text


# ══════════════════════════════════════════════════════════════════════
# 場景文字把畫面講成「這是一幅畫/示意圖」，用後設視角拉開距離
# ══════════════════════════════════════════════════════════════════════
#
# 2026-08 orchestrator.py 開始把生圖時的 image_prompt 英文原文（脫敏、濾掉
# 畫風片語後）直接傳給問題生成步驟當構圖參考，實測發現本地模型偶爾還是會把
# 畫風描述的殘留語感翻譯進場景文字，變成「一幅水彩畫描繪了1970年代台灣煤礦
# 場景」這種用後設視角描述「這是一幅畫」的句子——跟「場景文字不能寫成長者
# 站在畫面裡」是同一種問題的相反方向：不是把長者拉進畫面裡，是把畫面本身
# 講成一件被觀賞的作品，一樣會讓長者聽起來像在聽別人介紹一幅畫，而不是
# 沉浸式的場景描述，跟「像在描述一幅畫」這句規則裡的比喻本意（客觀、第三
# 人稱）恰恰相反。已經從源頭把 image_prompt 裡的畫風片語濾掉（見
# orchestrator.py _strip_style_descriptors），這裡加一道事後防護，避免模型
# 自己聯想出其他「這是一幅畫/示意圖」的講法。
_ARTWORK_FRAME_RE = re.compile(r"水彩畫|這是一幅畫|一幅.{0,4}畫作|插畫|示意圖|畫面描繪|畫中")


def scene_text_frames_as_artwork(scene_text: str) -> bool:
    """True 代表場景文字把畫面講成「這是一幅畫/示意圖」，用後設視角拉開跟長者的距離，需要重新生成。"""
    if not scene_text:
        return False
    return bool(_ARTWORK_FRAME_RE.search(scene_text))


# ══════════════════════════════════════════════════════════════════════
# 把 prompt 裡的範例句子原句照抄當答案
# ══════════════════════════════════════════════════════════════════════
#
# question_5w1h.txt 裡的範例是每次呼叫都會附上的固定文字。實測發現本地模型
# 遇到情境跟範例接近時（例如職業是導遊、畫面有海/夕陽），會直接把範例裡的
# 「夕陽下的海港，你以前都怎麼帶團介紹？」原句背出來，而不是根據這次真正的
# 【眼前畫面元素】重新生成——這樣問出來的地點/細節可能跟這次畫面實際內容
# 對不上（例如畫面元素只有「海洋、船隻、落日、導遊」，卻冒出範例裡才有的
# 「海港」一詞）。用完全比對抓這種原句照抄，抓到就重新生成。
#
# 只用「完全比對」而非模糊比對：目的是抓「一字不漏照抄」這個明確訊號，
# 不是要禁止長者剛好聊到類似情境——後者是正常對話，不該被誤擋。
_KNOWN_PROMPT_EXAMPLES = {
    "廠門口旁邊，你通常往哪個方向走？",
    "阿珠姐那時候，你們都聊些什麼？",
    "是過年的時候，還是大拜拜的時候？",
    "站在黑板前，你通常怎麼開始上課？",
    "夕陽下的海港，你以前都怎麼帶團介紹？",
    "像這樣的工作場所，你以前都做些什麼？",
    "像這樣的廟口，你小時候都去做什麼？",
    "紡織廠的大門在黃昏的光線下顯得格外寧靜，工人們陸續走出廠門，往各自的方向散去。",
    "聽起來那段跟阿珠姐一起做工的日子很熱鬧呢。",
    "廟口的攤販剛擺出來，香氣混著人聲，好不熱鬧。",
}


def echoes_prompt_example(text: str) -> bool:
    """True 代表這段文字跟 question_5w1h.txt 裡的範例句子完全一樣，是照抄範例而非真正生成。"""
    return text.strip() in _KNOWN_PROMPT_EXAMPLES


# ══════════════════════════════════════════════════════════════════════
# 承接語／場景文字用千篇一律的空泛套語，沒有具體呼應長者剛才說的內容
# ══════════════════════════════════════════════════════════════════════
#
# question_5w1h.txt【承接語／場景文字規則】明文規定「承接語要具體呼應長者剛才
# 說的內容（提到誰、提到什麼事），不能只是空泛的稱讚」，dpo/collect_data.py
# 的 TRACK_C_REJECTION_RULES 也有對應的 generic_formula 規則（訓練資料裡早就
# 有這條規則的 chosen/rejected 對比），但 runtime 一直沒有對應的事後防護——
# 2026-08 實測 STEP2 自由追問，10次裡有6次承接語落在「我們接著聊聊這個吧」
# 這類完全沒提到長者剛才說了什麼人事物的空泛套語，是目前實測命中率最高的
# 違規模式。這裡用完全比對／子字串比對抓已知的固定套語，跟 echoes_prompt_example
# 同一種判準取捨：只抓明確訊號，容許有其他還沒抓到的變形漏網，好過模糊比對
# 誤傷真的有具體呼應內容的承接語。
_GENERIC_ACK_PATTERNS = (
    "我們接著聊聊這個吧",
    "我們繼續聊",
    "那我們繼續",
    "好，我們繼續",
    "謝謝你的分享",
    "謝謝您的分享",
    "這真是很棒的回憶",
)


def is_generic_acknowledgment(scene_text: str) -> bool:
    """True 代表承接語／場景文字是千篇一律的空泛套語，沒有具體呼應長者剛才說的
    內容（提到誰、提到什麼事），需要重新生成。"""
    if not scene_text:
        return False
    return any(p in scene_text for p in _GENERIC_ACK_PATTERNS)


# ══════════════════════════════════════════════════════════════════════
# 格式/內容規則
# ══════════════════════════════════════════════════════════════════════
#
# 這幾條規則在 question_5w1h.txt 裡都有明文規定，但先前只有口頭指示，沒有對應
# 的事後防護。2026-08 用真實模型（rememo-llama3）實測發現：即使前面幾道關卡
# 都通過，仍有相當比例的輸出違反這幾條規則——尤其「要求精確地名/時間」跟
# 「要求描述畫面內容」這兩條，連 dpo/semantic_audit.py 的訓練資料稽核規則都
# 沒有涵蓋，是這次才發現的漏洞，優先補在這裡。
#
# too_long/double_question/memory_test 跟幾個已知用詞瑕疵（先/咱們/搭把手/
# 收場/頭一句/做下來說下去）跟 dpo/data_quality.py 的 fix-wording 子命令
# （2026-08 前是獨立的 dpo/fix_xian_wording.py，後併入 data_quality.py）規則
# 同源——那支模組 import 了 dpo/collect_data.py（需要 ANTHROPIC_API_KEY 才能
# 匯入），不適合讓正式環境的 app/ 依賴訓練 pipeline，所以這裡用同樣的 regex
# 重新定義一份，兩邊之後如果要調規則要記得同步改。dpo/data_quality.py 自己的
# 註解也記錄了一個重要的實測結論：這類格式層級的規則丟給 LLM 判斷不可靠
# （同一句明顯的是非題丟給 Haiku 判斷4次只抓到1次），regex 判準反而更穩定。
_FORMAT_PUNCT_RE = re.compile(r"[，。、！？!?,.\s「」『』（）()]")
_DOUBLE_QUESTION_RE = re.compile(r"[？?]")
# 2026-08 用真正的 production prompt 實測補範例時，額外抓到「你記得」（沒有
# 「還」字）這個變形（例如「你記得戲院裡放過哪些電影類型？」）——語氣上一樣是
# 在測長者的記憶力，但原本的字面只抓「還記得／記不記得」，漏放了這個更短的
# 講法，這裡補上「記得」本身也算命中。
_MEMORY_TEST_RE = re.compile(r"^你?(還記得|記得|記不記得)")

_XIAN_RE = re.compile(r"(?<!最)先(?!生|夫|父|母|人|前|天)")
_ZANMEN_RE = re.compile(r"咱")
_DABASHOU_RE = re.compile(r"搭把手|搭一把手")
_SHOUCHANG_RE = re.compile(r"收場.{0,3}(回家|下班|下工)")
_TOUYIJU_RE = re.compile(r"頭一句(?!話)")
_BOOKISH_VC_RE = re.compile(r"做下來|說下去")

# 「精確地名/時間」跟「要求描述畫面內容」：2026-08 用病患4真實資料實測 STEP1
# 開場問題時發現的兩個新漏洞（見對話紀錄），連 dpo/semantic_audit.py 的11條
# 訓練資料稽核規則都沒有明確涵蓋——question_5w1h.txt 裡只有口頭指示「不需要
# 精確數字/年份/人名/地名」，從沒被做成 chosen/rejected 的對比訓練資料，
# 訓練訊號比其他規則弱，實測違規率明顯偏高，需要事後防護補強。
#
# 「哪個/哪些/哪條+地點類名詞」（例如「哪些特色景點」）、「哪個國家」都曾經
# 想過要擋，但實測後判斷這類問法其實有機會展開成敘事——尤其問到跟長者職業/
# 專長直接相關的內容時（例如問導遊「最喜歡帶團去哪個國家」），長者常常會接著
# 說原因、說故事，不是答一個詞就結束；就算這次答得簡短，STEP2 自由追問也會
# 順著往下接，不會卡住。2026-08 決定只保留下面這組更明確、幾乎不可能展開成
# 敘事的字面（年份/時間/人名這類答了就結束的死板事實）。
_PRECISE_FACT_RE = re.compile(r"哪一?年|什麼時候|幾點|叫什麼|哪一?位")
# 「看到什麼/圖案/造型」這組字面漏掉了實測最常見的變形：「X裡/上/旁邊有什麼」
# （例如「菜籃子裡有什麼？」「秤砣上有什麼？」「菜攤旁邊有什麼？」）——這種
# 問法答案幾乎注定是列舉畫面裡看到的東西，跟「看到什麼」是同一種違規，但下面
# 這條錯誤訊息文字其實一直都有提到「不要問畫面本身有什麼」，只是 regex 本身
# 沒把這個講法寫進去，一直漏放。2026-08 第一版只抓「有什麼」緊接句尾（？/?）
# 的窄版本，漏掉「有什麼」後面還接了具體名詞的變形（例如「有什麼好吃的青菜」），
# 這裡改用負向後顧排除法：只要「有什麼」後面接的不是「想法/感覺/心情」這類
# 抽象反思詞，一律算違規——不管後面是句尾問號還是接了具體名詞，判準是同一個
# （這類抽象詞後面接的問法，例如「心裡有什麼想法」，才是允許的內省提問）。
_IMAGE_DESC_RE = re.compile(
    r"看到什麼|看見什麼|圖案|造型|內容是什麼|寫著什麼|寫什麼|上面寫"
    r"|(?:裡|裡面|上|上面|旁邊|附近)有(?:些)?什麼"
    r"(?!(?:想法|感覺|心情|回憶|打算|計畫|意義|感受|期待|看法|顧慮|牽掛))"
)

# 「哪一種/哪一樣…比較受歡迎/常見/多/好」（比較/排序類）跟「X讓你想到什麼」
# （籠統聯想類）：2026-08 討論時已經寫進 question_5w1h.txt 的自我檢查文字，
# 但當時只補了 prompt 文字、沒有對應的事後防護，一直是漏放狀態——這兩種問法
# 答案本質上是「選一個名稱」或「一兩個字帶過」，跟 _PRECISE_FACT_RE／
# _IMAGE_DESC_RE 是同一類「答案不是敘事」的違規，補在這裡一起用同一套
# retry_feedback 機制處理。
_COMPARISON_TRAP_RE = re.compile(r"哪一?[種樣].{0,15}(?:比較|更)")
_VAGUE_ASSOCIATION_RE = re.compile(r"讓你想到什麼")

# 「您」：question_5w1h.txt 明文規定「稱呼長者一律用「你」...不要用「您」——
# 「您」念起來太正式，會破壞老朋友聊天的溫暖感」，但這條規則之前也只有口頭
# 指示、沒有對應的事後防護，2026-08 實測發現模型偶爾還是會漏用「您」。
_NIN_RE = re.compile(r"您")


def check_format_rules(question_text: str, scene_text: str) -> tuple[str, str] | tuple[None, None]:
    """
    檢查 question_5w1h.txt 明文規定、但先前沒有對應事後防護的幾條格式/內容規則。
    回傳 (違規原因代號, retry_feedback文字)；全部通過回傳 (None, None)。
    """
    q = question_text or ""
    combined = f"{scene_text or ''}{q}"

    length = len(_FORMAT_PUNCT_RE.sub("", q))
    if length > 20:
        return "too_long", (
            f"上一次的問題「{q}」共{length}字，超過20字上限。這次請把這句話縮短到20字以內，"
            "可以拿掉不影響意思的修飾詞。"
        )

    if len(_DOUBLE_QUESTION_RE.findall(q)) > 1:
        return "double_question", (
            f"上一次的問題「{q}」裡有兩個問號，等於一次問兩件事，長者會不知道先回答哪一個。"
            "這次請只保留一個問題。"
        )

    if _MEMORY_TEST_RE.match(q):
        return "memory_test", (
            f"上一次的問題「{q}」用「你還記得／記不記得」開頭，這是在測長者的記憶力而不是"
            "邀請他分享。這次請拿掉這個開頭，直接問內容本身。"
        )

    if _PRECISE_FACT_RE.search(q):
        return "precise_fact", (
            f"上一次的問題「{q}」要求長者說出精確的地名/國家/時間/人名，這類問題長者答不出來"
            "時容易感到挫折。這次請改問過程、感受或互動，不要問需要精確事實性答案的問題。"
        )

    if _IMAGE_DESC_RE.search(q):
        return "image_description", (
            f"上一次的問題「{q}」要長者描述這張AI示意圖裡的畫面內容（例如看到什麼、圖案、"
            "造型）。長者根本沒看過這張剛生成的圖，這樣問等於逼他編答案。這次請把畫面元素"
            "當成引子，問長者自己實際經歷過的事，不要問畫面本身有什麼。"
        )

    if _COMPARISON_TRAP_RE.search(q):
        return "comparison_trap", (
            f"上一次的問題「{q}」問「哪一種/哪一樣…比較受歡迎/常見/多」，答案是選一個"
            "名稱出來，不是敘事。這次請改問過程、感受或互動，例如改問長者怎麼跟客人"
            "介紹賣得最好的那款、或那件事帶給長者的感受。"
        )

    if _VAGUE_ASSOCIATION_RE.search(q):
        return "vague_association", (
            f"上一次的問題「{q}」問「X讓你想到什麼」，太抽象籠統、沒有具體切入點，長者"
            "常常只回一兩個字就結束。這次請改用情感/意義、陪伴的人、感官記憶其中一種"
            "具體角度切入，不要用這種籠統聯想問法。"
        )

    if _NIN_RE.search(combined):
        return "nin_wording", (
            "上一次的內容用了「您」，這個字念起來太正式，會破壞老朋友聊天的溫暖感。"
            "這次請全部改用「你」稱呼長者。"
        )

    if _XIAN_RE.search(combined):
        return "xian_wording", (
            "上一次的內容用了「先＋動詞」這種贅字句型（例如「先做什麼」「先準備」）。"
            "這次請拿掉「先」這個字，改成更口語自然的講法。"
        )
    if _ZANMEN_RE.search(combined):
        return "zanmen_wording", (
            "上一次的內容用了「咱們／咱」，這是北方/大陸口語用詞，不是台灣長者平常會聽到的"
            "說法。這次請改用「我們」。"
        )
    if _DABASHOU_RE.search(combined):
        return "dabashou_wording", (
            "上一次的內容用了「搭把手」，這是北方/大陸口語用詞。這次請改用「幫忙」「幫個忙」"
            "「來湊一腳」這類台灣長者會用的講法。"
        )
    if _SHOUCHANG_RE.search(combined):
        return "shouchang_wording", (
            "上一次的內容把「收場」接「回家/下班/下工」，這是誤用——「收場」是抽象語境。"
            "這次描述收拾東西準備離開的具體動作請改用「收工」「收拾」。"
        )
    if _TOUYIJU_RE.search(combined):
        return "touyiju_wording", (
            "上一次的內容用了省略「話」字的「頭一句」，這種縮略講法語法不完整。"
            "這次請寫成完整的「第一句話」。"
        )
    if _BOOKISH_VC_RE.search(combined):
        return "bookish_verb_complement", (
            "上一次的內容用了「做下來」「說下去」這種讀起來生硬、像書面翻譯腔的動補搭配。"
            "這次請改用「做出來」「完成」「說出口」這類老朋友聊天真的會用的講法。"
        )

    return None, None


# ══════════════════════════════════════════════════════════════════════
# 整合入口：包住既有的生成函式，加上檢查 + 重試 + 保底
# ══════════════════════════════════════════════════════════════════════

SAFE_FALLBACK_SCENE_TEXT = "我們換個輕鬆一點的方向聊聊吧。"
# 「今天過得還好嗎？」本身就是「嗎」結尾的是非題，違反下面 is_yesno_question
# 要擋的規則——保底語句自己不能犯它要防的錯，改成開放式問法。
SAFE_FALLBACK_QUESTION = "現在心裡在想些什麼呢？"
SAFE_FALLBACK_CLOSING_TEXT = "謝謝您今天的分享，辛苦了。"


async def guarded_generate(
    generate_fn,
    taboo_words: list[str],
    llm_service,
    max_retry: int = 1,
    text_keys: tuple[str, ...] = ("scene_text", "question"),
    fallback: dict | None = None,
    **generate_kwargs,
) -> dict:
    """
    包住任一個「產生文字內容」的生成函式，加上格式/內容規則檢查、禁忌話題防護，
    以及不論有沒有設禁忌詞都會執行的「AI示意圖被當成真實地點」等結構性防護。

    Args:
        generate_fn: 原本的生成方法（例如 self._generate_open_followup），
                     必須是 async，回傳一個 dict
        taboo_words: 這位長者的禁忌詞列表；為空時跳過禁忌話題檢查，
                     但下方「示意圖當成真實地點」等檢查一律會執行
        llm_service: LLMService 實例
        max_retry:   違規時重新生成的次數上限
        text_keys:   要納入禁忌話題檢查的欄位名稱（不同生成函式的回傳key不一樣，
                     例如 _generate_open_followup 用 scene_text/question，
                     _generate_closing 用 closing_text/question，必須對齊，
                     否則會漏檢查或保底語句 key 對不上導致呼叫端出錯）
        fallback:    違規重試後仍失敗時的保底回傳值，須包含與 generate_fn
                     相同的 key。未指定時預設使用 scene_text/question 保底，
                     若 text_keys 有換過，務必也提供對應的 fallback。
        **generate_kwargs: 原封不動轉給 generate_fn 的參數

    Returns:
        generate_fn 原本格式的 dict。若重試後仍違規，回傳 fallback。
    """
    if fallback is None:
        fallback = {
            "scene_text": SAFE_FALLBACK_SCENE_TEXT,
            "question": SAFE_FALLBACK_QUESTION,
        }

    attempt = 0
    retry_feedback = ""
    while attempt <= max_retry:
        call_kwargs = dict(generate_kwargs)
        if retry_feedback:
            # 把上一次違反了什麼規則直接告訴模型，而不是原封不動再問一次——
            # 本地弱模型對某些情境（例如職業=導遊+海/島嶼元素）容易是系統性
            # 偏誤，不是隨機雜訊，單純重新取樣常常還是踩同一個雷。
            # generate_fn（_generate_question / _generate_open_followup /
            # _generate_supplement_question / _generate_closing）都必須支援
            # retry_feedback 這個參數，否則這裡會 TypeError。
            call_kwargs["retry_feedback"] = retry_feedback
        result = await generate_fn(**call_kwargs)
        retry_feedback = ""

        # 不論這位長者有沒有設禁忌詞，都要擋「把AI示意圖當成長者真的去過/
        # 認得的地方」這個問題模式（question_5w1h.txt 有寫但本地模型常常
        # 沒遵守，見 treats_image_as_real_place 上方註解）。
        question_text = result.get("question", "")
        if treats_image_as_real_place(question_text):
            logger.warning(
                f"[ResponseGuard] 問題把AI示意圖當成真實地點: {question_text!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的問題「{question_text}」把AI生成的示意畫面當成長者真的去過、"
                "認得的特定地方（用「有沒有」「是不是」「認得」「熟不熟悉」等語氣要長者"
                "確認自己是否認識/去過/熟悉這個地方）。長者不可能認得剛生成的示意圖，"
                "這樣問只會讓他困惑。這次請把畫面元素當成某一類經驗、某一種場景的引子，"
                "改問這一類經驗的普遍情形，不要問長者對眼前這個特定畫面熟不熟悉、認不認得。"
            )
            attempt += 1
            continue

        # 更通用的是非題檢查（見 is_yesno_question 上方註解）：即使用「這樣的」
        # 泛稱逃過上面那條檢查，只要整句仍是「有/沒有」「是/不是」型的是非題，
        # 一樣擋下來重新生成。
        if is_yesno_question(question_text):
            logger.warning(
                f"[ResponseGuard] 問題是是非題: {question_text!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的問題「{question_text}」是是非題，只能回答「是/不是」或「有/沒有」。"
                "這次請改成真正開放式的問題，不要用「嗎」結尾，也不要用「有沒有」「是不是」"
                "「會不會」「認不認得」「熟不熟悉」這類詞（「A還是B」的二選一問法除外）。"
            )
            attempt += 1
            continue

        # 場景文字（引導語／承接語）不能把長者寫成親身站在畫面裡（見
        # scene_text_addresses_elder 上方註解）。只查 scene_text 這個 key，
        # 收尾語（closing_text）的結果沒有這個 key，天然不受影響。
        scene_text_val = result.get("scene_text", "")
        elder_name = (generate_kwargs.get("user") or {}).get("name", "")
        if scene_text_addresses_elder(scene_text_val, elder_name):
            logger.warning(
                f"[ResponseGuard] 場景文字把長者寫成站在畫面裡: {scene_text_val!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的場景文字「{scene_text_val}」用了「你」或長者本名，把長者寫成親身"
                "站在畫面裡（例如「你站在…」「張阿姨站在…」）。這次請改用第三人稱客觀描述"
                "畫面本身，這段文字完全不要出現「你」這個字或長者的名字，把「你」留到問題"
                "那一句再用。"
            )
            attempt += 1
            continue

        # 場景文字不能把畫面講成「這是一幅畫/示意圖」，用後設視角拉開跟長者的
        # 距離（見 scene_text_frames_as_artwork 上方註解）。只查 scene_text，
        # 理由同上一個檢查。
        if scene_text_frames_as_artwork(scene_text_val):
            logger.warning(
                f"[ResponseGuard] 場景文字把畫面講成一幅畫/示意圖: {scene_text_val!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的場景文字「{scene_text_val}」把畫面講成「一幅畫」「示意圖」，"
                "用後設視角在介紹一件作品，會讓長者覺得有距離感。這次請直接沉浸式描述"
                "畫面裡的場景本身（例如直接寫「礦坑入口處，煤炭散落一地」），不要提到"
                "「畫」「畫作」「插畫」「示意圖」這類詞。"
            )
            attempt += 1
            continue

        # 同樣不論有沒有設禁忌詞：擋「原句照抄 question_5w1h.txt 範例」，
        # 這種輸出可能跟這次真正的畫面元素對不上（見 echoes_prompt_example 上方註解）。
        echoed_field = next(
            (k for k in text_keys if echoes_prompt_example(result.get(k, ""))), None
        )
        if echoed_field:
            logger.warning(
                f"[ResponseGuard] {echoed_field} 原句照抄了 prompt 範例: "
                f"{result[echoed_field]!r}，重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的輸出「{result[echoed_field]}」原封不動照抄了 prompt 裡的範例句子，"
                "沒有根據這次真正的【眼前畫面元素】與長者資料生成。這次請根據這次實際提供的"
                "資料重新生成全新內容，不要使用範例裡的地點、物件或字句。"
            )
            attempt += 1
            continue

        # 承接語／場景文字不能是空泛套語，沒有具體呼應長者剛才說的內容
        # （見 is_generic_acknowledgment 上方註解）。只查 scene_text_val，
        # STEP1 場景文字實務上不會剛好等於這些對話式短句，天然不受影響。
        if is_generic_acknowledgment(scene_text_val):
            logger.warning(
                f"[ResponseGuard] 承接語是空泛套語: {scene_text_val!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的承接語「{scene_text_val}」是千篇一律的空泛套語，沒有具體"
                "呼應長者剛才說的內容（提到誰、提到什麼事）。這次請具體引用長者剛才"
                "說的話裡提到的人事物，例如「聽起來那段跟○○一起做工的日子很熱鬧呢」"
                "這種寫法，不要用套語帶過。"
            )
            attempt += 1
            continue

        # 格式/內容規則（too_long、double_question、memory_test、精確地名時間、
        # 要求描述畫面內容、已知用詞瑕疵，詳見 check_format_rules 上方註解）。
        # 2026-08 稽核發現這裡原本直接用上面的 scene_text_val（寫死只讀 "scene_text"
        # 這個 key），導致 closing_text／emotional_text 這類換了 key 名稱的欄位
        # 從沒被「您/先/咱們/搭把手」等用詞檢查覆蓋到——改成組合 text_keys 裡除了
        # question 以外的所有欄位，讓每一種 generate_fn 的非問題欄位都受到保護。
        wording_check_text = "".join(result.get(k, "") for k in text_keys if k != "question")
        format_rule, format_feedback = check_format_rules(question_text, wording_check_text)
        if format_rule:
            logger.warning(
                f"[ResponseGuard] 格式/內容規則違規({format_rule}): "
                f"question={question_text!r} scene_text={scene_text_val!r}，重新生成 (attempt={attempt})"
            )
            retry_feedback = format_feedback
            attempt += 1
            continue

        if taboo_words:
            combined = "".join(result.get(k, "") for k in text_keys)

            # Layer 1：先做便宜的字面檢查
            hits = keyword_prescan(combined, taboo_words)
            if hits:
                logger.warning(f"[ResponseGuard] Layer1字面命中: {hits}，重新生成 (attempt={attempt})")
                retry_feedback = (
                    f"上一次的輸出談到了長者的禁忌話題（{'、'.join(hits)}）。"
                    "這次請完全避開這個方向，不要提及或暗示相關內容。"
                )
                attempt += 1
                continue

            # Layer 2：語意檢查（同步，較貴）
            violated = await llm_topic_check(combined, taboo_words, llm_service)
            if violated:
                logger.warning(f"[ResponseGuard] Layer2語意違規，重新生成 (attempt={attempt})")
                retry_feedback = (
                    "上一次的輸出在語意上涉及了長者的禁忌話題，即使沒有直接用到禁忌詞字面。"
                    "這次請完全避開那個方向，不要往那個主題引導長者。"
                )
                attempt += 1
                continue

        return result

    logger.error(f"[ResponseGuard] 重試{max_retry}次仍違規，退回安全保底語句")
    return fallback
