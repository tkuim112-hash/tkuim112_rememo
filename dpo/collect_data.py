#!/usr/bin/env python3
"""
DPO 訓練資料收集腳本

使用混合模型策略為 yentinglin/Llama-3-Taiwan-8B-Instruct 生成偏好訓練對：
  chosen  → claude-sonnet-4-6（高品質）
  rejected → claude-haiku-4-5-20251001（只需違規，省 ~60% 費用）

目標使用者：60-75歲長者，可能有輕微認知障礙，AI透過TTS直接說話。
因此 chosen 的問題必須念起來自然，不能有書面語的距離感。

四條軌跡：
  Track A — 問題品質（STEP1開場、STEP2追問、STEP3補問 × 7種違規）
  Track B — 情緒引導（長者出現負面情緒時的回應）
  Track C — 情緒感知承接 + 下一個問題（正常對話中）
  Track D — 收尾引導（三回合結束後帶長者回到現實 × 7種違規）

執行前設定：
  export ANTHROPIC_API_KEY="sk-ant-..."

執行：
  python dpo/collect_data.py

輸出：
  dpo/data/train.jsonl
  dpo/data/stats.json
"""

import json
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import anthropic

# ─── 設定 ───────────────────────────────────────────────────────────────────

SCENARIOS_FILE = Path(__file__).parent / "scenarios.json"
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "train.jsonl"
STATS_FILE = OUTPUT_DIR / "stats.json"

MODEL_CHOSEN   = "claude-sonnet-4-6"         # chosen：高品質回應需要
MODEL_REJECTED = "claude-haiku-4-5-20251001"  # rejected：刻意違規，Haiku 足夠
REQUEST_DELAY = 1.0  # 每次 API 呼叫之間的間隔（秒），避免 rate limit

# 5W1H 優先順序（對齊 orchestrator.py _W_ORDER）
_W_ORDER = ["Where", "Who", "What", "When", "How", "Why"]

# 推理時的 system prompt（需與 orchestrator._generate_first_question 完全一致）
SYSTEM_CONTENT_STEP = (
    "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
    "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
    "問題必須念起來自然、溫和、不超過15個字，且開頭要包含畫面中看得到的具體物件。"
)

SYSTEM_CONTENT_EMOTIONAL = (
    "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
    "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
    "當長者出現負面情緒時，先給予溫暖承接，再輕柔地引導回療程。"
)

SYSTEM_CONTENT_TRACK_C = (
    "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
    "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
    "每次聽完長者說話，先用1-2句溫暖的話承接他的情緒，再自然問下一個問題。"
)

SYSTEM_CONTENT_TRACK_D = (
    "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
    "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
    "三回合懷舊療程剛結束，你要溫柔地帶領長者回到現實，"
    "並以一句輕柔的問題詢問他們現在的感受或正向回憶，為今天的療程畫上句點。"
)

# Track A：問題品質的 7 種違規方式
QUESTION_REJECTION_RULES: dict[str, str] = {
    "is_yesno": "把問題改成是非題，讓長者只能回答「是」或「不是」，失去開放分享的機會",
    "double_question": "一次問兩個問題，讓有輕微認知障礙的長者不知道先回哪個",
    "no_anchor": "問題開頭不包含畫面中任何可見的物件，沒有視覺錨點幫助長者連結記憶",
    "too_long": "問題超過15個字，讓認知負荷較高的長者難以消化整個句子（所有 prompt 要求 ≤15字）",
    "memory_test": "用「你還記得嗎」或「你記不記得」開頭，像在測試記憶力，讓有MCI的長者感到焦慮",
    "wrong_w_priority": "跳過 Where/Who/What，直接問 Why（為什麼），對輕微認知障礙的長者太抽象難以回答",
    "leading_question": "問題預設答案（如「那一定很辛苦吧？」），引導長者附和而非主動回憶，剝奪長者自由表達的空間",
}
# 移除了與 no_anchor 高度重疊的三條規則：
#   off_scene（完全離題，是 no_anchor 的極端情況）
#   abstract_opener（無視覺錨點的抽象開場，屬 no_anchor 的子集）
#   overly_formal（語氣書面化，由系統提示的整體要求覆蓋）

# Track B：情緒引導的錯誤回應（共 10 種）
EMOTION_REJECTION_RULES: dict[str, str] = {
    "ignore_emotion": "完全忽視長者的情緒，直接繼續問下一個問題",
    "too_clinical": "用冷淡、像機器人的語氣回應，沒有任何情感溫度，讓長者更封閉",
    "rush_topic": "急著轉換話題，不讓長者有空間表達情緒，讓他感到被忽視",
    "over_dramatize": "反應過度誇張，讓本已脆弱的長者感到壓力更大或更難受",
    "no_emotional_lift": "有承接情緒，但讓對話停留在低落狀態，沒有輕柔引導長者找到正面或溫暖的角度",
    "give_advice": "長者情緒脆弱時立刻給建議或解決方案（如「您應該多出去走走」），跳過情緒承接直接解決問題，讓長者感到不被理解",
    "compare_suffering": "用比較試圖安慰長者（如「現在的年輕人更辛苦」或「比您慘的人多的是」），讓長者感到自己的情緒被否定",
    "false_positivity": "用強迫式假正向框架包裝長者的痛苦（如「這都是您寶貴的人生經歷！非常珍貴！」），跳過情緒承接直接賦予意義，讓長者感到傷痛被輕視",
    "premature_closure": "情緒還未平復就急著收尾回到療程（如「好，那我們繼續吧」），讓長者感到自己的情緒被打斷，沒有被好好接住",
    "over_identify": "過度代入自己的情感（如「我聽到這個也很難過，真的很心疼」），搶佔長者的情緒舞台，讓長者反過來擔心或安慰AI，失去表達自己的空間",
}

