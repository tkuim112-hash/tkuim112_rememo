"""
在既有的體驗用治療師帳號（DEMO_EMAIL，需先用 create_organization.py 開通好）
所屬機構底下，塞入一批假的個案/療程資料，讓體驗者登入治療師後台就能直接點進
個案列表、療程紀錄、趨勢分析、追蹤與備註等頁面看，不需要真的跑一次 Unity 療程。

這支腳本不建帳號、不改密碼、不刪機構，只動 PATIENTS 裡列出的示範個案。

假資料的分布刻意比照真實療程資料（2026-11 對 DB 內既有療程做的統計）：
- 五項評分都是 1-4 分（見 routers/session.py _score_*），情緒分直接由情緒決定
  （低落 1／焦躁 2／亢奮 3／適當 4），互動頻率最常落在 2 分 short_replies，
  總分大多 15-19。
- 情緒以「適當」為大宗；每回合表情正向度平均約 40%、專注度約 60%。
- 每場 1-3 個一般回合（多數 3 個），第一回合有 1-2 題生圖前引導問題
  （stage='pre_image'）＋看圖問題，最後多半有一個「心得」回合。
- 暖身各卡指標差異很大（扭腰關節角度低、擴胸高），用 CARD_BASELINES 當基準。
- 故事摘要是「長者今天分享了…整體而言，長者…」的 LLM 摘要口吻；治療師備註
  多數是空的。

可重複執行：每次執行都會先刪掉該機構內同名的示範個案（patients 的 ON DELETE
CASCADE 會連帶清掉療程、回合、暖身結果、待辦與備註），再整批重建；療程日期以
「今天」往回推，重跑一次就能讓資料保持在近期。

用法：
    docker exec tku-care-app-1 python seed_demo_account.py
"""
import asyncio
import json
import random
import shutil
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from db.models import (
    PatientNote,
    PatientTodo,
    Patient,
    RoundExchange,
    Therapist,
    TherapyRound,
    TherapySession,
    WarmupCardResult,
)
from db.session import AsyncSessionLocal

DEMO_EMAIL = "tkuim112@gmail.com"

_MEDIA_IMAGES = Path("/media/images")
# 示範場次的情境圖直接沿用既有療程生成過的廚房/家常菜圖片：每張各複製一份到
# demo_scene_<n>/ 給所有示範場次共用（每張近 1MB，場次一多就不適合各存一份），
# 刪掉示範資料時也不會動到原圖。
_SOURCE_IMAGES = [
    "9477f74a-3047-483a-9a63-8b3c1de72c77",  # 灶腳炒菜、窗邊陽光
    "5d502cc3-f82b-44c6-89fa-b8a3947d7846",  # 瓦斯爐、一桌小菜
    "6722b226-bad9-4123-b439-19e1a492dd21",  # 圓桌上的家常菜
    "a43aea4f-a490-4c22-979e-2563c2927bca",  # 白蘿蔔、燉湯
]

# 之前版本用的全名，重跑時一併清掉
_LEGACY_NAMES = ["陳秀蘭", "王國雄", "林美玉"]

# 真實資料各卡（非略過）的平均：(關節角度/到位程度, 平滑度, 對稱性, 秒數)
CARD_BASELINES = {
    "arm_raise": (85, 66, 94, 9.2),
    "leg_kick": (57, 66, 73, 4.8),
    "march_in_place": (68, 51, 90, 14.3),
    "touch_knees": (69, 35, 82, 14.5),
    "arm_circle": (74, 70, 82, 6.8),
    "waist_twist": (40, 35, 55, 14.6),
    "chest_expand": (88, 50, 81, 8.5),
}

# 自動補場次時，每個平日有療程的機率
SESSION_RATE = 0.8

# uuid5 讓每個示範場次每次重跑都得到同一個 session_uuid
_UUID_NS = uuid.UUID("6f1c2a52-5d1e-4c35-9a57-3e0c7f0d2b11")

_NO_RESPONSE = "（長者未回應）"
_FIRST_SCENE = "我把你剛剛說的故事畫成一張圖了，想給你看看。"
_IMAGE_QUESTION = "你看看這張圖，想到什麼都可以跟我說。"


# ---------------------------------------------------------------------------
# 話題
#
# 每個話題是一場療程的內容骨架：
#   opener / a1      生圖前第一題與回答（stage=pre_image）
#   pre2             生圖前第二題 (問, 答)，可為 None
#   img_a            看圖後的回答
#   follow           第二回合的追問 [(問, 答), ...]
#   closing          第三回合 (問, 答)，None 則從 _CLOSINGS 依情緒挑
#   short            低落時第一題的簡短回答
#   share / detail   故事摘要用：「長者今天分享了{share}。{detail}。整體而言…」
#   sums             第一、二、三回合的一句話摘要
#
# 個案專屬話題（PATIENTS 的 featured）額外可以有：
#   round_emotions   每回合各自的情緒（不給就依場次情緒推）
#   note             治療師備註
# ---------------------------------------------------------------------------

