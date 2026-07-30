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
import re
from pathlib import Path
from services.llm import LLMService
from services.image import StabilityImageService
from services.rag_client import RealRAGClient
from services.user_profile_db import DBUserProfileClient
from privacy.deidentifier import Deidentifier
from safety.taboo_checker import guarded_generate
from safety.element_filter import filter_scene_elements

# 5W1H 優先順序（由易到難，對齊 問題設計規則.pdf；Why 條件式使用）
_W_ORDER = ["Where", "Who", "What", "When", "How", "Why"]

_W_DESC = {
    "Where": "地點（在哪裡、哪個地方）",
    "Who":   "人物（誰、哪個人）",
    "What":  "事物（什麼事、什麼東西）",
    "When":  "時間（什麼時候）",
    "How":   "方式（怎麼做、如何）",
    "Why":   "原因（為什麼、動機）",
}

_W_HINT = {
    "Where": "用 Where 角度問（哪裡、哪個地方）",
    "Who":   "用 Who 角度問（誰、哪個人）",
    "What":  "用 What 角度問（什麼事、什麼東西）",
    "When":  "用 When 角度問（什麼時候）",
    "How":   "用 How 角度問（怎麼做、如何）",
    "Why":   "用 Why 角度問（為什麼、原因、動機）——僅在長者狀態良好時使用",
}