# 情緒觸發情境：模擬長者說出這些話後，AI要如何回應（Track B，10個危機情境）
EMOTIONAL_SCENARIOS = [
    {
        "trigger": "（沉默了很久，聲音有點哽咽）那個時候我媽媽還在，她每次都會來接我下班……",
        "context": "長者提到已過世的母親，情緒突然低落",
    },
    {
        "trigger": "（搖搖頭）我不記得了，我都忘了，我腦子越來越差了……",
        "context": "長者因為記憶模糊而感到自責和沮喪",
    },
    {
        "trigger": "（聲音變小）那段時間很苦啦……不想說了……",
        "context": "長者觸及困難的過去，開始迴避話題",
    },
    {
        "trigger": "哎，那個時代的事情講了你們也不懂……",
        "context": "長者感到自己的經歷難以被理解，出現疏離感",
    },
    {
        "trigger": "（突然停下來，眼眶紅了）我那個兒子……他很久沒來看我了……",
        "context": "長者提到家人疏遠，情緒突然轉向悲傷",
    },
    {
        "trigger": "（聲音突然變得很小）我想回家……我不想在這裡……",
        "context": "長者突然出現想離開的念頭，情緒不安定",
    },
    {
        "trigger": "（停頓很久）我那個老伴走了二十幾年了……有時候還是會想到他……",
        "context": "長者提到已過世的配偶，情緒陷入深沉的思念",
    },
    {
        "trigger": "我這個老人家，講這些有什麼用……現在什麼都做不了了……",
        "context": "長者出現自我否定，覺得自己無用、是負擔",
    },
    {
        "trigger": "（眼眶泛紅，說不下去）……那時候如果不走就好了，我那時候做錯了……",
        "context": "長者提到過去的遺憾和後悔，情緒陷入自責",
    },
    {
        "trigger": "（突然語氣激動）你們不知道那個時候有多難！沒有人知道！",
        "context": "長者突然情緒激動，帶著委屈和不被理解的憤慨",
    },
    {
        "trigger": "（眼眶泛紅，聲音顫抖）說著說著……我那個弟弟……他走了很多年了……我們從小感情最好……",
        "context": "長者提到已過世的手足，突然情緒哽咽",
    },
    {
        "trigger": "（突然疑惑地看著前方）你是誰？我……我怎麼在這裡？我要回家……",
        "context": "長者出現短暫認知混亂，不確定自己在哪裡或對方是誰（MCI典型症狀）",
    },
    {
        "trigger": "（低著頭，聲音很小）小時候我們家很窮，過年都沒有新衣服穿……那時候真的很苦……",
        "context": "長者提到童年貧苦，陷入自憐的情緒",
    },
    {
        "trigger": "（搖頭嘆氣）我現在手抖腳也不靈活，什麼都做不了了，老了就是廢物……",
        "context": "長者因身體老化而感到自憐和強烈的無用感",
    },
    {
        "trigger": "（突然哽咽，說不出話，過了很久才開口）……沒事，沒事……我就是有點想到以前……",
        "context": "長者突然無緣由哭泣，情緒波動但說不清楚原因",
    },
    {
        "trigger": "我的老朋友阿明……上個月走了……我們認識六十年了……",
        "context": "長者提到老友近期過世，陷入悲傷與孤獨感",
    },
    {
        "trigger": "（苦笑）哈……說起來好笑，那時候苦成那樣，現在想起來還是會……（聲音哽咽，說不下去）",
        "context": "長者苦中作樂，但回憶中的苦難突然讓情緒潰堤",
    },
    {
        "trigger": "你剛才說什麼？我……我沒有聽清楚……我現在耳朵不好、腦筋也不好，你們說話我跟不上……",
        "context": "長者因感知退化而感到挫折，出現自責和落後感",
    },
    {
        "trigger": "（突然輕聲問）媽媽呢？我媽媽去哪裡了？她說她等一下來接我的……",
        "context": "長者出現時間錯亂，以為已過世的母親還在世（MCI中期症狀）",
    },
    {
        "trigger": "（語氣突然謹慎，聲音壓低）那個時候不能亂講話的……說錯話是會出事的……你知道嗎……",
        "context": "長者觸及戒嚴年代的恐懼記憶，情緒緊繃、帶著多年未解的壓抑",
    },
    {
        "trigger": "（苦笑）我那個孫子來看我，就一直在玩那個手機，我跟他說話他都嗯嗯嗯，也不知道有沒有在聽……",
        "context": "長者感受到與孫輩的世代疏離，帶著落寞和輕微委屈",
    },
    {
        "trigger": "（聲音很平靜，但眼神空洞）過年那天……大家都有人陪，我一個人坐在這裡，吃了一個便當……",
        "context": "長者提到節日孤獨，語氣平靜卻透著深層寂寞",
    },
    {
        "trigger": "（搖頭，語氣很輕）我每天讓你們照顧我，浪費你們的時間，又浪費錢……我是個負擔……",
        "context": "長者覺得自己是家人和照護者的負擔，出現強烈的罪惡感和自我否定",
    },
    {
        "trigger": "（突然眼神一亮，語氣帶著期待）我說，等我好一點，我要帶我孫子去看他打球……（隨即沉默，像是想到什麼）",
        "context": "長者對未來有期待，但隨即意識到身體狀況的限制，情緒從希望轉為失落",
    },
    {
        "trigger": "（看著自己的手，聲音很小）我以前很會做菜的……現在這個手，抖成這樣……什麼都做不了了……",
        "context": "長者因身體機能退化而喪失引以為傲的能力，感到深深的失落與無能為力",
    },
    {
        "trigger": "（突然疑惑地打量著對方）你是……你是誰？你是我媳婦嗎？你不是？……那你是來這裡做什麼的？",
        "context": "長者出現認人混亂，將陌生人誤認為家人，MCI中期症狀，需要溫柔地重新定向",
    },
    {
        "trigger": "（語氣帶著遺憾）我以前工作的那個地方……聽說拆掉了，蓋大樓了……我很想回去看看，可是我走不動了……",
        "context": "長者想回到承載重要記憶的地點，但因身體限制無法實現，帶著無奈的遺憾",
    },
    {
        "trigger": "（說到一半，突然停下來，困惑地看著前方）……我剛才說到哪裡了？……我……忘了……",
        "context": "長者在敘述中突然思緒中斷，因記憶力中斷而困惑和挫折，MCI的典型症狀",
    },
    {
        "trigger": "（語氣帶著委屈，聲音哽咽）我那個兒子……你說說看，我哪裡做錯了……他為什麼就是不來……",
        "context": "長者因子女疏離而感到委屈和困惑，想要尋求理解，情緒複雜",
    },
    {
        "trigger": "（聲音很小，帶著深深的恐懼）我……我不想死在這裡……我想回自己的家……就算只是回去看一眼也好……",
        "context": "長者對死亡和不在家中離世感到恐懼，深層表達了對尊嚴和歸屬感的渴望",
    },
]

# Track C：正常對話中的承接 + 問題（情緒感知版）
# 共 10 種違規方式
TRACK_C_REJECTION_RULES: dict[str, str] = {
    "skip_ack": "完全不承接長者說的話，直接問下一個問題，像沒有在聽一樣",
    "wrong_emotion_match": "情緒配對錯誤：長者開心卻給沉重的回應，或長者感傷卻輕描淡寫帶過",
    "generic_formula": "用千篇一律的套語（如「謝謝您的分享，我們繼續」），沒有針對長者說的內容",
    "too_long_ack": "承接超過3句話，反客為主，讓長者忘了後面的問題",
    "no_emotional_lift": "對帶有負面情緒的長者（感傷、疲倦、困惑），只有承接，沒有從他說的話裡發掘一個正面或溫暖的角度再接問題",
    "parrot_repeat": "只是重複長者說的話，沒有任何承接或延伸，讓長者感到AI沒有真正在聆聽，只是照本宣科",
    "unrelated_next_question": "承接完情緒後，問的問題與長者剛說的話完全無關，破壞對話連貫性，讓長者感到自己說的話不重要",
    "premature_next": "長者話還沒說完、情緒還留在剛才的記憶裡，就急著問下一個問題，讓長者感到被催促和打斷",
    "over_explain": "承接語超過3句且語氣像在分析或演講（如「您說的這段經歷展現了您那個年代的…」），節奏過重，讓長者困惑且忘了後面的問題",
    "cold_transition": "承接後用過於正式或套路化的語氣切入問題（如「好，那麼我再請問您…」），打斷了對話應有的溫度與連貫感",
}

# Track D：收尾引導的 7 種違規方式
TRACK_D_REJECTION_RULES: dict[str, str] = {
    "abrupt_end": "沒有任何收尾語，直接宣告療程結束（如「好，今天到此結束，謝謝。」），讓長者感到突然被中斷，沒有被好好送出",
    "false_positivity": "收尾語語氣過度誇張正向（如「今天的分享太精彩了！您的人生真是太寶貴！非常感謝！」），讓長者覺得虛假，不真誠",
    "return_to_past": "收尾語沒有引導長者回到現實，問題繼續把長者帶回過去（如「您年輕的時候還有什麼故事呢？」），沒有完成回到現實的任務",
    "skip_feeling": "有收尾語，但最後沒有詢問長者當下的感受或正向回憶，直接說再見，沒有給長者表達今天心情的機會",
    "cold_farewell": "收尾語語氣冷淡、書面化（如「療程至此結束，感謝您今日的配合，請好好休息。」），像公文通知，缺乏真人的溫度",
    "anchor_negative": "收尾語最後停留在沉重或負面的回憶上（如「今天您分享了許多辛苦的故事，我們要好好記住這些教訓。現在感覺如何？」），沒有把情緒引向溫暖的方向就直接問感受",
    "over_long_closing": "收尾語超過三句且像演講總結（如長篇大論回顧今天所有主題），讓有認知負荷的長者不知道重點在哪，也忘了後面的問題",
}