GENERIC_TOPICS = [
    dict(opener="說到過年，你有什麼故事想跟我分享呢？像是想到一個人，或者一件事，都可以。",
         a1="以前過年全家一起包水餃，要包好幾百顆。", short="過年很熱鬧。",
         pre2=("那時候身邊有誰陪著你呢？", "我媽媽跟兄弟姊妹，大家圍在桌子旁邊。"),
         img_a="這個灶腳跟我們以前的很像。",
         follow=[("包水餃的時候，誰包得最好看？", "我媽媽，她包的像元寶一樣。"),
                 ("水餃裡面都包什麼餡呢？", "高麗菜跟豬肉，有時候會包一個硬幣進去。")],
         closing=None, share="除夕全家一起包水餃的回憶", detail="長者提到媽媽包的水餃像元寶一樣漂亮，還會在裡面包硬幣討吉利",
         sums=["長者回憶除夕全家一起包水餃。", "長者說媽媽包的水餃像元寶，還會包硬幣。"]),
    dict(opener="說到去菜市場，我很好奇，這件事最讓你開心的是哪個部分呢？",
         a1="以前每天早上都去菜市場，跟賣菜的都很熟。", short="以前常去。",
         pre2=("那個時候，通常是什麼時間做這件事呢？", "一大早，六點多就去了。"),
         img_a="這些菜看起來很新鮮。",
         follow=[("你最常跟哪一攤買呢？", "巷口那個阿婆，她都會多送我一把蔥。")],
         closing=None, share="以前每天上菜市場買菜的生活", detail="長者提到和攤販都很熟，賣菜阿婆常會多送一把蔥",
         sums=["長者回憶每天一早去菜市場買菜。", "長者說熟識的攤販會多送蔥。"]),
    dict(opener="說到端午節，你有什麼故事想跟我分享呢？",
         a1="以前每年都包好幾十顆粽子，掛在竹竿上。", short="有包粽子。",
         pre2=("那時候是在哪裡包的呢？", "在家裡的灶腳，地上擺一大盆。"),
         img_a="那個大鍋子就是用來煮粽子的。",
         follow=[("你包的粽子裡面會放什麼料？", "香菇、蛋黃、花生，還有滷肉。"),
                 ("包粽子最難的是哪一步？", "綁繩子，綁不緊就會散掉。")],
         closing=None, share="以前端午節包粽子的回憶", detail="長者提到一次包幾十顆粽子掛在竹竿上，內餡有香菇、蛋黃、花生和滷肉",
         sums=["長者回憶端午節包幾十顆粽子。", "長者說明粽子內餡，提到綁繩最難。"]),
    dict(opener="說到中秋節，你們家都怎麼過呢？",
         a1="全家在門口烤肉，小孩子拿柚子皮當帽子。", short="會烤肉。",
         pre2=None, img_a="桌上的菜好像我們中秋那天。",
         follow=[("中秋節還會吃什麼呢？", "月餅啊，我最喜歡吃綠豆椪。")],
         closing=None, share="中秋節全家烤肉的回憶", detail="長者提到孩子們會戴柚子皮帽子，自己最喜歡吃綠豆椪",
         sums=["長者回憶中秋全家在門口烤肉。", "長者說最喜歡吃綠豆椪。"]),
    dict(opener="說到冬至，你有什麼故事想跟我分享呢？",
         a1="冬至一定要吃湯圓，吃了才長一歲。", short="要吃湯圓。",
         pre2=("那時候是誰煮給你吃的呢？", "我阿嬤，她都自己搓。"),
         img_a="鍋子在冒煙，好像在煮湯圓。",
         follow=[("你喜歡吃甜的還是鹹的湯圓？", "鹹的，加茼蒿跟香菇，很香。")],
         closing=None, share="冬至吃湯圓的習俗", detail="長者提到小時候由阿嬤親手搓湯圓，自己偏好加茼蒿和香菇的鹹湯圓",
         sums=["長者提到冬至吃湯圓長一歲。", "長者說喜歡吃鹹湯圓。"]),
    dict(opener="說到媽媽煮的菜，我很好奇，你最懷念的是哪一道呢？",
         a1="我媽媽煎的菜脯蛋，到現在都還記得那個味道。", short="菜脯蛋。",
         pre2=("那個時候，通常是什麼時間吃到的呢？", "晚餐，配稀飯。"),
         img_a="以前就是在這種爐子上煎的。",
         follow=[("那個菜脯蛋有什麼特別的地方？", "菜脯是自己曬的，比較鹹比較香。")],
         closing=None, share="對媽媽煎的菜脯蛋的懷念", detail="長者提到菜脯是家裡自己曬的，晚餐配稀飯最對味",
         sums=["長者懷念媽媽煎的菜脯蛋。", "長者說菜脯是自家曬的。"]),
    dict(opener="說到以前帶便當，你有什麼故事想跟我分享呢？",
         a1="以前都用鐵便當盒，中午放在蒸飯箱蒸。", short="有帶便當。",
         pre2=None, img_a="這些小菜很適合放便當。",
         follow=[("便當裡面最常放什麼？", "一顆滷蛋，再加一點高麗菜，有時候有香腸。")],
         closing=None, share="以前帶鐵便當盒的日子", detail="長者提到中午便當放進蒸飯箱，裡面常有滷蛋和高麗菜",
         sums=["長者回憶用鐵便當盒蒸便當。", "長者說便當常有滷蛋和高麗菜。"]),
    dict(opener="說到喝茶，你有什麼故事想跟我分享呢？",
         a1="飯後一定要泡一壺茶，跟鄰居坐在門口聊天。", short="喜歡喝茶。",
         pre2=("那時候，天氣或光線是什麼樣子呢？", "傍晚，太陽快下山，有一點風。"),
         img_a="那個茶壺跟我家的很像。",
         follow=[("你最喜歡喝什麼茶？", "烏龍茶，要用滾水沖才香。")],
         closing=None, share="飯後泡茶、跟鄰居聊天的習慣", detail="長者提到傍晚會坐在門口泡茶，最喜歡用滾水沖的烏龍茶",
         sums=["長者說飯後喜歡泡茶跟鄰居聊天。", "長者偏好用滾水沖烏龍茶。"]),
    dict(opener="說到拜拜，你會準備些什麼呢？",
         a1="初一十五都要拜，要準備水果跟發粿。", short="要拜拜。",
         pre2=None, img_a="供桌上的菜就是要這樣擺。",
         follow=[("發粿是自己做的嗎？", "以前是自己蒸的，要笑得開才好。")],
         closing=None, share="初一十五準備供品拜拜的習慣", detail="長者提到以前會自己蒸發粿，講究要蒸到「笑得開」才吉利",
         sums=["長者提到初一十五拜拜的供品。", "長者說自己蒸的發粿要笑得開。"]),
    dict(opener="說到第一次煮飯，你還記得那時候的事嗎？",
         a1="第一次煮飯煮到燒焦，被我媽媽唸。", short="記得一點。",
         pre2=("那時候大概幾歲呢？", "十歲左右吧。"),
         img_a="這個鍋子看起來很難顧火。",
         follow=[("後來是誰教你煮的？", "我阿嬤，她說水要淹過米一個指節。")],
         closing=None, share="第一次煮飯就燒焦的趣事", detail="長者提到後來由阿嬤教導，用一個指節的高度來量水",
         sums=["長者回憶第一次煮飯燒焦。", "長者說阿嬤教他用指節量水。"]),
    dict(opener="說到夏天，你會想到吃什麼呢？",
         a1="夏天一定要吃仙草，我們自己煮仙草茶。", short="吃仙草。",
         pre2=None, img_a="廚房裡好熱的感覺。",
         follow=[("仙草是怎麼煮的？", "要煮很久，煮到黑黑的才好。")],
         closing=None, share="夏天自己煮仙草茶消暑的回憶", detail="長者提到仙草要熬很久、煮到顏色變黑才好喝",
         sums=["長者說夏天喜歡吃仙草。", "長者描述自己煮仙草的方法。"]),
    dict(opener="說到鄰居，你們以前會互相分享吃的嗎？",
         a1="會啊，煮多了就端一碗給隔壁。", short="會啊。",
         pre2=None, img_a="這一桌菜可以分好幾家。",
         follow=[("鄰居有回送你什麼嗎？", "隔壁阿桑會送她自己醃的鹹菜。")],
         closing=None, share="以前跟鄰居互相分享飯菜的人情味", detail="長者提到煮多了就端給隔壁，鄰居也會回送自家醃的鹹菜",
         sums=["長者說煮多了會端給鄰居。", "長者提到鄰居回送醃鹹菜。"]),
    dict(opener="說到生日，你們家都怎麼慶祝呢？",
         a1="生日要吃豬腳麵線，吃了才會長壽。", short="吃麵線。",
         pre2=None, img_a="那碗麵線看起來很好吃。",
         follow=[("誰的生日讓你印象最深？", "我爸爸七十歲那年，全家都回來。")],
         closing=None, share="生日吃豬腳麵線的習俗", detail="長者提到印象最深的是父親七十大壽，全家都回來團聚",
         sums=["長者提到生日吃豬腳麵線。", "長者回憶父親七十大壽全家團聚。"]),
    dict(opener="說到以前家裡的廚房，你最記得什麼呢？",
         a1="以前廚房有一個大灶，要燒柴，煙很大。", short="有大灶。",
         pre2=("那個時候，通常是什麼時間開始燒火呢？", "下午四五點，準備煮晚餐。"),
         img_a="這個窗戶跟我們家的很像。",
         follow=[("那時候誰負責顧火？", "小孩子負責添柴，我常常被燻到流眼淚。")],
         closing=None, share="以前家裡燒柴大灶的回憶", detail="長者提到小時候負責添柴顧火，常被煙燻到流眼淚",
         sums=["長者回憶家裡燒柴的大灶。", "長者說小時候負責添柴。"]),
    dict(opener="說到做料理，我很好奇，這件事最讓你開心的是哪個部分呢？",
         a1="煮晚餐給家人吃，看他們吃光光很有成就感。", short="煮給家人吃。",
         pre2=("那個時候，通常是什麼時間做這件事呢？", "傍晚。"),
         img_a="很像當時的氛圍。",
         follow=[("家人最喜歡你煮的哪一道菜？", "番茄炒蛋，每次都搶著吃。")],
         closing=None, share="煮晚餐給家人吃的成就感", detail="長者提到家人最愛吃番茄炒蛋，每次都搶著吃",
         sums=["長者分享煮晚餐給家人吃的成就感。", "長者說家人最愛番茄炒蛋。"]),
    dict(opener="說到吃早餐，你有什麼故事想跟我分享呢？",
         a1="以前早上都吃稀飯配醬瓜，還有花生麵筋。", short="吃稀飯。",
         pre2=None, img_a="那個碗跟我們家以前用的一樣。",
         follow=[("早餐是誰準備的呢？", "家裡最早起的人，就先把稀飯煮好。")],
         closing=None, share="以前早餐吃稀飯配醬菜的習慣", detail="長者提到稀飯常配醬瓜和花生麵筋",
         sums=["長者回憶早餐吃稀飯配醬瓜。", "長者說早餐由最早起的家人先煮好。"]),
]