_STEP_TASKS = {
    "STEP1": "生成第一個【開場問題】，引導長者進入回憶（優先問 Where 或 What）",
    "STEP2": "根據長者剛才說的話，順著內容自然追問，不限制哪個W，完全跟著長者走",
    "STEP3": "生成一個【補充問題】，探索還未涵蓋的W維度（Why 僅在長者狀態良好時詢問）",
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


def _emotion_guidance(emotion: str, slow_response: bool = False) -> str:
    # 情緒偵測不見得可靠（例如中風、面癱等生理因素會讓表情辨識失準），
    # 未知情緒保守地當作「不明確」處理，不要預設為 happy 就貿然深入提問。
    guidance = _EMOTION_GUIDANCE.get(emotion, _EMOTION_GUIDANCE["neutral"])
    if slow_response:
        # 純語氣調整，不影響任何流程判斷（是否轉話題、是否結束回合）——
        # 只是延遲久不代表長者在掙扎放棄，也可能是認真在回想，
        # 所以刻意不拿這個訊號去驅動任何決策，只放軟語氣、給多一點耐心。
        guidance += "長者這題想了比較久才回答，語氣放慢一點、多一些耐心與肯定，不要催促。"
    return guidance


# 單回合題數上限、補問上限：避免為了湊滿 5W1H 連環追問到底。
# 話題自然結束時，最多補問 _MAX_SUPPLEMENT_PER_ROUND 個未涵蓋的W就結束回合，
# 不強求六個W維度都要問過一輪。
_MAX_QUESTIONS_PER_ROUND = 5
_MAX_SUPPLEMENT_PER_ROUND = 2

# 回應延遲門檻（毫秒）：超過這個時間只用來放軟語氣（見 _emotion_guidance），
# 不影響 quick_end / can_continue 等任何流程判斷，避免誤判認真思考的長者為放棄。
#
# ⚠️ 這個時間量的是「AI問題送出（含TTS合成完成）」到「長者送出回答」之間的差距，
# 無法扣掉「長者實際聽完整段語音播放」所花的時間（後端不知道前端播放何時結束），
# 場景文字+問題最長可到 75 字，光聽完就可能要十幾秒。門檻刻意設得比較保守，
# 就是為了在這個已知的量測誤差之上，還留一段真正屬於「長者反應時間」的空間，
# 減少把「還在聽AI講話」誤判成「長者想很久」的機率。
_SLOW_RESPONSE_MS = 30_000


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


def _strip_leaked_brackets(text: str) -> str:
    return _LEAK_BRACKET_RE.sub("", text).strip()


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


def _load_prompt(filename: str) -> str:
    """從 app/prompts/ 讀取 prompt 模板，找不到就回傳空字串。"""
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
        print(f"[Orchestrator] ── 回合 {round_number} 開始 ──")

        user = await self.user_profile.get_user(user_id)
        if not user:
            raise ValueError(f"找不到使用者: {user_id}")
        if topic_override:
            user = {**user, "today_topic": topic_override, "topic_category": [topic_override]}
        print(f"  → {user['name']}，主題: {user['today_topic']}")

        image_plan = await self._plan_image(user)
        print(f"  → 圖片元素: {image_plan['elements']}")

        safe_prompt = self.deidentifier.desensitize_text(
            image_plan["image_prompt"], taboos=user["taboos"]
        )

        try:
            image_path = await self.image.generate(
                prompt=safe_prompt,
                session_id=session_id,
                round_number=round_number,
            )
        except Exception as e:
            print(f"  → 圖片生成失敗（不影響對話主流程，長者端這回合沒有配圖）: {e}")
            image_path = ""
        print(f"  → 圖片: {image_path}")

        memories = await self.rag.retrieve_memories(
            user_id=user_id,
            query=f"{user['today_topic']} {user['main_occupation']}",
            limit=3,
        )

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
            # 對齊的保底字典，不能用預設值。
            fallback={
                "scene_text": "我們換個輕鬆一點的方向聊聊吧。",
                "question": "現在心裡在想些什麼呢？",
                "covered_w": [],
            },
            step="STEP1",
            user=user,
            scene_elements=image_plan["elements"],
            covered_w=[],
            memories=memories,
        )
        print(f"  → STEP1 問題: {q['question']}（W: {q['covered_w']}）")

        state = {
            "user_id": user_id,
            "session_id": session_id,
            "round": round_number,
            "scene_elements": image_plan["elements"],
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
        elapsed_ms: int | None = None,
    ) -> dict:
        """
        狀態機核心：根據長者回應決定下一步。

        Args:
            elder_response: 長者說的話（STT 轉譯結果）
            state: 上一輪回傳的 state dict
            emotion: Kinect 即時偵測的情緒（happy/excited/angry/sad，見 app/routers/sensor.py）
            elapsed_ms: 從上一題送出到這次回答送出的時間（毫秒），
                由 app/routers/session.py 算好傳入。只用來讓 _emotion_guidance
                放軟語氣（見 _SLOW_RESPONSE_MS），不影響任何流程判斷——
                延遲久不代表長者要放棄，可能只是認真在回想。

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
        question_count   = state.get("question_count", 1)
        supplement_count = state.get("supplement_count", 0)

        slow_response = elapsed_ms is not None and elapsed_ms > _SLOW_RESPONSE_MS

        print(f"[Orchestrator] process_response | round={state['round']} "
              f"last={last_type} covered={covered_w} skipped={skipped_w} "
              f"questions={question_count} supplements={supplement_count} "
              f"elapsed_ms={elapsed_ms} slow_response={slow_response}")

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
                    question_count, supplement_count, slow_response,
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
                user=user, scene_elements=scene_els, covered_w=covered_w,
                skipped_w=skipped_w, elder_response=elder_response, emotion=emotion,
                slow_response=slow_response,
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
                question_count, supplement_count, slow_response,
            )

    async def suggest_next_questions(
        self,
        user_id: str,
        covered_w: list[str],
        skipped_w: list[str],
        scene_elements: list[str],
        emotion: str = "happy",
        limit: int = 3,
    ) -> list[str]:
        """給治療師畫面「AI 建議追問語（參考用）」用：依序取接下來 limit 個
        尚未涵蓋的 W 維度，各生成一題候選追問語。純參考展示，不影響系統
        實際問長者的邏輯（那條路徑走 process_response，完全不受這裡影響）。"""
        user = await self.user_profile.get_user(user_id)
        if not user:
            return []
        done = set(covered_w) | set(skipped_w)
        target_ws = [w for w in _W_ORDER if w not in done][:limit]
        questions: list[str] = []
        for target_w in target_ws:
            try:
                result = await guarded_generate(
                    self._generate_supplement_question,
                    taboo_words=user["taboos"],
                    llm_service=self.llm,
                    user=user, scene_elements=scene_elements, covered_w=covered_w,
                    target_w=target_w, emotion=emotion,
                )
                if result.get("question"):
                    questions.append(result["question"])
            except Exception as e:
                print(f"[Orchestrator] 建議追問語生成失敗（{target_w}，不影響主流程）: {e}")
        return questions

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
        slow_response: bool = False,
    ) -> dict:
        result = await guarded_generate(
            self._generate_supplement_question,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            max_retry=3,  # 理由同 STEP1 呼叫處：多幾次嘗試換更高機率避開保底句
            user=user, scene_elements=scene_els, covered_w=covered_w, target_w=target_w,
            emotion=emotion, slow_response=slow_response,
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
        slow_response: bool = False,
    ) -> dict:
        """
        話題結束後：最多補問 _MAX_SUPPLEMENT_PER_ROUND 個未涵蓋的W，或結束回合。
        Why 需長者狀態良好才問。

        5W1H 覆蓋度只用來決定「補問哪一個W」，不是「六個維度都要問過才能結束」——
        補問次數或本回合題數一旦超過上限，就算還有W沒問到，也直接自然收尾，
        避免對話變成連環打勾清單。
        """
        next_w = self._next_uncovered_w(covered_w, skipped_w)
        if not next_w:
            return await self._end_action(state, user, elder_response, emotion)

        if (
            supplement_count >= _MAX_SUPPLEMENT_PER_ROUND
            or question_count >= _MAX_QUESTIONS_PER_ROUND
        ):
            print(f"  → 補問已達上限（{supplement_count}/{_MAX_SUPPLEMENT_PER_ROUND}），話題自然結束")
            return await self._end_action(state, user, elder_response, emotion)

        if next_w == "Why":
            elder_state_good = await self._check_elder_state_good(elder_response, emotion)
            print(f"  → Why 長者狀態良好: {elder_state_good}")
            if not elder_state_good:
                skipped_w.append("Why")
                next_w = self._next_uncovered_w(covered_w, skipped_w)
                if not next_w:
                    return await self._end_action(state, user, elder_response, emotion)

        return await self._ask_supplement(
            user, scene_els, covered_w, skipped_w, next_w, state, emotion,
            question_count, supplement_count, slow_response,
        )

    # ══════════════════════════════════════════════════════════════
    # 私有：LLM 判斷
    # ══════════════════════════════════════════════════════════════

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

    # ══════════════════════════════════════════════════════════════
    # 私有：問題生成
    # ══════════════════════════════════════════════════════════════

    async def _plan_image(self, user: dict) -> dict:
        """請 LLM 規劃圖片元素與生圖 prompt。"""
        prompt = f"""你是懷舊療法的圖片規劃師。請根據長者資料規劃一張場景圖。

