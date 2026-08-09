"""
療程編排器(Orchestrator)。

實作懷舊療法完整狀態機：三回合、5W1H追蹤、開放訪談與補問路徑。

=== 對話流程 ===

1. 前端呼叫 start_round(round_number=1) 開始第一回合
   → 生圖 + STEP1 開場問題 + 初始 state

2. 每次長者回答後，前端呼叫 process_response(elder_response, state)
   → 狀態機判斷下一步，回傳 action + 下一個問題 + 更新後的 state

   action 說明：
     "open_followup"    → 話題豐富，繼續順著長者深入（有 scene_text + question）
     "ask_supplement_w" → 話題結束，切入未問的W維度（有 scene_text + question）
     "end_round"        → 本回合W全部覆蓋或跳過，前端用 next_round 呼叫 start_round
     "end_session"      → 第三回合完成，療程結束

=== 回合結束條件（對齊 問題設計規則.pdf STEP4）===
  - 5W1H 全部自然涵蓋
  - 所有還沒涵蓋的 W 都試過但沒得到答覆
  - 對話已無法繼續延伸

=== LLM Prompt 對齊 ===
問題生成格式對齊 dpo/collect_data.py：
  - STEP1 問題 → build_inference_prompt（Track A）
  - 自由追問   → build_track_c_inference_prompt（Track C，對應 PDF STEP2）
  - STEP3 補問 → build_inference_prompt（Track A）

===  禁忌話題防護整合 ===
生成完成後，透過 app/safety/taboo_checker.py 的 guarded_generate() 包裝，
檢查 AI 輸出是否觸及 Patient.taboo_words 相關話題（字面 + 語意兩層），
違規時重新生成或退回安全保底語句。這是最後一道事後攔截；每個生成函式
的 prompt 本身也都會先把 taboo_words 帶給 LLM，讓模型從生成當下就
主動避開，而不是完全依賴這道事後防護。
"""
import json
import random
import re
from pathlib import Path
from services.llm import LLMService
from services.image import StabilityImageService
from services.rag_client import RealRAGClient
from services.user_profile_db import DBUserProfileClient
from privacy.deidentifier import Deidentifier
from safety.response_guard import guarded_generate
from safety.element_filter import filter_scene_elements

# 5W1H 涵蓋清單（Why 條件式使用、優先序最低）。補問時不是永遠照這個順序
# 掃第一個沒涵蓋的——5W是動態引導技術，不是必須照1到5走完的線性流程，
# 實際挑選見 _next_step_or_end（非Why維度隨機挑選，Why 仍最後才輪到）。
_W_ORDER = ["Where", "Who", "What", "When", "How", "Why"]

_W_DESC = {
    "Where": "地點（在哪裡、哪個地方、場所）",
    "Who":   "人物（誰、哪個人、關係）",
    "What":  "事物（什麼事、什麼東西、發生什麼事）",
    "When":  "時間（什麼時候、季節、人生階段）",
    "How":   "方式（怎麼做、如何、過程、感受）",
    "Why":   "原因（為什麼、動機）",
}

_W_HINT = {
    "Where": "用 Where 角度問（哪裡、哪個地方、場所）",
    "Who":   "用 Who 角度問（誰、哪個人、關係）",
    "What":  "用 What 角度問（什麼事、什麼東西、發生什麼事）",
    "When":  "用 When 角度問（什麼時候、季節、人生階段）",
    "How":   "用 How 角度問（怎麼做、如何、過程、感受）",
    "Why":   "用 Why 角度問（為什麼、原因、動機）——僅在長者狀態良好時使用",
}

_STEP_TASKS = {
    "STEP1": "生成第一個【開場問題】，引導長者進入回憶（優先問 Where 或 What）",
    "STEP2": "根據長者剛才說的話，順著內容自然追問，不限制哪個W，完全跟著長者走",
    "STEP3": "從還沒問到的方向裡挑一個順著長者剛才的話自然深入問下去，不是核對清單（Why 僅在長者狀態良好時詢問）",
}

_STEP_TYPE_LABEL = {
    "STEP1": "STEP1開場",
    "STEP2": "STEP2自由追問",
    "STEP3": "STEP3補問",
}

# Kinect 即時偵測情緒（app/routers/sensor.py 的 emotion_raw）→ 給 LLM 的語氣指引
_EMOTION_GUIDANCE = {
    "sad":     "長者目前情緒低落，請優先給予溫暖同理與正向肯定，語氣放柔，暫緩深入提問，可引導至輕鬆或正向的話題。",
    "angry":   "長者目前情緒焦躁不安，請先安撫情緒、語氣放緩，避免追問敏感或原因類問題。",
    "excited": "長者目前情緒較亢奮，維持溫暖但避免過度刺激。",
    "happy":   "長者情緒穩定，正常延續對話即可。",
    "neutral": "長者目前情緒不明確或平穩，語氣維持溫和平穩，避免貿然追問需要較多情緒能量的問題（如原因、動機類），先觀察長者反應再決定是否深入。",
}


def _emotion_guidance(emotion: str) -> str:
    # 情緒偵測不見得可靠（例如中風、面癱等生理因素會讓表情辨識失準），
    # 未知情緒保守地當作「不明確」處理，不要預設為 happy 就貿然深入提問。
    return _EMOTION_GUIDANCE.get(emotion, _EMOTION_GUIDANCE["neutral"])


# 單回合題數上限、補問上限：避免為了湊滿 5W1H 連環追問到底。
# 話題自然結束時，最多補問 _MAX_SUPPLEMENT_PER_ROUND 個未涵蓋的W就結束回合，
# 不強求六個W維度都要問過一輪。
_MAX_QUESTIONS_PER_ROUND = 5
_MAX_SUPPLEMENT_PER_ROUND = 2


# 正式環境用的是本地 Ollama 模型（rememo-llama3，見 app/config.py），不是頂尖級
# 模型，指令遵循度較弱，偶爾會把 prompt 裡「問題：（格式說明）」這種待填格式
# 範本原封不動照抄回來，當成自己的答案（尤其 prompt 越長、規則越密，這種「範本
# 回聲」越容易發生）。與其每次針對某一種洩漏樣式加一條 regex 打地鼠，這裡改成
# 通用策略：任何括號內容（全形/半形）一律清掉——真人講給長者聽的問題/場景文字/
# 承接語本來就不該有括號註解，清掉不會誤傷正常輸出。
_LEAK_BRACKET_RE = re.compile(r"[（(][^）)]*[）)]")

# 清洗後若整段變空（代表 LLM 那一行輸出「整句」都是洩漏出來的格式說明，不是真的
# 在回答），退回這句通用、任何情境都安全的開放式問題，而不是把空字串送給長者。
# 開放式、非是非題，適用任何上下文，符合 question_5w1h.txt 的規則。
_FALLBACK_QUESTION = "還有什麼想說的呢？"

# 「場景文字／承接語」（長者聽到的引導語，先鋪陳再接問題）曾經只在 question 欄位
# 清洗後為空時才有保底，scene_text/承接語/收尾語本身完全沒有保底——本地模型偶爾
# 只吐得出「問題：」那一行、漏掉「場景文字：」或「承接語：」整行時，長者會直接聽到
# 一句沒頭沒尾的問題，沒有任何引導鋪陳（曾實際發生：長者端「引導語不見了」）。
# 這裡補上通用、任何情境都安全的保底鋪陳語，跟 _FALLBACK_QUESTION 同一套邏輯。
_FALLBACK_SCENE_TEXT = "我們接著聊聊這個吧。"
_FALLBACK_CLOSING_TEXT = "謝謝你今天的分享，辛苦了。"

# Unity 端問題撥放完15秒沒按麥克風時送出的合成 marker（GameController.cs
# AutoSubmitNoResponse），不是長者真的說的話，不該拿去問 LLM 有沒有情緒訊號。
_NO_RESPONSE_MARKER = "（長者未回應）"


def _strip_leaked_brackets(text: str) -> str:
    return _LEAK_BRACKET_RE.sub("", text).strip()