# 第二回合 AI 回應、第三回合（收尾）問答、心得問答：比照真實資料常見的句型
_SCENES = [
    "聽你這樣說，我彷彿也看到了當時的畫面。",
    "那段時光，真的很溫暖你心裡呢！",
    "聽起來真的很有意思，我也很想嚐嚐看呢！",
    "這張圖確實沒辦法把每個細節都畫得剛剛好，聽你這樣說，你記得的畫面比圖裡的還要豐富。",
]
_CLOSING_SCENES = ["今天跟你分享這些，聽起來很開心呢！", "我也很喜歡聽你講這些故事。"]
_CLOSING_QUESTIONS = [
    "今天聊完，心裡有什麼特別溫暖或開心的事嗎？",
    "剛剛你分享的這些，還有什麼想再多說一點的呢？",
    "現在想到這些，你心裡是什麼感覺呢？",
]
_REFLECTION_QUESTIONS = ["回想整場聊下來，你有什麼想跟我分享的呢？", "今天聊完之後，你現在感覺如何呢？"]
_CLOSINGS = {
    "適當": ["想到以前，覺得很溫馨。", "很懷念，那時候大家都在。", "很開心，好像回到以前。"],
    "亢奮": ["好開心！講到都停不下來！", "還想再講，下次再跟你說！"],
    "焦躁": ["有點累了，想休息。", "不知道啦，就這樣。"],
    "低落": ["還好。", _NO_RESPONSE],
}
_REFLECTIONS = {
    "適當": ["今天聊得很開心。", "很好，想起很多以前的事。", "下次還要來。", "我玩得很開心，希望下次還可以再來。"],
    "亢奮": ["太開心了，還想再講！", "今天講得好高興！"],
    "焦躁": ["有點累了。", "不想講了。"],
    "低落": ["沒什麼。", "嗯。", _NO_RESPONSE],
}
_STORY_TAIL = {
    "適當": ["整體而言，長者似乎心情愉悅，對過去的回憶充滿懷念之情。",
            "整體而言，長者的語氣和精神狀態似乎都很穩定和積極。",
            "整體而言，長者似乎對療程感到滿意，並且願意分享個人經歷和感受。"],
    "亢奮": ["整體而言，長者在談論這些往事時心情愉悅且雀躍不已，語速偏快。"],
    "焦躁": ["整體而言，長者偶爾表現出一些不耐和焦慮的情緒，需要適度引導。"],
    "低落": ["長者今天回答較簡短，沒有進一步描述感受。整體而言，長者精神狀態較差，情緒略顯低落。"],
}

