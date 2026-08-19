"""
手動測試腳本：走一次完整療程（三回合＋心得環節）。

只打真的服務：
  - LLMService  → 原生 Ollama app（.env 的 OLLAMA_HOST=http://localhost:11434，
    OLLAMA_MODEL=cwchang/llama-3-taiwan-8b-instruct:q4_k_m，未經DPO訓練的基底模型）

不生圖：FakeImageService 頂替 OpenAIImageService，不打 OpenAI Images API，
直接回空字串——orchestrator._start_scene_after_detail 生圖失敗本來就有既有
的降級路徑（見該處說明），這裡就是刻意觸發那條路徑，只測LLM文字生成。

RAG（Qdrant）、user_profile（Postgres）用假的 in-memory 實作頂替，不連任何
資料庫，長者資料直接寫死在 TEST_USER。回合之間承接的 carryover、主題紀錄
（原本存在 Redis，見 app/routers/session.py _cache_round_carryover／
_append_session_topic）這裡改用本地變數頂替，組法照抄該檔
session_respond 對 action=="end_round"/"end_session" 的處理。

三回合跑完後接著跑心得環節（app/services/closing_templates.py：
build_closing_invitation 產生的收尾語已經呼應過回合3的回答、接系統整合肯定
與感謝語，長者對這句開場邀請語的回答只記錄不再另外生成收尾訊息），跟正式
部署行為一致，全程只用LLM，不生圖。

用法：在 app/ 目錄下執行 `python manual_test_full_round.py`，每一題會印出
scene_text/question，接著在終端機手動輸入「長者」的回答，Enter 空白代表
長者沒回應（沉默逾時）。全部跑完（心得環節問完最後一題並記錄回答後）自動停止。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# orchestrator.py 頂層 import 了 services.user_profile_db → db.session，
# db.session 建立 async engine 時會在 import 階段就檢查 DATABASE_URL 是否
# 存在（create_async_engine 本身是lazy connection，不會真的連線，只是缺這個
# 環境變數會直接 raise）。這裡不會真的用到 DB，塞一個假值滿足檢查即可。
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost:5432/unused")

import asyncio

from config import settings
from services.llm import LLMService
from services.closing_templates import build_closing_invitation
from privacy.deidentifier import Deidentifier
from orchestrator import TherapyOrchestrator, _NO_RESPONSE_MARKER

SESSION_ID = "manual-test-session"


class FakeUserProfileClient:
    """頂替 DBUserProfileClient，不連 Postgres，直接回傳寫死的長者資料。"""

    def __init__(self, user: dict):
        self._user = user

    async def get_user(self, user_id: str) -> dict | None:
        return self._user if user_id == self._user["user_id"] else None


class FakeRAGClient:
    """頂替 RealRAGClient，不連 Qdrant。retrieve_memories 回空list，orchestrator
    會自然退回「沒有記憶」的生圖路徑；save_memory 單純吃掉不做事。"""

    async def retrieve_memories(self, user_id: str, query: str, limit: int = 3) -> list[dict]:
        return []

    async def save_memory(self, **kwargs) -> None:
        pass


class FakeImageService:
    """頂替 OpenAIImageService，不打 OpenAI Images API。orchestrator._start_scene_
    after_detail 呼叫 self.image.generate(...) 本身就包在 try/except 裡（生圖失敗
    不影響對話主流程，見該處說明），直接回空字串就能讓它自然走「這回合沒有配圖」
    的既有降級路徑，不用真的生一張圖。"""

    def __init__(self) -> None:
        self.output_dir = Path(".")

    async def generate(self, prompt: str, session_id: str, round_number: int) -> str:
        return ""

    async def close(self) -> None:
        pass


TEST_USER = {
    "user_id": "manual-test-user",
    "name": "陳阿嬤",
    "birth_year": 1945,
    "birth_place": "台南",
    "main_occupation": "裁縫師",
    "taboos": [],
    "today_topic": "中秋節",
    "preferences": "聽廣播",
}


def _print_turn(label: str, result: dict) -> None:
    print(f"\n=== {label}（action={result.get('action')}）===")
    if result.get("scene_text"):
        print(f"[場景/承接語] {result['scene_text']}")
    if result.get("question"):
        print(f"[問題] {result['question']}")
    if result.get("image_path"):
        print(f"[圖片] {result['image_path']}")


async def _ask_elder(prompt: str = "\n長者回答（直接 Enter 表示沒回應）：") -> str:
    elder_response = input(prompt).strip()
    return elder_response or _NO_RESPONSE_MARKER


async def run_round(
    orchestrator: TherapyOrchestrator, round_number: int, carryover: dict | None,
) -> tuple[dict, dict, str]:
    """跑完一整個回合，回傳 (最後一次 result, 最後一次非None的state, 長者最後一句回答)。"""
    if round_number == 1:
        result = await orchestrator.start_round(
            user_id=TEST_USER["user_id"], session_id=SESSION_ID, round_number=1,
        )
    else:
        result = await orchestrator.start_round(
            user_id=TEST_USER["user_id"], session_id=SESSION_ID,
            round_number=round_number, carryover=carryover,
        )
    _print_turn(f"回合{round_number} 開場", result)

    state = result["state"]
    last_elder_response = ""
    while result["state"] is not None:
        last_elder_response = await _ask_elder()
        state = result["state"]
        result = await orchestrator.process_response(
            elder_response=last_elder_response, state=state,
        )
        _print_turn(f"回合{round_number} 回應", result)

    return result, state, last_elder_response


async def main() -> None:
    print(f"[設定] OLLAMA_HOST={settings.ollama_host}  OLLAMA_MODEL={settings.ollama_model}")
    print("[提醒] 這是本機原生 Ollama 的基底模型，不是DPO微調後的 rememo-llama3，"
          "問題生成品質不代表正式部署行為。\n")

    llm = LLMService()
    image = FakeImageService()

    orchestrator = TherapyOrchestrator(
        llm=llm,
        image=image,
        rag=FakeRAGClient(),
        user_profile=FakeUserProfileClient(TEST_USER),
        deidentifier=Deidentifier(),
    )

    try:
        topics: list[str] = []
        carryover: dict | None = None
        round3_response = ""

        for round_number in (1, 2, 3):
            result, last_state, last_elder_response = await run_round(
                orchestrator, round_number, carryover,
            )
            action = result.get("action")

            # carryover／主題紀錄組法照抄 app/routers/session.py session_respond
            # 對 action=="end_round" 的處理（原本寫進 Redis，這裡只是本地變數）。
            if action == "end_round" and round_number in (1, 2):
                carryover = {"last_elder_response": last_elder_response, "emotion": ""}
                if round_number == 1:
                    carryover.update({
                        "scene_elements": last_state["scene_elements"],
                        "scene_composition": last_state["scene_composition"],
                        "pre_image_detail": last_state.get("pre_image_detail", ""),
                        "topic_category": last_state.get("topic_category"),
                        "topic_senses": last_state.get("topic_senses", []),
                        "round1_covered_w": last_state.get("covered_w", []),
                        # 照抄 session.py round==1 carryover 的
                        # round1_last_question（2026-08-18稽核，第四次，使用者
                        # 提案）——round 1 最後一題問了什麼，round 2 開場生成時
                        # 當參考資訊。原本這裡帶的是round 1整份round_qa_log，
                        # 實測回報累積的內容越長承接語／問題品質越差，已改成
                        # 只帶最後一題的單一字串。
                        "round1_last_question": last_state.get("last_question_text", ""),
                        # 照抄 session.py round==1 carryover 的
                        # round1_covered_senses（2026-08-18新增，見
                        # orchestrator.py _start_round2_free_followup 的
                        # known_senses 說明）——round 1 已涵蓋的感官，避免
                        # round 2 開場又問一次已經答過的感官（實測案例：
                        # round 1 問過「七星潭邊有什麼味道」，round 2 開場
                        # 原句又問了一次）。
                        "round1_covered_senses": last_state.get("covered_senses", []),
                    })
                if last_state.get("topic_category"):
                    topics.append(last_state["topic_category"])

            if action == "end_session":
                round3_response = last_elder_response
                break

        # 三回合結束，接心得環節（見 app/services/closing_templates.py，
        # app/routers/session.py session_respond 對 end_session 的處理）。
        # 收尾語呼應長者剛才在回合3的回答，emotion 這裡沒有 Kinect 資料，傳空值。
        print("\n=== 心得環節 ===")
        invitation = await build_closing_invitation(topics, round3_response, "", llm)
        if invitation["scene_text"]:
            print(f"[場景/承接語] {invitation['scene_text']}")
        if invitation["thanks_text"]:
            print(f"[感謝語] {invitation['thanks_text']}")
        print(f"[問題] {invitation['question']}")
        # 承接語＋系統整合肯定＋感謝語已經在上面的開場邀請語呼應過回合3的回答了
        # （見 build_closing_invitation），長者這題的回答只需要記錄，不再另外
        # 生成第二段收尾訊息（見 app/routers/session.py session_closing 說明）。
        input("\n長者回答（直接 Enter 表示沒回應）：").strip()
        print("\n=== 療程全部結束 ===")
    finally:
        await llm.close()
        await image.close()


if __name__ == "__main__":
    asyncio.run(main())
