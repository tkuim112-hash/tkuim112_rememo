"""
STEP2/STEP3 承接語（scene_text）生成器：抽取→造句，取代舊版「一次自由
生成一大段 JSON」裡承接語的那半段。問題（question）的生成方式不受影響，
orchestrator.py 繼續呼叫原本的自由生成函式取得問題，只是不採用它順便
生出來的承接語——見 orchestrator.py _generate_open_followup／
_generate_supplement_question 的呼叫方式。

設計：
  1. 先用 LLM 做「抽取」，不是「生成」——從長者這句話裡找一個原字出現過的
     具體關鍵詞/片段（span），程式碼直接用 str.find 切片，模型只決定切哪裡、
     不負責文字內容本身，結構上不可能編造。抽取跟語氣分類（utterance_type／
     is_thin）合併成同一次LLM呼叫的同一份JSON裡（見 _ask_extract_and_
     classify），不是分開兩次呼叫。
  2. 有抽到才叫模型「造句」：參考範例語氣，用這個關鍵詞寫一句自然的承接語，
     依 utterance_type（一般分享／反駁糾正）、is_thin（講完沒有更多可延伸）
     分流成不同範例，避免同一套「邀請繼續說」的語氣套用在不適合的情境。
  3. 事後檢查（沿用 response_guard.py 現成的規則）沒過就用更高溫度重試
     一次；兩次都沒過，退回機械模板代入（保證合格）；完全沒抽到關鍵詞則
     退回通用保底句。

2026-09-14稽核（使用者提案，撈真實session log發現切題延遲很高後追查）：
抽取跟分類原本是兩個獨立函式（_ask_extract／_classify_utterance），用
asyncio.gather同時送出，docstring原本記載「實測比循序快約25%」。這次
查 tku-care-ollama-1 的啟動紀錄才發現：這顆模型每次載入都是 Parallel:1
（因為稍早把 num_ctx 調到24576、KV cache吃掉大部分VRAM，Ollama自動把
並行槽數降到1）——代表「同時」送出的兩個請求，GPU那端還是排隊一個一個
算，asyncio.gather省下的只是HTTP/排程的一點點開銷，不是真的平行運算。
既然反正是排隊，不如直接合併成一次LLM呼叫（一份JSON schema同時要關鍵詞
＋類型＋span＋utterance_type＋is_thin），省下一趟完整的prompt處理＋
生成時間，比「假平行、真排隊」的兩次呼叫快。抽取失敗需要重打時（見
extract_keyword），只重打抽取那一小塊（沿用原本的_ask_extract／
_EXTRACT_SYSTEM_RETRY），不用連分類也一起重打——分類這次已經拿到、跟
抽取抽不抽得到無關，不該被迫陪著重算。

（原型驗證過程與各項防呆的實測依據見 manual_test_ack_template_prototype.py
／manual_test_ack_compose_prototype.py，這裡只保留正式上線需要的邏輯。）
"""
import json
import re

from safety.response_guard import (
    ack_text_too_long, is_generic_acknowledgment,
    scene_text_echoes_elder_response, scene_text_is_a_question,
    check_format_rules, ai_claims_personal_memory_llm,
    ack_claims_recollection, _ECHO_MIN_OVERLAP_LEN,
)
from services.llm import LLMService

_PUNCT_RE = re.compile(r"[，。！？、\s「」『』]")

# ══════════════════════════════════════════════════════════════════════
# 關鍵詞／span 抽取
# ══════════════════════════════════════════════════════════════════════

_EXTRACT_SYSTEM = (
    "你要從長者說的話裡，抽出一個他這句話裡真正提到、具體的詞或短語"
    "（1-6字，人事物、地點、動作都可以），請完全照抄長者原話裡出現過的"
    "字（不要自己發明、換句話說、或用同義詞取代）。如果長者這句話沒有實質"
    "內容（例如沒有回應、只有語助詞、口吃重複、完全答非所問看不出具體"
    "內容），請如實在keyword欄位填「無」，不用勉強硬湊一個詞出來。"
    "請往代名詞指向的那個具體人事物/事情本身找關鍵詞（例如長者說「他們"
    "算有時候會嫌」，重點不是「他們」，是「會嫌」這個具體的事）——「他」"
    "「她」「他們」「你」「我」「這個」「那個」「這兩個」「這些」這類"
    "代名詞本身沒有具體內容，不適合當關鍵詞。請只挑一小段明顯短於長者"
    "原話的關鍵詞，不要整句照抄。"
    "另外要判斷這個詞屬於「noun」（具體的人事物/地點名稱，例如人名、"
    "歌手、料理名、地點）還是「event」（動作/事情/狀態，例如「會嫌」"
    "「最愛」「做料理」「沒辦法選擇」這類描述發生了什麼事的詞），填進"
    "type欄位。"
    "另外找出這句話裡最能代表重點的一段連續文字（不是整句話，是其中"
    "一段最關鍵的部分，大約5-20字，能帶出具體細節，比keyword豐富）——"
    "回傳這段文字的開頭2-4個字（span_start）和結尾2-4個字（span_end），"
    "兩者都請取自原文裡真實連續出現過的字。如果這句話本身就很短、找不出"
    "比keyword更豐富的一段，span_start／span_end都填「無」。"
)