# 情緒 → 每回合 (專注度, 表情正向, 肢體與語調波動) 中心值（真實資料各情緒的平均），
# 以及該情緒常見的訊號代碼
_EMOTION_SIGNALS = {
    "適當": ((59, 42, 22), ["face_smile_slight", "body_lean_forward", "speaker_speaking", "speaker_pitch_var_high",
                          "face_mouth_moved", "body_constricted", "face_not_detected"]),
    "亢奮": ((62, 46, 46), ["face_smile", "body_sway_high", "speaker_pitch_var_high", "speaker_speaking", "body_lean_forward"]),
    "焦躁": ((56, 26, 40), ["face_frown", "body_shoulder_raise", "body_sway_high", "speaker_pitch_var_high", "body_constricted"]),
    "低落": ((48, 28, 18), ["face_frown", "body_head_drop", "eye_looking_away", "eye_closed_drowsy", "body_lean_back"]),
}
_EMOTION_SCORE = {"低落": 1, "焦躁": 2, "亢奮": 3, "適當": 4}


# ---------------------------------------------------------------------------
# 個案
#
# span_days       最早一場療程是幾天前；之後平日約八成天數有一場，一直排到最後一場精選場次
# level_curve     評分基準（1-4）隨時間的控制點，t=0 最早一場、t=1 最後一場，線性內插
# warmup_curve    暖身指標相對 CARD_BASELINES 的加減
# response_curve  每回合平均回應秒數
# emotion_curve   場次情緒的抽樣權重
# featured        精選場次：(幾天前, 情緒, 個案專屬話題)，內容比自動補的場次更貼近
#                 個案背景，集中在最近一個多月
# ---------------------------------------------------------------------------