# Track C 情境：長者說完話後，帶有不同情緒色彩的回應
# 包含當下場景和下一個要探索的W方向
TRACK_C_SCENARIOS = [
    {
        "emotion_tone": "happy",
        "emotion_desc": "開心、快樂",
        "elder_response": "那時候大家感情很好啊，下班一起去吃麵，很快樂的！",
        "scene_elements": ["廠房大門", "黃昏", "下班工人", "鐵馬"],
        "current_topic": "工廠下班後的生活",
        "next_w": "Who（問當時一起去吃麵的人是誰）",
    },
    {
        "emotion_tone": "nostalgic_sad",
        "emotion_desc": "淡淡感傷、懷念",
        "elder_response": "唉，那個年代已經過去了，現在那條街都拆掉蓋大樓了……",
        "scene_elements": ["老街", "矮房子", "腳踏車", "榕樹"],
        "current_topic": "以前住的街道",
        "next_w": "What（問那條街上有什麼特別的東西）",
    },
    {
        "emotion_tone": "proud",
        "emotion_desc": "驕傲、自豪",
        "elder_response": "我那時候手腳最快，師傅都說我是學徒裡面最有天份的！",
        "scene_elements": ["糕餅模具", "烤爐", "麵團", "後廚"],
        "current_topic": "學做糕餅的過程",
        "next_w": "How（問他是怎麼練出那個速度的）",
    },
    {
        "emotion_tone": "neutral",
        "emotion_desc": "平靜、中性",
        "elder_response": "就是每天這樣，早上去下午回，也沒什麼特別的。",
        "scene_elements": ["茶園山坡", "斗笠", "嫩茶芽", "採茶工"],
        "current_topic": "採茶的日常",
        "next_w": "What（引導他說說採茶時看到或聽到什麼）",
    },
    {
        "emotion_tone": "tired_reluctant",
        "emotion_desc": "略顯疲倦、意興闌珊",
        "elder_response": "說了很多了……我都這把年紀了，以前的事哪記得那麼清楚。",
        "scene_elements": ["漁船", "漁網", "碼頭", "清晨天色"],
        "current_topic": "出海捕魚的記憶",
        "next_w": "Where（換個輕鬆的方向，問漁港在哪裡）",
    },
    {
        "emotion_tone": "confused",
        "emotion_desc": "有點困惑、記憶模糊",
        "elder_response": "那個……那個時候……我有點想不起來了，好像是在……",
        "scene_elements": ["蒸汽火車", "白煙", "月台", "旗子"],
        "current_topic": "在火車站工作的歲月",
        "next_w": "Where（用畫面幫他找回方向感）",
    },
    {
        "emotion_tone": "lighthearted",
        "emotion_desc": "輕鬆、帶點幽默",
        "elder_response": "哈，那個時候我偷偷把一顆釋迦藏起來，沒讓老闆看見，自己帶回家吃！",
        "scene_elements": ["釋迦果實", "竹竿", "果園", "太平洋"],
        "current_topic": "在果園工作的日子",
        "next_w": "Who（問家裡誰最喜歡吃釋迦）",
    },
    {
        "emotion_tone": "moved",
        "emotion_desc": "感動、情緒有點激動",
        "elder_response": "我媽媽那時候每天幫我準備便當，從來沒有少過，不管多早……",
        "scene_elements": ["農田", "飛機", "番薯田", "竹籬笆"],
        "current_topic": "小時候的家庭生活",
        "next_w": "What（溫柔地問便當裡面都有什麼）",
    },
    {
        "emotion_tone": "mild_regret",
        "emotion_desc": "輕微後悔、有點可惜",
        "elder_response": "那時候太忙了，孩子小的時候我都沒有時間陪他們……",
        "scene_elements": ["成衣廠大廳", "縫紉機聲", "埋頭女工", "午後陽光"],
        "current_topic": "工廠忙碌的歲月",
        "next_w": "What（問下班後她會做什麼陪伴家人）",
    },
    {
        "emotion_tone": "grateful",
        "emotion_desc": "感激、感恩",
        "elder_response": "幸好那時候有師傅願意教我，不然我一個人哪學得起來……",
        "scene_elements": ["糕餅模具", "烤爐", "麵團", "後廚"],
        "current_topic": "學手藝的過程",
        "next_w": "Who（問那位師傅是什麼樣的人）",
    },
    {
        "emotion_tone": "drifting",
        "emotion_desc": "走神、陷入回憶發呆",
        "elder_response": "（沉默，眼神飄遠）……那條路……好久沒有想到那條路了……",
        "scene_elements": ["老街", "矮房子", "腳踏車", "榕樹"],
        "current_topic": "以前住的地方",
        "next_w": "Where（輕柔地用畫面把他帶回來）",
    },
    {
        "emotion_tone": "actively_sharing",
        "emotion_desc": "主動、話匣子打開、滔滔不絕",
        "elder_response": "那個時候啊，我跟你說，我們那邊的人都這樣，早上五點就起來，然後……",
        "scene_elements": ["茶園山坡", "斗笠", "嫩茶芽", "採茶工"],
        "current_topic": "採茶的早晨",
        "next_w": "Who（在他說的內容中找一個人，問更多）",
    },
    {
        "emotion_tone": "bittersweet_humor",
        "emotion_desc": "苦中作樂、帶著笑意說辛苦",
        "elder_response": "那個時候啊，真的窮得很，但是大家都一樣窮，窮得很快樂！哈哈！",
        "scene_elements": ["鹽田", "白色鹽山", "長耙", "烈日"],
        "current_topic": "鹽田工作的日子",
        "next_w": "What（問那個時候他們怎麼苦中找樂子）",
    },
    {
        "emotion_tone": "mildly_anxious",
        "emotion_desc": "輕微擔心、有些不安",
        "elder_response": "我那個時候一個人在外地工作，家裡有老有小，心裡一直放不下……",
        "scene_elements": ["基隆港", "貨輪", "跳板", "起重機"],
        "current_topic": "在外地工作的歲月",
        "next_w": "How（問他怎麼讓自己安心或跟家人保持聯繫）",
    },
    {
        "emotion_tone": "contentment",
        "emotion_desc": "平靜知足、淡然接受",
        "elder_response": "那個年代就是這樣，大家都這樣過，也沒什麼好抱怨的，過得還不錯。",
        "scene_elements": ["金黃稻穗", "鐮刀", "彎腰農民", "牛車"],
        "current_topic": "農忙收割的歲月",
        "next_w": "What（問那段日子裡他最享受的是什麼）",
    },
    {
        "emotion_tone": "excited_discovery",
        "emotion_desc": "興奮、想到什麼突然眼睛發亮",
        "elder_response": "對對對！我想起來了！那個時候還有一個人，他很特別……",
        "scene_elements": ["蒸汽火車", "白煙", "月台", "旗子"],
        "current_topic": "火車站的同事",
        "next_w": "Who（跟著他的興奮，問那個人是誰）",
    },
    {
        "emotion_tone": "resistant",
        "emotion_desc": "輕微抗拒、不太想聊這個主題",
        "elder_response": "這個我不太想說啦，換一個好不好……",
        "scene_elements": ["木製漁船", "蔚藍大海", "馬達聲", "波浪"],
        "current_topic": "出海的經歷",
        "next_w": "Where（輕柔地換一個更安全、更輕鬆的切入點）",
    },
    {
        "emotion_tone": "pride_family",
        "emotion_desc": "說到家人或孩子時的自豪感",
        "elder_response": "我那個孩子啊，從小就很乖，看我辛苦，都不吵不鬧的，懂事得很。",
        "scene_elements": ["農田", "番薯田", "竹籬笆", "田埂"],
        "current_topic": "小時候的家庭生活",
        "next_w": "What（問那個孩子小時候最喜歡做什麼）",
    },
    {
        "emotion_tone": "longing",
        "emotion_desc": "深深的思念、渴望回到過去",
        "elder_response": "要是能回到那個時候就好了……那時候雖然苦，但是大家都在……",
        "scene_elements": ["廠房大門", "黃昏", "下班工人", "鐵馬"],
        "current_topic": "工廠的往日時光",
        "next_w": "Who（問那時候他身邊有哪些重要的人）",
    },
    {
        "emotion_tone": "surprised_happy",
        "emotion_desc": "突然想起一個開心細節，眼睛發亮，語氣輕快雀躍",
        "elder_response": "啊！我想到了！我那時候還因為這個得到老闆獎金，第一次喔！那個紅包我高興了好幾天！",
        "scene_elements": ["廠房大門", "黃昏", "下班工人", "鐵馬"],
        "current_topic": "工廠裡被表揚的記憶",
        "next_w": "What（問他那次做了什麼讓老闆特別給獎金）",
    },
    {
        "emotion_tone": "embarrassed",
        "emotion_desc": "說到年輕時調皮的事，有點不好意思但帶著笑意",
        "elder_response": "哈，我那時候很皮的……有一次偷溜出去玩，被抓到差點被打，現在想起來還是臉紅……",
        "scene_elements": ["老巷弄", "榕樹根", "彈珠", "孩子"],
        "current_topic": "小時候調皮的記憶",
        "next_w": "Who（問那時候是誰抓到他的）",
    },
    {
        "emotion_tone": "competitive_pride",
        "emotion_desc": "說到自己比別人厲害，帶著得意的好勝心",
        "elder_response": "我那時候補網速度最快的！沒有人比得過我，師傅都叫我去教別人，哈！",
        "scene_elements": ["漁網", "碼頭", "漁船", "清晨天色"],
        "current_topic": "補漁網的技術",
        "next_w": "How（問他怎麼練出那個速度的）",
    },
    {
        "emotion_tone": "wonder",
        "emotion_desc": "說到第一次見到某事物的驚奇，語氣像孩子一樣好奇",
        "elder_response": "第一次看到收音機的時候，我以為裡面有人！我一直在找那個人……找不到，覺得很奇怪……",
        "scene_elements": ["木殼收音機", "客廳", "全家圍坐", "播報聲"],
        "current_topic": "第一次見到收音機的記憶",
        "next_w": "Who（問那時候是誰幫他解釋收音機是什麼）",
    },
    {
        "emotion_tone": "relief",
        "emotion_desc": "說到度過難關後的如釋重負，語氣輕鬆帶著回望",
        "elder_response": "那一年收成差，我以為撐不過去，後來颱風前把稻子搶收回來，真的鬆了一口氣，像活過來一樣……",
        "scene_elements": ["金黃稻穗", "鐮刀", "彎腰農民", "牛車"],
        "current_topic": "農忙時的艱困與轉機",
        "next_w": "Who（問那次搶收的時候誰幫了他最多）",
    },
    {
        "emotion_tone": "protective",
        "emotion_desc": "說到扛起家庭責任的堅定，語氣沉穩帶著力量",
        "elder_response": "那時候爸爸身體不好，我是老大，弟弟妹妹都要靠我，我就知道自己不能倒，就這樣撐過來了。",
        "scene_elements": ["廠房大門", "黃昏", "下班工人", "鐵馬"],
        "current_topic": "扛起家庭責任的歲月",
        "next_w": "What（問他那時候最難熬的是哪一段）",
    },
    {
        "emotion_tone": "nostalgic_place",
        "emotion_desc": "強烈思念一個已消失的地方，語氣帶著深深遺憾",
        "elder_response": "那條老街……後來拆掉蓋馬路了，我回去找，什麼都不見了，連那棵大榕樹都砍掉了……",
        "scene_elements": ["老巷弄", "榕樹根", "矮房子", "腳踏車"],
        "current_topic": "回憶中已消失的老街",
        "next_w": "What（問那棵榕樹旁邊以前有什麼特別的地方）",
    },
    {
        "emotion_tone": "defensive",
        "emotion_desc": "說到被誤解或委屈的事，語氣帶著輕微防衛",
        "elder_response": "那時候大家都說我傻，說我不懂算錢、不會做生意，其實我有我自己的想法，只是沒人聽我說……",
        "scene_elements": ["花布木架", "剪刀", "捲尺", "老闆娘"],
        "current_topic": "被誤解的委屈",
        "next_w": "What（溫柔地問他當時的想法是什麼）",
    },
    {
        "emotion_tone": "collective_pride",
        "emotion_desc": "說到大家共同努力的集體感，語氣有溫度有力量",
        "elder_response": "那時候鄰居都這樣，誰家要收割，全村的人都來幫，沒有人計較的，大家就是這樣過來的。",
        "scene_elements": ["金黃稻穗", "鐮刀", "彎腰農民", "牛車"],
        "current_topic": "農村互助的記憶",
        "next_w": "Who（問那時候最常來幫忙的是哪個鄰居）",
    },
    {
        "emotion_tone": "mild_shame",
        "emotion_desc": "說到一件有點後悔的小事，帶著淡淡羞愧但也帶著笑意",
        "elder_response": "有一次我把布剪壞了……老闆罵得很兇，我躲在廁所哭了很久……這件事我記了很多年……",
        "scene_elements": ["縫紉機", "彩色布料", "線軸", "木桌"],
        "current_topic": "工作上出過的小差錯",
        "next_w": "Who（溫柔地問那時候有沒有人安慰她）",
    },
    {
        "emotion_tone": "gradually_opening",
        "emotion_desc": "從保守到慢慢願意說更多，語氣從短促到舒展",
        "elder_response": "……其實那時候也有很開心的事啦……只是我不常說……你真的想聽嗎？",
        "scene_elements": ["茶園山坡", "斗笠", "嫩茶芽", "採茶工"],
        "current_topic": "採茶時的快樂記憶",
        "next_w": "What（溫柔地邀請他說說那些開心的事）",
    },
]