# 第一次抽取常在長者這句話的「主語」剛好是代名詞時整句放棄（例如「他們算
# 有時候會嫌」抽到「他們」被防呆擋掉），這次明講先不管主語、往後面接的
# 具體動作/事情找。
_EXTRACT_SYSTEM_RETRY = (
    "你要從長者說的話裡，抽出這句話描述的具體動作或事情本身（1-6字），"
    "請完全使用長者原話裡出現過的字（不要自己發明、換句話說）。長者這句"
    "話的主語可能是代名詞（他/他們/我們/這個/這兩個），請先不管主語，"
    "去找主語後面接的那個具體動作/事情——例如「他們算有時候會嫌」，具體"
    "的事情是「會嫌」；「這兩個都是我的最愛」，具體的事情是「最愛」或"
    "「沒辦法選擇」。只有這句話完全沒有任何具體動作/事情可抽才填「無」。"
    "這裡抽到的一定是動作/事情本身，type欄位固定填「event」。同樣要找"
    "span_start／span_end，規則同上，找不到就填「無」。"
)

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string"},
        "type": {"type": "string", "enum": ["noun", "event"]},
        "span_start": {"type": "string"},
        "span_end": {"type": "string"},
    },
    "required": ["keyword", "type", "span_start", "span_end"],
}

# utterance_type／is_thin 這兩個判斷只需要看長者這句話本身，跟前面關鍵詞
# 抽不抽得到無關——2026-09-14前是拆成獨立呼叫、用asyncio.gather跟抽取
# 同時送出；後來查 tku-care-ollama-1 的啟動紀錄發現這顆模型是 Parallel:1
# （num_ctx=24576把VRAM吃到只夠開1個並行槽），「同時」送出的兩個請求
# 其實在GPU端還是排隊做，asyncio.gather省不到真正的運算時間，改成跟抽取
# 合併進同一份JSON schema、同一次呼叫（見下方 _EXTRACT_AND_CLASSIFY_
# SCHEMA／_ask_extract_and_classify），省下一趟完整的prompt處理＋生成
# 時間。這裡的prose維持獨立常數，只是後面組合進同一份system prompt，
# 不用複製一份文字。
_CLASSIFY_SYSTEM = (
    "你要判斷長者這句話屬於哪一種類型，填進utterance_type欄位，只能選"
    "一個：\n"
    "「substantive」：長者有給出具體、有實質內容的陳述或分享（不管長不"
    "長，只要是完整、連貫的一句話都算，即使內容很簡短、講完就沒有更多"
    "可以延伸的東西，也算substantive，不要因為短就選別的類型）。\n"
    "「objection」：長者在反駁、糾正、或否定某個說法/假設（例如「不是"
    "XX吧」「才沒有」「沒有啦」這種語氣，重點是在否定/糾正什麼，不是在"
    "分享新內容）。\n"
    "另外判斷這句話講完後還有沒有可以延伸追問的空間，填進is_thin欄位"
    "（true/false）：長者這句話本身已經表達完整、後面不容易再延伸出"
    "更多細節（例如「我也忘記了」「差不多了」這種簡短、講完就結束的話），"
    "才填true，其餘一律false。"
)

# 合併後的schema：第一次呼叫同時要關鍵詞抽取＋語氣分類全部6個欄位。
_EXTRACT_AND_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string"},
        "type": {"type": "string", "enum": ["noun", "event"]},
        "span_start": {"type": "string"},
        "span_end": {"type": "string"},
        "utterance_type": {"type": "string", "enum": ["substantive", "objection"]},
        "is_thin": {"type": "boolean"},
    },
    "required": [
        "keyword", "type", "span_start", "span_end", "utterance_type", "is_thin",
    ],
}