def _element_fallback(scene_elements: list[str], with_covered_w: bool = True) -> dict:
    """
    guarded_generate 重試多次仍違規時的最終保底值。之前保底句是完全通用、跟
    這次情境無關的固定字串（「現在心裡在想些什麼呢？」）——2026-08 用真實
    pipeline 實測發現：加了 too_long 等格式規則檢查後，落到這個保底句的比例
    不低（單場測試將近一半），代表有不小比例的回合長者聽到的問題其實跟眼前
    這次的畫面、記憶完全無關。這裡改成至少帶著這次真正的畫面元素組出保底句，
    純字串組合、保證合規，不用再多呼叫一次LLM，不影響延遲。
    """
    elements_str = _natural_join(scene_elements or [])
    first = scene_elements[0] if scene_elements else None
    fallback = {
        "scene_text": (
            f"眼前的畫面裡有{elements_str}，我們換個方向聊聊吧。"
            if elements_str else _FALLBACK_SCENE_TEXT
        ),
        "question": f"{first}，讓你想到什麼？" if first else _FALLBACK_QUESTION,
    }
    if with_covered_w:
        fallback["covered_w"] = []
    return fallback


def _natural_join(items: list[str]) -> str:
    """把元素清單接成一句話唸起來自然的中文列舉（最後一項用「和」，不是逗號堆疊到底）。"""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return "、".join(items[:-1]) + "和" + items[-1]


def _retry_feedback_section(retry_feedback: str) -> str:
    """
    guarded_generate 偵測到違規、重新生成時會帶入 retry_feedback（見
    safety/taboo_checker.py），內容是「上一次輸出踩了哪條規則」的具體說明。
    這裡把它插進 user_content，讓模型看到「哪裡錯了、要怎麼修」，而不是
    對本地弱模型的系統性偏誤盲目重新取樣、常常還是踩同一個雷。
    """
    if not retry_feedback:
        return ""
    return f"\n【上一次輸出有問題，這次務必修正】\n{retry_feedback}\n"


_STYLE_ONLY_FRAGMENTS_RE = re.compile(
    r"watercolor painting style|nostalgic warm tones|warm high-contrast palette|"
    r"warm tones|high-contrast palette|"
    r"avoid adjacent blue-green-purple tones|no text",
    re.IGNORECASE,
)


def _strip_style_descriptors(image_prompt: str) -> str:
    """
    image_prompt 是 _plan_image 送去 Stability AI 的英文生圖描述，開頭固定是
    「watercolor painting style」，常常還帶「nostalgic warm tones」「no text」
    這類畫風/色調指令——這些是給 Stability AI 的生圖技術參數，不是場景內容。

    2026-08：原本讓問題生成的模型自己讀到這整段英文、自行忽略畫風用詞（見
    _composition_section 的指示），實測不可靠——模型曾把「watercolor painting
    style, 1970s Taiwan coal mine...」直接翻譯成「一幅水彩畫描繪了1970年代
    台灣煤礦場景」，用後設視角把畫面講成「這是一幅畫」，跟長者拉開距離。改成
    在傳給問題生成步驟之前，先用逗號分段濾掉這些已知的畫風/色調片語，只留下
    真正描述場景內容的部分，不再指望模型自己守住「請忽略」這句提醒——這是
    從源頭消除誘因，比事後靠一句提示語更可靠。
    """
    if not image_prompt:
        return image_prompt
    parts = [p.strip() for p in image_prompt.split(",")]
    kept = [p for p in parts if p and not _STYLE_ONLY_FRAGMENTS_RE.search(p)]
    return ", ".join(kept)


def _composition_section(scene_composition: str) -> str:
    """
    scene_composition 這裡放的是 _plan_image 送去 Stability AI 的 image_prompt
    原文（英文，已經用 _strip_style_descriptors 濾掉畫風/色調片語），不是另外
    請 LLM 生成一份中文描述——2026-08 原本讓 LLM 在規劃圖片的同一次回應裡多
    生成一個中文 scene_composition 欄位，實測發現這個欄位常常跟 elements 對
    不上（同一次回應裡兩個各自生成的欄位，本地弱模型顧不好兩邊一致），還得
    額外加一致性檢查、重試，仍不保證對齊。image_prompt 才是生圖當下唯一真正
    決定畫面長相的描述，直接原文轉傳給問題生成步驟，不會有「兩份描述互相
    打架」的問題，也不用再多一次 LLM 生成。若不提供，模型會把元素清單當成
    互不相干的詞袋，自己亂兜空間關係（例如「坐在木桌前的腳踏車上」），生出
    不合理的畫面。
    """
    if not scene_composition:
        return ""
    return (
        f"\n【這些元素在畫面裡實際的擺放/動作關係（生圖當下的原始描述，英文）】\n"
        f"{scene_composition}\n"
        f"（這段英文是生圖時實際使用的描述，如果場景文字要提到這些元素之間的空間"
        f"或動作關係，要以這段內容為準，不要自己另外想像一個不同的組合方式）\n"
    )


def _load_prompt(filename: str) -> str:
    """
    從 app/prompts/ 讀取 prompt 模板，找不到就回傳空字串。

    question_5w1h.txt（_generate_question／_generate_open_followup／
    _generate_supplement_question 共用）的【承接語／場景文字規則】設計目的，
    是讓長者透過 AI 生成的圖回想過去，具體運作方式是一個三段式漏斗，之後
    新增或調整那組規則時，都用這個原則判斷合不合理（2026-08 從 prompt 本文
    搬到這裡——這段是寫給未來維護 prompt 的人看的設計理念，不是要模型照做的
    指令，留在 prompt 裡只會佔用模型的注意力額度，不影響它的實際輸出）：
      1. 圖片本身負責「schema活化」——生成圖片時選的是同年代、同職業背景的人
         普遍會有印象的通俗元素，不是長者的真實地點，作用是喚起長者腦中對應
         那個年代／職業／場景的一整套熟悉印象，不是要長者辨認「這張照片」本身
      2. 場景文字負責「雙重編碼鋪墊＋泛指邀請」——具體描寫畫面元素，讓長者
         同時看到、聽到同一組具體物件，加深 schema 被活化的強度（dual-coding：
         視覺＋語音兩個管道疊加，記憶痕跡比單一管道更容易被觸發），同時用
         泛指語氣暗示「這是一類情境」而不是「這就是你的過去」，避免長者因為
         畫面細節對不上而卡住
      3. 問題負責「回想動作本身」——問的不是畫面裡有什麼，而是長者自己那個
         年代／職業裡「你的版本是怎樣」，長者要主動把被畫面喚起的 schema
         填進自己真實的細節、事件、情感，這一步才是懷舊治療真正發生的地方
    場景文字自己不需要、也不應該試圖直接達成「回想」的效果——它的成敗判準是
    「有沒有做好活化與鋪墊，讓緊接著的問題更容易被回答」，不是這句話本身寫得
    夠不夠感人、夠不夠有畫面感。
    """
    path = Path(__file__).parent / "prompts" / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