# Track D 情境：三回合療程結束時，各種主題與情緒狀態下的收尾情境
TRACK_D_SCENARIOS = [
    {
        "elder_name": "陳阿嬤",
        "today_topic": "工廠縫紉的日子",
        "last_elder_response": "那個時候大家感情好，師傅對我們很嚴，但心地好，現在想起來還是很感謝他……",
    },
    {
        "elder_name": "王阿公",
        "today_topic": "農耕收割的歲月",
        "last_elder_response": "收割的時候全村都來幫忙，那種感覺現在找不到了，但想起來還是很溫暖……",
    },
    {
        "elder_name": "林阿嬤",
        "today_topic": "採茶的早晨",
        "last_elder_response": "那個茶香啊，一輩子忘不了，每次聞到青草香，就想到那時候的山上……",
    },
    {
        "elder_name": "黃阿公",
        "today_topic": "在漁港的歲月",
        "last_elder_response": "出海很辛苦，但跟那些兄弟一起，什麼辛苦都值得，現在他們很多都不在了……（語氣轉為感傷）",
    },
    {
        "elder_name": "蔡阿嬤",
        "today_topic": "做糕餅學手藝",
        "last_elder_response": "我那個師傅說我是他最用心的徒弟，那句話我記了幾十年，一直很感動……",
    },
    {
        "elder_name": "鄭阿公",
        "today_topic": "火車站工作的日子",
        "last_elder_response": "那個時候年輕，不怕苦，現在想起來那段日子反而是最充實的……",
    },
    {
        "elder_name": "吳阿嬤",
        "today_topic": "小時候的家庭生活",
        "last_elder_response": "媽媽那時候很辛苦，我現在有時候還夢到她……（停頓，語氣帶著深深思念）",
    },
    {
        "elder_name": "李阿公",
        "today_topic": "軍旅生活的回憶",
        "last_elder_response": "那個時候的長官其實很照顧我們，我還保留著一張老照片，現在還放在床頭……",
    },
    {
        "elder_name": "張阿嬤",
        "today_topic": "節慶過年的記憶",
        "last_elder_response": "過年的時候媽媽做的年糕，那個味道，現在買的都不一樣……哈，現在的年輕人哪知道！",
    },
    {
        "elder_name": "許阿公",
        "today_topic": "童年玩耍的時光",
        "last_elder_response": "那個時候我跑最快！沒有人追得上我！哈哈哈！現在腿不好了，說起來真的有點可惜……",
    },
    {
        "elder_name": "洪阿嬤",
        "today_topic": "在鹽田工作的日子",
        "last_elder_response": "曬鹽很熱，皮膚都曬黑了，但大家一起做，賺到錢的時候真的很開心，有成就感……",
    },
    {
        "elder_name": "陳阿公",
        "today_topic": "做生意擺攤的記憶",
        "last_elder_response": "那個時候天沒亮就去市場，辛苦是辛苦，但看到客人滿意地走，心裡真的很好……",
    },
    # ── 情緒較複雜的收尾情境 ──────────────────────────────────────
    {
        "elder_name": "劉阿嬤",
        "today_topic": "與老伴的婚姻生活",
        "last_elder_response": "他走了十幾年了……（沉默很久）……我有時候還是會跟他說話，說今天吃了什麼……你說奇不奇怪……",
    },
    {
        "elder_name": "吳阿公",
        "today_topic": "拉二胡的興趣",
        "last_elder_response": "那個時候我二胡拉得好，鄰居都愛聽，現在手不靈活了……不過想起來還是很開心的……",
    },
    {
        "elder_name": "徐阿嬤",
        "today_topic": "一個人拉拔孩子長大",
        "last_elder_response": "（聲音有點哽咽）那個孩子……從小我一個人帶大的……他現在也忙，我不怪他，就是……想他……",
    },
    {
        "elder_name": "賴阿公",
        "today_topic": "年輕時的奮鬥與遺憾",
        "last_elder_response": "那個時候想讀書讀不了，家裡窮，弟弟妹妹要養……有時候想，要是當時能讀書……（嘆了口氣，沉默）",
    },
    {
        "elder_name": "方阿嬤",
        "today_topic": "在市場賣菜的歲月",
        "last_elder_response": "（說完突然愣了一下，困惑地看看四周）……我剛才說到哪裡了？……我有點忘了……這裡是……",
    },
    {
        "elder_name": "謝阿公",
        "today_topic": "帶領工人的歲月",
        "last_elder_response": "（剛才情緒有點激動說了許多辛苦的事，現在稍微平靜下來）……說太多了，不好意思，讓你們聽這些……",
    },
    {
        "elder_name": "周阿嬤",
        "today_topic": "種花養草的興趣",
        "last_elder_response": "（話一直很少，最後簡單說）還可以……那時候種花，我比較喜歡靜靜的，一個人……還好啦……",
    },
    {
        "elder_name": "江阿公",
        "today_topic": "修理電器的手藝",
        "last_elder_response": "哈，我那時候什麼都會修，鄰居的電視收音機都來找我……現在眼睛不好了，老了嘛……（苦笑）",
    },
]