# 合併後的system prompt：抽取規則（_EXTRACT_SYSTEM）+ 分類規則
# （_CLASSIFY_SYSTEM），中間補一句銜接說明兩者是各自獨立的判斷。
_EXTRACT_AND_CLASSIFY_SYSTEM = (
    _EXTRACT_SYSTEM
    + "\n\n除了上面的關鍵詞抽取，你還要另外做兩個獨立的判斷，分別填進"
    "utterance_type／is_thin欄位——這兩個判斷只需要看長者這句話本身，"
    "不受前面抽不抽得到關鍵詞影響：\n\n"
    + _CLASSIFY_SYSTEM
)

_KNOWN_UTTERANCE_TYPES = frozenset({"substantive", "objection"})

# 長者原話裡常見的語助詞/連接詞前綴，不影響語意，代模板前先清掉。
_FILLER_PREFIX_RE = re.compile(r"^(算是|算|就是|就|應該|可能|其實|反正|大概|好像|大約|也是|也)+")

# 純代名詞/指示詞/語助詞——抽到這些代表沒抓到具體內容，一律當「沒抽到」
# 處理（去標點後完全相等才算，不是子字串比對，避免誤傷「他們家」這種
# 代名詞+具體內容的合法情況）。
_PRONOUN_ONLY = frozenset({
    "他", "她", "他們", "她們", "你", "你們", "我", "我們", "大家",
    "這個", "那個", "這兩個", "那兩個", "這些", "那些", "這", "那",
    "這樣", "那樣", "這個時候", "那個時候", "有時候",
    "嗯", "啊", "喔", "呃", "誒", "唉", "哦",
})

# 抽出的詞若佔長者原話正規化後六成以上（或超過長度上限），代表這句話本來
# 就沒有可切出來的具體重點，當「抽不到」處理。
_ECHO_RATIO_THRESHOLD = 0.6
_MAX_KEYWORD_LEN = 8

# STT病態重複（同一字元連續出現10次以上）不是正常語句，抓到就不呼叫LLM
# 抽取，直接當沒有實質內容。
_PATHOLOGICAL_REPEAT_RE = re.compile(r"(.)\1{9,}")


def _is_pathologically_garbled(text: str) -> bool:
    return bool(text) and bool(_PATHOLOGICAL_REPEAT_RE.search(text))


def _validate_keyword(kw: str, elder_response: str) -> str | None:
    """套用防呆規則（去贅字前綴／必須是原話子字串／非代名詞／回聲比例／
    長度上限），回傳清掉贅字前綴後的乾淨關鍵詞；不合格回傳 None。"""
    if not kw or kw in ("無", "没有", "沒有", "無法判斷"):
        return None
    kw = _FILLER_PREFIX_RE.sub("", kw).strip()
    if not kw:
        return None
    norm_elder = _PUNCT_RE.sub("", elder_response)
    norm_kw = _PUNCT_RE.sub("", kw)
    if not norm_kw or norm_kw not in norm_elder:
        return None
    if norm_kw in _PRONOUN_ONLY:
        return None
    if len(norm_kw) / len(norm_elder) > _ECHO_RATIO_THRESHOLD:
        return None
    if len(norm_kw) > _MAX_KEYWORD_LEN:
        return None
    return kw


# span：不要求LLM自己寫出這段文字，只要求標出開頭/結尾幾個字，實際文字
# 由程式碼直接從長者原話切片——結構上不可能編造。
_MIN_SPAN_LEN = 5
_MAX_SPAN_LEN = 22
_SPAN_ECHO_RATIO_THRESHOLD = 0.75
# 這幾個詞幾乎都需要接補語/子句才完整，span 結尾落在這裡代表話說到一半
# 就被切斷，讀起來不通順。
_DANGLING_END_RE = re.compile(
    r"(覺得|認為|知道|發現|看到|聽到|因為|但是|而且|還有|所以|也是)$"
)


def _extract_span(elder_response: str, span_start: str, span_end: str) -> str | None:
    """機械式從長者原話裡切出 span_start 到 span_end（含）之間的連續文字；
    找不到、太短、太長、或幾乎等於整句話，都回傳 None（呼叫端退回用
    keyword，不算失敗）。優先在保留標點的原文裡找，讀起來才會保留原本的
    停頓；找不到才退回去標點後的版本再試一次。"""
    if not span_start or not span_end or span_start in ("無", "沒有"):
        return None
    span_start = span_start.strip("，。！？、， \t「」『』")
    span_end = span_end.strip("，。！？、， \t「」『』")
    if not span_start or not span_end:
        return None

    def _slice(text: str, start: str, end: str) -> str | None:
        i = text.find(start)
        if i == -1:
            return None
        j = text.find(end, i)
        if j == -1:
            return None
        return text[i:j + len(end)]

    span = _slice(elder_response, span_start, span_end)
    if span is None:
        norm_elder = _PUNCT_RE.sub("", elder_response)
        norm_start = _PUNCT_RE.sub("", span_start)
        norm_end = _PUNCT_RE.sub("", span_end)
        if not norm_start or not norm_end:
            return None
        span = _slice(norm_elder, norm_start, norm_end)
        if span is None:
            return None

    norm_span = _PUNCT_RE.sub("", span)
    norm_elder_len = len(_PUNCT_RE.sub("", elder_response))
    if not (_MIN_SPAN_LEN <= len(norm_span) <= _MAX_SPAN_LEN):
        return None
    if norm_elder_len and len(norm_span) / norm_elder_len > _SPAN_ECHO_RATIO_THRESHOLD:
        return None
    if _DANGLING_END_RE.search(norm_span):
        return None
    return span