【長者資料】
姓名：{user['name']}
年齡：{2026 - user['birth_year']} 歲
出生地：{user['birth_place']}
職業背景：{user['main_occupation']}
今日主題：{user['today_topic']}

【任務】
規劃一張水彩風格的回憶場景圖，符合主題，要能引發長者的回憶。

【嚴格規定】
1. 每個元素必須是「不需要湊近看細節、一眼就能辨認形狀」的大範圍實體物件或情境
   （例如：建築物、交通工具、農具、地景、天色），問題會直接錨定在第一個元素上。
2. 絕對不要用「需要讀出文字」的元素（黑板文字、招牌字樣、書頁內容、標語等）——
   AI 生圖無法穩定畫出清楚可讀的文字，長者也答不出畫面上寫了什麼。
3. 絕對不要用「需要辨識特定人物身份或表情」的元素（小人物、遠處人臉、某個人的
   表情）——AI 生圖無法穩定畫出清楚的人臉細節，長者無從辨認畫裡的人是誰。
4. 每個元素必須是「同年代、同職業背景的人普遍會有印象」的常見物件，不要選個人
   化程度太高、地域限定太窄或太罕見的物件（例如特定花卉品種、特定小眾嗜好用
   品）——長者答不出自己沒印象的東西，元素越通俗普遍，長者才越可能真的有共鳴。
5. image_prompt 要指定暖色調、高對比配色，且明確避免藍、綠、紫三色互相鄰接
   （例如寫 "warm high-contrast palette, avoid adjacent blue-green-purple
   tones"）——年長者對藍/綠/紫及其鄰近色的辨識能力較弱，色差不夠大會導致
   長者根本看不清楚畫面裡的錨點物件。
6. 元素之間、以及元素與長者的職業背景/今日主題之間，必須符合現實邏輯，不能互相
   矛盾（例如：導遊、業務跑外勤這類白天在外活動的職業，畫面不要無故選夜景；適合
   用夜景的情境是活動本身就發生在晚上，如夜市、廟會、夜校、值夜班等）。第5點要求
   的暖色高對比，白天陽光、黃昏落日一樣能達成，不是只有夜晚才符合。