PATIENTS = [
    {
        "name": "陳",
        "birth_year": 1941,
        "hometown": "台南市鹽水區",
        "occupation": "辦桌總舖師助手",
        "family": "先生已過世，育有二子一女，目前與大兒子同住",
        "preferences": "煮菜、台語老歌、拜拜、種花",
        "taboo_words": "先生過世",
        "caregiver_name": "陳先生",
        "caregiver_relationship": "長子",
        "caregiver_phone": "0912-345-678",
        # 前期常低落、回答短，逐步進步；中段感冒請假後明顯退步（約 3 週），
        # 恢復後持續進步到參與度高、回應變快
        "span_days": 150,
        "level_curve": [(0, 2.7), (0.35, 3.5), (0.42, 2.6), (0.55, 2.8), (0.7, 3.5), (1, 3.8)],
        "warmup_curve": [(0, -22), (0.35, -4), (0.42, -20), (0.55, -16), (0.7, -2), (1, 6)],
        "response_curve": [(0, 34), (0.35, 20), (0.42, 33), (0.55, 30), (0.7, 18), (1, 13)],
        "emotion_curve": [(0, {"適當": 5, "低落": 3, "焦躁": 2}),
                          (0.35, {"適當": 9, "低落": 1}),
                          (0.4, {"適當": 3, "低落": 5, "焦躁": 2}), (0.55, {"適當": 4, "低落": 4, "焦躁": 2}),
                          (0.7, {"適當": 9, "亢奮": 1}), (1, {"適當": 9, "亢奮": 1})],
        "featured": [
            (31, "適當", dict(
                opener="說到辦桌，我很好奇，這件事最讓你開心的是哪個部分呢？",
                a1="我最會炒米粉，火要大，手要快，客人都說好吃。", short="炒米粉。",
                pre2=("那時候身邊有誰陪著你呢？", "總舖師師傅，還有好幾個幫手。"),
                img_a="這個鍋子跟我們以前用的很像，不過我們的更大。",
                follow=[("除了炒米粉，還有哪道菜讓你印象深刻？", "魷魚螺肉蒜也是我負責的，客人都說好喝。"),
                        ("客人稱讚的時候，你心裡感覺怎麼樣？", "很歡喜啊，覺得辛苦都值得。")],
                closing=None, share="年輕時在辦桌當助手的拿手菜", detail="長者提到自己最會炒米粉，強調火要大、手要快，魷魚螺肉蒜也很受客人好評",
                sums=["長者說自己最拿手炒米粉。", "長者提到負責的魷魚螺肉蒜很受好評。"],
                note="談到拿手菜明顯放鬆，會主動補充細節。")),
            (24, "適當", dict(
                opener="說到過年，你有什麼故事想跟我分享呢？像是想到一個人，或者一件事，都可以。",
                a1="過年圍爐都是我在煮，一桌十幾道。", short="過年我煮。",
                pre2=None, img_a="這張桌子好像我們家的，孩子都坐這邊等吃飯。",
                follow=[("孩子們最喜歡吃哪一道？", "他們最愛我滷的肉，一鍋都吃光光。"),
                        ("那個滷肉有什麼秘訣嗎？", "冰糖要先炒，醬油要好，滷久一點就對了。")],
                closing=("現在過年是誰在煮呢？", "現在都是大兒子在煮了，味道有像我。"),
                share="每年除夕一手包辦圍爐年菜的回憶", detail="長者提到孩子們最愛她滷的肉，現在由大兒子接手掌廚，味道很像她煮的",
                sums=["長者說過年圍爐由她煮十幾道菜。", "長者分享滷肉秘訣。", "長者欣慰大兒子接手煮年菜。"])),
            (17, "亢奮", dict(
                opener="說到過年前的準備，你最記得什麼呢？",
                a1="過年一定要做菜頭粿，蘿蔔要刨很多！", short="做粿。",
                pre2=("那時候是誰陪你一起做的？", "我媽媽，還有隔壁的阿珠！"),
                img_a="對對對，就是這種白蘿蔔，要選重的才好吃！",
                follow=[("做菜頭粿最費工的是哪一步？", "米漿要自己磨，以前用石磨，手都磨到酸。")],
                closing=None, share="過年前做菜頭粿的過程", detail="長者提到要自己用石磨磨米漿、刨很多蘿蔔絲，講到一半還想站起來示範",
                sums=["長者興奮說起過年要做菜頭粿。", "長者回憶以前用石磨磨米漿。"],
                round_emotions=["亢奮", "亢奮", "適當"],
                note="情緒偏高昂，中途有站起來示範動作，需留意安全。")),
            (10, "適當", dict(
                opener="說到以前的廚房，你有什麼故事想跟我分享呢？",
                a1="以前灶腳燒柴，煮飯會有鍋巴，孩子都搶著吃。", short="有鍋巴。",
                pre2=None, img_a="窗戶那邊以前會曬菜脯。",
                follow=[("現在在家裡，你還會幫忙廚房的事嗎？", "現在我還會幫兒子挑菜，他說我挑得最乾淨。"),
                        ("被兒子這樣稱讚，感覺怎麼樣？", "覺得自己還有用啊，很好。")],
                closing=None, share="早年燒柴煮飯、鍋巴是孩子零食的回憶", detail="長者提到現在仍會在家幫兒子挑菜，覺得自己還有用",
                sums=["長者回憶燒柴煮飯的鍋巴。", "長者提到現在仍會幫忙挑菜。"])),
            (3, "適當", dict(
                opener="說到辦桌，有沒有哪一次讓你特別難忘呢？",
                a1="第一次自己負責三十桌，緊張到前一天睡不著。", short="三十桌。",
                pre2=("那個時候，通常是什麼時間開始準備呢？", "半夜三點就起來洗菜了。"),
                img_a="一桌一桌排好，看起來很澎湃。",
                follow=[("那一場最後結果怎麼樣？", "最後客人都說好吃，師傅還拍我的肩膀。"),
                        ("那一刻你心裡在想什麼？", "想說我也可以做到，很驕傲。")],
                closing=None, share="第一次獨自負責三十桌辦桌的經歷", detail="長者提到前一晚緊張到睡不著，最後順利完成，還被師傅拍肩稱讚",
                sums=["長者回憶第一次獨自負責三十桌。", "長者說辦桌成功被師傅稱讚。"],
                note="參與度持續提升，本次主動延伸話題，下次可聊她提到的種花。")),
        ],
        "todos": [
            ("下次療程可延伸「種花」話題（長者主動提起）", False, "一般", 4),
            ("與長子確認今年圍爐是否讓長者參與備菜", False, "高優先", 10),
            ("療程座椅加扶手，留意亢奮時起身", True, "高優先", -14),
        ],
        "notes": [
            (148, "示範治療師", "初次評估：認知功能輕度退化，對辦桌、煮菜相關話題反應最好。"),
            (98, "家屬（長子）", "長者感冒發燒請假一週，回來後精神較差、食慾也不好。"),
            (80, "示範治療師", "感冒後體力尚未恢復，暖身動作多張卡跳過，療程話題改輕鬆一點。"),
            (40, "家屬（長子）", "家屬表示長者回家後會主動講療程內容，心情比以前好。"),
            (3, "示範治療師", "近三個月參與度明顯上升，暖身動作完成度也進步，持續觀察。"),
        ],
    },
    {
        "name": "王",
        "birth_year": 1944,
        "hometown": "基隆市",
        "occupation": "漁船輪機長",
        "family": "與太太同住，女兒住台北每週回來",
        "preferences": "釣魚、聽廣播、喝茶、吃海產",
        "taboo_words": "船難、颱風",
        "caregiver_name": "王小姐",
        "caregiver_relationship": "女兒",
        "caregiver_phone": "0933-222-111",
        # 剛來時較被動，前兩個多月逐步進步；最近一個月太太住院後明顯退步，
        # 近 3 次中 2 次以上焦躁/低落 → 個案列表會出現「需留意」提示
        "span_days": 120,
        "level_curve": [(0, 2.8), (0.6, 3.8), (1, 2.6)],
        "warmup_curve": [(0, -15), (0.6, 6), (1, -14)],
        "response_curve": [(0, 30), (0.6, 13), (1, 31)],
        "emotion_curve": [(0, {"適當": 5, "低落": 3, "焦躁": 2}), (0.5, {"適當": 9, "亢奮": 1}),
                          (0.65, {"適當": 8, "焦躁": 1, "低落": 1}),
                          (0.8, {"焦躁": 1, "低落": 1}), (1, {"焦躁": 1, "低落": 2})],
        "featured": [
            (33, "適當", dict(
                opener="說到以前跑船，你有什麼故事想跟我分享呢？",
                a1="回港的時候，我太太都會煮一鍋魚湯等我。", short="太太煮魚湯。",
                pre2=("那時候大概是幾點回到家呢？", "天剛亮，五六點。"),
                img_a="我家廚房就是這樣，小小的但很溫暖。",
                follow=[("那鍋魚湯是怎麼煮的呢？", "用剛抓的魚，加薑絲，什麼都不用加就很甜。")],
                closing=None, share="出海回港後太太煮魚湯等他的回憶", detail="長者提到魚湯只用剛抓的魚加薑絲，不用其他調味就很鮮甜",
                sums=["長者說回港時太太會煮魚湯等他。", "長者描述薑絲魚湯的做法。"])),
            (26, "適當", dict(
                opener="跑船回來，你最想念家裡的什麼呢？",
                a1="下船最想吃家裡的飯，我太太煎白帶魚最好吃。", short="想吃家裡的飯。",
                pre2=None, img_a="這一桌很像我回家那天的晚餐。",
                follow=[("在船上的時候，工作辛苦嗎？", "輪機室很熱，四十幾度，衣服都濕透。")],
                closing=None, share="下船後最想念太太煎的白帶魚", detail="長者也提到年輕時在輪機室工作，溫度高達四十幾度",
                sums=["長者下船最想念太太煎的白帶魚。", "長者回憶輪機室高溫工作的辛苦。"])),
            (19, "焦躁", dict(
                opener="說到家裡的廚房，你有什麼故事想跟我分享呢？",
                a1="我太太以前都在這種廚房煮飯。", short="太太在煮。",
                pre2=None, img_a="窗戶外面以前看得到海。",
                follow=[("看著海的時候，你會想起什麼？", "有一次浪很大，船差點翻掉，很危險、很危險。"),
                        ("那時候一定很害怕，現在你很安全喔。", "嗯……對，現在在家。")],
                closing=None, share="太太在家中廚房煮飯的回憶", detail="長者話題轉到某次出海遇到大浪時情緒明顯激動，重複提到「很危險」，經引導後才回到家常話題",
                sums=["長者提到太太以前在類似的廚房煮飯。", "長者回憶出海遇大浪，情緒激動。"],
                round_emotions=["適當", "焦躁", "焦躁"],
                note="觸及出海遇險回憶時出現焦躁，下次避開海上危險相關話題。")),
            (12, "適當", dict(
                opener="退休之後，你在家都做些什麼呢？",
                a1="白蘿蔔燉排骨，冬天喝很好。", short="燉湯。",
                pre2=None, img_a="蘿蔔很大顆。",
                follow=[("這道湯是誰教你的？", "我太太，她說蘿蔔要切大塊。")],
                closing=None, share="退休後在家幫太太燉湯", detail="長者提到冬天喜歡用白蘿蔔燉排骨，是太太教的，回答比以往簡短",
                sums=["長者提到冬天喜歡喝白蘿蔔排骨湯。", "長者說燉湯是太太教的。"])),
            (5, "低落", dict(
                opener="說到全家一起吃飯，你有什麼故事想跟我分享呢？",
                a1="以前一家人吃飯很熱鬧，現在比較冷清。", short="現在比較冷清。",
                pre2=None, img_a="以前都坐滿。",
                follow=[("最近家裡的狀況怎麼樣呢？", "女兒工作忙，太太最近身體也不好。")],
                closing=None, share="以前全家一起吃飯的熱鬧", detail="長者提到最近太太身體不好、女兒工作忙較少回家，覺得家裡很冷清",
                sums=["長者感嘆以前全家吃飯熱鬧，現在冷清。", "長者提到女兒忙碌、太太身體欠佳。"],
                note="情緒低落，可能與家中近況有關，已記錄待辦聯繫女兒。")),
        ],
        "todos": [
            ("聯繫女兒了解太太健康狀況與家中近況", False, "高優先", 2),
            ("下次療程避開海上遇險、颱風相關話題", False, "高優先", 7),
            ("評估是否轉介社工進行家庭支持", False, "一般", 14),
        ],
        "notes": [
            (118, "示範治療師", "初次評估：喜歡聊海產、家人。曾經歷船難，相關話題需迴避。剛來較被動，回答偏短。"),
            (60, "示範治療師", "適應良好，參與度和暖身完成度都明顯進步，會主動聊跑船時的趣事。"),
            (19, "示範治療師", "療程中談到出海遇大浪時明顯焦躁，已引導回家常話題並安撫。"),
            (5, "家屬（女兒）", "電話中女兒表示母親近期住院檢查，父親在家較常獨處。"),
        ],
    },
    {
        "name": "林",
        "birth_year": 1947,
        "hometown": "彰化縣鹿港鎮",
        "occupation": "國小老師",
        "family": "與先生同住，兒子在國外",
        "preferences": "書法、做糕餅、看歌仔戲",
        "taboo_words": "",
        "caregiver_name": "林先生",
        "caregiver_relationship": "先生",
        "caregiver_phone": "04-777-1234",
        # 新個案，前期狀態良好；中段在家跌倒扭傷腳踝，暖身與參與度退步約兩週，
        # 復原後回升並略優於初期
        "span_days": 75,
        "level_curve": [(0, 3.3), (0.3, 3.7), (0.4, 2.7), (0.55, 2.9), (0.7, 3.6), (1, 3.8)],
        "warmup_curve": [(0, -4), (0.3, 2), (0.4, -24), (0.55, -18), (0.7, 0), (1, 6)],
        "response_curve": [(0, 18), (0.3, 14), (0.4, 27), (0.55, 24), (0.7, 14), (1, 11)],
        "emotion_curve": [(0, {"適當": 8, "亢奮": 2}), (0.3, {"適當": 8, "亢奮": 2}),
                          (0.4, {"適當": 3, "低落": 4, "焦躁": 3}), (0.55, {"適當": 5, "低落": 3, "焦躁": 2}),
                          (0.7, {"適當": 8, "亢奮": 2}), (1, {"適當": 8, "亢奮": 2})],
        "featured": [
            (15, "適當", dict(
                opener="說到小時候在鹿港，你有什麼故事想跟我分享呢？",
                a1="小時候媽媽教我做紅龜粿，模子我現在還留著。", short="做紅龜粿。",
                pre2=("那時候大概幾歲呢？", "七八歲吧，還要站在板凳上。"),
                img_a="跟我媽媽的灶腳好像，也有一個大鍋子。",
                follow=[("那個模子長什麼樣子呢？", "上面刻的是龜殼的花紋，代表長壽。"),
                        ("你現在還會做嗎？", "過年還會做一點，給鄰居吃。")],
                closing=None, share="小時候媽媽教她做紅龜粿的回憶", detail="長者提到當年的粿模至今還保留著，上面龜殼花紋象徵長壽",
                sums=["長者回憶媽媽教她做紅龜粿。", "長者說明粿模的龜殼花紋象徵長壽。"],
                note="表達清楚，話題豐富，認知狀況良好。")),
            (8, "適當", dict(
                opener="說到當老師的時候，有什麼難忘的事嗎？",
                a1="冬至我都會帶學生在教室搓湯圓。", short="搓湯圓。",
                pre2=None, img_a="我們借學校廚房煮，學生都圍在旁邊看。",
                follow=[("學生們後來有跟你聯絡嗎？", "有學生畢業二十年回來看我，還記得搓湯圓。")],
                closing=None, share="當老師時帶學生在教室搓湯圓的回憶", detail="長者提到有學生畢業二十年後回來探望，還記得當年一起搓湯圓",
                sums=["長者回憶冬至帶學生搓湯圓。", "長者說畢業多年的學生仍記得搓湯圓。"])),
            (1, "亢奮", dict(
                opener="最近有什麼讓你開心的事嗎？",
                a1="我兒子下個月要回來！我要做鳳眼糕給他。", short="兒子要回來。",
                pre2=None, img_a="我要在廚房準備好多好多！",
                follow=[("他小時候最喜歡吃什麼？", "就是鳳眼糕，他小時候一次可以吃半盒，被我罵。")],
                closing=None, share="兒子下個月要從國外回來的期待", detail="長者提到兒子小時候最愛吃她做的鳳眼糕，計畫做一大盒給他",
                sums=["長者期待兒子回國，計畫做鳳眼糕。", "長者回憶兒子小時候愛吃鳳眼糕。"],
                round_emotions=["亢奮", "適當", "適當"],
                note="得知兒子要回國情緒高昂，話題主動且連貫。")),
        ],
        "todos": [
            ("下次療程延伸「書法」話題", False, "一般", 6),
            ("暖身避開踢腿、原地踏步等下肢動作，直到腳踝復原", True, "高優先", -30),
        ],
        "notes": [
            (73, "示範治療師", "初次評估：認知功能良好，表達流暢，適合以「教學生涯」與「糕餅」為主題。"),
            (46, "家屬（先生）", "長者昨天在家浴室滑倒扭傷右腳踝，已就醫，醫師建議休養兩週。"),
            (40, "示範治療師", "腳踝受傷後情緒較煩躁、擔心走路，暖身表現明顯下降。"),
            (25, "示範治療師", "腳踝已大致復原，參與度回升，情緒也恢復穩定。"),
        ],
    },
]


