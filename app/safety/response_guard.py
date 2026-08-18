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
# 2026-08-17稽核（實測後補，使用者確認）：「有什麼X嗎」這種句型雖然字面上
# 命中「嗎」，但跟「你去過嗎」「你認得這個地方嗎」這種純粹的存在/確認型
# 問句不一樣——「什麼」還留著開放內容的成分，長者比較會被引導具體說出
# 「有貝殼、有海浪」這類東西，不是死板的二選一。比照「A還是B」的例外處理，
# 「什麼」出現在「嗎」前面（中間允許夾雜其他字）時不算是非題。
_WH_BEFORE_MA_RE = re.compile(r"什麼.{0,15}嗎")

# 2026-08-18稽核（實測後補，使用者確認）：跟上面「有什麼X嗎」同一種例外，
# 只是「什麼」換到「有沒有」後面——「有沒有什麼味道」「有沒有什麼聲音」
# 這種問法，「什麼」一樣留著開放內容的成分，長者會被引導具體說出「聞到
# 什麼」「聽到什麼」，不是死板的二選一。這個句型還是官方的感官記憶保底
# 問句本尊（見 orchestrator.py _SENSE_QUESTION，5句裡有4句是這個結構：
# 「有沒有什麼聲音」「有沒有什麼味道」「有沒有嚐到什麼味道」「有沒有摸到
# 或感覺到什麼」），本來就是question_5w1h.txt明文教的自然問法，卻被字面
# 上的「有沒有」誤判成是非題重打——這條例外原本漏放，只做了「什麼在嗎
# 前面」，沒做「什麼在有沒有後面」。
_ANYTHING_AFTER_HAVE_NOT_HAVE_RE = re.compile(r"有沒有.{0,6}什麼")


def is_yesno_question(text: str) -> bool:
    """
    True 代表這句問題只能回答「是/不是」「有/沒有」，是是非題，需要重新生成。
    「A還是B」這種二選一問句、「有什麼X嗎」「有沒有什麼X」這種帶開放內容
    的問句，都是question_5w1h.txt明文允許或使用者確認過的例外，不算是非題。
    """
    if not text:
        return False
    if _ALTERNATIVE_CHOICE_RE.search(text):
        return False
    if _WH_BEFORE_MA_RE.search(text):
        return False
    if _ANYTHING_AFTER_HAVE_NOT_HAVE_RE.search(text):
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
#
# 2026-08-17稽核（retired，不再掛進guarded_generate）：這條規則原本要防的
# 是STEP1開場「單純描述畫面本身」的場景文字，但STEP1（_generate_question）
# 後來改用_parse_step1_response，已經不產出scene_text這個欄位了（畫面出示
# 的職責搬到_generate_image_reveal_reaction，那支函式故意改用reaction_text
# 當key，見該函式docstring說明，就是為了繞開這條規則）。現在唯一還會產生
# scene_text的兩支函式（_generate_open_followup／_generate_supplement_
# question，STEP2/STEP3的承接語）本身的prompt都要求「稱呼長者一律用『你』」
# 「具體呼應長者剛才說的內容」，這條規則對它們來說100%是攔錯人（實測案例：
# 「聽你這麼一說，大家在院子裡打鬧的樣子，應該很有趣吧」這種正常承接語被
# 誤擋）。函式本身保留（含下面的判準邏輯），只是不再掛進guarded_generate
# 的檢查清單——如果之後又有新的generate_fn需要「禁止把長者寫進畫面裡」這種
# 語意，可以重新掛回來，但要注意只掛給真的需要第三人稱敘述的那個key，不要
# 再對所有scene_text一視同仁。
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
    "廟口的攤販剛擺出來，香氣混著人聲，好熱鬧。",
    # 2026-08-17稽核（實測後補，隨後撤回）：一度把 _generate_image_reveal_
    # reaction 分類1的範例句（orchestrator.py「改用自己的話接，例如...」
    # 那句）加進這份清單，理由是「這句不會跟judgment_evidence_unsupported
    # 搶額度，因為兩者不是同時發生」——這個判斷是錯的，judgment_evidence_
    # unsupported 檢查的是「判斷依據」欄位有沒有編造內容，任何分類（含1）
    # 只要長者反應訊號弱，都可能同時觸發「編造依據」跟「照抄範例」兩條
    # 規則，跟2026-08-16那次拿掉分類2/3/4範例句的理由其實是同一種情況。
    # 使用者確認：這裡的照抄範例本身不是問題，只要分類跟判斷依據有根據
    # 就好，不需要為了防照抄，犧牲原本就更值得攔的judgment_evidence_
    # unsupported檢查的重試額度——維持2026-08-16當時的結論，這句不放進來。
    # 2026-08-17稽核（實測後補）：_generate_open_followup（STEP2）自己內嵌在
    # 【任務】說明裡教「承接語不能是問句」用的示範句（orchestrator.py
    # 4851-4854）——實測長者答得很薄（例如只說「就是切仔麵」）時，模型會把
    # 這兩句範例原封不動背出來當真實輸出，而不是根據這次長者實際說的話重新
    # 生成。原本這種情況只被 scene_text_addresses_elder 誤抓（因為範例句剛好
    # 含「你」），retry_feedback 講的是錯的重點；那條規則已經retired（見該
    # 函式上方註解），改成正確地由這裡的照抄檢查攔下。
    "原來是切仔麵，光聽你這樣說就覺得很有味道。",
    "那時候通常都跟誰一起去吃？",
    # 2026-08-16稽核：_generate_image_reveal_reaction 自己的4類反應分類範例句
    # （orchestrator.py）2026-08一度加進這份清單，但實測發現這條檢查會跟新
    # 加的 judgment_evidence_unsupported 檢查搶同一份重試額度——長者反應
    # 訊號很弱時，模型常常同時撞到「編造判斷依據」跟「照抄分類範例」兩條
    # 規則，3次重試很容易被兩條規則輪流吃光，反而更常掉到完全通用的保底句。
    # 使用者判斷「照抄範例」本身不是問題（承接語內容跟範例像，只要分類跟
    # 判斷依據是根據長者這次實際說的話推出來的就好），比起「編造依據」是
    # 更值得攔的問題，決定把這4句從清單移除，把重試額度留給後者。
}


def echoes_prompt_example(text: str) -> bool:
    """True 代表這段文字跟 question_5w1h.txt 裡的範例句子完全一樣，是照抄範例而非真正生成。"""
    return text.strip() in _KNOWN_PROMPT_EXAMPLES