async def _ask_extract(
    system_prompt: str, elder_response: str, llm: LLMService, temperature: float = 0,
) -> tuple[str, str, str | None]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"長者說：「{elder_response}」"},
    ]
    raw = await llm.chat(messages, temperature=temperature, format=_EXTRACT_SCHEMA)
    try:
        data = json.loads(raw)
        kw = str(data.get("keyword", "")).strip()
        kw_type = str(data.get("type", "")).strip()
        span_start = str(data.get("span_start", "")).strip()
        span_end = str(data.get("span_end", "")).strip()
    except Exception:
        return "", "noun", None
    span = _extract_span(elder_response, span_start, span_end)
    return kw, (kw_type if kw_type in ("noun", "event") else "noun"), span


async def _ask_extract_and_classify(
    elder_response: str, llm: LLMService, temperature: float = 0,
) -> tuple[str, str, str | None, str, bool]:
    """合併後的第一次呼叫：一次JSON同時要關鍵詞抽取＋語氣分類（見上方
    _EXTRACT_AND_CLASSIFY_SCHEMA 說明的稽核理由）。回傳 (keyword原始值,
    type, span, utterance_type, is_thin)；解析失敗時比照原本 _ask_extract
    ／_classify_utterance 個別失敗時的保守預設值。"""
    messages = [
        {"role": "system", "content": _EXTRACT_AND_CLASSIFY_SYSTEM},
        {"role": "user", "content": f"長者說：「{elder_response}」"},
    ]
    raw = await llm.chat(messages, temperature=temperature, format=_EXTRACT_AND_CLASSIFY_SCHEMA)
    try:
        data = json.loads(raw)
        kw = str(data.get("keyword", "")).strip()
        kw_type = str(data.get("type", "")).strip()
        span_start = str(data.get("span_start", "")).strip()
        span_end = str(data.get("span_end", "")).strip()
        utterance_type = str(data.get("utterance_type", "")).strip()
        is_thin = bool(data.get("is_thin", False))
    except Exception:
        return "", "noun", None, "substantive", False
    span = _extract_span(elder_response, span_start, span_end)
    utterance_type = utterance_type if utterance_type in _KNOWN_UTTERANCE_TYPES else "substantive"
    return kw, (kw_type if kw_type in ("noun", "event") else "noun"), span, utterance_type, is_thin


async def extract_keyword(
    elder_response: str, llm: LLMService,
) -> tuple[str | None, str, str | None, str, bool]:
    """回傳 (關鍵詞或None, 詞性noun/event, span或None, utterance_type, is_thin)。
    抽不到（沒有實質內容、模型發明的詞、抽到代名詞、或整句照抄）第一個值
    回傳 None，呼叫端應退回保底句。第一次抽不到時換個角度（找動作/事情
    本身，不是主語）再試一次。

    第一次呼叫（_ask_extract_and_classify）同時拿到抽取＋分類結果——見
    該函式與 _EXTRACT_AND_CLASSIFY_SCHEMA 上方2026-09-14稽核說明：這顆
    本地模型是 Parallel:1，兩個獨立呼叫「同時」送出在GPU端還是排隊算，
    不如合併成一次省下一趟完整往返。第一次抽不到關鍵詞需要重打時，只
    重打抽取那一小塊（_ask_extract＋_EXTRACT_SYSTEM_RETRY），不用連
    分類也一起重打——分類這次已經拿到、跟抽不抽得到關鍵詞無關。最後真的
    沒抽到關鍵詞時，分類結果用不到（呼叫端 build_ack 對「沒有關鍵詞」的
    情況一律退回 _TEMPLATES_EMPTY，不看 utterance_type／is_thin），維持
    跟原本一樣回傳「substantive, False」佔位，不是遺漏。"""
    if not elder_response or not elder_response.strip():
        return None, "noun", None, "substantive", True
    if _is_pathologically_garbled(elder_response):
        return None, "noun", None, "substantive", False

    raw_kw, kw_type, span, utterance_type, is_thin = await _ask_extract_and_classify(
        elder_response, llm,
    )
    kw = _validate_keyword(raw_kw, elder_response)
    if not kw:
        raw_kw, kw_type, span = await _ask_extract(_EXTRACT_SYSTEM_RETRY, elder_response, llm)
        kw = _validate_keyword(raw_kw, elder_response)

    if not kw:
        return None, kw_type, span, "substantive", False

    return kw, kw_type, span, utterance_type, is_thin


