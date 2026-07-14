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
生成完成後，透過 app/safety/crisis_detector.py 的 guarded_generate() 包裝，
檢查 AI 輸出是否觸及 Patient.taboo_words 相關話題（字面 + 語意兩層），
違規時重新生成或退回安全保底語句。詳見 app/safety/taboo_checker.py。
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
}


def _emotion_guidance(emotion: str) -> str:
    return _EMOTION_GUIDANCE.get(emotion, _EMOTION_GUIDANCE["happy"])


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

        image_path = await self.image.generate(
            prompt=safe_prompt,
            session_id=session_id,
            round_number=round_number,
        )
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

        covered_w   = list(state["covered_w"])
        skipped_w   = list(state["skipped_w"])
        last_type   = state["last_question_type"]
        last_w      = state.get("last_w_asked", "")
        scene_els   = state["scene_elements"]

        print(f"[Orchestrator] process_response | round={state['round']} "
              f"last={last_type} covered={covered_w} skipped={skipped_w}")

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
                    user, scene_els, covered_w, skipped_w, elder_response, state, emotion
                )

        # ── STEP2：自由對話中背景追蹤 W 覆蓋 ────────────────────
        if not quick_end:
            newly_covered = await self._detect_covered_w(elder_response, covered_w)
            for w in newly_covered:
                if w not in covered_w:
                    covered_w.append(w)
            if newly_covered:
                print(f"  → 自然涵蓋 W: {newly_covered}，covered={covered_w}")

        # ── 5W1H 全部涵蓋 → 結束回合 ─────────────────────────────
        if not self._next_uncovered_w(covered_w, skipped_w):
            print("  → 5W1H 全部涵蓋，結束回合")
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
                user=user, scene_elements=scene_els, covered_w=covered_w,
                skipped_w=skipped_w, elder_response=elder_response, emotion=emotion,
            )
            new_state = {
                **state,
                "covered_w": covered_w,
                "skipped_w": skipped_w,
                "last_question_type": "open",
                "last_w_asked": "",
            }
            return {
                "action": "open_followup",
                "scene_text": result["scene_text"],
                "question": result["question"],
                "state": new_state,
            }
        else:
            # STEP3：話題結束，切入未問的 W
            return await self._next_step_or_end(
                user, scene_els, covered_w, skipped_w, elder_response, state, emotion
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
    ) -> dict:
        result = await guarded_generate(
            self._generate_supplement_question,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            user=user, scene_elements=scene_els, covered_w=covered_w, target_w=target_w,
            emotion=emotion,
        )
        print(f"  → 補問 W({target_w}): {result['question']}")
        new_state = {
            **state,
            "covered_w": covered_w,
            "skipped_w": skipped_w,
            "last_question_type": "supplement_w",
            "last_w_asked": target_w,
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
                    text_keys=("closing_text", "question"),
                    fallback={
                        "closing_text": "謝謝您今天的分享，辛苦了。",
                        "question": "今天過得還好嗎？",
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
    ) -> dict:
        """話題結束後：找下一個W補問，或結束回合。Why 需長者狀態良好才問。"""
        next_w = self._next_uncovered_w(covered_w, skipped_w)
        if not next_w:
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
            user, scene_els, covered_w, skipped_w, next_w, state, emotion
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
    ) -> dict:
        """收尾引導：三回合結束後帶領長者從回憶回到現實，詢問感受或正向回憶。"""
        system_content = (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "三回合懷舊療程剛結束，你要溫柔地帶領長者回到現實，"
            "並以一句輕柔的問題詢問他們現在的感受或正向回憶，為今天的療程畫上句點。"
        )

        last_response_section = (
            f"\n【長者最後說的話】\n{elder_response}\n" if elder_response else ""
        )
        user_content = (
            f"【長者資料】\n"
            f"姓名：{user['name']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"{last_response_section}"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【任務】\n"
            f"三回合療程剛剛結束。請設計收尾引導，需包含：\n"
            f"1. 收尾語：1-2句，溫暖肯定長者今天的分享，語氣輕鬆自然，不誇張\n"
            f"2. 問題：一句輕柔的開放式問題（≤15字），詢問以下其中一項：\n"
            f"   - 現在的感受或心情（例：「現在心裡感覺怎麼樣呢？」）\n"
            f"   - 今天最讓長者開心的回憶（例：「今天哪個故事讓您最開心？」）\n"
            f"   - 想帶走的正向感受（例：「今天有什麼讓您覺得溫暖的事？」）\n"
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
回傳一個 JSON 物件，**只回 JSON，不要任何說明文字或 markdown 標記**。
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
        return self._extract_json(raw)

    async def _generate_question(
        self,
        step: str,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        elder_response: str = "",
        memories: list[dict] | None = None,
        emotion: str = "happy",
    ) -> dict:
        """
        生成 STEP1/2/3 問題。
        prompt 格式對齊 dpo/collect_data.py build_inference_prompt（Track A）。
        Returns: {"scene_text": str, "question": str, "covered_w": list[str]}
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "問題必須念起來自然、溫和、不超過15個字，且開頭要包含畫面中看得到的具體物件。"
            "絕對不在輸出中加任何括號說明或格式標記。"
            "絕對不用是非題。"
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
            f"\n【輸出格式】\n"
            f"場景文字：（30-60字，給長者聽的場景描述）\n"
            f"問題：（≤15字，開放式，開頭要有畫面中的具體物件）\n"
            f"問題類型：{_STEP_TYPE_LABEL[step]}\n"
            f"本回合已涵蓋的W："
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_question_response(raw)

    async def _generate_open_followup(
        self,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        skipped_w: list[str],
        elder_response: str,
        emotion: str = "happy",
    ) -> dict:
        """
        STEP2 開放式追問（Track C）：承接長者情緒，自然延伸問題，順道帶出未涵蓋的W。
        prompt 格式對齊 dpo/collect_data.py build_track_c_inference_prompt。
        Returns: {"scene_text": str, "question": str}
        """
        system_content = (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "每次聽完長者說話，先用1-2句溫暖的話承接他的情緒，再自然問下一個問題。"
        )

        elements_str = "、".join(scene_elements)
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"

        uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
        uncovered_str = "、".join(uncovered) if uncovered else "無（已全部涵蓋）"

        user_content = (
            f"長者剛才說：\n「{elder_response}」\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【尚未涵蓋的W維度】\n{uncovered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n請先承接長者的情緒（1-2句，符合他當下的心情），"
            f"再順著長者說的話問下一個問題（≤15字，開頭含畫面元素，開放式）。\n"
            f"問題要自然跟著對話走，同時盡量帶出【尚未涵蓋的W維度】中的某一個。\n"
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
    ) -> dict:
        """
        W 補問：明確針對尚未涵蓋的 W 維度切入（STEP3 格式）。
        Returns: {"scene_text": str, "question": str}
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "問題必須念起來自然、溫和、不超過15個字，且開頭要包含畫面中看得到的具體物件。"
            "絕對不在輸出中加任何括號說明或格式標記。"
            "絕對不用是非題。"
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
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"生成一個【補充問題】，探索還未涵蓋的W維度。{_W_HINT[target_w]}\n"
            f"\n【輸出格式】\n"
            f"場景文字：（15-30字，幫長者重新聚焦）\n"
            f"問題：（≤15字，開放式，開頭要有畫面中的具體物件）\n"
            f"問題類型：STEP3補問\n"
            f"本回合已涵蓋的W："
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_question_response(raw)

    # ══════════════════════════════════════════════════════════════
    # 私有：解析 LLM 輸出
    # ══════════════════════════════════════════════════════════════

    def _parse_question_response(self, raw: str) -> dict:
        """解析 STEP1/2/3 的結構化輸出。"""
        result: dict = {"scene_text": "", "question": "", "covered_w": []}
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("場景文字："):
                result["scene_text"] = line[len("場景文字："):].strip()
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
            elif line.startswith("本回合已涵蓋的W："):
                w_raw = line[len("本回合已涵蓋的W："):].strip()
                result["covered_w"] = [
                    w.strip()
                    for w in w_raw.replace("，", "、").split("、")
                    if w.strip()
                ]
        if not result["question"]:
            result["question"] = raw.strip()
        # 清洗：移除 LLM 偶發輸出的格式提示和 W 標籤
        for key in ("scene_text", "question"):
            val = result[key]
            val = re.sub(r"（W:\s*\[.*?\]）", "", val)  
            val = re.sub(r"（\d+字以內）", "", val)       
            val = re.sub(r"（[A-Z]+\d+[^）]*）", "", val) 
            result[key] = val.strip()
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