# ══════════════════════════════════════════════════════════════════════
# 生成內容出現「阿珠姐」——範例句唯一使用的示範人名，但這次真實資料裡沒有
# ══════════════════════════════════════════════════════════════════════
#
# 2026-08-18稽核（實測後補）：echoes_prompt_example 只做「整句完全比對」，
# 抓不到模型把範例句小幅改寫（換一兩個字）後當真實內容輸出的情況——實測
# 案例：這次生圖前訪談的長者資料完全沒提到「阿珠姐」（只有長者跟家人去
# 七星潭散步的故事），round 2開場的承接語跟問題卻分別是「原來你們那時候
# 一起做工，阿珠姐人很好呢」「阿珠姐那時候，大家都聊些什麼？」——這兩句
# 都是 _KNOWN_PROMPT_EXAMPLES 裡的範例句（「聽起來那段跟阿珠姐一起做工的
# 日子很熱鬧呢」「阿珠姐那時候，你們都聊些什麼？」）換了一兩個字的改寫版
# （「你們」→「大家」），剛好逃過整句完全比對。
#
# 只鎖定「阿珠姐」這一個詞，不用更廣泛的模糊相似度比對：_KNOWN_PROMPT_
# EXAMPLES 裡有些句型（例如「是過年的時候，還是X的時候？」）是常見、合法
# 的中文問句骨架，真實長者資料剛好套用同樣骨架是正常情況，模糊比對整句
# 相似度容易誤傷這種巧合；但「阿珠姐」是範例句專屬拿來示範的虛構人名，不
# 是句型骨架，字面比對就能精準抓，不需要模糊比對整句相似度。
#
# 判準是「有條件」的（不是看到就一律擋）：只有這個名字出現在生成內容裡、
# 卻沒出現在這次真實提供的長者資料（real_context，即 elder_response＋
# pre_image_detail）時才算違規——現實中「阿珠」是常見的長輩稱呼，不能排除
# 真的有長者提過一位「阿珠姐」，這種情況下擋掉會誤傷長者真實提到的人物。
_EXAMPLE_PERSON_NAME_RE = re.compile(r"阿珠姐")


def leaks_example_person_name(text: str, real_context: str) -> bool:
    """True 代表生成內容提到 question_5w1h.txt 範例句專屬的示範人名「阿珠姐」，
    但這次真實提供的長者資料（real_context）裡完全沒有這個名字——代表是模型
    把範例句的人名誤植成真實內容，不是長者真的提過這個人，需要重新生成。"""
    if not text or not _EXAMPLE_PERSON_NAME_RE.search(text):
        return False
    return not _EXAMPLE_PERSON_NAME_RE.search(real_context or "")


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

# 2026-08-18稽核（使用者提案，實測後補）：跟上面固定套語同一類問題的
# 「模板」變形，只是套用的話題名稱每次不同、不能用固定字串比對——
# orchestrator.py _generate_open_followup 的docstring本身就明講過這個
# 反面範例（「原來是跟鄰居一起洗衣服，真懷念」：長者剛才實際說的是
# 「後來大家都改用洗衣機了，方便很多」，承接語卻跳過這句、改回頭重提
# 更早、已經聊過的舊主題），但實測抓到幾乎一模一樣的案例又發生了一次
# （「原來是跟家人一起烤肉，真懷念。」）——證明只在prompt裡用文字規則
# 加一次反面範例不夠可靠，這裡補上對應的事後防護：「原來是」開頭、
# 「懷念」收尾的固定句型，本質上就是「重提話題本身＋套一個懷舊語氣詞」，
# 沒有任何具體、只有長者剛才那句話才會出現的細節，跟 _GENERIC_ACK_
# PATTERNS 是同一種空泛套語，只是外層包了一層看似具體、實際上只是話題
# 名稱代換的殼。只抓「原來是...懷念」這個結構，不擋所有用「原來」開頭
# 的句子——「原來」本身是正常的口語承接詞（prompt自己的正確示範句
# 「原來後來都改用洗衣機了，省了不少功夫呢」也用「原來」開頭），問題
# 只在「原來是」這個特定組合＋句尾收在「懷念」，才是空泛模板的訊號。
_GENERIC_NOSTALGIA_TEMPLATE_RE = re.compile(r"原來是.{0,20}懷念")


def is_generic_acknowledgment(scene_text: str) -> bool:
    """True 代表承接語／場景文字是千篇一律的空泛套語，沒有具體呼應長者剛才說的
    內容（提到誰、提到什麼事），需要重新生成。"""
    if not scene_text:
        return False
    if any(p in scene_text for p in _GENERIC_ACK_PATTERNS):
        return True
    return bool(_GENERIC_NOSTALGIA_TEMPLATE_RE.search(scene_text))


# ══════════════════════════════════════════════════════════════════════
# 承接語／場景文字本身被寫成問句
# ══════════════════════════════════════════════════════════════════════
#
# question_5w1h.txt【承接語規則】明文規定「承接語本身不能寫成問句、不能用
# 「呢」「嗎」這類疑問語尾詞結尾——承接語的工作是接住長者剛才的話，真正的
# 提問留給緊接著的「問題：」欄位；承接語裡如果偷埋了一個問句，會跟後面的
# 「問題：」重複問兩次，語氣也會顯得矛盾」，但這條規則先前只有口頭指示，
# 沒有對應的事後防護（2026-08 實測抓到「大家烤肉的時候都聊些什麼呢？」這種
# 承接語，本身就是一句完整的問句，後面又緊接著另一個「問題：」，長者聽起來
# 會被連問兩次）。跟 is_generic_acknowledgment 同時查 scene_text／reaction_text
# 這兩個key——都是「承接語」性質的欄位，同一條規則。
_SCENE_TEXT_QUESTION_RE = re.compile(r"[呢嗎]\s*[？?]?\s*$|[？?]\s*$")


def scene_text_is_a_question(scene_text: str) -> bool:
    """True 代表承接語／場景文字本身被寫成問句（用「呢」「嗎」這類疑問語尾詞
    結尾，或直接以問號收尾），需要重新生成。"""
    if not scene_text:
        return False
    return bool(_SCENE_TEXT_QUESTION_RE.search(scene_text))


# ══════════════════════════════════════════════════════════════════════
# 承接語跟問題文字幾乎一模一樣
# ══════════════════════════════════════════════════════════════════════
#
# 2026-08-17稽核（實測後補）：round 2開場（_generate_open_followup）實測
# 抓到「承接語：七星潭邊散步，真不錯。」「問題：七星潭邊散步，真不錯。」
# 這種輸出——兩個欄位被本地弱模型寫成同一句話，長者會被迫聽到同一句話
# 唸兩次，且這一輪其實沒有真的問到新問題（承接語的工作是接住長者剛才的
# 話，問題的工作是問一個新方向，兩者職責不同，內容不該一樣）。這是跟
# scene_text_is_a_question／is_generic_acknowledgment同一類「承接語沒有
# 盡到承接語該做的事」的結構性問題，之前完全沒有事後防護。
#
# 判準：正規化（去標點空白）後完全相同才算違規，不做模糊比對——避免誤傷
# 「承接語跟問題剛好都提到同一個關鍵詞/人事物」這種正常情況（例如承接語
# 提到「阿珠姐」、問題也提到「阿珠姐」，這是好的呼應，不該被這條攔下）。
def scene_text_duplicates_question(ack_text: str, question_text: str) -> bool:
    """True 代表承接語（或反應文字）跟問題文字幾乎一模一樣，模型把兩個
    欄位當成同一句話重複輸出，需要重新生成。"""
    if not ack_text or not question_text:
        return False
    normalized_ack = _PUNCT_STRIP_RE.sub("", ack_text)
    normalized_q = _PUNCT_STRIP_RE.sub("", question_text)
    return bool(normalized_ack) and normalized_ack == normalized_q