class TherapyOrchestrator:
    """指揮所有 service，實作懷舊療法完整狀態機。"""

    def __init__(
        self,
        llm: LLMService,
        image: StabilityImageService,
        rag: RealRAGClient,
        user_profile: DBUserProfileClient,
        deidentifier: Deidentifier,
    ):
        self.llm = llm
        self.image = image
        self.rag = rag
        self.user_profile = user_profile
        self.deidentifier = deidentifier

    # ══════════════════════════════════════════════════════════════
    # 公開 API
    # ══════════════════════════════════════════════════════════════

    async def start_round(
        self,
        user_id: str,
        session_id: str,
        round_number: int = 1,
        topic_override: str | None = None,
    ) -> dict:
        """
        開始回合 n（n=1,2,3）：生成場景圖 + STEP1 開場問題。

        Args:
            topic_override: 治療師啟動療程時手動輸入的今日主題，蓋過
                Patient.scene_weights 推導出的預設主題。同一場療程的 round 2/3
                應沿用同一個值，由呼叫端（app/routers/session.py）從 Redis
                session meta 讀出後重新傳入。

        Returns dict 含：
          user_name, today_topic, scene_text, scene_elements,
          image_path, question, memories_used,
          state  ← 傳給下一輪 process_response 用
        """
        # 以下各步驟的計時 print 是效能除錯用（RAG 先撈記憶、餵進 _plan_image，
        # 移除 RAG-Fusion 多查詢+RRF 後總耗時 97s→58s），正式上線前會拿掉。
        import time as _time
        _t0 = _time.time()
        print(f"[Orchestrator] ── 回合 {round_number} 開始 ──")

        user = await self.user_profile.get_user(user_id)
        if not user:
            raise ValueError(f"找不到使用者: {user_id}")
        if topic_override:
            user = {**user, "today_topic": topic_override}
        print(f"  → {user['name']}，主題: {user['today_topic']}")

        # ── 步驟 1：RAG 最先撈（跟生圖無關，可以最早發出）──
        _t1 = _time.time()
        # 撈 limit=3 筆候選（同一場療程3回合的查詢字串都一樣，撈回來的候選
        # 名單大致相同），但每次只會挑其中一筆單獨餵給 _plan_image——2026-08
        # 實測發現多筆記憶疊加組成 memory_section 時，就算每筆單獨都在安全
        # 長度內，疊加起來還是會讓模型完全失焦、退化成「公園野餐」這種通用
        # 內容，所以送進 _plan_image 的永遠只有一筆。用 round_number 輪流挑
        # 不同筆（見下方 candidate_memories 選取邏輯），三回合各自扣住不同的
        # 真實記憶，比每回合都固定挑最高分那一筆更能避免三張圖內容太相似。
        # retrieve_memories 現在失敗時會丟例外（見 rag_client.py 說明），這裡
        # 只在「真的失敗」（例如 Ollama/RAG服務還沒起來）時才等3秒重試一次；
        # 「這位長者本來就還沒有任何記憶」會正常回傳空list、不會進到這個
        # except，不會白白多等3秒——這兩種情況之前長得一模一樣，每個全新
        # 病患開場都要多等3秒，2026-08 修掉這個問題。
        try:
            candidate_memories = await self.rag.retrieve_memories(
                user_id=user_id,
                query=f"{user['today_topic']} {user['main_occupation']}",
                limit=3,
            )
        except Exception as e:
            import asyncio as _asyncio
            print(f"  → [RAG] 第一次失敗（{e}），等 3 秒後 retry...")
            await _asyncio.sleep(3)
            try:
                candidate_memories = await self.rag.retrieve_memories(
                    user_id=user_id,
                    query=f"{user['today_topic']} {user['main_occupation']}",
                    limit=3,
                )
            except Exception as e2:
                print(f"  → [RAG] 重試仍失敗（{e2}），這回合當作沒有記憶處理")
                candidate_memories = []
        print(f"  → [計時] RAG 撈回憶: {_time.time()-_t1:.1f}s")

        memories = (
            [candidate_memories[(round_number - 1) % len(candidate_memories)]]
            if candidate_memories else []
        )

        # ── 步驟 2：把 memories 餵進 _plan_image，讓 LLM 從回憶挑元素 ──
        _t2 = _time.time()
        image_plan = await self._plan_image(user, memories=memories)
        print(f"  → [計時] LLM 規劃圖片: {_time.time()-_t2:.1f}s")
        print(f"  → 圖片元素: {image_plan['elements']}")
        # 濾掉畫風/色調片語後才傳給問題生成步驟，Stability AI 那邊還是用完整的
        # image_plan["image_prompt"]（見下面 safe_prompt），兩者用途不同不能共用。
        clean_composition = _strip_style_descriptors(image_plan["image_prompt"])

        safe_prompt = self.deidentifier.desensitize_text(
            image_plan["image_prompt"], taboos=user["taboos"]
        )

        _t3 = _time.time()
        try:
            image_path = await self.image.generate(
                prompt=safe_prompt,
                session_id=session_id,
                round_number=round_number,
            )
        except Exception as e:
            print(f"  → 圖片生成失敗（不影響對話主流程，長者端這回合沒有配圖）: {e}")
            image_path = ""
        print(f"  → [計時] Stability AI 生圖: {_time.time()-_t3:.1f}s")
        print(f"  → 圖片: {image_path}")

        _t4 = _time.time()
        q = await guarded_generate(
            self._generate_question,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            # guarded_generate 現在同時檢查是非題／AI示意圖當真地點／場景文字用你／
            # 照抄範例四種違規，本地弱模型一次踩中其中一種的機率不低，預設
            # max_retry=1（共2次嘗試）常常兩次都失敗、直接退回保底句，導致長者
            # 常常聽到千篇一律的「現在心裡在想些什麼呢」而不是真正的問題。調高到
            # 3 次重試（共4次嘗試）換取更高機率拿到真正生成的內容，代價是失敗時
            # 會多幾次 LLM 往返、增加延遲——這裡判斷對長者體感的影響是回答內容
            # 品質優先於多等一兩秒。
            max_retry=3,
            # 這裡的呼叫端會讀 q["covered_w"]（見下方 state 組裝），guarded_generate
            # 預設的保底字典只有 scene_text/question，沒有 covered_w，重試仍失敗
            # 退回保底時會少這個 key、導致 KeyError('covered_w') 讓開場直接炸掉
            # （2026-07 曾實際發生：療程開場失敗: 'covered_w'）。所以這裡要自帶
            # 對齊的保底字典，不能用預設值。用 _element_fallback 帶著這次真正的
            # 畫面元素組保底句，而不是完全通用、跟這次情境無關的固定字串——
            # 2026-08 實測發現加了 too_long 等格式檢查後，落到保底句的比例
            # 不低，通用保底句等於讓長者聽到跟眼前畫面、記憶完全無關的問題。
            fallback=_element_fallback(image_plan["elements"]),
            step="STEP1",
            user=user,
            scene_elements=image_plan["elements"],
            scene_composition=clean_composition,
            covered_w=[],
        )
        print(f"  → [計時] LLM 生問題: {_time.time()-_t4:.1f}s")
        print(f"  → STEP1 問題: {q['question']}（W: {q['covered_w']}）")
        print(f"  → [計時] orchestrator 總計: {_time.time()-_t0:.1f}s")

        state = {
            "user_id": user_id,
            "session_id": session_id,
            "round": round_number,
            "scene_elements": image_plan["elements"],
            "scene_composition": clean_composition,
            "covered_w": q["covered_w"],
            "skipped_w": [],
            "last_question_type": "step1",
            "last_w_asked": "",
            "question_count": 1,      # STEP1 開場問題算本回合第 1 題
            "supplement_count": 0,
        }

        return {
            "user_name": user["name"],
            "today_topic": user["today_topic"],
            "scene_text": q["scene_text"],
            "scene_elements": image_plan["elements"],
            "image_path": image_path,
            "question": q["question"],
            "memories_used": memories,
            "state": state,
        }

    async def start_session_opening(self, user_id: str, session_id: str) -> dict:
        """backward-compat：直接呼叫 start_round(1)。"""
        return await self.start_round(user_id, session_id, round_number=1)

    async def process_response(
        self,
        elder_response: str,
        state: dict,
        emotion: str = "",
    ) -> dict:
        """
        狀態機核心：根據長者回應決定下一步。

        Args:
            elder_response: 長者說的話（STT 轉譯結果）
            state: 上一輪回傳的 state dict
            emotion: Kinect 即時偵測的情緒（happy/excited/angry/sad，見 app/routers/sensor.py）

        Returns dict 含：
          action     : "open_followup" | "ask_supplement_w" | "end_round" | "end_session"
          scene_text : 承接語或場景文字（end_* 時為空字串）
          question   : 下一個問題（end_* 時為空字串）
          state      : 更新後的狀態（end_* 時為 None）
          next_round : 下一回合編號（僅 end_round 時有值）
        """
        user_id = state["user_id"]
        user = await self.user_profile.get_user(user_id)
        if not user:
            raise ValueError(f"找不到使用者: {user_id}")

        covered_w        = list(state["covered_w"])
        skipped_w        = list(state["skipped_w"])
        last_type        = state["last_question_type"]
        last_w           = state.get("last_w_asked", "")
        scene_els        = state["scene_elements"]
        scene_comp       = state.get("scene_composition", "")
        question_count   = state.get("question_count", 1)
        supplement_count = state.get("supplement_count", 0)

        print(f"[Orchestrator] process_response | round={state['round']} "
              f"last={last_type} covered={covered_w} skipped={skipped_w} "
              f"questions={question_count} supplements={supplement_count}")

        # ── 情緒觸發偵測（Track B，必須跑在 _is_quick_end 之前）─────
        # _is_quick_end 用「不記得／不知道／忘了」關鍵字判斷放棄話題，但這類字面
        # 也會出現在自責/沮喪的情緒表達裡（例如「我不記得了，都忘了，腦子越來越
        # 差了……」），如果先跑 quick_end，這類句子會被誤判成單純放棄話題，
        # 跳過情緒支持直接進下一題——正好是 Track B 的 ignore_emotion／rush_topic
        # 規則要懲罰的行為，所以這個判斷必須放在最前面。
        is_emotional_trigger = (
            False if elder_response.strip() == _NO_RESPONSE_MARKER
            else await self._detect_emotional_trigger(elder_response)
        )
        print(f"  → 情緒觸發偵測: {is_emotional_trigger}")
        if is_emotional_trigger:
            await self.rag.save_memory(
                user_id=user_id,
                session_id=state["session_id"],
                text=elder_response,
                emotion=emotion,
            )
            result = await guarded_generate(
                self._generate_emotional_response,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=3,
                text_keys=("emotional_text", "question"),
                fallback={
                    "emotional_text": "謝謝你願意說這些，我在這裡陪著你。",
                    "question": _FALLBACK_QUESTION,
                },
                user=user, elder_response=elder_response,
            )
            # 情緒支持不受 _MAX_QUESTIONS_PER_ROUND 限制：即使本回合題數已達上限，
            # 偵測到情緒觸發仍優先回應，不直接跳去收尾——情緒支持優先於節奏控制。
            # question_count 仍要 +1，下一輪正常判斷時題數已達上限就會照常收尾。
            new_state = {
                **state,
                "covered_w": covered_w,
                "skipped_w": skipped_w,
                "last_question_type": "emotional_support",
                "last_w_asked": "",
                "question_count": question_count + 1,
                "supplement_count": supplement_count,
            }
            return {
                "action": "emotional_support",
                "scene_text": result["emotional_text"],
                "question": result["question"],
                "state": new_state,
            }

        # ── 快速結束判斷（不叫 LLM）───────────────────────────────
        quick_end = self._is_quick_end(elder_response)
        print(f"  → 快速結束: {quick_end}")

        # ── 後台資料更新（放棄性/過短回答不入庫，避免汙染向量檢索）──
        if not quick_end:
            await self.rag.save_memory(
                user_id=user_id,
                session_id=state["session_id"],
                text=elder_response,
                emotion=emotion,
            )

        # ── 補問路徑：先確認上一個 W 是否被回答 ─────────────────
        if last_type == "supplement_w" and last_w:
            w_answered = await self._check_w_answered(elder_response, last_w)
            print(f"  → W({last_w}) 是否被回答: {w_answered}")

            if w_answered:
                if last_w not in covered_w:
                    covered_w.append(last_w)
                # 繼續往下走偵測 W + 決定下一步
            else:
                skipped_w.append(last_w)
                return await self._next_step_or_end(
                    user, scene_els, covered_w, skipped_w, elder_response, state, emotion,
                    question_count, supplement_count, scene_composition=scene_comp,
                )

        # ── STEP2：自由對話中背景追蹤 W 覆蓋 ────────────────────
        if not quick_end:
            newly_covered = await self._detect_covered_w(elder_response, covered_w)
            for w in newly_covered:
                if w not in covered_w:
                    covered_w.append(w)
            if newly_covered:
                print(f"  → 自然涵蓋 W: {newly_covered}，covered={covered_w}")

        # ── 5W1H 全部自然涵蓋 → 結束回合（順其自然的好結局，不是硬湊出來的）──
        if not self._next_uncovered_w(covered_w, skipped_w):
            print("  → 5W1H 全部涵蓋，結束回合")
            return await self._end_action(state, user, elder_response, emotion)

        # ── 單回合題數已達上限 → 結束回合（避免對話冗長）───────────
        if question_count >= _MAX_QUESTIONS_PER_ROUND:
            print(f"  → 已達單回合題數上限（{_MAX_QUESTIONS_PER_ROUND}），結束回合")
            return await self._end_action(state, user, elder_response, emotion)

        # ── 話題能否繼續？ ────────────────────────────────────────
        if quick_end:
            can_continue = False
        else:
            can_continue = await self._decide_topic_continuation(elder_response, user, scene_els)
        print(f"  → 話題能否繼續: {can_continue}")

        if can_continue:
            # STEP2：承接情緒 + 開放追問（Track C），包上禁忌話題防護
            result = await guarded_generate(
                self._generate_open_followup,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=3,  # 理由同 STEP1 呼叫處：多幾次嘗試換更高機率避開保底句
                # _generate_open_followup 回傳沒有 covered_w 這個 key，跟 STEP1/STEP3
                # 用的 _generate_question/_generate_supplement_question 不一樣。
                fallback=_element_fallback(scene_els, with_covered_w=False),
                user=user, scene_elements=scene_els, covered_w=covered_w,
                skipped_w=skipped_w, elder_response=elder_response, emotion=emotion,
                scene_composition=scene_comp,
            )
            new_state = {
                **state,
                "covered_w": covered_w,
                "skipped_w": skipped_w,
                "last_question_type": "open",
                "last_w_asked": "",
                "question_count": question_count + 1,
                "supplement_count": supplement_count,
            }
            return {
                "action": "open_followup",
                "scene_text": result["scene_text"],
                "question": result["question"],
                "state": new_state,
            }
        else:
            # 話題自然結束：5W1H 覆蓋度只用來挑「補問哪個W」，
            # 不是「六個維度都要問過才能結束」——_next_step_or_end 內部
            # 會依 _MAX_SUPPLEMENT_PER_ROUND 判斷是否還要補問，還是直接收尾。
            return await self._next_step_or_end(
                user, scene_els, covered_w, skipped_w, elder_response, state, emotion,
                question_count, supplement_count, scene_composition=scene_comp,
            )

    # ══════════════════════════════════════════════════════════════
    # 私有：狀態機輔助
    # ══════════════════════════════════════════════════════════════

    def _next_uncovered_w(self, covered_w: list, skipped_w: list) -> str | None:
        done = set(covered_w) | set(skipped_w)
        for w in _W_ORDER:
            if w not in done:
                return w
        return None

    async def _ask_supplement(
        self,
        user: dict,
        scene_els: list,
        covered_w: list,
        skipped_w: list,
        target_w: str,
        state: dict,
        emotion: str = "happy",
        question_count: int = 1,
        supplement_count: int = 0,
        elder_response: str = "",
        scene_composition: str = "",
    ) -> dict:
        result = await guarded_generate(
            self._generate_supplement_question,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            max_retry=3,  # 理由同 STEP1 呼叫處：多幾次嘗試換更高機率避開保底句
            fallback=_element_fallback(scene_els),
            user=user, scene_elements=scene_els, covered_w=covered_w, target_w=target_w,
            emotion=emotion, elder_response=elder_response, scene_composition=scene_composition,
        )
        print(f"  → 補問 W({target_w}): {result['question']}")
        new_state = {
            **state,
            "covered_w": covered_w,
            "skipped_w": skipped_w,
            "last_question_type": "supplement_w",
            "last_w_asked": target_w,
            "question_count": question_count + 1,
            "supplement_count": supplement_count + 1,
        }
        return {
            "action": "ask_supplement_w",
            "scene_text": result["scene_text"],
            "question": result["question"],
            "state": new_state,
        }

    async def _end_action(
        self,
        state: dict,
        user: dict = None,
        elder_response: str = "",
        emotion: str = "happy",
    ) -> dict:
        current_round = state["round"]
        if current_round >= 3:
            print("  → 三回合完成，療程結束")
            closing = (
                await guarded_generate(
                    self._generate_closing,
                    taboo_words=user["taboos"],
                    llm_service=self.llm,
                    max_retry=3,  # 理由同 STEP1 呼叫處：多幾次嘗試換更高機率避開保底句
                    text_keys=("closing_text", "question"),
                    fallback={
                        "closing_text": "謝謝你今天的分享，辛苦了。",
                        "question": "現在心裡在想些什麼呢？",
                    },
                    user=user, elder_response=elder_response, emotion=emotion,
                )
                if user else {"closing_text": "", "question": ""}
            )
            return {
                "action": "end_session",
                "scene_text": closing["closing_text"],
                "question": closing["question"],
                "state": None,
            }
        print(f"  → 回合 {current_round} 結束，進入回合 {current_round + 1}")
        return {
            "action": "end_round",
            "next_round": current_round + 1,
            "scene_text": "",
            "question": "",
            "state": None,
        }

    async def _next_step_or_end(
        self,
        user: dict,
        scene_els: list,
        covered_w: list,
        skipped_w: list,
        elder_response: str,
        state: dict,
        emotion: str = "happy",
        question_count: int = 1,
        supplement_count: int = 0,
        scene_composition: str = "",
    ) -> dict:
        """
        話題結束後：最多補問 _MAX_SUPPLEMENT_PER_ROUND 個未涵蓋的W，或結束回合。
        Why 需長者狀態良好才問。

        5W1H 覆蓋度只用來決定「補問哪一個W」，不是「六個維度都要問過才能結束」——
        補問次數或本回合題數一旦超過上限，就算還有W沒問到，也直接自然收尾，
        避免對話變成連環打勾清單。

        目標W的選擇不是永遠照 _W_ORDER 固定順序掃第一個沒涵蓋的——那樣等於
        機械化地把5W1H當成必須照1到5走完的線性流程。改成在非Why的未涵蓋
        維度裡隨機挑一個，Why仍維持最低優先序（只有其他都涵蓋時才輪到，
        且仍要通過長者狀態良好判斷）。
        """
        uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
        if not uncovered:
            return await self._end_action(state, user, elder_response, emotion)

        # 懷舊治療重點是長者的成就感／愉悅感，不是把5W1H打勾湊滿——情緒已經
        # 正向、且本回合已經補問過至少一次，代表這一輪已經達到效果，優先
        # 自然收尾，不用為了湊剩下的W硬多問一題。
        if supplement_count >= 1 and emotion in ("happy", "excited"):
            print(f"  → 長者情緒{emotion}且已補問過，優先收尾（成就感優先於題數）")
            return await self._end_action(state, user, elder_response, emotion)

        if (
            supplement_count >= _MAX_SUPPLEMENT_PER_ROUND
            or question_count >= _MAX_QUESTIONS_PER_ROUND
        ):
            print(f"  → 補問已達上限（{supplement_count}/{_MAX_SUPPLEMENT_PER_ROUND}），話題自然結束")
            return await self._end_action(state, user, elder_response, emotion)

        non_why = [w for w in uncovered if w != "Why"]
        if non_why:
            next_w = random.choice(non_why)
        else:
            elder_state_good = await self._check_elder_state_good(elder_response, emotion)
            print(f"  → Why 長者狀態良好: {elder_state_good}")
            if not elder_state_good:
                skipped_w.append("Why")
                return await self._end_action(state, user, elder_response, emotion)
            next_w = "Why"

        return await self._ask_supplement(
            user, scene_els, covered_w, skipped_w, next_w, state, emotion,
            question_count, supplement_count,
            elder_response=elder_response, scene_composition=scene_composition,
        )

    # ══════════════════════════════════════════════════════════════
    # 私有：LLM 判斷
    # ══════════════════════════════════════════════════════════════

    async def _detect_emotional_trigger(self, elder_response: str) -> bool:
        """
        判斷長者這句話有沒有透露出需要優先安撫的情緒訊號（對齊 dpo/collect_data.py
        的 Track B／EMOTIONAL_SCENARIOS：提到已故親人、自責記憶力衰退、痛苦往事、
        突然情緒低落等）。這類情況要先給情緒支持，不能直接當一般話題結束或延續處理。

        必須明確排除「單純不知道/不記得（沒有情緒起伏）」——這種情況本來就該讓
        _is_quick_end 的關鍵字判斷接手。兩者的判準若混在一起，會漏掉像
        「我不記得了，我都忘了，我腦子越來越差了……」這種同時包含「不記得/忘了」
        字面、但其實是自責沮喪而非單純放棄話題的情況（Track B 情境 b002 就是
        這個模式）。這個判斷必須跑在 _is_quick_end 之前，不然這類句子會先被
        關鍵字判斷攔截成單純的放棄話題，跳過情緒支持直接進下一題。
        """
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            "請判斷長者這句話有沒有透露出需要優先安撫的情緒訊號，例如：\n"
            "- 提到已故的親人或痛苦的死別經驗\n"
            "- 因為記憶力衰退、做不到某件事而自責、沮喪\n"
            "- 描述過去的苦難、創傷或不想多談的痛苦經歷\n"
            "- 情緒突然低落、哽咽、聲音變小、話說到一半停住\n"
            "YES：有上述情緒訊號，需要先安撫再繼續。\n"
            "NO：只是正常回答問題、平靜陳述，或單純不知道/不記得（沒有情緒起伏）。\n"
            "只回 YES 或 NO，不要任何說明。"
        )
        raw = await self.llm.ask(prompt)
        return raw.strip().upper().startswith("Y")

    async def _decide_topic_continuation(
        self, elder_response: str, user: dict, scene_elements: list
    ) -> bool:
        """判斷長者的回應是否值得繼續順著深入。"""
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            "請判斷：長者的回應是否足夠豐富，值得繼續順著他說的話深入探索？\n"
            "YES：長者說了具體的人、事、地點或感受，有自然延伸的空間。\n"
            "NO：長者回應很短、說不知道、沉默，或話題已自然結束。\n"
            "只回 YES 或 NO，不要任何說明。"
        )
        raw = await self.llm.ask(prompt)
        return raw.strip().upper().startswith("Y")

    async def _check_w_answered(self, elder_response: str, w_dimension: str) -> bool:
        """判斷長者是否回答了指定的 W 維度問題。"""
        desc = _W_DESC.get(w_dimension, w_dimension)
        prompt = (
            f"長者被問到一個關於「{desc}」的問題後，回答了：\n"
            f"「{elder_response}」\n\n"
            f"長者的回答是否有提到「{desc}」的相關資訊？\n"
            "YES：有提到。\n"
            "NO：沒有提到，或回應與問題無關。\n"
            "只回 YES 或 NO，不要任何說明。"
        )
        raw = await self.llm.ask(prompt)
        return raw.strip().upper().startswith("Y")

    def _is_quick_end(self, elder_response: str) -> bool:
        """短回答或放棄關鍵字 → 直接標記話題結束，不呼叫 LLM。"""
        # _NO_RESPONSE_MARKER 字數超過5字、也不含放棄關鍵字，要獨立判斷，
        # 不然接不到問題設計規則.pdf「沉默超過10秒→轉話題」這條。
        if elder_response.strip() == _NO_RESPONSE_MARKER:
            return True
        if len(elder_response.strip()) < 5:
            return True
        give_up = ["不記得", "不知道", "忘了", "忘記了", "不清楚", "沒印象"]
        return any(kw in elder_response for kw in give_up)

    async def _detect_covered_w(
        self, elder_response: str, already_covered: list[str]
    ) -> list[str]:
        """偵測長者回應中自然涵蓋了哪些尚未記錄的 W 維度（STEP2 背景追蹤）。"""
        unchecked = [w for w in _W_ORDER if w not in already_covered]
        if not unchecked:
            return []
        desc_list = "\n".join(f"- {w}：{_W_DESC[w]}" for w in unchecked)
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            f"請判斷這段話是否有涵蓋以下W維度（有提到相關資訊即算涵蓋）：\n"
            f"{desc_list}\n\n"
            f"只列出有涵蓋的維度名稱（英文），用頓號分隔（例：Where、Who）。\n"
            f"若都沒有，只回「無」。不要任何說明。"
        )
        raw = await self.llm.ask(prompt)
        raw = raw.strip()
        if raw == "無" or not raw:
            return []
        return [
            w.strip()
            for w in raw.replace("，", "、").split("、")
            if w.strip() in _W_DESC
        ]

    async def _check_elder_state_good(self, elder_response: str, emotion: str = "happy") -> bool:
        """判斷長者狀態是否良好，適合詢問 Why（原因/動機）。"""
        if emotion in ("sad", "angry"):
            return False
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            "請判斷長者目前狀態是否良好（情緒穩定、回應積極、有分享意願）？\n"
            "YES：狀態良好，可以詢問較深層的問題（如為什麼）。\n"
            "NO：狀態不佳、沉默或有負面情緒，不適合追問原因。\n"
            "只回 YES 或 NO，不要任何說明。"
        )
        raw = await self.llm.ask(prompt)
        return raw.strip().upper().startswith("Y")

    async def _generate_closing(
        self,
        user: dict,
        elder_response: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
    ) -> dict:
        """
        收尾引導：三回合結束後帶領長者從回憶回到現實，詢問感受或正向回憶。

        2026-07-31 曾試過拆成「收縮期→結果期」兩次獨立呼叫（對應 Chao, Chen,
        Liu, & Clark, 2008 的四階段模型），但實測本地模型（DPO 訓練資料 Track D
        一律是「收尾語+問題」成對出現）看到只要求單一欄位的新任務形狀時會退化成
        逐字複誦長者的話，收尾語品質反而比單次呼叫差，因此改回單次生成。

        retry_feedback: 見 _generate_question 的同名參數說明。
        """
        # 讀 app/prompts/closing.txt，跟 dpo/collect_data.py 的 Track D 共用同一份
        # 文字來源（見該檔 _load_production_closing_prompt()），避免這份系統提示
        # 又走回「兩邊各自 hardcode 一份、改一邊忘改另一邊」的老路——Track A/C 的
        # question_5w1h.txt 已經吃過這個虧。
        system_content = _load_prompt("closing.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "三回合懷舊療程剛結束，你要溫柔地帶領長者回到現實，"
            "並以一句輕柔的問題詢問他們現在的感受或正向回憶，為今天的療程畫上句點。"
        )

        last_response_section = (
            f"\n【長者最後說的話】\n{elder_response}\n" if elder_response else ""
        )
        taboo_str = "、".join(user["taboos"]) if user["taboos"] else "無"
        user_content = (
            f"【長者資料】\n"
            f"姓名：{user['name']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"{last_response_section}"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及或引導）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"三回合療程剛剛結束，請設計收尾引導：先用收尾語溫暖肯定長者今天的分享並帶回現實，"
            f"再問一句關於現在感受或今天正向回憶的問題（詳細規則見系統提示）。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"收尾語：（1-2句，30字以內）\n"
            f"問題：（≤15字）"
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_closing_response(raw)

    def _parse_closing_response(self, raw: str) -> dict:
        """解析收尾引導的輸出。"""
        result: dict = {"closing_text": "", "question": ""}
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("收尾語："):
                result["closing_text"] = line[len("收尾語："):].strip()
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("closing_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            result["question"] = _FALLBACK_QUESTION
        if not result["closing_text"]:
            print(f"[Orchestrator] ⚠ 收尾語（引導語）欄位是空的，退回保底收尾語。"
                  f"原始輸出: {raw[:200]!r}")
            result["closing_text"] = _FALLBACK_CLOSING_TEXT
        return result

    async def _generate_emotional_response(
        self,
        user: dict,
        elder_response: str,
        retry_feedback: str = "",
    ) -> dict:
        """
        長者觸發情緒訊號（見 _detect_emotional_trigger）時的專屬回應：先給情緒
        支持，再輕柔引導回療程。

        system_content／user_content 逐字比照 dpo/collect_data.py 的
        build_emotional_inference_prompt（Track B），避免又製造一次 Track A/C/D
        都曾發生過的 train/serve prompt 漂移——那支函式本身就沒有情緒欄位（跟
        Track A/C/D 不同），這裡故意不加 emotion 參數，維持跟訓練資料一致。
        """
        system_content = (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，當他出現負面情緒時，你要先給予情緒支持，再輕柔地引導回療程。"
        )
        taboo_str = "、".join(user["taboos"]) if user["taboos"] else "無"
        user_content = (
            f"長者剛才說了：\n{elder_response}\n\n"
            f"【禁忌話題（絕對不可主動提及或追問細節）】\n{taboo_str}\n\n"
            f"請先給予溫暖的情緒回應，再加上一句輕柔的後續引導。若長者說的內容本身就觸及\n"
            f"【禁忌話題】，承接要溫和但不深入追問細節，儘快輕柔地轉向安全的方向；\n"
            f"後續引導也不能引導向【禁忌話題】。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"情緒回應：（溫暖承接情緒，30-50字）\n"
            f"後續引導：（一句輕柔的問題或肯定，引導回療程）"
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_emotional_response(raw)

    def _parse_emotional_response(self, raw: str) -> dict:
        """解析 Track B（情緒回應 + 後續引導）的輸出。

        故意不用 scene_text 當 key（見 process_response 呼叫 guarded_generate
        的說明）：scene_text_addresses_elder 檢查會把任何出現「你」的 scene_text
        判定成違規，但情緒回應語意上必須用「你」直接安慰長者，用專屬 key 名稱
        天然繞開這個誤判，比照 closing_text 的既有模式。
        """
        result: dict = {"emotional_text": "", "question": ""}
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("情緒回應："):
                result["emotional_text"] = line[len("情緒回應："):].strip()
                current_field = "emotional_text"
            elif line.startswith("後續引導："):
                result["question"] = line[len("後續引導："):].strip()
                current_field = "question"
            elif current_field == "emotional_text":
                result["emotional_text"] = f"{result['emotional_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("emotional_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            result["question"] = _FALLBACK_QUESTION
        if not result["emotional_text"]:
            print(f"[Orchestrator] ⚠ 情緒回應欄位是空的，退回保底安撫語。"
                  f"原始輸出: {raw[:200]!r}")
            result["emotional_text"] = "謝謝你願意說這些，我在這裡陪著你。"
        return result

    # ══════════════════════════════════════════════════════════════
    # 私有：問題生成
    # ══════════════════════════════════════════════════════════════

    async def _decide_scene_anchor(self, user: dict) -> str:
        """
        決定這次 _plan_image 該用哪個錨點，回傳 'hometown'／'occupation'／'interest'。
        獨立判斷、不跟生圖 prompt 混在一起，理由見 _plan_image 的說明。
        """
        if not user.get("preferences"):
            return "occupation"
        prompt = (
            f"今日主題：「{user['today_topic']}」\n\n"
            f"這個主題最貼近長者的哪一種真實生活情境？\n"
            f"A：家鄉／成長地／故鄉生活\n"
            f"B：職業背景（{user['main_occupation']}）\n"
            f"C：興趣嗜好（{user['preferences']}）\n\n"
            f"只回答 A、B 或 C 其中一個字母，不要任何說明或標點。"
        )
        raw = await self.llm.ask(prompt)
        choice = raw.strip().upper()[:1]
        return {"A": "hometown", "B": "occupation", "C": "interest"}.get(choice, "occupation")

    def _build_plan_image_prompt(
        self, user: dict, anchor: str, memories: list[dict] | None = None,
        hide_field: str | None = None,
    ) -> str:
        """
        組 _plan_image 的 prompt。【長者資料】固定完整顯示，anchor 只決定
        【任務】那句指令這次要用哪個情境。hide_field 只在重試時才會給值
        （見 _plan_image），把對應欄位從長者資料整個拿掉。
        """
        memory_section = ""
        if memories:
            memory_section = "\n【長者相關回憶（優先從這裡挑場景元素）】\n"
            for m in memories:
                memory_section += f"- {m.get('summary', m.get('text', ''))}\n"

        profile_lines = [
            f"姓名：{user['name']}",
            f"年齡：{2026 - user['birth_year']} 歲",
            f"出生地：{user['birth_place']}",
        ]
        if hide_field != "main_occupation":
            profile_lines.append(f"職業背景：{user['main_occupation']}")
        if hide_field != "preferences":
            profile_lines.append(f"興趣：{user.get('preferences') or '無'}")
        profile_lines.append(f"今日主題：{user['today_topic']}")
        profile_block = "\n".join(profile_lines)

        # 有記憶／沒記憶是完全分開的兩條指令文字，不要用一句條件句（若有...若無...）
        # 硬湊在一起——實測發現硬湊在一起時，即使真的有記憶，模型也會被句子裡
        # 沒用到的另一半分支文字干擾，變成兩邊都不用、跑去生成完全不相關的內容。
        if memories:
            task_instruction = (
                "這次的每一個畫面元素都只從【長者相關回憶】的內容延伸挑選，完全"
                "不使用興趣、職業背景或出生地。回憶裡有幾個具體東西就用幾個，"
                "不用湊到4個，不要新增回憶沒提到的細節。"
            )
        elif anchor == "hometown":
            task_instruction = (
                "這次主場景以【出生地】為背景，結合【職業背景】的真實生活情境挑選"
                "元素（想像長者在自己的家鄉從事這份職業的真實一刻），不要用到興趣，"
                "元素彼此都要屬於同一個情境。"
            )
        elif anchor == "interest":
            task_instruction = (
                "這次主場景以【興趣】的真實生活情境挑選元素（想像長者從事這項興趣"
                "時的真實一刻），不要用到職業背景，元素彼此都要屬於同一個情境。"
            )
        else:
            task_instruction = (
                "這次主場景以【職業背景】的真實生活情境挑選元素（想像長者在職業"
                "現場的真實一刻），不要用到興趣，元素彼此都要屬於同一個情境。"
            )

        return f"""你是懷舊療法的圖片規劃師。請根據長者資料規劃一張場景圖。

【長者資料】
{profile_block}
{memory_section}
【任務】
規劃一張水彩風格的回憶場景圖，符合主題，要能引發長者的回憶。

{task_instruction}

不要只憑【今日主題】天馬行空聯想，也不要選跟長者實際生活背景無關的通俗畫面。

【嚴格規定】
1. 每個元素必須是「不需要湊近看細節、一眼就能辨認形狀」的大範圍實體物件或情境
   （例如：建築物、交通工具、農具、地景、天色），問題會直接錨定在第一個元素上。
2. 絕對不要用「需要讀出文字」的元素（黑板文字、招牌字樣、書頁內容、標語等）——
   AI 生圖無法穩定畫出清楚可讀的文字，長者也答不出畫面上寫了什麼。
3. 絕對不要用「需要辨識特定人物身份或表情」的元素（小人物、遠處人臉、某個人的
   表情）——AI 生圖無法穩定畫出清楚的人臉細節，長者無從辨認畫裡的人是誰。
4. 絕對不要生成代表「長者本人」的人物元素——如果這次情境是長者自己的職業或
   興趣，元素只能是這個情境裡的物件、地點，或跟長者互動的其他人（同事、客人、
   家人等），不能生成一個「正在做這件事的人」當元素（例如長者職業是賣菜，不能
   生成「賣菜阿嬤」「賣菜的人」這種元素）——問題生成時會把這個元素當成長者以外
   的第三人來問，變成要長者回答「你最喜歡跟她聊些什麼」這種問長者自己的問題。
5. 每個元素必須是「同年代、同職業背景的人普遍會有印象」的常見物件，不要選個人
   化程度太高、地域限定太窄或太罕見的物件（例如特定花卉品種、特定小眾嗜好用
   品）——長者答不出自己沒印象的東西，元素越通俗普遍，長者才越可能真的有共鳴。
6. image_prompt 要指定暖色調、高對比配色，且明確避免藍、綠、紫三色互相鄰接
   （例如寫 "warm high-contrast palette, avoid adjacent blue-green-purple
   tones"）——年長者對藍/綠/紫及其鄰近色的辨識能力較弱，色差不夠大會導致
   長者根本看不清楚畫面裡的錨點物件。
7. 元素之間、以及元素與長者的職業背景/今日主題之間，必須符合現實邏輯，不能互相
   矛盾（例如：導遊、業務跑外勤這類白天在外活動的職業，畫面不要無故選夜景；適合
   用夜景的情境是活動本身就發生在晚上，如夜市、廟會、夜校、值夜班等）。第6點要求
   的暖色高對比，白天陽光、黃昏落日一樣能達成，不是只有夜晚才符合。
8. 回傳一個 JSON 物件，**只回 JSON，不要任何說明文字或 markdown 標記**。
格式：
{{
  "elements": ["元素1", "元素2", "元素3", "元素4"],
  "image_prompt": "英文 prompt 給 Stability AI，包含水彩風格、年代、場景、元素"
}}

範例（主題=運動會）：
{{
  "elements": ["操場", "黃昏", "大隊接力", "加油聲"],
  "image_prompt": "watercolor painting style, 1940s Taiwan elementary school sports day, relay race, children running on dirt track, dusk light, nostalgic warm tones, no text"
}}
"""

    async def _plan_image(self, user: dict, memories: list[dict] | None = None) -> dict:
        """
        請 LLM 規劃圖片元素與生圖 prompt，可傳入 memories 讓 LLM 從回憶挑元素。

        沒有記憶時，先用 _decide_scene_anchor 獨立判斷這次該用出生地+職業、
        純職業、還是純興趣當主場景。長者資料一律完整顯示所有欄位，只在
        【任務】那句指令裡明確告訴模型這次要用哪個情境。

        某些職業（例如導遊，本質就是「帶人看東西、介紹文化」）即使給了明確
        指令，仍可能把職業跟興趣兩個不相干的情境湊進同一張圖——這裡加一層
        事後偵測：elements/image_prompt 同時出現職業跟興趣的字面就重新生成
        一次，且只有這次重試才把沒被選中的那個欄位從長者資料整個拿掉
        （hide_field），physically 保證不會再犯。只重試一次，延遲上限可控。
        """
        anchor = "hometown"  # 有記憶時錨點不影響結果，memory_section 優先權更高，這裡給預設值即可
        if not memories:
            anchor = await self._decide_scene_anchor(user)

        prompt = self._build_plan_image_prompt(user, anchor, memories)
        raw = await self.llm.ask(prompt)
        plan = self._extract_json(raw)
        plan["elements"] = filter_scene_elements(plan.get("elements", []))

        occupation = user.get("main_occupation", "")
        interest = user.get("preferences", "")
        if not memories and occupation and interest:
            combined = " ".join(plan.get("elements", [])) + " " + plan.get("image_prompt", "")
            if occupation in combined and interest in combined:
                # anchor 選了哪個，就拿掉沒被選中的那個候選欄位重試一次
                hide_field = "preferences" if anchor in ("hometown", "occupation") else "main_occupation"
                print(f"  → ⚠ 圖片元素同時混進職業（{occupation}）跟興趣（{interest}），"
                      f"重新生成一次（拿掉{hide_field}）: {plan.get('elements')}")
                prompt_retry = self._build_plan_image_prompt(
                    user, anchor, memories, hide_field=hide_field,
                )
                raw_retry = await self.llm.ask(prompt_retry)
                plan = self._extract_json(raw_retry)
                plan["elements"] = filter_scene_elements(plan.get("elements", []))

        return plan

    async def _generate_question(
        self,
        step: str,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        scene_composition: str = "",
        elder_response: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
    ) -> dict:
        """
        生成 STEP1/2/3 問題。
        prompt 格式對齊 dpo/collect_data.py build_inference_prompt（Track A）。
        Returns: {"scene_text": str, "question": str, "covered_w": list[str]}

        retry_feedback: guarded_generate 偵測到上一次輸出違規時傳入的具體說明，
            見 _retry_feedback_section。

        2026-08：這裡原本有 memory_section（直接把記憶文字塞進問題生成
        prompt），實測是null result甚至有違規訊號，且這個欄位從沒進過DPO
        訓練資料，train/serve本來就不對齊。個人化已經由 _plan_image 的
        記憶→畫面元素這條路徑達成（有驗證過、且元素本來就走模型訓練過的
        elements_str 格式），這裡不需要再重複做一次，故拿掉。
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "問題必須念起來自然、溫和、不超過15個字，且開頭要包含畫面中看得到的具體物件。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        topic_str    = user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"

        elder_section = f"\n【長者剛才說的話】\n{elder_response}\n" if elder_response else ""

        user_content = (
            f"【長者資料】\n"
            f"姓名：{user['name']}\n"
            f"職業背景：{user['main_occupation']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"興趣：{user.get('preferences') or '無'}\n"
            f"懷舊治療主題類別：{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"{elder_section}"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n{_STEP_TASKS[step]}\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"思考：（主題判斷：一句話判斷今日主題最貼近哪個核心主題；"
            f"切入角度：一到兩句話決定這題要用什麼當錨點、往哪個方向問——"
            f"兩段都要寫，不會念給長者聽）\n"
            f"場景文字：（30-60字，給長者聽的場景描述）\n"
            f"問題：（≤15字，開放式，開頭要有畫面中的具體物件）\n"
            f"問題類型：{_STEP_TYPE_LABEL[step]}\n"
            f"本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個W維度名稱本身，"
            f"不要自創其他詞彙、不要加括號說明）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_question_response(raw, scene_elements=scene_elements)

    async def _generate_open_followup(
        self,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        skipped_w: list[str],
        elder_response: str,
        scene_composition: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
    ) -> dict:
        """
        STEP2 開放式追問（Track C）：承接長者情緒，自然延伸問題，順道帶出未涵蓋的W。
        prompt 格式對齊 dpo/collect_data.py build_track_c_inference_prompt。
        Returns: {"scene_text": str, "question": str}

        retry_feedback: 見 _generate_question 的同名參數說明。
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "每次聽完長者說話，先用1-2句溫暖的話具體承接他的情緒，再順著他說的話自然問下一個問題，"
            "不必勉強拉回畫面元素。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        topic_str    = user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"

        uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
        uncovered_str = "、".join(uncovered) if uncovered else "無（已全部涵蓋）"

        user_content = (
            f"長者剛才說：\n「{elder_response}」\n"
            f"\n【今日主題】\n{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【尚未涵蓋的W維度】\n{uncovered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n請先承接長者的情緒（1-2句，符合他當下的心情，具體呼應他剛才說的內容），"
            f"再順著長者說的話問下一個問題（≤15字，開頭可用長者剛提到的具體人事物，"
            f"也可以用畫面元素，開放式，不必勉強拉回畫面）。\n"
            f"⚠️ 觸發條件檢查（每次回應前都要先看一次）：長者剛才的話裡有沒有把決定權"
            f"丟回來的句子？分兩種，處理方式不同：(a) 像「換一個好不好」「聊點別的吧」"
            f"這種已經做出決定、明確要求換話題的句子——承接語要溫暖地肯定他這個選擇"
            f"（例如「不想說的事就不用勉強」），但不用承諾「你想聊什麼，我們就聊什麼」"
            f"這種空話；「有溫度」跟「不做空頭承諾」要同時做到，不能為了避免空話就把"
            f"承接語縮成只剩「沒關係」兩三個字，那樣反而顯得冷淡生硬；接著問一個新方向"
            f"的具體問題就是在尊重他的要求；"
            f"(b) 像「你真的想知道嗎」「我要跟你說嗎」「你想聽嗎」這種還沒決定、把決定"
            f"權真的丟回來問你的反問句——這種才需要把主導權完全交還，問題絕對不能硬拉去"
            f"不相干的話題（那樣會讓「主導權在你」這句話顯得言行不一，是這條規則最容易"
            f"出錯的地方），而是問一個尊重他步調、讓他自己決定要不要繼續/現在說或晚點說"
            f"的問題。\n"
            f"先判斷長者是不是正說得起勁、自己滔滔不絕地敘述——如果是，「問題」改用"
            f"聊天中真的會脫口而出的簡短延續句（例如「後來呢？」「你們還做了什麼？」），"
            f"順著他的話往下接就好，不用刻意湊出結構完整、以W維度為目標的問題；只有"
            f"長者的敘述明顯停下來、需要換方向時，才自然地把問題帶到【尚未涵蓋的W維度】"
            f"其中一個上。承接語（同理、具體呼應長者剛才說的內容）不受這條影響，維持原本要求。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"承接語：（1-2句，30字以內）\n"
            f"問題：（≤15字）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_track_c_response(raw, scene_elements=scene_elements)

    async def _generate_supplement_question(
        self,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        target_w: str,
        scene_composition: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
        elder_response: str = "",
    ) -> dict:
        """
        W 補問：明確針對尚未涵蓋的 W 維度切入（STEP3 格式）。
        Returns: {"scene_text": str, "question": str}

        retry_feedback: 見 _generate_question 的同名參數說明。
        elder_response: 長者在 STEP2 自由追問裡剛說的話——STEP2 是完全跟著長者
            話走的自由對話，內容常常已經飄離眼前畫面，補問的場景文字要負責把
            注意力拉回來，若不知道長者剛才說了什麼，拉回來的方式只能是憑空
            接畫面，答非所問、不像在聊天（2026-07 使用者回饋發現這裡漏了
            elder_response，_next_step_or_end 手上明明有這個值卻沒往下傳）。
            預設空字串是為了兼容沒有對應到單一長者回應、本來就沒有值可傳的呼叫端。
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "問題必須念起來自然、溫和、不超過15個字，且開頭要包含畫面中看得到的具體物件。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        topic_str    = user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"
        elder_section = f"\n【長者剛才說的話】\n{elder_response}\n" if elder_response else ""

        user_content = (
            f"【長者資料】\n"
            f"姓名：{user['name']}\n"
            f"職業背景：{user['main_occupation']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"興趣：{user.get('preferences') or '無'}\n"
            f"懷舊治療主題類別：{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"{elder_section}"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"生成一個問題，順著長者剛才的話跟眼前畫面自然地深入問下去，不是在核對清單。"
            f"{_W_HINT[target_w]}\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"思考：（主題判斷：一句話判斷今日主題最貼近哪個核心主題；"
            f"切入角度：一到兩句話決定這題要用什麼當錨點、往哪個方向問——"
            f"兩段都要寫，不會念給長者聽）\n"
            f"場景文字：（15-30字，幫長者重新聚焦到畫面，若【長者剛才說的話】有內容，"
            f"盡量從那句話自然接回畫面，不要憑空硬轉）\n"
            f"問題：（≤15字，開放式，開頭要有畫面中的具體物件）\n"
            f"問題類型：STEP3補問\n"
            f"本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個W維度名稱本身，"
            f"不要自創其他詞彙、不要加括號說明）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_question_response(raw, scene_elements=scene_elements)

    # ══════════════════════════════════════════════════════════════
    # 私有：解析 LLM 輸出
    # ══════════════════════════════════════════════════════════════

    def _parse_question_response(
        self, raw: str, scene_elements: list[str] | None = None,
    ) -> dict:
        """解析 STEP1/2/3 的結構化輸出。

        scene_elements: 若 LLM 沒吐出「場景文字：」那一行，用這份畫面元素清單
        組一句「畫面裡有OOO」的保底鋪陳語，而不是完全籠統、跟畫面無關的通用句——
        場景文字本來的功能就是幫長者建立畫面感，保底時也不該把這個功能整個丟掉。
        """
        result: dict = {"scene_text": "", "question": "", "covered_w": []}
        thinking = ""
        # current_field 追蹤「目前正在填哪個欄位」，讓後續沒有標籤的行可以接到
        # 上一個標籤欄位——本地模型偶爾會把「問題：」單獨放一行、實際問題文字
        # 放在下一行，原本逐行比對「這行開頭是不是問題：」的寫法抓不到這種格式，
        # 會讓 result["question"] 停留空字串，觸發下面的「question 欄位是空的」
        # 保底邏輯，把整段原始輸出（思考+場景文字+問題+W全部黏在一起）誤判成
        # 問題內容塞進去——2026-08 實測發現這是造成 too_long 重試的常見成因。
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("思考："):
                # CoT 草稿行：question_5w1h.txt 的【思考欄位】規則要求模型先在這裡
                # 判斷主題方向、選錨點，再輸出正式內容。這行故意不進 result、不會
                # 被念給長者聽，只印出來方便觀察模型的選題邏輯、調整 prompt。
                thinking = line[len("思考："):].strip()
                current_field = "thinking"
            elif line.startswith("場景文字："):
                result["scene_text"] = line[len("場景文字："):].strip()
                current_field = "scene_text"
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif line.startswith("本回合已涵蓋的W："):
                # 先清掉可能洩漏的括號說明（例如模型自己加註「（因...較難...改以...）」），
                # 不然裡面的中文逗號會被當成 W 之間的分隔符，把整段說明文字拆成好幾個
                # 假的 W 值，汙染後續判斷「已經問過哪些W」的邏輯。
                w_raw = _strip_leaked_brackets(line[len("本回合已涵蓋的W："):].strip())
                result["covered_w"] = [
                    w.strip()
                    for w in w_raw.replace("，", "、").split("、")
                    if w.strip()
                ]
                current_field = None
            elif line.startswith("問題類型："):
                current_field = None  # 這欄不儲存，但要停止把後面的行接到問題/場景文字
            elif current_field == "scene_text":
                result["scene_text"] = f"{result['scene_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
            elif current_field == "thinking":
                thinking = f"{thinking} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("scene_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            print(f"[Orchestrator] ⚠ 問題欄位清洗後是空的（本地模型把格式範本原封不動echo回來），"
                  f"退回{'含畫面元素的' if first_element else ''}保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if not result["scene_text"]:
            fallback = (
                f"眼前的畫面裡有{elements_str}，我們接著聊聊這個吧。"
                if (elements_str := _natural_join(scene_elements or [])).strip()
                else _FALLBACK_SCENE_TEXT
            )
            print(f"[Orchestrator] ⚠ 場景文字（引導語）欄位是空的，退回{'含畫面元素的' if scene_elements else ''}"
                  f"保底鋪陳語，避免長者只聽到問題、沒有任何引導。原始輸出: {raw[:200]!r}")
            result["scene_text"] = fallback
        if thinking:
            print(f"  → 思考: {thinking}")
        return result

    def _parse_track_c_response(self, raw: str, scene_elements: list[str] | None = None) -> dict:
        """解析 Track C（承接語 + 問題）的輸出。"""
        result: dict = {"scene_text": "", "question": ""}
        # 同 _parse_question_response：追蹤目前正在填哪個欄位，處理標籤跟內容
        # 分兩行的情況（見該函式的說明）。
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("承接語："):
                result["scene_text"] = line[len("承接語："):].strip()
                current_field = "scene_text"
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif current_field == "scene_text":
                result["scene_text"] = f"{result['scene_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("scene_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            print(f"[Orchestrator] ⚠ Track C 問題欄位清洗後是空的，退回"
                  f"{'含畫面元素的' if first_element else ''}保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if not result["scene_text"]:
            print(f"[Orchestrator] ⚠ Track C 承接語（引導語）欄位是空的，退回保底鋪陳語。"
                  f"原始輸出: {raw[:200]!r}")
            result["scene_text"] = _FALLBACK_SCENE_TEXT
        return result

    def _extract_json(self, text: str) -> dict:
        """從 LLM 回應中萃取 JSON。"""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            start = text.find("{")
            end   = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"LLM 沒有回有效的 JSON: {text[:200]}") from e