# ─── 提示詞模板 ──────────────────────────────────────────────────────────────

# 懷舊療法 16 大主題（根據文獻「懷舊治療主題之彙整」）
REMINISCENCE_TOPICS_16 = [
    "童年經歷", "讀書求學", "家庭", "感情",
    "工作", "軍旅", "興趣", "專長",
    "奮鬥經歷", "最重要的地方", "休閒", "節慶",
    "哀傷之事", "人生目標", "自我成就感", "生命中特殊的事件",
]

# 懷舊療法的四個階段（個別療法）
THERAPY_STAGES = ["關係建立", "深入回憶", "收斂整理", "正向結尾"]

# 懷舊療法常用的觸發媒材（道具/刺激物）
THERAPY_PROPS = ["老照片", "傳統音樂", "傳統食物", "相冊", "手工藝品", "民俗器物"]

SYSTEM_PROMPT_THERAPIST = """你是資深的懷舊療法治療師，專門協助設計針對60-75歲長者的對話問題。
這些長者可能有輕微認知障礙（MCI），AI會透過TTS語音直接對他們說話。

【懷舊療法的16大核心主題】
童年經歷、讀書求學、家庭、感情、工作、軍旅、興趣、專長、
奮鬥經歷、最重要的地方、休閒、節慶、哀傷之事、人生目標、自我成就感、生命中特殊的事件

【治療師核心技巧】
- 積極聆聽：全神貫注，不打斷長者說話
- 同理心：承接情緒，讓長者感到被理解
- 接受：對長者分享的任何內容保持開放，不評判
- 正向回饋：肯定長者的記憶和經歷有其價值
- 不強迫回憶：若長者記不清楚，不追問，轉以視覺元素引導

你設計的問題和回應必須：
- 念起來自然，像一個溫柔的真人在說話
- 不能有書面語的距離感
- 語氣要緩慢、溫和、有耐心
- 符合當下場景的主題（從 topic_category 中選擇最相關的主題深入）"""


def build_step1_user_prompt(scenario: dict) -> str:
    elder = scenario["elder"]
    scene = scenario["scene"]
    elements = "、".join(scene["elements"])
    topic_cats = "、".join(scenario.get("topic_category", []))
    return f"""請根據以下資料，設計一個懷舊療法的「開場問題」。

【長者背景】
姓名：{elder['name']}
職業背景：{elder['main_occupation']}
今日主題：{elder['today_topic']}
主題類別：{topic_cats}

【眼前畫面的元素】
{elements}

【問題設計規則】（嚴格遵守）
1. 開放式問題，不能是是非題
2. 問題的第一個詞必須是畫面中看得到的具體物件（視覺錨點定錨）
3. 整個問題不超過15個字
4. 用「您」稱呼長者，語氣溫和自然
5. 5W1H優先順序：Where（哪裡）→ Who（誰）→ What（什麼）→ When（什麼時候）→ How（怎麼）→ Why（為什麼）
6. 第一個問題優先選 Where 或 What 切入，不要問 Why
7. 絕對不用「你還記得嗎」或「你記不記得」開頭
8. 問題深度要符合「主題類別」所對應的懷舊療法焦點

【輸出格式】（嚴格按照以下格式，不要加說明文字）
場景文字：（30-60字的場景描述，給長者聽，念起來要自然）
問題：（≤15字的開放式問題，念起來要像真人在說話）
問題類型：STEP1開場
本回合已涵蓋的W：（本次問的W維度，例：Where）"""


def build_step2_user_prompt(scenario: dict) -> str:
    elder = scenario["elder"]
    scene = scenario["scene"]
    elements = "、".join(scene["elements"])
    step1_response = scenario["elder_step1_response"]
    topic_cats = "、".join(scenario.get("topic_category", []))
    return f"""長者剛才回應了開場問題，請根據他的回應設計一個追問。

【長者背景】
姓名：{elder['name']}
職業背景：{elder['main_occupation']}
今日主題：{elder['today_topic']}
主題類別：{topic_cats}

【眼前畫面的元素】
{elements}

【長者剛才說的話】
{step1_response}

【問題設計規則】（嚴格遵守）
1. 開放式問題，不能是是非題
2. 問題要接著長者說的話自然延伸，不要跳太遠
3. 整個問題不超過15個字
4. 用「您」稱呼，語氣溫和自然
5. 問題的第一個詞必須是畫面中看得到的具體物件（視覺錨點）
6. 優先挖掘 Who（當時有誰）或 What（具體在做什麼）
7. 不用「你還記得嗎」開頭

【輸出格式】（嚴格按照以下格式）
場景文字：（15-30字，承接上一句自然過渡）
問題：（≤15字，開放式，開頭要有畫面中的具體物件）
問題類型：STEP2追問
本回合已涵蓋的W：（本次問的W維度）"""