# ══════════════════════════════════════════════════════════════════════
# 承接語先把「為什麼」的答案講成既定事實，問題又問一次同一個為什麼
# ══════════════════════════════════════════════════════════════════════
#
# 2026-08-18稽核（使用者提案，實測後補）：STEP3補問W(Why)時實測抓到「承接語：
# 聽你這麼說，準備青椒是為了孫女喜歡。」「問題：為什麼要準備青椒呢？」——
# 承接語用「是為了...」把原因講成已經確定的事實，問題卻緊接著又問一次
# 「為什麼」，長者會很困惑：原因不是你剛剛才講的嗎，怎麼又問一次？追查
# 推測是模型想遵守「承接語要具體呼應內容」這條規則，但長者原話其實沒有
# 講出原因（Why本來就是這題想問、還沒問過的維度），模型為了讓承接語聽起來
# 「具體」，自己編了一個原因當事實講出來，跟後面問題欄位真正要問的東西
# 撞在一起，變成自問自答又重問。
#
# 跟 scene_text_duplicates_question（兩欄位文字幾乎相同）是不同的失效
# 模式——這裡兩句文字並不相似，問題出在「承接語已經斷定的內容」跟「問題
# 想問的內容」語意上是同一件事，其中一個變得多餘、矛盾。判準：承接語用
# 「是為了」「是因為」這種「原因＝X」的斷定句型（不是長者自己說的、是
# AI在承接語裡自己下的判斷），問題又是「為什麼／為何」開頭的疑問句，兩者
# 同時成立就算數——只鎖定「是為了／是因為」這種明確斷定原因的句型，不擋
# 承接語裡泛泛提到「因為」但不是在斷定原因本身的情況（例如轉述長者原話），
# 降低誤傷正常呼應內容的承接語的機率。
_REASON_ASSERTION_RE = re.compile(r"是為了|是因為")
_WHY_QUESTION_RE = re.compile(r"為什麼|為何")


def scene_text_preempts_why_question(scene_text: str, question_text: str) -> bool:
    """True 代表承接語已經把「為什麼」的答案講成既定事實（例如「是為了
    孫女喜歡」），問題卻又問一次同一個為什麼，長者會被迫聽到自問自答又
    重問，需要重新生成。"""
    if not scene_text or not question_text:
        return False
    return bool(_REASON_ASSERTION_RE.search(scene_text)) and bool(_WHY_QUESTION_RE.search(question_text))


# ══════════════════════════════════════════════════════════════════════
# 「判斷依據」欄位引用了長者沒說過的話
# ══════════════════════════════════════════════════════════════════════
#
# _generate_image_reveal_reaction（orchestrator.py，2026-08-16稽核）要求LLM
# 先在「判斷依據」欄位列出長者這句反應裡的具體線索，再據此分類、寫承接語
# ——但實測發現本地弱模型在長者反應訊號很弱/很簡短時（例如長者只說「喔...
# 嗯...是喔」這種語助詞），會直接編一句長者沒說過的話當依據（例如寫「長者
# 只籠統說『不太一樣』」，但長者根本沒講過這幾個字），看起來像有在推理，
# 實際上是套用範例模板／幻覺。這比原本「分類判斷不準」更隱蔽，因為分類
# 欄位看起來有憑有據，容易被忽略。
#
# 判準：只檢查「判斷依據」裡有沒有用「」／『』引號直接引用的片段——沒加
# 引號的依據可能是合理的語意摘要/改寫（例如「長者語氣中帶著想念」），不
# 強制逐字比對，避免誤傷正常的改寫式依據；引號代表模型在宣稱「長者說過
# 這句話」，這種宣稱才需要跟長者原話核對是否存在。
_QUOTED_RE = re.compile(r"[「『][^」』]{2,}[」』]")
_PUNCT_STRIP_RE = re.compile(r"[，。！？、\s「」『』]")
_CLAUSE_SPLIT_RE = re.compile(r"[，。；！？]")

# 2026-08-18稽核（使用者提案，實測後補）：實測案例——判斷依據寫「長者說
# 「很像很漂亮」，但沒有明確說「像」或「一致」，也沒有指出任何具體差異。」，
# 「一致」兩字被當成模型「宣稱長者說過這句話」抓到重打，但這句話的真正
# 語意是在陳述「長者沒有明確說『像』或『一致』」——是誠實地說長者沒講過
# 這幾個字，不是宣稱長者講過。原本判準不分青紅皂白，只要引號內容原話裡
# 找不到就當成編造，沒考慮到引號前面可能有否定詞把整句意思反過來。改成
# 先找出引號所在的子句（往前找到最近的逗號/句號等分隔符），只有這個子句
# 裡沒有否定詞時，才視為「宣稱長者說過」，需要跟原話核對；子句裡有否定詞
# 時，代表模型在陳述「長者沒說過」，跟原話找不到這段文字是一致的，不算
# 編造。
_NEGATION_CUES = ("沒有", "沒說", "沒", "不是", "並非", "並沒", "未")


def judgment_evidence_unsupported(evidence: str, elder_response: str) -> bool:
    """True 代表「判斷依據」裡用引號引用的內容，長者這次的原話裡實際上
    沒有出現，是編造出來的，需要重新生成。引號前面的子句裡如果有否定詞
    （代表模型在說「長者沒講過這句話」），則不算編造，見上方註解。"""
    if not evidence or not elder_response:
        return False
    normalized_response = _PUNCT_STRIP_RE.sub("", elder_response)
    for match in _QUOTED_RE.finditer(evidence):
        normalized_quote = _PUNCT_STRIP_RE.sub("", match.group())
        if not normalized_quote or normalized_quote in normalized_response:
            continue
        clause_start = 0
        for sep in _CLAUSE_SPLIT_RE.finditer(evidence, 0, match.start()):
            clause_start = sep.end()
        clause = evidence[clause_start:match.start()]
        if any(neg in clause for neg in _NEGATION_CUES):
            continue
        return True
    return False


# 2026-08-17稽核（實測後補）：分類「1」代表長者覺得圖跟記憶一致，但實測
# 案例（長者說「柚子帽不像他印象中的樣子」）判斷依據欄位忠實引用了「不像」
# 兩字，分類卻還是選1——證據寫對了，分類數字選反，不是judgment_evidence_
# unsupported要抓的「編造證據」問題，是另一種模型推理不一致：本地量化基底
# 模型（未經DPO）對否定詞的判斷不穩定，見orchestrator.py
# _generate_image_reveal_reaction docstring 2026-08-17第九次稽核的同類案例
# （那次是「先肯定後接具體差異」被只吃到開頭的「像」；這次是整句只有一個
# 「不像」也漏讀）。只挑意思明確、跟「圖跟記憶像不像」這個情境緊密綁定的
# 否定詞，不用「不是」這種太廣泛、任何情境都可能出現的詞，避免像
# deidentifier.py之前的姓氏regex誤傷「任何」那樣，反而誤傷不相關文字。
_RESEMBLANCE_NEGATION_RE = re.compile(
    "不像|不太像|不大像|不一樣|不太一樣|不同|有落差|有差異|不太對"
)


