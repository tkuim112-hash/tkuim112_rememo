"""
使用者個人資料服務客戶端。

⚠️ 目前是 Mock 版本(假資料),等資料庫組的 API 完成後,
   把 MockUserProfileClient 替換成 HTTPUserProfileClient 即可。
   其他程式碼完全不用動,因為兩者介面一致。

對應的個人資料表單欄位(來自治療師後台):
  - 頭貼、姓名、出生年、出生地
  - 主要職業經歷、家人姓名關係
  - 喜好(食物、地點、節慶)
  - 禁忌話題
  - 今日主題(每次療程治療師指定)
"""
from typing import Protocol, TypedDict


class FamilyMember(TypedDict):
    name: str
    relation: str


class Preferences(TypedDict):
    foods: list[str]
    places: list[str]
    festivals: list[str]


class UserProfile(TypedDict):
    """
    這是後端期望從資料庫服務拿到的格式。
    跟資料庫組對接時,他們的 API 要回傳這個結構。
    """
    user_id: str
    name: str
    avatar_path: str | None
    birth_year: int
    birth_place: str
    main_occupation: str
    family: list[FamilyMember]
    preferences: Preferences
    taboos: list[str]
    today_topic: str


class UserProfileClient(Protocol):
    """介面合約 — Mock 版和真實版都要符合"""
    async def get_user(self, user_id: str) -> UserProfile | None: ...
    async def close(self) -> None: ...


class MockUserProfileClient:
    """
    模擬個人資料服務,讓後端可以獨立開發。
    等資料庫組的真實服務上線,把 main.py 裡的 MockUserProfileClient
    換成 HTTPUserProfileClient 即可。
    """

    FAKE_USERS: dict[str, UserProfile] = {
        "user_001": {
            "user_id": "user_001",
            "name": "王大明",
            "avatar_path": None,
            "birth_year": 1945,
            "birth_place": "台中",
            "main_occupation": "紡織廠工人",
            "family": [
                {"name": "王小美", "relation": "女兒"},
                {"name": "陳玉蘭", "relation": "妻子(已故)"},
            ],
            "preferences": {
                "foods": ["肉圓", "麻油雞"],
                "places": ["逢甲夜市"],
                "festivals": ["中秋節"],
            },
            "taboos": ["二兒子車禍"],
            "today_topic": "運動會",
        },
        "user_002": {
            "user_id": "user_002",
            "name": "林春嬌",
            "avatar_path": None,
            "birth_year": 1948,
            "birth_place": "淡水",
            "main_occupation": "家管",
            "family": [
                {"name": "林志明", "relation": "兒子"},
            ],
            "preferences": {
                "foods": ["阿給", "魚丸湯"],
                "places": ["淡水老街"],
                "festivals": ["農曆新年"],
            },
            "taboos": [],
            "today_topic": "童年放學",
        },
    }

    async def get_user(self, user_id: str) -> UserProfile | None:
        """從 Mock 資料庫拿一個使用者"""
        return self.FAKE_USERS.get(user_id)

    async def close(self) -> None:
        """假介面,跟真實版保持一致"""
        pass


# === 之後真實版本會長這樣(先註解著當參考)===
# class HTTPUserProfileClient:
#     def __init__(self, base_url: str):
#         self.client = httpx.AsyncClient(base_url=base_url, timeout=5.0)
#
#     async def get_user(self, user_id: str) -> UserProfile | None:
#         try:
#             resp = await self.client.get(f"/users/{user_id}")
#             if resp.status_code == 404:
#                 return None
#             resp.raise_for_status()
#             return resp.json()
#         except httpx.HTTPError:
#             return None
#
#     async def close(self):
#         await self.client.aclose()