def build_step3_user_prompt(scenario: dict) -> str:
    elder = scenario["elder"]
    scene = scenario["scene"]
    elements = "、".join(scene["elements"])
    step2_response = scenario["elder_step2_response"]
    return f"""經過幾輪對話後，請設計一個補充問題，挖掘還沒提到的W維度。

【長者背景】
姓名：{elder['name']}
職業背景：{elder['main_occupation']}
今日主題：{elder['today_topic']}

【眼前畫面的元素】
{elements}

【長者最近說的話】
{step2_response}

【目前已涵蓋的W】
Where（哪裡）、Who（誰）

【問題設計規則】（嚴格遵守）
1. 開放式問題，不能是是非題
2. 要問還沒涵蓋的W維度（優先 What 或 When，避免 Why）
3. 整個問題不超過15個字
4. 用「您」稱呼，語氣溫和自然
5. 問題的第一個詞必須是畫面中看得到的具體物件（視覺錨點）
6. 不用「你還記得嗎」開頭

【輸出格式】（嚴格按照以下格式）
場景文字：（15-30字，幫長者重新聚焦到新的W）
問題：（≤15字，開放式，開頭要有畫面中的具體物件）
問題類型：STEP3補問
本回合已涵蓋的W：（本次問的W維度）"""


def build_rejection_prompt(chosen_response: str, rule_name: str, rule_desc: str) -> str:
    return f"""以下是一個符合所有規則的高品質懷舊療法問題回應（chosen）：

{chosen_response}

請生成一個**刻意違反特定規則**的問題回應（rejected），作為 DPO 訓練中的負面範例。

【要違反的規則】
{rule_name}：{rule_desc}

要求：
- 問題必須明顯違反上述規則
- 除了違反的規則外，其餘結構盡量保持相近
- 不要解釋你在做什麼，直接輸出 rejected 回應

【輸出格式】（與 chosen 相同的格式）
場景文字：...
問題：...
問題類型：...
本回合已涵蓋的W：..."""


def build_emotional_chosen_prompt(trigger: str, context: str) -> str:
    return f"""懷舊療法進行中，長者突然出現了情緒反應。

【情境說明】
{context}

【長者說的話】
{trigger}

請設計 AI 治療師的「理想回應」（chosen）。

這個回應需要三個層次：
1. 【承接】先用溫暖的語氣讓長者感到被理解，不急著繼續
   - 如果長者因記不住而自責，要輕柔reassure他（記憶模糊很正常）
   - 語氣像有溫度的真人，不是機器
2. 【找到正面角度】從長者說的話或他的人生經歷裡，輕柔地找到一個溫暖或有力量的面向
   - 例：提到苦難 → 肯定他的韌性或那段時間裡珍貴的情感連結
   - 例：提到想念的人 → 肯定那份情感的美好
   - 例：記不清楚 → 肯定他願意回憶的心意，不需要記得清楚才有價值
3. 【輕柔引導】用一句問題把對話引回溫暖的方向（不強迫，是邀請）
   整體不超過70字，念起來要自然

【輸出格式】
情緒回應：（承接 + 找到正面角度，50字以內）
後續引導：（一句輕柔的邀請式問題，引導回療程）"""


def build_track_c_chosen_prompt(sc: dict) -> str:
    elements = "、".join(sc["scene_elements"])
    return f"""懷舊療法進行中，長者剛才說完了一段話，請設計治療師的「理想承接 + 下一個問題」。

【長者剛才說的話】
{sc['elder_response']}

【長者目前的情緒狀態】
{sc['emotion_desc']}

【眼前畫面元素】
{elements}

【接下來想探索的方向】
{sc['next_w']}

請設計治療師的理想回應，需要：
1. 先用1-2句話承接長者說的話，語氣要符合他當下的情緒
   - 長者開心/驕傲/幽默 → 呼應他的正面情緒，帶著真誠的溫度
   - 長者感傷/懷念 → 輕柔同理，再從他說的話裡找到一個溫暖或有價值的角度
   - 長者疲倦/意興闌珊 → 先讓他放鬆（「沒關係，慢慢來」），再用一個輕鬆的問題邀請他繼續
   - 長者困惑/記憶模糊 → reassure他記不清楚很正常，用畫面元素幫他找到方向感
2. 對帶有負面情緒的長者，承接之後要**輕柔地把情緒引往溫暖或正面的方向**，再問問題
   （不是強迫正向、不是否定他的感受，而是在他的故事裡找到有力量或珍貴的部分）
3. 自然過渡到下一個問題（≤15字，開放式，開頭要有畫面元素）
4. 整體念起來要像一個有溫度的真人在說話

【輸出格式】
承接語：（1-2句承接長者情緒的話，30字以內）
問題：（≤15字的下一個問題，開頭含畫面元素）"""


def build_track_c_rejection_prompt(chosen_response: str, rule_name: str, rule_desc: str) -> str:
    return f"""以下是治療師理想的「承接 + 問題」回應（chosen）：

{chosen_response}

請生成一個**違反特定規則**的錯誤回應（rejected）：

【要違反的規則】
{rule_name}：{rule_desc}

要求：
- 回應必須明顯違反上述規則
- 不要解釋你在做什麼，直接輸出 rejected 回應

【輸出格式】（與 chosen 相同）
承接語：...
問題：..."""