def classification_contradicts_negation(
    classification: str, evidence: str, elder_response: str,
) -> bool:
    """True 代表分類判成「1」（肯定/一致），但長者原話裡卻明確出現「不像」
    這類否定相似詞——分類判斷跟長者實際說的話自相矛盾，需要重新生成。

    2026-08-17稽核（第二次，實測後補）：原本連 evidence 一起掃，這是跟
    classification_lacks_discrepancy_evidence 同一種bug——evidence是模型
    自己寫的自由文字，「沒有具體指出哪裡不一樣」這種在解釋「長者沒有講出
    差異」的句子，字面上還是含「不一樣」三個字，會被誤判成長者的話裡有
    否定詞，實際上根本沒有（實測案例：長者說「我覺得很像我記憶中的樣子」，
    分類1本來是對的，卻被這個false positive攔下來白重試一次，重試時
    retry_feedback又誤導模型去猜「原話裡有否定詞」，連帶引發下一輪
    judgment_evidence_unsupported攔截——一次false positive拖累了兩輪
    重試）。改成只查長者原話，不受模型評語措辭影響，跟該函式的修法同步。
    """
    if classification != "1":
        return False
    return bool(_RESEMBLANCE_NEGATION_RE.search(elder_response))


# 2026-08-17稽核（實測後補，鏡像案例，retired，不再掛進guarded_generate）：
# 上面那條抓的是分類1但證據裡有否定詞的矛盾；這次抓到反方向的錯誤——長者
# 說「我覺得很漂亮」，完全是單純正面評價，沒有任何「不像/不一樣」這類跟
# 記憶比對有關的差異訊號，卻被判成分類2（有差異）。
#
# 當初設計只檢查分類2、不含分類3：分類3允許用「我家不是用那種桌子跟椅子」
# 這類「不是＋具體物件」的表達方式指出差異，這種講法不會命中
# _RESEMBLANCE_NEGATION_RE（規則本身刻意只收窄範圍明確的詞，見上方說明），
# 如果分類3也套用這條檢查，會把合法的分類3案例誤判成「缺乏證據」，反而
# 誤傷——這正是原本刻意排除分類3的理由。2026-08-18稽核（使用者提案）：
# _generate_image_reveal_reaction 把分類2、3合併成一個「有差異」分類後
# （見該函式說明），原本分類3那種「不是＋具體物件」但不含「不像/不一樣」
# 字面的合法差異表達，現在也會被分類成「2」，會被這條檢查誤判成「缺乏
# 證據」、retry_feedback還會誤導模型把正確的「有差異」改判成「肯定」，
# 比單純誤擋更嚴重（不只浪費一次重試，還把分類導向錯誤方向）。這正是
# 當初刻意排除分類3的那個問題，合併後在分類2身上重現，函式保留但不再
# 掛進 guarded_generate 檢查清單，跟 scene_text_addresses_elder 同一種
# retired 處理。
def classification_lacks_discrepancy_evidence(
    classification: str, evidence: str, elder_response: str,
) -> bool:
    """True 代表分類判成「2」（有差異），但長者原話裡找不到任何差異/否定
    相似詞——分類判斷缺乏證據支持，需要重新生成。

    只查 elder_response、不查 evidence：evidence 是模型自己寫的自由文字，
    常常會在「解釋長者『沒有』講出哪裡不一樣」時，原句就寫出「沒有指出哪裡
    不一樣」這種話——這句解釋本身就含有「不一樣」三個字，如果把 evidence
    也一起掃，会被這種「提到差異詞、但其實是在說『沒有差異訊號』」的自我
    矛盾文字騙過去，變成永遠測不出這個bug（實測踩到：evidence寫「沒有具體
    指出圖片跟他記得的哪裡不一樣」，combined查詢會誤判成「有」不一樣）。
    只信長者自己的原話，不受模型評語措辭影響。
    """
    if classification != "2":
        return False
    return not bool(_RESEMBLANCE_NEGATION_RE.search(elder_response))


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
# 2026-08-17稽核（實測後補）：_DOUBLE_QUESTION_RE 只數問號數量，抓不到「用
# 逗號接兩個各自帶疑問詞的分句、結尾共用同一個問號」這種複合問句（實測
# 案例：「河堤那邊有什麼特別的風景，你們都在聊些什麼？」——整句只有一個
# 問號，但其實是兩個問題：風景有什麼特別的、聊些什麼）。改成額外檢查：
# 用逗號拆開後，如果有兩個以上分句各自獨立帶疑問詞，一樣算複合問句，不管
# 問號有幾個。只挑意思明確的疑問詞（什麼/哪裡/哪個/哪一/誰/怎麼/為什麼/
# 多少），避免誤判——像「如果你願意，你想聊些什麼呢」這種「條件子句＋
# 單一問題」的正常句型，條件子句本身不含疑問詞，只有1個分句命中，不會
# 被誤攔。
_WH_MARKER_RE = re.compile(r"什麼|哪裡|哪個|哪一|誰|怎麼|為什麼|多少")


def _has_multi_wh_clauses(q: str) -> bool:
    """True 代表逗號分隔的分句裡，有兩個以上各自獨立帶疑問詞——即使全句
    只有一個問號，還是等於一次問兩件事，長者會不知道先回答哪一個。"""
    clauses = re.split(r"[，,]", q)
    return sum(1 for c in clauses if _WH_MARKER_RE.search(c)) >= 2
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
# 2026-08-17稽核（實測後補）：question_5w1h.txt 裡「廟口的攤販...好不熱鬧」
# 這句範例本身是「好不＋形容詞＝很＋形容詞」的古典/書面語構句（好不熱鬧＝
# 很熱鬧），模型學了這個構句、套進新內容繼續生成（「...好不熱鬧」），語法
# 沒錯，但長者聽TTS唸出來容易把「不」聽成字面否定、誤解成「不熱鬧」，跟
# 「咱們／搭把手」這類「語法對但不適合唸給長者聽」是同一類問題。只抓已經
# 踩到過的「好不熱鬧」這個具體詞（跟_DABASHOU_RE/_SHOUCHANG_RE一樣窄），
# 不擴大成「好不＋任意形容詞」的通用模式——「好不容易」是日常大量使用、
# 完全不會讓人誤解的常用語，一併擋掉會誤傷正常講法。來源範例句已經同步改成
# 「好熱鬧」（見 question_5w1h.txt 與下面 _KNOWN_PROMPT_EXAMPLES）。
_HAOBU_RE = re.compile(r"好不熱鬧")
# 2026-08-17稽核（實測後補）：closing.txt【收尾語規則】早就明文禁止「好了」
# 「就先聊到這裡」「就到這邊」這類聽起來想結束對話、打發人的轉折語，但只有
# prompt文字、從沒有對應的事後防護——這次實測抓到同一種毛病的變形：「不過
# 現在時間不早了，我們也該回去了」，用「時間到了要送客」當藉口，一樣是趕人
# 語氣，只是沒用到「好了」這幾個字，沒被口頭規則的具體例句涵蓋到。
_RUSHING_CLOSING_RE = re.compile(r"時間不早|該回去了|該走了|該回家了")