# ══════════════════════════════════════════════════════════════════════
# 機械模板：造句兩次都失敗時的安全網
# ══════════════════════════════════════════════════════════════════════

_TEMPLATES_NOUN: dict[str, list[str]] = {
    "happy": [
        "聽到你說到{kw}，我很想多聽你說說。",
        "你提到{kw}，我在聽。",
        "說到{kw}，我們可以再多聊聊這個。",
        "誒，{kw}啊，我想多聽聽。",
    ],
    "excited": [
        "{kw}，我也跟著聽得很專心。",
        "你提到{kw}，我很想多聽你說說。",
        "說到{kw}，這個我想多聊聊。",
        "喔，{kw}，我在聽。",
    ],
    "sad": [
        "{kw}，聽你這麼說，我在旁邊陪著你。",
        "你提到{kw}，我在聽。",
        "{kw}，這段對你來說一定不容易。",
        "誒，{kw}，我陪著你。",
    ],
    "angry": [
        "{kw}，我在仔細聽。",
        "你提到{kw}，我在聽。",
        "{kw}，這件事我聽進去了。",
        "喔，{kw}，我有聽到。",
    ],
    "neutral": [
        "聽到你說到{kw}，我很想多聽你說說。",
        "你提到{kw}，這個我記下來了。",
        "說到{kw}，我們可以再多聊聊。",
        "喔，{kw}啊，這個我想多聽聽。",
    ],
}

_TEMPLATES_EVENT: dict[str, list[str]] = {
    "happy": [
        "{kw}，聽你這麼說，我很想多聽你說說。",
        "聽到你說{kw}，我在專心聽著。",
        "{kw}這件事，我們可以再多聊聊。",
        "誒，{kw}，我想多聽聽。",
    ],
    "excited": [
        "{kw}，我也跟著聽得很專心。",
        "聽到你說{kw}，我很想多聽你說說。",
        "{kw}這件事，這個我想多聊聊。",
        "喔，{kw}，我在聽。",
    ],
    "sad": [
        "{kw}，聽你這麼說，讓我很有感觸。",
        "{kw}這件事，聽起來不容易。",
        "聽到你說{kw}，我在聽。",
        "誒，{kw}，我陪著你。",
    ],
    "angry": [
        "{kw}，我在仔細聽。",
        "{kw}這件事，我聽進去了。",
        "聽到你說{kw}，我在聽。",
        "喔，{kw}，我有聽到。",
    ],
    "neutral": [
        "{kw}，聽你這麼說，我在聽。",
        "{kw}這件事，聽起來蠻特別的。",
        "聽到你說{kw}，我能感覺到那種心情。",
        "喔，{kw}，這個我想多聽聽。",
    ],
}

# 長者在反駁/糾正時（例如「不是料理課吧」），不套「這個我想多聽聽」這種
# 當成新話題分享來回應的模板（答非所問），直接承認被糾正，不追問原因。
_TEMPLATES_OBJECTION_WITH_KW = [
    "喔，原來不是{kw}啊。",
    "這樣啊，不是{kw}，我搞錯了。",
    "喔，不是{kw}，我知道了。",
]
_TEMPLATES_OBJECTION_GENERIC = [
    "喔，原來如此，我搞錯了。",
    "這樣啊，我聽錯了。",
    "喔，我搞錯意思了。",
]

# 長者這句話沒有實質內容或破碎到看不出在說什麼時，誠實地不假裝還有東西
# 可以「多聽你說說」。三句輪替避免每次都聽到一模一樣的話，且都維持「接著
# 聊這個」（不是「換個話題」）的方向，跟下一題通常還在問同一話題其他面向
# 的邏輯一致。
_FALLBACK_ACK = "我們接著聊聊這個吧。"
_TEMPLATES_EMPTY = [
    "我們再多聊聊這個吧。",
    "好，這個我們可以再聊聊。",
    "嗯，我們再多聊一點這個。",
]

