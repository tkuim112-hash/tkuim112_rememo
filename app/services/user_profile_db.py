"""
DB 版 UserProfileClient。
跟 MockUserProfileClient 同一個介面,從 PostgreSQL 撈真實病患資料。
回傳的 dict key 刻意跟 mock 一致,所以 orchestrator 不用大改。
"""
from sqlalchemy import select
from db.session import AsyncSessionLocal
from db.models import Patient


class DBUserProfileClient:
    """從 patients 表撈個人資料。"""
    
    async def get_user(self, user_id: str) -> dict | None:
        """
        根據 user_id 撈病患資料。
        
        Args:
            user_id: PATIENTS.id 的字串形式(例如 "1", "42")
        
        Returns:
            dict,key 跟 mock 一致:
              user_id, name, birth_year, birth_place, main_occupation, taboos
            ⚠️ 注意:不含 today_topic,由 main.py 從 start_scene 注入。
            找不到或 user_id 非合法整數時回 None。
        """
        try:
            patient_pk = int(user_id)
        except (ValueError, TypeError):
            return None
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Patient).where(Patient.id == patient_pk)
            )
            patient = result.scalar_one_or_none()
        
        if not patient:
            return None
        
        # taboo_words 是 TEXT,假設以逗號分隔 → 解成 list
        taboos = []
        if patient.taboo_words:
            taboos = [w.strip() for w in patient.taboo_words.split(",") if w.strip()]
        
        # ⚠️ key 對齊 mock,避免動 orchestrator
        return {
            "user_id": str(patient.id),
            "name": patient.name,
            "birth_year": patient.birth_year,
            "birth_place": patient.hometown or "",     # DB: hometown → mock: birth_place
            "main_occupation": patient.occupation,       # DB: occupation → mock: main_occupation
            "taboos": taboos,
        }
    
    async def close(self):
        """目前不持有 session,留空但保留介面相容性。"""
        pass