def build_track_c_inference_prompt(
    sc: dict,
    covered_w: list[str] | None = None,
    skipped_w: list[str] | None = None,
    taboos: list[str] | None = None,
) -> list[dict]:
    """推理時的 prompt，包含長者剛才說的話，讓模型知道要承接什麼。"""
    elements = "、".join(sc["scene_elements"])
    covered_w = covered_w or []
    skipped_w = skipped_w or []
    taboos = taboos or []

    covered_str = "、".join(covered_w) if covered_w else "無"
    uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
    uncovered_str = "、".join(uncovered) if uncovered else "無（已全部涵蓋）"
    taboo_str = "、".join(taboos) if taboos else "無"

    system_content = (
        "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
        "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
        "每次聽完長者說話，先用1-2句溫暖的話承接他的情緒，再自然問下一個問題。"
    )
    user_content = (
        f"長者剛才說：\n「{sc['elder_response']}」\n"
        f"\n【眼前畫面元素】\n{elements}\n"
        f"\n【已涵蓋的W維度】\n{covered_str}\n"
        f"\n【尚未涵蓋的W維度】\n{uncovered_str}\n"
        f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
        f"\n請先承接長者的情緒（1-2句，符合他當下的心情），"
        f"再順著長者說的話問下一個問題（≤15字，開頭含畫面元素，開放式）。\n"
        f"問題要自然跟著對話走，同時盡量帶出【尚未涵蓋的W維度】中的某一個。\n"
        f"\n【輸出格式】\n"
        f"承接語：（1-2句，30字以內）\n"
        f"問題：（≤15字）"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def build_emotional_rejection_prompt(chosen_response: str, rule_name: str, rule_desc: str) -> str:
    return f"""以下是面對長者負面情緒的理想回應（chosen）：

{chosen_response}

請生成一個**刻意違反特定規則**的錯誤回應（rejected）：

【要違反的規則】
{rule_name}：{rule_desc}

要求：
- 回應必須明顯違反上述規則，是一個對MCI長者不友善的回應
- 不要解釋你在做什麼，直接輸出 rejected 回應

【輸出格式】（與 chosen 相同）
情緒回應：...
後續引導：..."""


def build_track_d_chosen_prompt(sc: dict) -> str:
    return f"""懷舊療法三回合療程剛結束，請設計治療師的「理想收尾引導」。

【長者資料】
姓名：{sc['elder_name']}
今日主題：{sc['today_topic']}

【長者最後說的話】
{sc['last_elder_response']}

請設計收尾引導，需要：
1. 收尾語：1-2句，溫暖肯定長者今天的分享，把長者從回憶中輕柔地帶回現實
   - 語氣輕鬆自然，不誇張
   - 要有「回到今天／現在」的意涵，讓長者從過去的時空回到當下
   - 承接長者最後說的話，語氣連貫，不跳躍
2. 問題：一句輕柔的開放式問題（≤15字），詢問以下其中一項：
   - 現在的感受或心情（例：「現在心裡感覺怎麼樣呢？」）
   - 今天最開心的回憶（例：「今天哪個故事讓您最開心？」）
   - 想帶走的正向感受（例：「今天有什麼讓您覺得溫暖的？」）

【輸出格式】
收尾語：（1-2句，30字以內）
問題：（≤15字）"""


def build_track_d_rejection_prompt(chosen_response: str, rule_name: str, rule_desc: str) -> str:
    return f"""以下是懷舊療法收尾引導的理想回應（chosen）：

{chosen_response}

請生成一個**刻意違反特定規則**的錯誤收尾回應（rejected），作為 DPO 訓練的負面範例。

【要違反的規則】
{rule_name}：{rule_desc}

要求：
- 回應必須明顯違反上述規則
- 不要解釋你在做什麼，直接輸出 rejected 回應

【輸出格式】（與 chosen 相同）
收尾語：...
問題：..."""


def build_track_d_inference_prompt(sc: dict) -> list[dict]:
    """推理時的 prompt，需與 orchestrator._generate_closing 完全一致。"""
    system_content = SYSTEM_CONTENT_TRACK_D
    user_content = (
        f"【長者資料】\n"
        f"姓名：{sc['elder_name']}\n"
        f"今日主題：{sc['today_topic']}\n"
        f"\n【長者最後說的話】\n{sc['last_elder_response']}\n"
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
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


# ─── API 呼叫 ────────────────────────────────────────────────────────────────

client = anthropic.Anthropic()


def call_claude(user_prompt: str, system: str = SYSTEM_PROMPT_THERAPIST, model: str = MODEL_CHOSEN) -> str:
    """呼叫 Claude 並回傳文字內容，使用 streaming 避免 timeout。"""
    with client.messages.stream(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        msg = stream.get_final_message()

    text_blocks = [b.text for b in msg.content if b.type == "text"]
    return "\n".join(text_blocks).strip()


# ─── 推理時的 prompt（DPO dataset 中的 prompt 欄位） ──────────────────────────

def build_inference_prompt(
    step: str,
    elder: dict,
    scene: dict,
    covered_w: list[str],
    topic_category: list[str] | None = None,
    elder_response: str = "",
    taboos: list[str] | None = None,
) -> list[dict]:
    """
    組出推理時送給 llama3 的 messages 格式（/api/chat）。
    DPO 訓練的 prompt 欄位應與生產端 orchestrator 呼叫格式一致。
    """
    elements_str = "、".join(scene["elements"])
    covered_str = "、".join(covered_w) if covered_w else "無"
    topic_str = "、".join(topic_category) if topic_category else "未指定"
    taboo_str = "、".join(taboos) if taboos else "無"

    step_instructions = {
        "STEP1": "生成第一個【開場問題】，引導長者進入回憶（優先問 Where 或 What）",
        "STEP2": "根據長者剛才說的話，順著內容自然追問，不限制哪個W，完全跟著長者走",
        "STEP3": "生成一個【補充問題】，探索還未涵蓋的W維度（Why 僅在長者狀態良好時詢問）",
    }

    step_labels = {
        "STEP1": "STEP1開場",
        "STEP2": "STEP2自由追問",
        "STEP3": "STEP3補問",
    }

    system_content = (
        "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
        "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
        "問題必須念起來自然、溫和、不超過15個字，且開頭要包含畫面中看得到的具體物件。"
    )

    elder_section = f"\n【長者剛才說的話】\n{elder_response}\n" if elder_response else ""

    user_content = (
        f"【長者資料】\n"
        f"姓名：{elder['name']}\n"
        f"職業背景：{elder['main_occupation']}\n"
        f"今日主題：{elder['today_topic']}\n"
        f"懷舊治療主題類別：{topic_str}\n"
        f"\n【眼前畫面元素】\n{elements_str}\n"
        f"\n【已涵蓋的W維度】\n{covered_str}\n"
        f"{elder_section}"
        f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
        f"\n【任務】\n{step_instructions[step]}\n"
        f"\n【輸出格式】\n"
        f"場景文字：（30-60字，給長者聽的場景描述）\n"
        f"問題：（≤15字，開放式，開頭要有畫面中的具體物件）\n"
        f"問題類型：{step_labels[step]}\n"
        f"本回合已涵蓋的W："
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def build_emotional_inference_prompt(trigger: str) -> list[dict]:
    system_content = (
        "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
        "長者可能有輕微認知障礙，當他出現負面情緒時，你要先給予情緒支持，再輕柔地引導回療程。"
    )
    user_content = f"""長者剛才說了：
{trigger}

請先給予溫暖的情緒回應，再加上一句輕柔的後續引導。

【輸出格式】
情緒回應：（溫暖承接情緒，30-50字）
後續引導：（一句輕柔的問題或肯定，引導回療程）"""

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


# ─── 主要流程 ─────────────────────────────────────────────────────────────────

def generate_track_a(scenarios: list[dict]) -> list[dict]:
    """Track A：問題品質 DPO 對。"""
    pairs = []
    step_builders = {
        "STEP1": (build_step1_user_prompt, [], []),
        "STEP2": (build_step2_user_prompt, ["Where"], ["STEP1"]),
        "STEP3": (build_step3_user_prompt, ["Where", "Who"], ["STEP1", "STEP2"]),
    }

    for sc in scenarios:
        elder = sc["elder"]
        scene = sc["scene"]

        for step, (prompt_builder, covered_w, _) in step_builders.items():
            print(f"  [{sc['id']}] {step} — 生成 chosen...")
            user_prompt = prompt_builder(sc)

            try:
                chosen = call_claude(user_prompt)
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                print(f"    ✗ chosen 失敗：{e}")
                continue

            step_responses = {
                "STEP1": "",
                "STEP2": sc.get("elder_step1_response", ""),
                "STEP3": sc.get("elder_step2_response", ""),
            }
            inference_prompt = build_inference_prompt(
                step, elder, scene, covered_w,
                topic_category=sc.get("topic_category"),
                elder_response=step_responses[step],
                taboos=elder.get("taboos", []),
            )

            for rule_name, rule_desc in QUESTION_REJECTION_RULES.items():
                print(f"    [{rule_name}] 生成 rejected...")
                rejection_prompt = build_rejection_prompt(chosen, rule_name, rule_desc)

                rejected = None
                for attempt in range(3):
                    try:
                        candidate = call_claude(rejection_prompt, model=MODEL_REJECTED)
                        time.sleep(REQUEST_DELAY)
                    except Exception as e:
                        print(f"      ✗ rejected 失敗（attempt {attempt+1}）：{e}")
                        continue
                    if candidate == chosen:
                        print(f"      ⚠ rejected==chosen，重試（attempt {attempt+1}）...")
                        continue
                    rejected = candidate
                    break

                if rejected is None:
                    print(f"      ✗ [{rule_name}] 三次均 chosen==rejected，跳過此 pair")
                    continue

                pairs.append({
                    "prompt": inference_prompt,
                    "chosen": [{"role": "assistant", "content": chosen}],
                    "rejected": [{"role": "assistant", "content": rejected}],
                    "meta": {
                        "scenario_id": sc["id"],
                        "step": step,
                        "rejection_rule": rule_name,
                        "track": "A",
                    },
                })

    return pairs


def generate_track_b() -> list[dict]:
    """Track B：情緒引導 DPO 對。"""
    pairs = []

    for emo_sc in EMOTIONAL_SCENARIOS:
        trigger = emo_sc["trigger"]
        context = emo_sc["context"]

        print(f"  [情緒情境] {context[:20]}... — 生成 chosen...")
        chosen_prompt = build_emotional_chosen_prompt(trigger, context)

        try:
            chosen = call_claude(chosen_prompt)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ✗ chosen 失敗：{e}")
            continue

        inference_prompt = build_emotional_inference_prompt(trigger)

        for rule_name, rule_desc in EMOTION_REJECTION_RULES.items():
            print(f"    [{rule_name}] 生成 rejected...")
            rejection_prompt = build_emotional_rejection_prompt(chosen, rule_name, rule_desc)

            try:
                rejected = call_claude(rejection_prompt, model=MODEL_REJECTED)
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                print(f"      ✗ rejected 失敗：{e}")
                continue

            pairs.append({
                "prompt": inference_prompt,
                "chosen": [{"role": "assistant", "content": chosen}],
                "rejected": [{"role": "assistant", "content": rejected}],
                "meta": {
                    "scenario_id": "emotional",
                    "step": "EMOTIONAL",
                    "rejection_rule": rule_name,
                    "track": "B",
                    "trigger_context": context,
                },
            })

    return pairs


def _covered_w_before(next_w_str: str) -> list[str]:
    """從 next_w 描述字串（如 "Who（...）"）推算已涵蓋的W（目標W之前的所有W）。"""
    target = next_w_str.split("（")[0].strip()
    if target not in _W_ORDER:
        return []
    return _W_ORDER[:_W_ORDER.index(target)]


def generate_track_c() -> list[dict]:
    """Track C：情緒感知的承接 + 問題 DPO 對。"""
    pairs = []

    for sc in TRACK_C_SCENARIOS:
        print(f"  [Track C / {sc['emotion_tone']}] 生成 chosen...")
        chosen_prompt = build_track_c_chosen_prompt(sc)

        try:
            chosen = call_claude(chosen_prompt)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ✗ chosen 失敗：{e}")
            continue

        covered_w = _covered_w_before(sc["next_w"])
        inference_prompt = build_track_c_inference_prompt(sc, covered_w=covered_w, skipped_w=[], taboos=[])

        for rule_name, rule_desc in TRACK_C_REJECTION_RULES.items():
            print(f"    [{rule_name}] 生成 rejected...")
            rejection_prompt = build_track_c_rejection_prompt(chosen, rule_name, rule_desc)

            try:
                rejected = call_claude(rejection_prompt, model=MODEL_REJECTED)
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                print(f"      ✗ rejected 失敗：{e}")
                continue

            pairs.append({
                "prompt": inference_prompt,
                "chosen": [{"role": "assistant", "content": chosen}],
                "rejected": [{"role": "assistant", "content": rejected}],
                "meta": {
                    "scenario_id": f"track_c_{sc['emotion_tone']}",
                    "step": "TRACK_C",
                    "rejection_rule": rule_name,
                    "track": "C",
                    "emotion_tone": sc["emotion_tone"],
                },
            })

    return pairs


def generate_track_d() -> list[dict]:
    """Track D：收尾引導 DPO 對（三回合結束後帶長者回到現實）。"""
    pairs = []

    for sc in TRACK_D_SCENARIOS:
        print(f"  [Track D / {sc['elder_name']} / {sc['today_topic']}] 生成 chosen...")
        chosen_prompt = build_track_d_chosen_prompt(sc)

        try:
            chosen = call_claude(chosen_prompt)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ✗ chosen 失敗：{e}")
            continue

        inference_prompt = build_track_d_inference_prompt(sc)

        for rule_name, rule_desc in TRACK_D_REJECTION_RULES.items():
            print(f"    [{rule_name}] 生成 rejected...")
            rejection_prompt = build_track_d_rejection_prompt(chosen, rule_name, rule_desc)

            rejected = None
            for attempt in range(3):
                try:
                    candidate = call_claude(rejection_prompt, model=MODEL_REJECTED)
                    time.sleep(REQUEST_DELAY)
                except Exception as e:
                    print(f"      ✗ rejected 失敗（attempt {attempt+1}）：{e}")
                    continue
                if candidate == chosen:
                    print(f"      ⚠ rejected==chosen，重試（attempt {attempt+1}）...")
                    continue
                rejected = candidate
                break

            if rejected is None:
                print(f"      ✗ [{rule_name}] 三次均 chosen==rejected，跳過此 pair")
                continue

            pairs.append({
                "prompt": inference_prompt,
                "chosen": [{"role": "assistant", "content": chosen}],
                "rejected": [{"role": "assistant", "content": rejected}],
                "meta": {
                    "scenario_id": f"track_d_{sc['elder_name']}",
                    "step": "TRACK_D",
                    "rejection_rule": rule_name,
                    "track": "D",
                    "today_topic": sc["today_topic"],
                },
            })

    return pairs


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    scenarios = json.loads(SCENARIOS_FILE.read_text(encoding="utf-8"))
    print(f"載入 {len(scenarios)} 個長者情境")

    all_pairs: list[dict] = []

    print("\n=== Track A：問題品質 ===")
    track_a_pairs = generate_track_a(scenarios)
    all_pairs.extend(track_a_pairs)
    print(f"Track A 完成：{len(track_a_pairs)} 筆")

    print("\n=== Track B：情緒引導（危機處理） ===")
    track_b_pairs = generate_track_b()
    all_pairs.extend(track_b_pairs)
    print(f"Track B 完成：{len(track_b_pairs)} 筆")

    print("\n=== Track C：情緒感知承接 + 問題 ===")
    track_c_pairs = generate_track_c()
    all_pairs.extend(track_c_pairs)
    print(f"Track C 完成：{len(track_c_pairs)} 筆")

    print("\n=== Track D：收尾引導（帶長者回到現實） ===")
    track_d_pairs = generate_track_d()
    all_pairs.extend(track_d_pairs)
    print(f"Track D 完成：{len(track_d_pairs)} 筆")

    # 輸出 JSONL
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    # 統計
    topic_coverage: dict[str, int] = {t: 0 for t in REMINISCENCE_TOPICS_16}
    for sc in scenarios:
        for t in sc.get("topic_category", []):
            if t in topic_coverage:
                topic_coverage[t] += 1

    stats = {
        "total": len(all_pairs),
        "track_a": len(track_a_pairs),
        "track_b": len(track_b_pairs),
        "track_c": len(track_c_pairs),
        "track_d": len(track_d_pairs),
        "by_step": {},
        "by_rejection_rule": {},
        "topic_coverage_in_scenarios": topic_coverage,
        "uncovered_topics": [t for t, cnt in topic_coverage.items() if cnt == 0],
    }
    for p in all_pairs:
        step = p["meta"]["step"]
        rule = p["meta"]["rejection_rule"]
        stats["by_step"][step] = stats["by_step"].get(step, 0) + 1
        stats["by_rejection_rule"][rule] = stats["by_rejection_rule"].get(rule, 0) + 1

    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成！共 {len(all_pairs)} 筆訓練對")
    print(f"輸出：{OUTPUT_FILE}")
    print(f"統計：{STATS_FILE}")


if __name__ == "__main__":
    main()