def _interp(curve: list, t: float):
    """控制點線性內插；值可以是數字或 {情緒: 權重} dict。"""
    for (t0, v0), (t1, v1) in zip(curve, curve[1:]):
        if t <= t1:
            k = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            if isinstance(v0, dict):
                keys = sorted(set(v0) | set(v1))
                return {e: v0.get(e, 0) * (1 - k) + v1.get(e, 0) * k for e in keys}
            return v0 + (v1 - v0) * k
    return curve[-1][1]


def _prepare_scene_images() -> list[str]:
    paths = []
    for i, src_id in enumerate(_SOURCE_IMAGES):
        src = _MEDIA_IMAGES / src_id / "round_1.png"
        dst_dir = _MEDIA_IMAGES / f"demo_scene_{i}"
        if not src.exists():
            paths.append("")
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst_dir / "round_1.png")
        paths.append(f"/media/images/demo_scene_{i}/round_1.png")
    return paths


def _scores(rng: random.Random, level: float, emotion: str) -> dict:
    """依 routers/session.py 的評分規則產生五項分數與原因欄位。"""
    def pick(bias: float) -> int:
        return max(1, min(4, round(level + bias + rng.uniform(-0.6, 0.6))))

    participation = pick(-0.2)
    if emotion == "焦躁" and rng.random() < 0.3:
        participation = 1  # 干擾
    attention = pick(0)

    if emotion == "低落":
        endurance, endurance_reason = rng.choice([(1, "sad"), (2, "sad"), (3, "default")])
    else:
        endurance = max(2, pick(0.4))
        endurance_reason = "distracted" if endurance == 2 else "default"

    if emotion == "低落":
        interaction, interaction_reason = rng.choice([(2, "short_replies"), (2, "no_speech"), (1, "default")])
    else:
        interaction = pick(-0.8)
        interaction_reason = "default"
        if interaction <= 2:
            interaction, interaction_reason = 2, rng.choices(["short_replies", "no_speech"], [5, 1])[0]

    emotion_score = _EMOTION_SCORE[emotion]
    return dict(
        score_participation=participation, score_attention=attention,
        score_endurance=endurance, endurance_reason=endurance_reason,
        score_emotion=emotion_score,
        score_interaction=interaction, interaction_reason=interaction_reason,
        total_score=participation + attention + endurance + emotion_score + interaction,
    )


