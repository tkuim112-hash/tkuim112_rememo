"""
手動測試腳本：走一次完整1回合的療程流程。

只打真的服務：
  - LLMService  → 原生 Ollama app（.env 的 OLLAMA_HOST=http://localhost:11434，
    OLLAMA_MODEL=cwchang/llama-3-taiwan-8b-instruct:q4_k_m，未經DPO訓練的基底模型）
  - OpenAIImageService → 真的打 OpenAI Images API 生圖

RAG（Qdrant）跟 user_profile（Postgres）用假的 in-memory 實作頂替，不連任何
資料庫，長者資料直接寫死在 TEST_USER。

用法：在 app/ 目錄下執行 `python manual_test_full_round.py`，每一題會印出
scene_text/question，接著在終端機手動輸入「長者」的回答，Enter 空白代表
長者沒回應（沉默逾時）。回合結束（action=end_round 或 end_session）自動停止。
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
from services.image import OpenAIImageService
from privacy.deidentifier import Deidentifier
from orchestrator import TherapyOrchestrator, _NO_RESPONSE_MARKER


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


async def main() -> None:
    print(f"[設定] OLLAMA_HOST={settings.ollama_host}  OLLAMA_MODEL={settings.ollama_model}")
    print("[提醒] 這是本機原生 Ollama 的基底模型，不是DPO微調後的 rememo-llama3，"
          "問題生成品質不代表正式部署行為。\n")

    llm = LLMService()
    image = OpenAIImageService()
    # image.py 的 output_dir 寫死容器內路徑 /media/images，本機（非docker）
    # 對應到 repo 的 ./media/images，這裡覆寫成正確的本機路徑。
    image.output_dir = Path(__file__).resolve().parent.parent / "media" / "images"
    image.output_dir.mkdir(parents=True, exist_ok=True)

    orchestrator = TherapyOrchestrator(
        llm=llm,
        image=image,
        rag=FakeRAGClient(),
        user_profile=FakeUserProfileClient(TEST_USER),
        deidentifier=Deidentifier(),
    )

    try:
        result = await orchestrator.start_round(
            user_id=TEST_USER["user_id"],
            session_id="manual-test-session",
            round_number=1,
        )
        _print_turn("開場", result)

        while result["state"] is not None:
            elder_response = input("\n長者回答（直接 Enter 表示沒回應）：").strip()
            if not elder_response:
                elder_response = _NO_RESPONSE_MARKER
            result = await orchestrator.process_response(
                elder_response=elder_response,
                state=result["state"],
            )
            _print_turn("回應", result)

        print("\n=== 回合結束 ===")
    finally:
        await llm.close()
        await image.close()


if __name__ == "__main__":
    asyncio.run(main())