7. 回傳一個 JSON 物件，**只回 JSON，不要任何說明文字或 markdown 標記**。
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
        raw = await self.llm.ask(prompt)
        plan = self._extract_json(raw)
        # 就算 LLM 沒遵守上面的規則，這裡再用關鍵詞黑名單擋一次
        # （比照 taboo_checker.py 的 Layer 1 粗篩，零額外 LLM 呼叫）。
        plan["elements"] = filter_scene_elements(plan.get("elements", []))
        return plan

    async def _generate_question(
        self,
        step: str,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        elder_response: str = "",
        memories: list[dict] | None = None,
        emotion: str = "happy",
        retry_feedback: str = "",
    ) -> dict:
        """
        生成 STEP1/2/3 問題。
        prompt 格式對齊 dpo/collect_data.py build_inference_prompt（Track A）。
        Returns: {"scene_text": str, "question": str, "covered_w": list[str]}

        retry_feedback: guarded_generate 偵測到上一次輸出違規時傳入的具體說明，
            見 _retry_feedback_section。
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
        topic_str    = "、".join(user.get("topic_category", [])) or user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"

        elder_section = f"\n【長者剛才說的話】\n{elder_response}\n" if elder_response else ""
        memory_section = ""
        if memories:
            memory_section = "\n【過去分享的相關回憶】\n"
            for m in memories:
                memory_section += f"- {m.get('summary', m.get('text', ''))}\n"

        user_content = (
            f"【長者資料】\n"
            f"姓名：{user['name']}\n"
            f"職業背景：{user['main_occupation']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"懷舊治療主題類別：{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"{elder_section}"
            f"{memory_section}"
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
            f"本回合已涵蓋的W：（只填W名稱，不要加括號說明）"
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
        emotion: str = "happy",
        slow_response: bool = False,
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
        topic_str    = "、".join(user.get("topic_category", [])) or user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"

        uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
        uncovered_str = "、".join(uncovered) if uncovered else "無（已全部涵蓋）"

        user_content = (
            f"長者剛才說：\n「{elder_response}」\n"
            f"\n【今日主題】\n{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【尚未涵蓋的W維度】\n{uncovered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion, slow_response)}\n"
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
            f"問題要自然跟著對話走，同時盡量帶出【尚未涵蓋的W維度】中的某一個。\n"
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
        return self._parse_track_c_response(raw)

    async def _generate_supplement_question(
        self,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        target_w: str,
        emotion: str = "happy",
        slow_response: bool = False,
        retry_feedback: str = "",
    ) -> dict:
        """
        W 補問：明確針對尚未涵蓋的 W 維度切入（STEP3 格式）。
        Returns: {"scene_text": str, "question": str}

        retry_feedback: 見 _generate_question 的同名參數說明。
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
        topic_str    = "、".join(user.get("topic_category", [])) or user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"

        user_content = (
            f"【長者資料】\n"
            f"姓名：{user['name']}\n"
            f"職業背景：{user['main_occupation']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"懷舊治療主題類別：{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion, slow_response)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"生成一個【補充問題】，探索還未涵蓋的W維度。{_W_HINT[target_w]}\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"思考：（主題判斷：一句話判斷今日主題最貼近哪個核心主題；"
            f"切入角度：一到兩句話決定這題要用什麼當錨點、往哪個方向問——"
            f"兩段都要寫，不會念給長者聽）\n"
            f"場景文字：（15-30字，幫長者重新聚焦）\n"
            f"問題：（≤15字，開放式，開頭要有畫面中的具體物件）\n"
            f"問題類型：STEP3補問\n"
            f"本回合已涵蓋的W：（只填W名稱，不要加括號說明）"
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
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("思考："):
                # CoT 草稿行：question_5w1h.txt 的【思考欄位】規則要求模型先在這裡
                # 判斷主題方向、選錨點，再輸出正式內容。這行故意不進 result、不會
                # 被念給長者聽，只印出來方便觀察模型的選題邏輯、調整 prompt。
                thinking = line[len("思考："):].strip()
            elif line.startswith("場景文字："):
                result["scene_text"] = line[len("場景文字："):].strip()
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
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
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("scene_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            print(f"[Orchestrator] ⚠ 問題欄位清洗後是空的（本地模型把格式範本原封不動echo回來），"
                  f"退回保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = _FALLBACK_QUESTION
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

    def _parse_track_c_response(self, raw: str) -> dict:
        """解析 Track C（承接語 + 問題）的輸出。"""
        result: dict = {"scene_text": "", "question": ""}
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("承接語："):
                result["scene_text"] = line[len("承接語："):].strip()
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("scene_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            print(f"[Orchestrator] ⚠ Track C 問題欄位清洗後是空的，退回保底問題。"
                  f"原始輸出: {raw[:200]!r}")
            result["question"] = _FALLBACK_QUESTION
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