def _round_signals(rng: random.Random, emotion: str) -> tuple[list[int], list[str]]:
    centers, pool = _EMOTION_SIGNALS[emotion]
    pcts = [max(3, min(95, c + rng.randint(-12, 12))) for c in centers]
    return pcts, rng.sample(pool, rng.randint(1, 4))


def _build_rounds(rng: random.Random, topic: dict, emotion: str) -> list[dict]:
    """把話題展開成回合列表，每回合 dict(emotion, scene, summary, exchanges[, is_reflection])。"""
    low = emotion == "低落"
    round_emotions = topic.get("round_emotions")

    def emo(i: int) -> str:
        if round_emotions and i < len(round_emotions):
            return round_emotions[i]
        if emotion in ("焦躁", "亢奮") and i == 0:
            return "適當"  # 真實資料常見：開場還好，聊到後面情緒才起伏
        return emotion

    def answer(text: str) -> str:
        return _NO_RESPONSE if low and rng.random() < 0.35 else text

    first = [(topic["opener"], topic["short"] if low else topic["a1"], "pre_image")]
    if topic["pre2"]:
        first.append((topic["pre2"][0], answer(topic["pre2"][1]), "pre_image"))
    first.append((_IMAGE_QUESTION, "嗯。" if low else topic["img_a"], None))
    rounds = [dict(emotion=emo(0), scene=_FIRST_SCENE, summary=topic["sums"][0], exchanges=first)]

    follow = topic["follow"][:1] if low else topic["follow"]
    rounds.append(dict(
        emotion=emo(1), scene=rng.choice(_SCENES), summary=topic["sums"][1],
        exchanges=[(q, answer("不太記得了。") if low else a, None) for q, a in follow],
    ))

    # 第三回合：話題有自己的 closing 就一定有；其餘看情緒，低落多半聊不到第三回合
    if topic["closing"] or (not low and rng.random() < 0.75):
        if topic["closing"]:
            q, a = topic["closing"]
            summary = topic["sums"][2] if len(topic["sums"]) > 2 else ""
        else:
            q, a, summary = rng.choice(_CLOSING_QUESTIONS), rng.choice(_CLOSINGS[emo(2)]), ""
        rounds.append(dict(emotion=emo(2), scene=rng.choice(_CLOSING_SCENES), summary=summary,
                           exchanges=[(q, a, None)]))

    if rng.random() < (0.5 if low else 0.8):
        rounds.append(dict(emotion=emotion, scene="", summary="", is_reflection=True,
                           exchanges=[(rng.choice(_REFLECTION_QUESTIONS), rng.choice(_REFLECTIONS[emotion]), None)]))
    return rounds


def _story(rng: random.Random, topic: dict, emotion: str) -> str:
    return f"長者今天分享了{topic['share']}。{topic['detail']}。{rng.choice(_STORY_TAIL[emotion])}"


def _build_schedule(rng: random.Random, p: dict, today: date) -> list[tuple[date, str, dict, bool]]:
    """(日期, 情緒, 話題, 是否精選)，依日期排序。比照日照中心的作息，自動補的
    場次排在平日、每天 SESSION_RATE 機率有一場（一週約 4 場，趨勢分析的週檢視
    才看得出一週內的變化），只避開精選場次當天；話題依序輪流，同一話題隔一段
    時間才會再出現（真實長者本來就常重複聊同一件事）。"""
    featured = [(today - timedelta(days=d), emo, topic, True) for d, emo, topic in p["featured"]]
    featured_dates = {f[0] for f in featured}
    start = today - timedelta(days=p["span_days"])
    end = max(featured_dates)
    topics = GENERIC_TOPICS[:]
    rng.shuffle(topics)
    schedule = list(featured)
    d, n = start, 0
    while d < end:
        if d.weekday() < 5 and d not in featured_dates and rng.random() < SESSION_RATE:
            t = (d - start).days / (end - start).days
            weights = _interp(p["emotion_curve"], t)
            emotion = rng.choices(list(weights), weights=list(weights.values()))[0]
            schedule.append((d, emotion, topics[n % len(topics)], False))
            n += 1
        d += timedelta(days=1)
    return sorted(schedule, key=lambda x: x[0])