# is_thin：長者這句話完整、連貫，但講完就沒有更多東西可以延伸（例如「我也
# 忘記了」），套用預設「邀請繼續說」的模板語意上不通，改用簡短、不預設
# 還有下文的收束句。
_TEMPLATES_THIN = [
    "{kw}，好，我知道了。",
    "嗯，{kw}，這樣啊。",
    "{kw}，好，我們接著聊。",
    "喔，{kw}，了解了。",
]

_KNOWN_EMOTIONS = frozenset({"happy", "excited", "sad", "angry", "neutral"})


def _emotion_bucket(emotion: str | None) -> str:
    """情緒偵測不一定可靠，未知/不在名單裡的情緒一律保守當neutral。"""
    return emotion if emotion in _KNOWN_EMOTIONS else "neutral"


# 「{kw}，...」這種kw後面直接接逗號的模板，2字event型關鍵詞（例如「忘記」）
# 光禿禿放句首不成完整子句，補「了」讓它變成完整子句；情態助動詞開頭
# （會/要/想/能/可以/該/得）已經是「情態+動詞」結構，不比照處理。
_MODAL_PREFIX_RE = re.compile(r"^(會|要|想|能|可以|該|得)")
_SAFE_FOR_ASPECT_MARKER = "{kw}，"


def _needs_completed_aspect_marker(keyword: str, kw_type: str) -> bool:
    return (
        kw_type == "event" and len(keyword) == 2
        and not _MODAL_PREFIX_RE.match(keyword) and not keyword.endswith("了")
    )


def _fill_substantive_template(
    keyword: str, kw_type: str, emotion: str | None, index: int, span: str | None = None,
) -> str:
    """套用在 utterance_type=substantive 的情況：span 有效時優先拿來取代
    keyword，帶出比單一詞更具體的內容，但只能用在「{kw}，...」這種kw後面
    直接接逗號的模板——塞進「{kw}這件事，...」會變成重複nominalize的怪
    句子。"""
    family = _TEMPLATES_EVENT if kw_type == "event" else _TEMPLATES_NOUN
    variants = family[_emotion_bucket(emotion)]

    if span:
        span_safe_variants = [v for v in variants if _SAFE_FOR_ASPECT_MARKER in v]
        if span_safe_variants:
            template = span_safe_variants[index % len(span_safe_variants)]
            return template.format(kw=span)

    template = variants[index % len(variants)]
    use_keyword = keyword
    if _needs_completed_aspect_marker(keyword, kw_type) and _SAFE_FOR_ASPECT_MARKER in template:
        use_keyword = keyword + "了"
    return template.format(kw=use_keyword)


def build_ack(
    keyword: str | None, kw_type: str, emotion: str | None, index: int,
    span: str | None, utterance_type: str, is_thin: bool,
) -> str:
    """先按 utterance_type 分流，不是每句話都硬套 substantive 那條
    「抽內容+套模板」的路——objection 有專門承認被糾正的模板；完全沒抽到
    關鍵詞退回 _TEMPLATES_EMPTY；is_thin 用短、不預設還有下文的版本。"""
    if not keyword:
        return _TEMPLATES_EMPTY[index % len(_TEMPLATES_EMPTY)]
    if utterance_type == "objection":
        return _TEMPLATES_OBJECTION_WITH_KW[index % len(_TEMPLATES_OBJECTION_WITH_KW)].format(kw=keyword)
    if is_thin:
        return _TEMPLATES_THIN[index % len(_TEMPLATES_THIN)].format(kw=keyword)
    return _fill_substantive_template(keyword, kw_type, emotion, index, span)


# ══════════════════════════════════════════════════════════════════════
# 承接語造句
# ══════════════════════════════════════════════════════════════════════

_STYLE_EXAMPLES = [
    "聽到你說到裝飾，我很想多聽你說說。",
    "料理課這件事，聽起來很有意思。",
    "你提到姜蕙，讓我印象很深。",
    "說到工作，讓我覺得很值得多聊聊。",
    "拍手，這個我記下來了，想再多聊聊這個。",
]
_STYLE_EXAMPLES_OBJECTION = [
    "喔，原來不是料理課啊。",
    "這樣啊，不是姜蕙，我搞錯了。",
    "喔，不是工作，我知道了。",
]
_STYLE_EXAMPLES_THIN = [
    "忘記，好，我知道了。",
    "嗯，江輝之，這樣啊。",
    "喔，差不多了，了解了。",
]