# 2026-08-17稽核（實測後補）：長者看完AI示意圖、指出跟記憶不一樣（分類2/3，
# 見 orchestrator.py _generate_image_reveal_reaction）時，實測案例承接語寫成
# 「聽你這樣說，你記得的畫面跟這張圖片不太一樣，很有趣。」——長者是認真在
# 糾正AI畫錯的地方，把這件事講成「很有趣」等於把他的記憶落差當成趣聞/笑話
# 看待，跟分類3原本就禁止的「沒關係／不重要」是同一種不尊重長者的輕描淡寫，
# 只是換了個聽起來正面、但一樣不合適的詞。只在「不一樣/不像/不同」這類差異
# 語境同時出現「很有趣/好玩/好笑」時才算違規——這兩組詞單獨出現在其他情境
# 都可能是正常用法（例如長者自己說某段回憶「很有趣」），只有兩者同時出現在
# 同一段承接語裡才是這裡要擋的組合。
_TRIVIALIZE_DISCREPANCY_RE = re.compile(
    r"(?:不像|不太像|不大像|不一樣|不太一樣|不同|有落差|有差異|不太對)"
    r".{0,15}(?:很有趣|好玩|好笑|有意思)"
    r"|(?:很有趣|好玩|好笑|有意思)"
    r".{0,15}(?:不像|不太像|不大像|不一樣|不太一樣|不同|有落差|有差異|不太對)"
)

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
# check_format_rules 回傳的違規代號裡，只會檢查 question_text（q）本身、
# 不會檢查 scene_text/reaction_text 等其他欄位的規則——guarded_generate 的
# question_only_retry_fn 機制（見該函式說明）靠這份清單判斷「這個違規原因
# 是不是保證跟其他欄位無關」，安全地只重新生成 question、不用讓已經驗證過的
# 承接語跟著陪葬重生。double_question/memory_test/precise_fact/image_
# description/comparison_trap/vague_association 都跟 too_long 一樣只查 q
# （見 check_format_rules 內部實作），nin/xian/zanmen 等用詞規則因為查的是
# scene_text+q 的 combined，不在此列——沒辦法排除是 scene_text 那邊的問題。
_QUESTION_ONLY_FORMAT_RULES = frozenset({
    "too_long", "double_question", "memory_test", "precise_fact",
    "image_description", "comparison_trap", "vague_association",
    "sense_as_method",
})

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
#
# 2026-08-18稽核（使用者提案，實測後補）：「裡」前面加負向後顧排除
# 「家」「心」——實測案例：「你們家裡有什麼特別的烤肉食材嗎？」被判成
# image_description違規，但這句問的是長者真實生活裡的事（家裡/記憶裡），
# 不是要長者描述AI示意圖的畫面內容，跟這條規則真正要擋的「菜籃子裡有
# 什麼」（問畫面裡的物件）性質完全不同——原本的判準只看「裡」這個字，
# 沒管前面接的是「家」「心」這類生活/記憶場域，還是「菜籃子」「圖」這類
# 畫面裡的容器/物件。只排除「上」「旁邊」「附近」不加這個限制，因為目前
# 沒有「家上」「心上」這類實測案例，「心上」本身也已經被下面的抽象反思詞
# 後顧排除（例如「心上有什麼想法」）涵蓋掉一部分。
_IMAGE_DESC_RE = re.compile(
    r"看到什麼|看見什麼|圖案|造型|內容是什麼|寫著什麼|寫什麼|上面寫"
    r"|(?<!家)(?<!心)(?:裡|裡面|上|上面|旁邊|附近)有(?:些)?什麼"
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

# 「你/你們＋怎麼＋聽/聞/嚐」：把被動感官接收動作講成有「方法/技巧」可問。
# 2026-08-18稽核（實測後補）：question_5w1h.txt「感官記憶當切入角度」的
# 自然問法本來就是「有沒有什麼X，讓你印象特別深呢」這種問印象/記憶本身的
# 句型（見 orchestrator.py _SENSE_QUESTION），但本地模型在改用感官切入時
# 會把感官動作本身當成有「方法」可問，套用「怎麼」這個How維度慣用詞（實測
# 案例：「七星潭的海浪聲你都怎麼聽呢」「...你們以前都怎麼聽呢」），聽/聞/
# 嚐這類被動感官接收動作本來就沒有值得問的「方法」，聽起來很拗口——即使
# orchestrator.py 的提示詞已經補了具體範例、明文禁止這個句型，仍重複踩到
# 同一個模式，只教不查不夠可靠，補上事後防護。
#
# 只挑「聽/聞/嚐」，不含「看」——「你怎麼看」是極常見、完全自然的中文
# 慣用語（「這件事你怎麼看」＝問意見/看法），跟「怎麼聽/聞/嚐」這種問被動
# 感官接收「方法」的不自然用法完全是兩回事，納入會大量誤傷正常問句。不含
# 「摸」「吃」——「你們都怎麼吃」「這個要怎麼摸」在某些情境下是合理的過程/
# 方式問題（例如吃法、操作方式），不像「聽/聞/嚐」幾乎沒有例外。
_SENSE_METHOD_RE = re.compile(r"你(?:們)?.{0,10}怎麼.{0,3}(?:聽|聞|嚐)")

# 下面 retry_feedback 引用的三句範例，文字逐字對齊 orchestrator.py 的
# _SENSE_QUESTION（聽覺/嗅覺/味覺三項，跟 _SENSE_METHOD_RE 涵蓋的三種
# 感官一致）——這支模組是 safety/ 底下的獨立防護層，orchestrator.py 已經
# import 了這支模組的 guarded_generate，回頭 import _SENSE_QUESTION 會
# 造成循環 import，只能複製這三句文字，跟 dpo/data_quality.py 因同樣理由
# 複製 dpo/collect_data.py 規則是同一種取捨（見該檔說明）。orchestrator.py
# 那邊如果改了這三句的措辭，這裡要記得同步改，避免兩邊分開維護、之後改
# 一邊忘改另一邊（跟 round1_qa_log carryover 當初在 session.py／manual_
# test_full_round.py 分開寫漏同步是同一種教訓，使用者已經實測踩過一次）。
_SENSE_METHOD_EXAMPLES = (
    "「那個時候，有沒有什麼聲音，讓你印象特別深呢？」"
    "「那個時候，空氣中有沒有什麼味道呢？」"
    "「那時候，有沒有嚐到什麼味道呢？」"
)

# 「您」：question_5w1h.txt 明文規定「稱呼長者一律用「你」...不要用「您」——
# 「您」念起來太正式，會破壞老朋友聊天的溫暖感」，但這條規則之前也只有口頭
# 指示、沒有對應的事後防護，2026-08 實測發現模型偶爾還是會漏用「您」。
_NIN_RE = re.compile(r"您")

# 2026-08-18稽核（使用者提案，實測後補）：跟「您」同一種毛病的變形——
# question_5w1h.txt 明文規定稱呼長者一律用「你」，但實測抓到承接語把長者
# 寫成第三人稱的「長者」（例如「長者剛才提到跟家人一起在院子烤肉。」），
# 聽起來像案例紀錄／醫療文件的口吻，不是直接對長者說話，跟「您」太正式
# 是同一種「破壞老朋友聊天的溫暖感」問題，只是這次是人稱錯誤而不是敬語
# 太正式。這句話原本應該念成「你剛才提到跟家人一起在院子烤肉」，模型卻
# 用第三人稱的「長者」取代了「你」，長者聽了會覺得AI在講別人的事、不是
# 在跟自己說話。
_THIRD_PERSON_ELDER_RE = re.compile(r"長者")


def check_format_rules(question_text: str, scene_text: str) -> tuple[str, str] | tuple[None, None]:
    """
    檢查 question_5w1h.txt 明文規定、但先前沒有對應事後防護的幾條格式/內容規則。
    回傳 (違規原因代號, retry_feedback文字)；全部通過回傳 (None, None)。
    """
    q = question_text or ""
    combined = f"{scene_text or ''}{q}"

    length = len(_FORMAT_PUNCT_RE.sub("", q))
    if length > 30:
        return "too_long", (
            f"上一次的問題「{q}」共{length}字，超過30字上限。這次請把這句話縮短到30字以內，"
            "可以拿掉不影響意思的修飾詞。"
        )

    if len(_DOUBLE_QUESTION_RE.findall(q)) > 1:
        return "double_question", (
            f"上一次的問題「{q}」裡有兩個問號，等於一次問兩件事，長者會不知道先回答哪一個。"
            "這次請只保留一個問題。"
        )

    if _has_multi_wh_clauses(q):
        return "double_question", (
            f"上一次的問題「{q}」用逗號連接了兩個各自帶疑問詞的分句，即使結尾只有一個"
            "問號，還是等於一次問兩件事，長者會不知道先回答哪一個。這次請只保留一個"
            "問題，拿掉其中一個分句。"
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

    if _SENSE_METHOD_RE.search(q):
        return "sense_as_method", (
            f"上一次的問題「{q}」把「聽/聞/嚐」這類被動感官接收動作講成有「方法」"
            "可問（例如「你都怎麼聽」），聽起來很不自然——這類感官本來就沒有值得問"
            f"的「方法」。這次請改用這種自然問法：{_SENSE_METHOD_EXAMPLES}（都是"
            "「有沒有什麼X，讓你印象特別深呢」這種問印象/記憶本身的句型），套用"
            "同一個句型、換成跟這次情境相關的感官對象即可，不要用「你/你們+怎麼+"
            "聽/聞/嚐」這種句型。"
        )

    if _NIN_RE.search(combined):
        return "nin_wording", (
            "上一次的內容用了「您」，這個字念起來太正式，會破壞老朋友聊天的溫暖感。"
            "這次請全部改用「你」稱呼長者。"
        )

    if _THIRD_PERSON_ELDER_RE.search(combined):
        return "third_person_elder_wording", (
            f"上一次的內容「{combined}」用第三人稱的「長者」稱呼對方（例如「長者剛才"
            "提到...」），聽起來像案例紀錄，不是直接在跟長者說話。這次請把「長者」"
            "全部改成「你」，直接對長者說話。"
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
    if _HAOBU_RE.search(combined):
        return "haobu_wording", (
            "上一次的內容用了「好不熱鬧」，這是「好不＋形容詞＝很＋形容詞」的古典構句"
            "（意思其實是「很熱鬧」），但長者聽語音唸出來容易把「不」聽成字面否定、"
            "誤會成「不熱鬧」。這次請直接改用「好熱鬧」，不要用「好不」這種構句。"
        )
    if _RUSHING_CLOSING_RE.search(combined):
        return "rushing_closing_wording", (
            f"上一次的內容用了「{combined}」裡的「時間不早了／該回去了」這類藉口時間到了"
            "要送客的講法，聽起來像在趕長者走，跟「好了，就先聊到這裡」是同一種毛病。"
            "這次請拿掉跟時間有關的藉口，直接用「回到現在」的感受或問題自然銜接，"
            "不要暗示長者耽誤了時間、該被送走了。"
        )
    if _TRIVIALIZE_DISCREPANCY_RE.search(combined):
        return "trivialize_discrepancy_wording", (
            f"上一次的內容把長者指出AI畫面跟記憶不一樣這件事，講成「很有趣/好玩」這類"
            "輕鬆、當趣聞看待的詞——長者是認真在糾正AI畫錯的地方，這樣講會讓他覺得"
            "自己的記憶被當成好玩的事，不是被尊重地聽見，跟「沒關係/不重要」是同一種"
            "不合適的輕描淡寫。這次請拿掉「很有趣/好玩/好笑」這類詞，改用認真、"
            "珍惜長者記得這麼清楚的語氣接住。"
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
    question_only_retry_fn=None,
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
        question_only_retry_fn: 選用。2026-08稽核發現：generate_fn 一次生成
                     承接語＋問題兩個欄位，只要問題那半段違規（例如too_long），
                     整包就會被丟掉重新生成——即使承接語當次已經通過所有檢查
                     （例如已經正確判斷出4類反應之一、寫出很好的承接語），也會
                     被迫陪著重生一次，實測發現這種「問題違規、承接語其實沒問題」
                     的情況並不少見。提供這個參數後，一旦違規原因確定只跟
                     question有關（treats_image_as_real_place／is_yesno_question／
                     format_rule 屬於 _QUESTION_ONLY_FORMAT_RULES），就鎖住當次
                     其餘欄位、之後只呼叫這支函式重新生成 question，不用讓
                     generate_fn 的其他欄位跟著重新賭一次；一旦遇到任何無法排除
                     是其他欄位問題的違規，立刻解鎖、退回原本整包重新生成。
                     這支函式必須接受跟 generate_fn 相同的呼叫參數（含
                     retry_feedback），回傳至少含 "question" 的 dict（有
                     "covered_w" 就一併採用，沒有則視為空清單）。
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
    # 2026-08-18稽核（使用者提案，實測後補）：呼叫端可能已經自己帶了一次
    # retry_feedback 進來（例如呼叫端自己核對過上一次輸出有問題、帶著
    # 具體修正指示呼叫這裡）——但下面每次違規檢查都是「retry_feedback =
    # 新訊息」直接整段覆蓋，不是累加。實測案例：呼叫端帶了「換一個全新
    # 角度」的feedback，attempt=0真的换了角度，卻連帶踩到別的格式規則
    # （haobu_wording），這裡的retry_feedback被格式規則的訊息整個蓋掉，
    # attempt=1完全看不到「換角度」這個原始指示，只知道「別用haobu這個
    # 詞」，同時也徹底失去「要錨定在原本這個場景」的提醒——連續兩三輪
    # 格式規則觸發、覆蓋，模型漸漸飄到跟原本場景（七星潭/散步）完全無關
    # 的內容（例如「讀書上課」）。改成把呼叫端帶進來的這份retry_feedback
    # 當基底，之後每次格式規則觸發的feedback都疊加在它後面、不覆蓋掉，
    # 讓呼叫端最初的指示能撐過整個重試迴圈。
    base_retry_feedback = generate_kwargs.pop("retry_feedback", "") or ""
    retry_feedback = ""
    # 見 question_only_retry_fn 參數說明：非 None 代表「這次違規確定只跟
    # question 有關」，下一輪改呼叫 question_only_retry_fn 只重生 question，
    # 其餘欄位沿用這份鎖住的值；None 代表沒有鎖定，走原本整包重新生成。
    locked_fields: dict | None = None
    while attempt <= max_retry:
        call_kwargs = dict(generate_kwargs)
        combined_feedback = "\n".join(f for f in (base_retry_feedback, retry_feedback) if f)
        if combined_feedback:
            # 把上一次違反了什麼規則直接告訴模型，而不是原封不動再問一次——
            # 本地弱模型對某些情境（例如職業=導遊+海/島嶼元素）容易是系統性
            # 偏誤，不是隨機雜訊，單純重新取樣常常還是踩同一個雷。
            # generate_fn（_generate_question / _generate_open_followup /
            # _generate_supplement_question / _generate_closing）都必須支援
            # retry_feedback 這個參數，否則這裡會 TypeError。
            call_kwargs["retry_feedback"] = combined_feedback
        if locked_fields is not None:
            q_result = await question_only_retry_fn(**call_kwargs)
            result = {
                **locked_fields,
                "question": q_result.get("question", ""),
                "covered_w": q_result.get("covered_w", []),
            }
        else:
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
            # 這條規則只檢查 question_text，跟其他欄位（例如承接語）無關，
            # 可以安全鎖定其餘欄位、下一輪只重生 question（見 question_only_
            # retry_fn 參數說明）。
            if question_only_retry_fn is not None:
                locked_fields = {k: v for k, v in result.items() if k not in ("question", "covered_w")}
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
            # 理由同上：只檢查 question_text，可以安全鎖定其餘欄位。
            if question_only_retry_fn is not None:
                locked_fields = {k: v for k, v in result.items() if k not in ("question", "covered_w")}
            attempt += 1
            continue

        # scene_text_addresses_elder 這條檢查 2026-08-17 稽核後從這裡移除，
        # 見該函式上方註解的retired說明——現在唯一還會產生scene_text欄位的
        # 兩支函式（_generate_open_followup／_generate_supplement_question，
        # 也就是STEP2/STEP3的承接語）本身的prompt都明確要求「稱呼長者一律
        # 用『你』」「具體呼應長者剛才說的內容」，這條檢查對它們來說100%是
        # 攔錯人（實測案例：「聽你這麼一說，大家在院子裡打鬧的樣子，應該很
        # 有趣吧」這種正常呼應長者的承接語被誤擋）。真正該防的「把長者寫成
        # 站在AI示意圖裡」這個情境，在_generate_image_reveal_reaction改用
        # reaction_text當key之後就已經不會再命中scene_text這個key了。
        scene_text_val = result.get("scene_text", "")

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
            # 理由同上：查的是承接語／場景文字欄位，解鎖退回整包重新生成。
            locked_fields = None
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
            # echoed_field 可能是 question 以外的欄位（例如承接語），不能排除
            # 是被鎖定的欄位照抄範例，解鎖退回整包重新生成。
            locked_fields = None
            attempt += 1
            continue

        # 生成內容出現範例句專屬人名「阿珠姐」，但這次真實資料裡沒有這個人
        # （見 leaks_example_person_name 上方註解）——combined 查全部 text_keys
        # 欄位（承接語／問題都可能誤植），real_context 用這次真實提供的長者
        # 資料組成，只在名字「不在」真實資料裡才算違規，避免誤傷長者真的
        # 提過同名親友的情況。
        combined_for_name_check = "".join(result.get(k, "") for k in text_keys)
        real_context = (
            f"{generate_kwargs.get('elder_response', '')} "
            f"{generate_kwargs.get('pre_image_detail', '')}"
        )
        if leaks_example_person_name(combined_for_name_check, real_context):
            logger.warning(
                f"[ResponseGuard] 輸出提到範例人名「阿珠姐」，但這次長者資料"
                f"裡沒有這個人: {combined_for_name_check!r}，重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的輸出「{combined_for_name_check}」提到了「阿珠姐」，但這次長者"
                "實際提供的資料裡完全沒有這個人——這是把 prompt 裡範例句的示範人名"
                "誤植成真實內容了。這次請完全根據這次真正的長者資料重新生成，不要"
                "使用「阿珠姐」這個名字，也不要使用範例句裡的其他地點、物件或字句。"
            )
            # 名字可能出現在承接語或問題任一欄位，不能排除是被鎖定的欄位造成，
            # 解鎖退回整包重新生成。
            locked_fields = None
            attempt += 1
            continue

        # 承接語／場景文字不能是空泛套語，沒有具體呼應長者剛才說的內容
        # （見 is_generic_acknowledgment 上方註解）。
        #
        # 2026-08稽核：這裡原本只查 scene_text_val（寫死 "scene_text" 這個
        # key），導致 _generate_image_reveal_reaction／_generate_quick_end_recap
        # 用的 "reaction_text" key完全沒被這條規則覆蓋到——那兩支函式的任務
        # 說明明明也要求「不能只是空泛的稱讚」（例如 _generate_quick_end_recap
        # 明講「不能只是空泛的稱讚（例如不寫「謝謝你告訴我這些」...」），卻沒有
        # 對應的事後防護，是漏放。改成 scene_text_val 為空時退回讀 reaction_text
        # ——不用像 check_format_rules 那樣完整迭代整個 text_keys，因為
        # closing_text／emotional_text 的核心內容本來就常常合理包含「謝謝你的
        # 分享」這類語意（收尾語、情緒支持的本質就是要感謝/肯定），套用這條
        # 規則會造成大量誤判，只有 reaction_text 跟 scene_text 一樣是「呼應
        # 長者剛才說的內容」性質的承接語，才適合共用同一條檢查。
        ack_check_text = scene_text_val or result.get("reaction_text", "")
        if is_generic_acknowledgment(ack_check_text):
            logger.warning(
                f"[ResponseGuard] 承接語是空泛套語: {ack_check_text!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的承接語「{ack_check_text}」是千篇一律的空泛套語，沒有具體"
                "呼應長者剛才說的內容（提到誰、提到什麼事）。這次請具體引用長者剛才"
                "說的話裡提到的人事物，例如「聽起來那段跟○○一起做工的日子很熱鬧呢」"
                "這種寫法，不要用套語帶過。"
            )
            # 這條規則查的正是承接語欄位本身，解鎖退回整包重新生成。
            locked_fields = None
            attempt += 1
            continue

        # 承接語／場景文字本身不能被寫成問句（見 scene_text_is_a_question
        # 上方註解）。跟上面 is_generic_acknowledgment 共用同一組 scene_text／
        # reaction_text 判準理由——都是「承接語」性質的欄位。
        #
        # 2026-08-18稽核：_generate_image_reveal_reaction 原本的分類2（長者
        # 覺得圖跟記憶不一樣、但還沒具體講出哪裡不同）承接語設計成要長者
        # 回答的問題，曾經是這條規則唯一的例外（見git歷史）——使用者決定
        # 把這個分類併入「有差異」分類，不再追問「哪裡不一樣」，所有分類的
        # 承接語現在都不該是問句，這條規則不再需要例外。
        if scene_text_is_a_question(ack_check_text):
            logger.warning(
                f"[ResponseGuard] 承接語被寫成問句: {ack_check_text!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的承接語「{ack_check_text}」本身被寫成一句問句（用「呢」"
                "「嗎」結尾或直接用問號收尾）。承接語的工作是接住長者剛才的話，"
                "不是提問，真正的提問要留給緊接著的「問題：」欄位，不然長者會"
                "被連問兩次、語氣也會顯得矛盾。這次請把承接語改寫成直述句"
                "（不要用「呢」「嗎」或問號收尾），該問的內容留到「問題：」"
                "欄位再問。"
            )
            # 這條規則查的正是承接語欄位本身，解鎖退回整包重新生成。
            locked_fields = None
            attempt += 1
            continue

        # 承接語跟問題文字幾乎一模一樣（見 scene_text_duplicates_question
        # 上方註解）。跟上面兩條共用同一組 scene_text／reaction_text 判準理由。
        if scene_text_duplicates_question(ack_check_text, question_text):
            logger.warning(
                f"[ResponseGuard] 承接語跟問題文字重複: {ack_check_text!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的輸出把承接語跟問題都寫成同一句「{ack_check_text}」。"
                "這是兩個不同的欄位：承接語負責用直述句呼應長者剛才說的話，"
                "問題負責問一個長者還沒被問過的新方向，兩者內容不能相同。"
                "這次請把這兩個欄位分開寫成不同的內容。"
            )
            # 兩個欄位都牽涉在違規判準裡，不能排除是被鎖定的欄位造成，解鎖。
            locked_fields = None
            attempt += 1
            continue

        # 承接語先把「為什麼」的答案講成既定事實，問題又問一次同一個為
        # 什麼（見 scene_text_preempts_why_question 上方註解）。跟上面
        # scene_text_duplicates_question 同一類「承接語跟問題職責衝突」
        # 的問題，只是這裡兩句文字不相似，衝突在語意層面。
        if scene_text_preempts_why_question(ack_check_text, question_text):
            logger.warning(
                f"[ResponseGuard] 承接語已經斷定原因、問題卻又問一次為什麼: "
                f"承接語={ack_check_text!r} 問題={question_text!r}，"
                f"重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的承接語「{ack_check_text}」已經用「是為了／是因為」"
                f"把原因講成既定事實，但問題「{question_text}」卻又問一次"
                "「為什麼」——長者會困惑：原因不是你剛剛才講的嗎，怎麼又問"
                "一次？這次請不要在承接語裡自己編一個原因當事實講出來，"
                "承接語只呼應長者已經明確說過的內容（例如提到的人事物本身），"
                "原因留給「問題」欄位去問，不要在承接語裡先講答案。"
            )
            # 兩個欄位都牽涉在違規判準裡，不能排除是被鎖定的欄位造成，解鎖。
            locked_fields = None
            attempt += 1
            continue

        # 「判斷依據」欄位編造了長者沒說過的話（見 judgment_evidence_unsupported
        # 上方註解）。只有 _generate_image_reveal_reaction 這類有「判斷依據」
        # 欄位的 generate_fn 會觸發——其餘沒有這個 key，result.get 拿到空字串，
        # 函式本身直接回 False，不受影響。
        evidence_val = result.get("judgment_evidence", "")
        elder_response_val = generate_kwargs.get("elder_response", "")
        if judgment_evidence_unsupported(evidence_val, elder_response_val):
            logger.warning(
                f"[ResponseGuard] 判斷依據引用了長者沒說過的話: {evidence_val!r}"
                f"（長者原話: {elder_response_val!r}），重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次的「判斷依據」欄位寫「{evidence_val}」，但長者這次的原話是"
                f"「{elder_response_val}」，裡面根本沒有這些內容——不能引用長者沒"
                "說過的話當依據。這次請只根據長者這次實際說出的字詞判斷；如果長者"
                "的反應內容很簡短、看不出明確的差異或情緒線索（例如只有語助詞、"
                "簡短回應），判斷依據就老實寫「反應內容簡短，看不出明確線索」，"
                "不要編造長者沒說過的話。"
            )
            # 判斷依據影響後面分類跟承接語的推理，不能排除是被鎖定的欄位造成，
            # 解鎖退回整包重新生成。
            locked_fields = None
            attempt += 1
            continue

        # 分類「1」（肯定/一致）但判斷依據或長者原話裡卻出現「不像」這類
        # 否定相似詞——證據寫對了、分類數字卻選反，見
        # classification_contradicts_negation 上方註解。只有
        # _generate_image_reveal_reaction 這類有「classification」欄位的
        # generate_fn 會觸發，其餘沒有這個key，result.get拿到空字串，
        # 函式本身直接回False，不受影響。
        classification_val = result.get("classification", "")
        if classification_contradicts_negation(classification_val, evidence_val, elder_response_val):
            logger.warning(
                f"[ResponseGuard] 分類={classification_val!r}（肯定/一致）但判斷"
                f"依據或長者原話裡有否定相似詞: 判斷依據={evidence_val!r} "
                f"長者原話={elder_response_val!r}，重新生成 (attempt={attempt})"
            )
            retry_feedback = (
                f"上一次判成分類1（長者覺得圖跟記憶一致），但長者這次的原話"
                f"「{elder_response_val}」裡明確出現「不像」「不一樣」這類否定"
                "相似詞，代表長者其實是在講差異，不是肯定——這次請重新完整"
                "讀一遍長者的原話，如果裡面有明確的否定相似詞，就不能判成"
                "分類1，要改判成分類2（有差異）。"
            )
            # 分類影響後面承接語的推理跟措辭，不能排除是被鎖定的欄位造成，
            # 解鎖退回整包重新生成。
            locked_fields = None
            attempt += 1
            continue

        # classification_lacks_discrepancy_evidence 已 retired，不再掛在這裡
        # ——見該函式上方 2026-08-18 稽核說明（分類2、3合併後，原本只排除
        # 分類3的誤判風險，現在會出現在合併後的分類2身上）。

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
            # 只有 _QUESTION_ONLY_FORMAT_RULES 裡的規則保證只查 question_text
            # 本身（見該常數說明），其餘規則（nin/xian/zanmen等）查的是
            # scene_text_val+question 的 combined，沒辦法排除是被鎖定的欄位
            # 造成違規，一律解鎖。
            if question_only_retry_fn is not None and format_rule in _QUESTION_ONLY_FORMAT_RULES:
                locked_fields = {k: v for k, v in result.items() if k not in ("question", "covered_w")}
            else:
                locked_fields = None
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
                # 禁忌話題查的是所有欄位合併後的 combined，沒辦法排除是被鎖定
                # 的欄位造成違規，解鎖。
                locked_fields = None
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
                locked_fields = None
                attempt += 1
                continue

        return result

    logger.error(f"[ResponseGuard] 重試{max_retry}次仍違規，退回安全保底語句")
    return fallback