def _warmup_results(rng: random.Random, offset: float) -> list[dict]:
    """每場 5 張卡，各卡以 CARD_BASELINES 為基準加上個案當下的 offset 再加雜訊。"""
    results = []
    skip_rate = 0.1 if offset > -10 else 0.25  # 狀況差的時候比較常被治療師跳過
    for order, key in enumerate(rng.sample(list(CARD_BASELINES), 5), start=1):
        if rng.random() < skip_rate:
            results.append(dict(card_key=key, card_order=order, status="skipped",
                                joint_angle_pct=None, smoothness_pct=None, symmetry_pct=None,
                                duration_seconds=round(rng.uniform(1.0, 4.0), 2)))
            continue
        ja, sm, sy, dur = CARD_BASELINES[key]

        def pct(base: int, spread: int) -> int:
            return max(0, min(100, round(base + offset + rng.randint(-spread, spread))))

        results.append(dict(
            card_key=key, card_order=order,
            status="manual" if rng.random() < 0.04 else "completed",
            joint_angle_pct=pct(ja, 10), smoothness_pct=pct(sm, 15), symmetry_pct=pct(sy, 8),
            # 動作越到位通常也越快完成
            duration_seconds=round(max(2.5, dur * (1 - offset / 60) * rng.uniform(0.75, 1.3)), 2),
        ))
    return results


async def _seed() -> None:
    rng = random.Random(20261102)
    today = date.today()
    scene_images = _prepare_scene_images()

    async with AsyncSessionLocal() as db:
        admin = (
            await db.execute(select(Therapist).where(Therapist.email == DEMO_EMAIL))
        ).scalar_one_or_none()
        if admin is None or admin.organization_id is None:
            print(f"錯誤：找不到 {DEMO_EMAIL} 這個治療師帳號，請先用 create_organization.py 開通", file=sys.stderr)
            sys.exit(1)
        org_id = admin.organization_id

        deleted = await db.execute(
            delete(Patient).where(
                Patient.organization_id == org_id,
                Patient.name.in_([p["name"] for p in PATIENTS] + _LEGACY_NAMES),
            )
        )
        if deleted.rowcount:
            print(f"已刪除舊的示範個案 {deleted.rowcount} 位")

        for p in PATIENTS:
            patient = Patient(
                organization_id=org_id,
                **{k: p[k] for k in ("name", "birth_year", "hometown", "occupation", "family", "preferences",
                                     "taboo_words", "caregiver_name", "caregiver_relationship", "caregiver_phone")},
                created_at=datetime.combine(today - timedelta(days=p["span_days"] + 2), time(9, 30)),
            )
            db.add(patient)
            await db.flush()

            schedule = _build_schedule(rng, p, today)
            start, end = schedule[0][0], schedule[-1][0]
            for s_date, emotion, topic, is_featured in schedule:
                t = (s_date - start).days / (end - start).days
                session = TherapySession(
                    session_uuid=str(uuid.uuid5(_UUID_NS, f"{p['name']}-{s_date.isoformat()}")),
                    patient_id=patient.id, therapist_id=admin.id, organization_id=org_id,
                    date=s_date, mode="interactive", start_scene="",
                    emotional_status=emotion, story_summary=_story(rng, topic, emotion),
                    therapist_note=topic.get("note", ""),
                    status="completed", topic="",
                    **_scores(rng, _interp(p["level_curve"], t), emotion),
                )
                db.add(session)
                await db.flush()

                # 真實資料有不少場次沒做暖身，精選場次一律有，其餘九成有
                if is_featured or rng.random() < 0.9:
                    started = datetime.combine(s_date, time(rng.choice([9, 10, 14, 15]), rng.randint(0, 50)))
                    for w in _warmup_results(rng, _interp(p["warmup_curve"], t)):
                        started += timedelta(seconds=(w["duration_seconds"] or 0) + rng.uniform(2, 5))
                        db.add(WarmupCardResult(session_id=session.id, created_at=started, **w))

                response_center = _interp(p["response_curve"], t)
                image = scene_images[rng.randrange(len(scene_images))]
                for r_idx, r in enumerate(_build_rounds(rng, topic, emotion), start=1):
                    pcts, signals = _round_signals(rng, r["emotion"])
                    is_reflection = r.get("is_reflection", False)
                    rnd = TherapyRound(
                        session_id=session.id, round_number=r_idx,
                        # 心得回合通常講比較久
                        response_time=round(max(2.0, response_center * rng.uniform(0.5, 1.5)
                                                * (1.8 if is_reflection else 1)), 1),
                        emotion=r["emotion"], type="心得" if is_reflection else None,
                        generated_scene=r["scene"],
                        patient_response="\n".join(a for _, a, _ in r["exchanges"] if a != _NO_RESPONSE),
                        scene_image=image if r_idx == 1 else "",
                        summary=r["summary"],
                        engagement_pct=pcts[0], happiness_pct=pcts[1], agitation_pct=pcts[2],
                        signal_codes=json.dumps(signals, ensure_ascii=False),
                    )
                    db.add(rnd)
                    await db.flush()
                    for q_no, (q, a, stage) in enumerate(r["exchanges"], start=1):
                        db.add(RoundExchange(round_id=rnd.id, question_number=q_no, question=q, answer=a, stage=stage))

            for content, done, priority, due_in in p["todos"]:
                db.add(PatientTodo(patient_id=patient.id, content=content, is_done=done, priority=priority,
                                   due_date=today + timedelta(days=due_in)))
            for days_ago, author, content in p["notes"]:
                db.add(PatientNote(patient_id=patient.id, author_id=admin.id, author_name=author, content=content,
                                   created_at=datetime.combine(today - timedelta(days=days_ago), time(16, 0))))

            print(f"  個案 {p['name']}：{len(schedule)} 場療程（{start} ~ {end}）")

        await db.commit()

    print(f"\n示範資料建立完成，用 {DEMO_EMAIL} 登入即可查看")


if __name__ == "__main__":
    asyncio.run(_seed())