_COMPOSE_RULES = (
    "規則：\n"
    "1. 寫成1句直述句作結尾，15字以內（不用「呢」「嗎」或問號收尾）。\n"
    "2. 一定要包含這次給你的關鍵詞本身（原字，不要換成同義詞），可以自然"
    "調整關鍵詞前後的助詞/語序，讓句子讀起來自然通順、不是機械複製貼上。\n"
    "3. 用自己的話簡短回應或轉述，讓長者感覺到你真的有在聽（不要把長者"
    "剛才說的原話整段複誦一次）。\n"
    "4. 「我」只用來表達你聽完這句話當下的情緒反應，例如「我也覺得很"
    "溫暖」（不要延伸成AI自己的親身經歷/家人/童年往事）。\n"
    "只回傳這一句話本身就好，不要其他文字/標點以外的說明。"
)


def _build_compose_system(utterance_type: str, is_thin: bool, taboo_words: list[str] | None = None) -> str:
    """依 utterance_type／is_thin 分流出不同的 system prompt——同一套
    「邀請繼續說」的造句邏輯套在 objection／thin 這兩種情況會答非所問，
    要在模型造句之前就給對的範例跟情境說明，不是靠事後檢查補救。"""
    if utterance_type == "objection":
        context = (
            "長者剛才是在反駁／糾正某個說法（不是在分享新內容），這個關鍵詞"
            "是長者否定掉的那個詞。你要造一句『承認自己會錯意、接受長者的"
            "糾正』的話，把重點放在承認搞錯了——例如長者說「不是料理課吧」，"
            "關鍵詞是「料理課」，正確方向是承認「喔，原來不是料理課」（而"
            "不是把這個關鍵詞當成新話題邀請長者多聊，例如「料理課這個話題"
            "我想多聊聊」這種寫法會答非所問）。\n\n"
        )
        examples = _STYLE_EXAMPLES_OBJECTION
    elif is_thin:
        context = (
            "長者這句話已經表達完整，講完就沒有更多可以延伸的細節了（不是"
            "破碎或沒有內容，只是這句話本身很短、講完就結束）。你要造一句"
            "簡短的收束式回應，用『好，我知道了』這類語氣收尾（不要用「多"
            "說說」「多聊聊」「多分享」這類暗示還有更多東西可以講的話——"
            "長者已經講完了，這樣問會顯得沒聽懂）。\n\n"
        )
        examples = _STYLE_EXAMPLES_THIN
    else:
        context = ""
        examples = _STYLE_EXAMPLES
    taboo_note = (
        f"請完全避開以下禁忌話題，即使長者剛才的話有關聯到也要繞開："
        f"{'、'.join(taboo_words)}。\n\n" if taboo_words else ""
    )
    return (
        "你是懷舊治療AI，長者剛才說了一句話，你已經抓到裡面一個具體的關鍵詞。"
        "現在要用這個關鍵詞造一句話，回應長者剛才說的內容。\n\n"
        + taboo_note
        + context
        + "參考下面幾句話的語氣、長度、句型（不要照抄句子結構，只是參考感覺）：\n"
        + "\n".join(f"- {ex}" for ex in examples) + "\n\n"
        + _COMPOSE_RULES
    )


# 本地弱模型偶爾不會照做修正指示，反而把指示本身或對它的反應講出來
# （例如把違規原因當成要複誦的內容用括號包起來、或接在正常句子後面自我
# 辯解）。跟 orchestrator.py _strip_leaked_brackets 同一種取捨：結構化
# 清洗，不嘗試理解模型多寫的那段文字在講什麼——清掉洩漏的括號（含全形/
# 半形方括號），只留第一個句子終止符之前的內容。
_LEAK_BRACKET_RE = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")
_UNMATCHED_LEAK_BRACKET_RE = re.compile(r"[（(【\[][^）)】\]]*$")
_SENTENCE_END_RE = re.compile(r"[。！？]")


def _strip_leaked_meta(text: str) -> str:
    text = _LEAK_BRACKET_RE.sub("", text).strip()
    text = _UNMATCHED_LEAK_BRACKET_RE.sub("", text).strip()
    match = _SENTENCE_END_RE.search(text)
    if match:
        return text[:match.end()].strip()
    return text


# scene_text_echoes_elder_response 有12字最小重疊門檻，對長者原話本身
# 少於12字的極短句等於整個跳過不查——但短句時複誦風險其實更高（造句規則
# 要求一定要包含關鍵詞原字，長者原話本身就短時，關鍵詞很容易等於整句話，
# 模型最省力的合法解法就是整句照抄）。這裡補一條不受12字門檻限制、只在
# 長者原話夠短時才啟用的檢查。
def _echoes_short_elder_response(text: str, elder_response: str) -> bool:
    norm_text = _PUNCT_RE.sub("", text)
    norm_elder = _PUNCT_RE.sub("", elder_response)
    if not norm_elder or len(norm_elder) >= _ECHO_MIN_OVERLAP_LEN:
        return False
    return norm_elder in norm_text


