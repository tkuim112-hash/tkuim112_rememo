"""
療程編排器(Orchestrator)。

把 LLM、STT、Image、RAG、UserProfile、Deidentifier 串起來,
完成一個完整的療程開場流程。

⚠️ 今晚版本(MVP):
   - 只做「開場流程」,不做多輪對話、回合切換、5W1H 追蹤
   - 目的:讓 RAG 對接時看到完整呼叫鏈
   - 之後會擴充成完整狀態機
"""
import json
from services.llm import LLMService
from services.image import StabilityImageService
from services.rag_client import MockRAGClient
from services.user_profile_client import MockUserProfileClient
from privacy.deidentifier import Deidentifier


class TherapyOrchestrator:
    """指揮所有 service,完成一場療程的開場。"""

    def __init__(
        self,
        llm: LLMService,
        image: StabilityImageService,
        rag: MockRAGClient,
        user_profile: MockUserProfileClient,
        deidentifier: Deidentifier,
    ):
        self.llm = llm
        self.image = image
        self.rag = rag
        self.user_profile = user_profile
        self.deidentifier = deidentifier

    async def start_session_opening(
        self,
        user_id: str,
        session_id: str,
    ) -> dict:
        """
        執行療程開場的完整流程,回傳所有產出物。
        
        Args:
            user_id: 長者 ID
            session_id: 本次療程 ID(由前端產生)
        
        Returns:
            dict containing:
              - user_name: 顯示用名字
              - scene_text: 給長者看的場景文字
              - scene_elements: 圖片元素清單(後續問題會用)
              - image_path: 生成的圖片路徑
              - question: 第一個問題文字
              - memories_used: RAG 檢索到的相關記憶(可能為空)
        """
        # ════════ STEP 1: 拿個人資料 ════════
        print(f"[Orchestrator] STEP 1: 取得 {user_id} 的個人資料")
        user = await self.user_profile.get_user(user_id)
        if not user:
            raise ValueError(f"找不到使用者: {user_id}")
        
        print(f"  → 使用者: {user['name']},今日主題: {user['today_topic']}")
        
        # ════════ STEP 2: LLM 規劃圖片內容 ════════
        print(f"[Orchestrator] STEP 2: LLM 規劃圖片內容")
        image_plan = await self._plan_image(user)
        print(f"  → 圖片元素: {image_plan['elements']}")
        print(f"  → 場景文字: {image_plan['scene_text'][:30]}...")
        
        # ════════ STEP 3: 脫敏圖片 prompt ════════
        print(f"[Orchestrator] STEP 3: 脫敏 prompt")
        raw_prompt = image_plan["image_prompt"]
        safe_prompt = self.deidentifier.desensitize_text(
            raw_prompt,
            taboos=user["taboos"]
        )
        print(f"  → 脫敏前: {raw_prompt[:50]}...")
        print(f"  → 脫敏後: {safe_prompt[:50]}...")
        
        # ════════ STEP 4: Stability 生圖 ════════
        print(f"[Orchestrator] STEP 4: 呼叫 Stability 生圖")
        image_path = await self.image.generate(
            prompt=safe_prompt,
            session_id=session_id,
            round_number=1,
        )
        print(f"  → 圖片存到: {image_path}")
        
        # ════════ STEP 5: RAG 檢索相關記憶 ════════
        print(f"[Orchestrator] STEP 5: RAG 檢索相關記憶")
        memories = await self.rag.retrieve_memories(
            user_id=user_id,
            query=f"{user['today_topic']} {user['main_occupation']}",
            limit=3,
        )
        print(f"  → 檢索到 {len(memories)} 條相關記憶")
        
        # ════════ STEP 6: LLM 生成第一個問題 ════════
        print(f"[Orchestrator] STEP 6: LLM 生第一個問題")
        question = await self._generate_first_question(
            user=user,
            scene_elements=image_plan["elements"],
            memories=memories,
        )
        print(f"  → 問題: {question}")
        
        # ════════ 回傳完整結果 ════════
        return {
            "user_name": user["name"],
            "today_topic": user["today_topic"],
            "scene_text": image_plan["scene_text"],
            "scene_elements": image_plan["elements"],
            "image_path": image_path,
            "question": question,
            "memories_used": memories,
        }

    # ────────── 私有方法 ──────────

    async def _plan_image(self, user: dict) -> dict:
        """
        請 LLM 規劃這次的圖片內容。
        
        回傳:
          - scene_text: 給長者看的中文場景描述
          - elements: 圖片元素清單(供後續問題定錨)
          - image_prompt: 送 Stability 用的英文 prompt
        """
        prompt = f"""你是懷舊療法的圖片規劃師。請根據長者資料規劃一張場景圖。

【長者資料】
姓名:{user['name']}
年齡:{2026 - user['birth_year']} 歲
出生地:{user['birth_place']}
職業背景:{user['main_occupation']}
今日主題:{user['today_topic']}

【任務】
規劃一張水彩風格的回憶場景圖,符合主題,要能引發長者的回憶。

【嚴格規定】
回傳一個 JSON 物件,**只回 JSON,不要任何說明文字或 markdown 標記**。
格式:
{{
  "scene_text": "30-60 字的中文場景描述,給長者看",
  "elements": ["元素1", "元素2", "元素3", "元素4"],
  "image_prompt": "英文 prompt 給 Stability AI,包含水彩風格、年代、場景、元素"
}}

範例(主題=運動會):
{{
  "scene_text": "黃昏的小學操場,孩子們在大隊接力,塵土飛揚,加油聲此起彼落",
  "elements": ["操場", "黃昏", "大隊接力", "加油聲"],
  "image_prompt": "watercolor painting style, 1940s Taiwan elementary school sports day, relay race, children running on dirt track, dusk light, nostalgic warm tones, no text"
}}
"""
        raw = await self.llm.ask(prompt)
        return self._extract_json(raw)

    async def _generate_first_question(
        self,
        user: dict,
        scene_elements: list[str],
        memories: list[dict],
    ) -> str:
        """根據圖片元素 + 個人資料生第一個問題。"""
        
        # 整理記憶上下文
        memory_context = ""
        if memories:
            memory_context = "\n【過去這位長者曾分享的相關回憶】\n"
            for m in memories:
                memory_context += f"- {m.get('summary', m.get('text', ''))}\n"
        
        prompt = f"""你是溫柔的懷舊療法引導師,正在陪伴日間照護中心的長者。

【長者背景】
姓名:{user['name']}
職業背景:{user['main_occupation']}
今日主題:{user['today_topic']}

【眼前的場景圖元素】
{', '.join(scene_elements)}
{memory_context}
【禁忌話題(絕對不可提及)】
{', '.join(user['taboos']) if user['taboos'] else '無'}

【任務】
請設計**第一個切入問題**,引導長者進入回憶。

【規則】
1. 一次只問一件事
2. 用「您」稱呼,語氣溫和緩慢
3. 問題的第一句必須包含畫面看得到的具體物件作為錨點
4. 開放式問題(不要是非題)
5. 不超過 25 個字
6. 優先用 Where 或 What 切入(最容易回答)
7. 結合長者個人背景

【嚴格規定】
**只回問題本身,不要任何前綴、說明、引號或 markdown**。
直接回中文問題文字。
"""
        question = await self.llm.ask(prompt)
        # 清理可能的多餘字符
        question = question.strip().strip('"').strip("「").strip("」").strip()
        return question

    def _extract_json(self, text: str) -> dict:
        """
        從 LLM 回應中萃取 JSON。
        LLM 有時會包 markdown 標記,要清理。
        """
        # 移除可能的 markdown ```json 標記
        text = text.strip()
        if text.startswith("```"):
            # 移除第一行 (```json 或 ```)
            text = text.split("\n", 1)[1] if "\n" in text else text
            # 移除最後的 ```
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            # 嘗試找到 JSON 物件的範圍
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end+1])
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"LLM 沒有回有效的 JSON: {text[:200]}") from e