_INVITE_MORE_PHRASES = ("多聽你說", "多聊聊", "多說說", "多分享", "多了解", "多聊一點")


async def _validate_composed(
    text: str, elder_response: str, keyword: str, llm: LLMService,
    is_thin: bool = False, taboo_words: list[str] | None = None,
) -> list[str]:
    """回傳所有踩到的違規原因（空清單代表通過）。check_format_rules／
    ai_claims_personal_memory_llm 是造句這個做法能不能用的關鍵：純字面
    規則（超字/空泛/複誦/問句）查不到「冒用第一人稱經歷」或「用第三人稱
    長者」這兩種造句才會重新打開的風險。

    便宜的規則式檢查（不需要額外LLM呼叫）先跑，只要已經有一條違規、確定
    這次要重試，就直接回傳、不再多花一次LLM呼叫查冒用經歷——compose_
    sentence 的重試不使用 retry_feedback（見該函式說明，實測帶了違規原因
    反而更差，改成純調溫度重骰），不需要蒐集「這次到底踩了幾條」的完整
    資訊，只要知道「有沒有違規」即可，這樣可以省下已經確定要丟棄的候選句
    上那次多餘的LLM呼叫。"""
    if not text:
        return ["空字串"]
    violations: list[str] = []
    norm_text = _PUNCT_RE.sub("", text)
    norm_kw = _PUNCT_RE.sub("", keyword)
    if norm_kw not in norm_text:
        violations.append("沒有包含關鍵詞本身")
    if ack_text_too_long(text) is not None:
        violations.append("超過48字")
    if is_generic_acknowledgment(text):
        violations.append("空泛套語")
    if scene_text_echoes_elder_response(text, elder_response):
        violations.append("整段複誦長者原話")
    elif _echoes_short_elder_response(text, elder_response):
        violations.append("整句照抄長者原話湊出關鍵詞")
    if scene_text_is_a_question(text):
        violations.append("被寫成問句")
    if is_thin and any(p in text for p in _INVITE_MORE_PHRASES):
        violations.append("is_thin卻邀請繼續說")
    if taboo_words and any(w in text for w in taboo_words):
        violations.append("提到禁忌話題")
    format_violations = check_format_rules("", text)
    if format_violations:
        violations.append(f"格式/用詞規則違規: {[c for c, _ in format_violations]}")
    # 2026-09-14稽核（真實session實測抓到）：ai_claims_personal_memory_llm
    # 對「(讓)我...想起」這個措辭放行過2次真實案例（見該函式上方
    # ack_claims_recollection 說明），改用免費的規則比對先擋，不用等到
    # 下面那次LLM呼叫、也不會被同一顆模型的偽陰性放過。
    if ack_claims_recollection(text):
        violations.append("AI自稱想起了什麼(疑似冒用經歷)")
    if violations:
        return violations
    if await ai_claims_personal_memory_llm(text, elder_response, llm):
        violations.append("疑似AI冒用第一人稱經歷")
    return violations


async def compose_sentence(
    keyword: str, kw_type: str, emotion: str | None, elder_response: str, llm: LLMService,
    span: str | None = None, utterance_type: str = "substantive", is_thin: bool = False,
    index: int = 0, taboo_words: list[str] | None = None, temperature: float | None = None,
) -> str:
    """回傳最終使用的承接語。造句兩次都沒過（跳脫溫度重骰，不帶違規原因
    重試——實測本地弱模型對這個窄任務不吃文字回饋，見模組頂端說明）就退回
    機械模板代入。"""
    compose_system = _build_compose_system(utterance_type, is_thin, taboo_words)
    for attempt in range(2):
        attempt_temp = temperature if temperature is not None else (0 if attempt == 0 else 0.7)
        messages = [
            {"role": "system", "content": compose_system},
            {"role": "user", "content": (
                f"長者剛才說：「{elder_response}」\n關鍵詞：{keyword}\n"
                f"請用「{keyword}」造一句話。"
            )},
        ]
        raw = await llm.chat(messages, temperature=attempt_temp)
        composed = _strip_leaked_meta(raw.strip().strip("「」\"'"))
        violations = await _validate_composed(
            composed, elder_response, keyword, llm, is_thin=is_thin, taboo_words=taboo_words,
        )
        if not violations:
            return composed
    return build_ack(keyword, kw_type, emotion, index, span, utterance_type, is_thin)


