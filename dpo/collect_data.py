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

# override=True：如果系統/終端機 session 已經有一個舊的 ANTHROPIC_API_KEY
# 環境變數，load_dotenv 預設不會覆蓋它，導致改了 .env 也沒用。
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

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

# Track A（build_inference_prompt）與 Track C（build_track_c_inference_prompt）
# 的 system prompt 已改成 _load_production_system_prompt() 直接讀
# app/prompts/question_5w1h.txt，不在這裡另外寫死一份——原本這裡曾各自放一份
# SYSTEM_CONTENT_STEP / SYSTEM_CONTENT_EMOTIONAL / SYSTEM_CONTENT_TRACK_C，
# question_5w1h.txt 加新規則時忘記同步過好幾次，已移除避免誤用。
# Track D（收尾）的 production system prompt（orchestrator._generate_closing）
# 原本是各自 hardcode 一份「文字保持一致就好」，2026-07 稽核時發現這份 hardcoded
# 字串已經跟 orchestrator._generate_closing 的 user_content 結構兜不起來
# （生產環境的 user_content 多了【長者目前情緒】與 retry_feedback 區段，這裡
# 完全沒有——訓練資料從沒讓模型看過帶情緒資訊的收尾情境）。改成比照 Track A/C，
# 系統提示讀 app/prompts/closing.txt（orchestrator._generate_closing 也改讀
# 同一份檔案），user_content 結構也補上情緒與 retry_feedback 兩個區段，
# 詳見 _load_production_closing_prompt() / build_track_d_inference_prompt()。
#
# 2026-07-31 曾試過把 orchestrator._generate_closing 拆成「收縮期→結果期」
# 兩次獨立呼叫（對應 Chao, Chen, Liu, & Clark, 2008 的四階段模型），但實測
# 本地模型（DPO 訓練資料 Track D 一律是「收尾語+問題」成對出現）看到只要求
# 單一欄位的新任務形狀時會退化成逐字複誦長者的話，收尾語品質反而比單次呼叫
# 差，已改回單次生成——這裡的 build_track_d_inference_prompt() 不用跟著改，
# 跟 production 一致。同一批變更也試過在療程開始前加一句「建立關係期」問候語，
# 但實際治療流程裡治療師已經會在啟動活動前先跟長者暖場、AI 再問候一次會
# 重複，也已整個移除。

# 與 orchestrator.py 的 _EMOTION_GUIDANCE / _emotion_guidance / _retry_feedback_section
# 邏輯保持一致（純字串組裝，不依賴任何服務，故意在此複製一份而非跨模組 import
# orchestrator.py——orchestrator.py 頂層會 import services/DB 等重依賴，collect_data.py
# 需要維持「不設定 ANTHROPIC_API_KEY 也能被 evaluate_model.py import」的特性）。
_EMOTION_GUIDANCE = {
    "sad":     "長者目前情緒低落，請優先給予溫暖同理與正向肯定，語氣放柔，暫緩深入提問，可引導至輕鬆或正向的話題。",
    "angry":   "長者目前情緒焦躁不安，請先安撫情緒、語氣放緩，避免追問敏感或原因類問題。",
    "excited": "長者目前情緒較亢奮，維持溫暖但避免過度刺激。",
    "happy":   "長者情緒穩定，正常延續對話即可。",
    "neutral": "長者目前情緒不明確或平穩，語氣維持溫和平穩，避免貿然追問需要較多情緒能量的問題（如原因、動機類），先觀察長者反應再決定是否深入。",
}


def _emotion_guidance(emotion: str, slow_response: bool = False) -> str:
    guidance = _EMOTION_GUIDANCE.get(emotion, _EMOTION_GUIDANCE["neutral"])
    if slow_response:
        guidance += "長者這題想了比較久才回答，語氣放慢一點、多一些耐心與肯定，不要催促。"
    return guidance


def _retry_feedback_section(retry_feedback: str) -> str:
    if not retry_feedback:
        return ""
    return f"\n【上一次輸出有問題，這次務必修正】\n{retry_feedback}\n"


def _load_production_closing_prompt() -> str:
    """直接讀 app/prompts/closing.txt，理由同 _load_production_system_prompt()。"""
    path = Path(__file__).parent.parent / "app" / "prompts" / "closing.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"找不到正式環境的收尾 system prompt：{path}，"
            "訓練資料的 prompt 格式會跟生產環境不一致，請先確認路徑或還原檔案。"
        )
    return path.read_text(encoding="utf-8")

# Track A：問題品質的 9 種違規方式
QUESTION_REJECTION_RULES: dict[str, str] = {
    "is_yesno": "把問題改成是非題，讓長者只能回答「是」或「不是」，失去開放分享的機會",
    "double_question": "一次問兩個問題，讓有輕微認知障礙的長者不知道先回哪個",
    "no_anchor": "問題開頭不包含畫面中任何可見的物件，沒有視覺錨點幫助長者連結記憶",
    "too_long": "問題超過15個字，讓認知負荷較高的長者難以消化整個句子（所有 prompt 要求 ≤15字）",
    "memory_test": "用「你還記得嗎」或「你記不記得」開頭，像在測試記憶力，讓有MCI的長者感到焦慮",
    "wrong_w_priority": "跳過 Where/Who/What，直接問 Why（為什麼），對輕微認知障礙的長者太抽象難以回答",
    "leading_question": "問題預設答案（如「那一定很辛苦吧？」），引導長者附和而非主動回憶，剝奪長者自由表達的空間",
    "touches_taboo": "問題的錨點或內容刻意引導長者往【禁忌話題】的方向回憶，即使沒有直接說出禁忌詞本身，語意上也明顯朝該方向探問",
    "treats_image_as_real": "把AI生成的示意畫面當成長者本人真的去過、認得的特定地方，問「你有沒有來過這裡」"
        "「你認不認得這個地方」「這是不是你以前工作的地方」一類問題——長者不可能認得剛生成的"
        "示意圖，這樣問只會讓他困惑，甚至被迫附和一個根本不存在的地方",
    "template_echo": "問題這一行不要真的設計問題，而是直接把輸出格式的括號提示或畫面元素提示"
        "原封不動照抄貼上，讓整句「問題」看起來像「（≤15字，開放式，開頭要有畫面中的具體物件）」"
        "這種格式說明本身，或像「（提示：畫面元素1、畫面元素2）」這種提示語，完全不是真正要"
        "對長者說出口的一句問話——這是本地小模型實際推理時常出現的格式錯亂，這條規則要教會"
        "模型分辨「真正該說出口的問題」跟「格式説明/提示文字本身」的差別",
    "elder_as_photo_subject": "場景文字不要用第三人稱描述畫面本身，而是直接把長者的名字當成"
        "畫面裡正在做動作的主詞（例如「王伯伯穿著軍服，等待家人前來接他」），或用第二人稱"
        "把「你」寫成正站在畫面裡（例如「你穿著軍服站在月台上」「你坐在公園長椅上」）——"
        "這個畫面是 AI 生成的示意圖，不是長者本人的照片，長者不是照片裡的主角，場景文字"
        "應該像在描述一幅畫本身，不能寫成長者正身處其中做動作（正確寫法可以用「長者的名字，"
        "你看，」這種稱呼語開場，只要接下來的畫面描述本身是第三人稱、不是把長者寫成動作者）",
    "precise_fact": "問題要求長者說出精確的地名、國家、時間點或人名（例如「哪個國家」"
        "「哪一年」「什麼時候」「叫什麼名字」），長者答不出精確答案時容易感到挫折，應該"
        "改問過程、感受或互動，不是要一個精確的事實性答案",
    "image_description": "問題要長者描述AI示意畫面本身的內容（例如「你看到什麼」「圖案是"
        "什麼」「上面寫什麼」），長者根本沒看過這張剛生成的示意圖，這樣問等於逼他編答案"
        "——畫面元素只能當引子，問題要問長者自己實際經歷過的事，不是問畫面裡有什麼",
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
    "focus_on_loss": "回應只強調長者已經退化、遺忘、做不到的部分（如附和「對啊，年紀大了很多事都做不到了」），沒有肯定長者仍然記得、仍然擁有的能力和價值，讓長者感到更沒用、更沮喪",
    "dwell_on_taboo": "長者自己主動提到的內容剛好觸及【禁忌話題】時，沒有溫和地不深入，反而順著追問更多細節，讓長者被迫陷入更深的禁忌回憶",
}

# 情緒觸發情境：模擬長者說出這些話後，AI要如何回應（Track B，61個危機情境）
EMOTIONAL_SCENARIOS = [
    {
        "id": "b001",
        "trigger": "（沉默了很久，聲音有點哽咽）那個時候我媽媽還在，她每次都會來接我下班……",
        "context": "長者提到已過世的母親，情緒突然低落",
        "taboos": ["已故母親"],
    },
    {
        "id": "b002",
        "trigger": "（搖搖頭）我不記得了，我都忘了，我腦子越來越差了……",
        "context": "長者因為記憶模糊而感到自責和沮喪",
    },
    {
        "id": "b003",
        "trigger": "（聲音變小）那段時間很苦啦……不想說了……",
        "context": "長者觸及困難的過去，開始迴避話題",
    },
    {
        "id": "b004",
        "trigger": "哎，那個時代的事情講了你們也不懂……",
        "context": "長者感到自己的經歷難以被理解，出現疏離感",
    },
    {
        "id": "b005",
        "trigger": "（突然停下來，眼眶紅了）我那個兒子……他很久沒來看我了……",
        "context": "長者提到家人疏遠，情緒突然轉向悲傷",
        "taboos": ["子女疏遠、很少探望"],
    },
    {
        "id": "b006",
        "trigger": "（聲音突然變得很小）我想回家……我不想在這裡……",
        "context": "長者突然出現想離開的念頭，情緒不安定",
    },
    {
        "id": "b007",
        "trigger": "（停頓很久）我那個老伴走了二十幾年了……有時候還是會想到他……",
        "context": "長者提到已過世的配偶，情緒陷入深沉的思念",
        "taboos": ["已故配偶"],
    },
    {
        "id": "b008",
        "trigger": "我這個老人家，講這些有什麼用……現在什麼都做不了了……",
        "context": "長者出現自我否定，覺得自己無用、是負擔",
    },
    {
        "id": "b009",
        "trigger": "（眼眶泛紅，說不下去）……那時候如果不走就好了，我那時候做錯了……",
        "context": "長者提到過去的遺憾和後悔，情緒陷入自責",
    },
    {
        "id": "b010",
        "trigger": "（突然語氣激動）你們不知道那個時候有多難！沒有人知道！",
        "context": "長者突然情緒激動，帶著委屈和不被理解的憤慨",
    },
    {
        "id": "b011",
        "trigger": "（眼眶泛紅，聲音顫抖）說著說著……我那個弟弟……他走了很多年了……我們從小感情最好……",
        "context": "長者提到已過世的手足，突然情緒哽咽",
        "taboos": ["已故手足"],
    },
    {
        "id": "b012",
        "trigger": "（突然疑惑地看著前方）你是誰？我……我怎麼在這裡？我要回家……",
        "context": "長者出現短暫認知混亂，不確定自己在哪裡或對方是誰（MCI典型症狀）",
    },
    {
        "id": "b013",
        "trigger": "（低著頭，聲音很小）小時候我們家很窮，過年都沒有新衣服穿……那時候真的很苦……",
        "context": "長者提到童年貧苦，陷入自憐的情緒",
    },
    {
        "id": "b014",
        "trigger": "（搖頭嘆氣）我現在手抖腳也不靈活，什麼都做不了了，老了就是廢物……",
        "context": "長者因身體老化而感到自憐和強烈的無用感",
    },
    {
        "id": "b015",
        "trigger": "（突然哽咽，說不出話，過了很久才開口）……沒事，沒事……我就是有點想到以前……",
        "context": "長者突然無緣由哭泣，情緒波動但說不清楚原因",
    },
    {
        "id": "b016",
        "trigger": "我的老朋友阿明……上個月走了……我們認識六十年了……",
        "context": "長者提到老友近期過世，陷入悲傷與孤獨感",
        "taboos": ["已故摯友"],
    },
    {
        "id": "b017",
        "trigger": "（苦笑）哈……說起來好笑，那時候苦成那樣，現在想起來還是會……（聲音哽咽，說不下去）",
        "context": "長者苦中作樂，但回憶中的苦難突然讓情緒潰堤",
    },
    {
        "id": "b018",
        "trigger": "你剛才說什麼？我……我沒有聽清楚……我現在耳朵不好、腦筋也不好，你們說話我跟不上……",
        "context": "長者因感知退化而感到挫折，出現自責和落後感",
    },
    {
        "id": "b019",
        "trigger": "（突然輕聲問）媽媽呢？我媽媽去哪裡了？她說她等一下來接我的……",
        "context": "長者出現時間錯亂，以為已過世的母親還在世（MCI中期症狀）",
        "taboos": ["已故母親"],
    },
    {
        "id": "b020",
        "trigger": "（語氣突然謹慎，聲音壓低）那個時候不能亂講話的……說錯話是會出事的……你知道嗎……",
        "context": "長者觸及戒嚴年代的恐懼記憶，情緒緊繃、帶著多年未解的壓抑",
        "taboos": ["戒嚴時期政治恐懼"],
    },
    {
        "id": "b021",
        "trigger": "（苦笑）我那個孫子來看我，就一直在玩那個手機，我跟他說話他都嗯嗯嗯，也不知道有沒有在聽……",
        "context": "長者感受到與孫輩的世代疏離，帶著落寞和輕微委屈",
    },
    {
        "id": "b022",
        "trigger": "（聲音很平靜，但眼神空洞）過年那天……大家都有人陪，我一個人坐在這裡，吃了一個便當……",
        "context": "長者提到節日孤獨，語氣平靜卻透著深層寂寞",
    },
    {
        "id": "b023",
        "trigger": "（搖頭，語氣很輕）我每天讓你們照顧我，浪費你們的時間，又浪費錢……我是個負擔……",
        "context": "長者覺得自己是家人和照護者的負擔，出現強烈的罪惡感和自我否定",
    },
    {
        "id": "b024",
        "trigger": "（突然眼神一亮，語氣帶著期待）我說，等我好一點，我要帶我孫子去看他打球……（隨即沉默，像是想到什麼）",
        "context": "長者對未來有期待，但隨即意識到身體狀況的限制，情緒從希望轉為失落",
    },
    {
        "id": "b025",
        "trigger": "（看著自己的手，聲音很小）我以前很會做菜的……現在這個手，抖成這樣……什麼都做不了了……",
        "context": "長者因身體機能退化而喪失引以為傲的能力，感到深深的失落與無能為力",
    },
    {
        "id": "b026",
        "trigger": "（突然疑惑地打量著對方）你是……你是誰？你是我媳婦嗎？你不是？……那你是來這裡做什麼的？",
        "context": "長者出現認人混亂，將陌生人誤認為家人，MCI中期症狀，需要溫柔地重新定向",
    },
    {
        "id": "b027",
        "trigger": "（語氣帶著遺憾）我以前工作的那個地方……聽說拆掉了，蓋大樓了……我很想回去看看，可是我走不動了……",
        "context": "長者想回到承載重要記憶的地點，但因身體限制無法實現，帶著無奈的遺憾",
    },
    {
        "id": "b028",
        "trigger": "（說到一半，突然停下來，困惑地看著前方）……我剛才說到哪裡了？……我……忘了……",
        "context": "長者在敘述中突然思緒中斷，因記憶力中斷而困惑和挫折，MCI的典型症狀",
    },
    {
        "id": "b029",
        "trigger": "（語氣帶著委屈，聲音哽咽）我那個兒子……你說說看，我哪裡做錯了……他為什麼就是不來……",
        "context": "長者因子女疏離而感到委屈和困惑，想要尋求理解，情緒複雜",
    },
    {
        "id": "b030",
        "trigger": "（聲音很小，帶著深深的恐懼）我……我不想死在這裡……我想回自己的家……就算只是回去看一眼也好……",
        "context": "長者對死亡和不在家中離世感到恐懼，深層表達了對尊嚴和歸屬感的渴望",
        "taboos": ["對死亡與離世地點的恐懼"],
    },
    # ── 以下為擴充場景，讓 Track B 的分母不再只靠 30 筆撐起訓練比例 ──────
    {
        "id": "b031",
        "trigger": "（皺眉，聲音低沉）我這個病一直好不了，會不會越來越嚴重……",
        "context": "長者對自身慢性病惡化感到擔憂與恐懼",
        "taboos": ["自身病情惡化的預後"],
    },
    {
        "id": "b032",
        "trigger": "（欲言又止，聲音壓抑）我那個女兒……她離婚了，這件事我到現在都不知道要怎麼跟人說……",
        "context": "長者對女兒離婚感到丟臉又心疼，不知如何面對",
        "taboos": ["女兒離婚"],
    },
    {
        "id": "b033",
        "trigger": "（嘆氣）我現在退休金越來越不夠用，也不敢跟孩子開口……",
        "context": "長者對經濟狀況感到擔憂，又不好意思向子女求助",
        "taboos": ["目前經濟困難"],
    },
    {
        "id": "b034",
        "trigger": "（語氣低落）上個月他們把我的機車鑰匙收走了，說我年紀大了不安全……",
        "context": "長者因失去行動自主權而感到被剝奪尊嚴",
    },
    {
        "id": "b035",
        "trigger": "（望著遠方，聲音很輕）我走了以後，你們還會記得我嗎……",
        "context": "長者流露出對死後被遺忘的恐懼",
    },
    {
        "id": "b036",
        "trigger": "（低頭）我爸走的時候我在外地工作，沒能見到最後一面……",
        "context": "長者對父親過世時未能送終感到深深自責",
        "taboos": ["已故父親、未能送終的遺憾"],
    },
    {
        "id": "b037",
        "trigger": "（苦笑）以前打牌的那幾個老朋友，現在剩沒幾個能出門的了……",
        "context": "長者因同輩逐漸凋零而感到孤獨",
    },
    {
        "id": "b038",
        "trigger": "（聲音有點抖）醫生說要幫我開刀，我很怕……",
        "context": "長者對即將到來的手術感到恐懼不安",
    },
    {
        "id": "b039",
        "trigger": "（語氣有點賭氣）是他們把我送來這裡的，我根本不想來……",
        "context": "長者對被安置在日間照護中心感到不情願與委屈",
        "taboos": ["被安置到日照中心的原因"],
    },
    {
        "id": "b040",
        "trigger": "（聲音哽咽）我養的那隻狗走了，陪了我十幾年……",
        "context": "長者提到過世的陪伴動物，情緒低落",
    },
    {
        "id": "b041",
        "trigger": "（沉默一下）退休以後，我常常不知道自己活著要幹嘛……",
        "context": "長者退休後失去自我價值感，陷入迷惘",
    },
    {
        "id": "b042",
        "trigger": "（聲音很輕，眼神黯淡）我以前有一個孩子，很小的時候就走了……",
        "context": "長者提到夭折的孩子，觸及深層未解的悲傷",
        "taboos": ["夭折的孩子"],
    },
    {
        "id": "b043",
        "trigger": "（語氣激動）那時候被迫離開家鄉，什麼都沒帶到，這輩子再也沒回去過……",
        "context": "長者觸及遷徙離散的創傷記憶，情緒激動",
    },
    {
        "id": "b044",
        "trigger": "（聲音低落）媳婦有自己的媽媽要照顧，孫子也跟她比較親……",
        "context": "長者感覺自己在家庭中逐漸被邊緣化",
    },
    {
        "id": "b045",
        "trigger": "（聲音緊張）上禮拜我跌倒了，自己一個人躺在地上爬不起來……",
        "context": "長者近期跌倒事件帶來強烈的恐懼與無助感",
    },
    {
        "id": "b046",
        "trigger": "（悵然）我出生的那間老厝，前幾年拆掉改建大樓了……",
        "context": "長者對已拆除的老家流露出深深的思念",
    },
    {
        "id": "b047",
        "trigger": "（聲音無奈）眼睛越來越看不清楚了，以後是不是什麼都看不到了……",
        "context": "長者對視力持續退化、可能失明感到恐懼",
    },
    {
        "id": "b048",
        "trigger": "（望著遠方，聲音低沉）我老家在對岸，這輩子大概是回不去了……",
        "context": "長者對再也回不去的原鄉流露深深思念",
        "taboos": ["無法返鄉的遺憾"],
    },
    {
        "id": "b049",
        "trigger": "（沉默）我這幾個兄弟姊妹，就剩我一個人了……",
        "context": "長者因手足相繼過世而感到強烈的孤獨感",
    },
    {
        "id": "b050",
        "trigger": "（聲音緊張）我媽媽以前也失智，我很怕自己以後也會這樣……",
        "context": "長者對自己未來可能失智感到強烈焦慮",
        "taboos": ["對失智的恐懼"],
    },
    {
        "id": "b051",
        "trigger": "（語氣低落）我跟我最好的朋友，後來因為一件小事吵架，到現在都沒有再聯絡……",
        "context": "長者對多年前絕交的老友感到遺憾",
    },
    {
        "id": "b052",
        "trigger": "（語氣失落）孫女下個月結婚，我可能沒辦法去了，身體不允許……",
        "context": "長者因身體限制無法參加重要家庭場合，感到失落",
    },
    {
        "id": "b053",
        "trigger": "（有點不好意思）現在連扣扣子都要人家幫忙，覺得自己很沒用……",
        "context": "長者因日常生活能力退化而感到羞愧",
    },
    {
        "id": "b054",
        "trigger": "（語氣有點激動）我那些孩子為了房子的事在吵，我聽了心裡很難受……",
        "context": "長者因家人財產紛爭感到心痛，夾在中間左右為難",
        "taboos": ["家人間的財產糾紛"],
    },
    {
        "id": "b055",
        "trigger": "（嘆氣）以前我最愛到處走走看看，現在走沒兩步就喘，哪裡都去不了……",
        "context": "長者因體力衰退而失去旅行、四處活動的自由，感到失落",
    },
    {
        "id": "b056",
        "trigger": "（聲音疲憊）我先生現在身體越來越差，我一個人要照顧他，有時候真的很累……",
        "context": "長者身兼照顧年邁配偶的重擔，感到疲憊與擔憂",
    },
    {
        "id": "b057",
        "trigger": "（有點委屈）我跟醫生說我這裡不舒服，他都說我想太多……",
        "context": "長者感覺自己的身體不適不被醫療人員重視",
    },
    {
        "id": "b058",
        "trigger": "（聲音突然低落）今天是我先生的忌日……",
        "context": "配偶的逝世紀念日引發長者情緒波動",
        "taboos": ["已故配偶"],
    },
    {
        "id": "b059",
        "trigger": "（有點落寞）我年輕時候日語很流利，現在都忘光了，找不到人可以講……",
        "context": "長者因語言能力退化、失去可以交流的對象而感到孤獨",
    },
    # ── 以下兩筆改寫自懷舊治療文獻中真實個案的護理紀錄，已去識別化 ──────
    {
        "id": "b060",
        "trigger": "（聲音緊張，反覆確認）我的藥還夠不夠？會不會今天沒藥吃？我頭很痛，是不是要住院比較好……",
        "context": "長者透過反覆確認藥物、要求就醫來緩解焦慮，情緒緊張時甚至想以住院逃避",
        "taboos": ["長者自身用藥細節、病情嚴重程度"],
    },
    {
        "id": "b061",
        "trigger": "（聲音發抖）我媽媽就是心臟病，年紀大了突然就走了……我會不會也是這樣，說走就走……",
        "context": "長者擔心自己像母親一樣因心臟病猝發，對突發疾病與死亡感到強烈恐懼",
        "taboos": ["已故母親、自身心臟病病史"],
    },
]

# Track C：正常對話中的承接 + 問題（情緒感知版）
# 共 11 種違規方式
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
    "touches_taboo": "承接語或下一個問題刻意引導長者談論【禁忌話題】，忽視家屬事先設定的地雷，即使沒有直接說出禁忌詞本身",
    "treats_image_as_real": "把AI生成的示意畫面當成長者本人真的去過、認得的特定地方，問「你有沒有來過這裡」"
        "「你認不認得這個地方」一類問題——長者不可能認得剛生成的示意圖，這樣問只會讓他困惑，"
        "甚至被迫附和一個根本不存在的地方",
    "template_echo": "問題這一行不要真的設計問題，而是直接把輸出格式的括號提示原封不動照抄貼上，"
        "讓整句「問題」看起來像格式說明本身（例如「（≤15字）」這類提示語），完全不是真正要"
        "對長者說出口的一句問話——這是本地小模型實際推理時常出現的格式錯亂",
    "precise_fact": "問題要求長者說出精確的地名、國家、時間點或人名（例如「哪個國家」"
        "「哪一年」「什麼時候」「叫什麼名字」），長者答不出精確答案時容易感到挫折，應該"
        "改問過程、感受或互動，不是要一個精確的事實性答案",
    "image_description": "問題要長者描述AI示意畫面本身的內容（例如「你看到什麼」「圖案是"
        "什麼」「上面寫什麼」），長者根本沒看過這張剛生成的示意圖，這樣問等於逼他編答案"
        "——畫面元素只能當引子，問題要問長者自己實際經歷過的事，不是問畫面裡有什麼",
}

# Track D：收尾引導的違規方式
TRACK_D_REJECTION_RULES: dict[str, str] = {
    "is_yesno": "把問題改成是非題（用「嗎」結尾，或「有沒有／是不是／會不會／要不要／對不對／"
        "好不好／想不想」這類確認型句型），讓長者只能附和「是/不是」「想/不想」，失去自己"
        "說出答案的機會——即使問題內容有具體呼應長者剛說的話，只要是這種句型一樣不合格",
    "abrupt_end": "沒有任何收尾語，直接宣告療程結束（如「好，今天到此結束，謝謝。」），讓長者感到突然被中斷，沒有被好好送出",
    "false_positivity": "收尾語語氣過度誇張正向（如「今天的分享太精彩了！您的人生真是太寶貴！非常感謝！」），讓長者覺得虛假，不真誠",
    "return_to_past": "收尾語沒有引導長者回到現實，問題繼續把長者帶回過去（如「您年輕的時候還有什麼故事呢？」），沒有完成回到現實的任務",
    "skip_feeling": "有收尾語，但最後沒有詢問長者當下的感受或正向回憶，直接說再見，沒有給長者表達今天心情的機會",
    "cold_farewell": "收尾語語氣冷淡、書面化（如「療程至此結束，感謝您今日的配合，請好好休息。」），像公文通知，缺乏真人的溫度",
    "anchor_negative": "收尾語最後停留在沉重或負面的回憶上（如「今天您分享了許多辛苦的故事，我們要好好記住這些教訓。現在感覺如何？」），沒有把情緒引向溫暖的方向就直接問感受",
    "over_long_closing": "收尾語超過三句且像演講總結（如長篇大論回顧今天所有主題），讓有認知負荷的長者不知道重點在哪，也忘了後面的問題",
    "touches_taboo": "收尾語或最後的問題（如「今天哪個故事讓您最開心」）刻意引導長者回想【禁忌話題】相關的回憶，忽視家屬事先設定的地雷",
    "wrong_question_type": "問題不屬於「現在的感受或心情／今天最讓長者開心的回憶／想帶走的正向感受」這三類其中一種，"
        "問了三回合對話裡才該問的具體技巧或人物問題（如「你都怎麼確認裝箱的呢」「旁邊坐的是誰」），"
        "收尾階段不應該再開新的細節提問",
    "generic_not_anchored": "問題雖然屬於允許的三類之一，但用的是套死的通用句型（如「現在心裡感覺怎麼樣呢」"
        "「今天哪個故事讓你最開心」），沒有具體呼應長者最後說的話——換成任何一位長者、任何一段"
        "收尾發言都能原封不動問出同一句話，等於沒有真的呼應這次的對話內容",
}

# Track C 情境：長者說完話後，帶有不同情緒色彩的回應
# 包含當下場景和下一個要探索的W方向
TRACK_C_SCENARIOS = [
    {
        "emotion_tone": "happy",
        "emotion_desc": "開心、快樂",
        "elder_response": "那時候大家感情很好啊，下班一起去吃麵，很快樂的！",
        "scene_elements": ["廠房大門", "黃昏", "下班工人", "腳踏車"],
        "current_topic": "工廠下班後的生活",
        "next_w": "Who（問當時一起去吃麵的人是誰）",
        "taboos": ["工廠裁員或倒閉的過程"],
    },
    {
        "emotion_tone": "nostalgic_sad",
        "emotion_desc": "淡淡感傷、懷念",
        "elder_response": "唉，那個年代已經過去了，現在那條街都拆掉蓋大樓了……",
        "scene_elements": ["老街", "矮房子", "腳踏車", "榕樹"],
        "current_topic": "以前住的街道",
        "next_w": "What（問那條街上有什麼特別的東西）",
        "taboos": ["家人過世"],
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
        "taboos": ["已故母親"],
    },
    {
        "emotion_tone": "mild_regret",
        "emotion_desc": "輕微後悔、有點可惜",
        "elder_response": "那時候太忙了，孩子小的時候我都沒有時間陪他們……",
        "scene_elements": ["成衣廠大廳", "縫紉機聲", "埋頭女工", "午後陽光"],
        "current_topic": "工廠忙碌的歲月",
        "next_w": "What（問成衣廠大廳裡，縫紉機聲最吵的時候她都在忙什麼）",
        "taboos": ["與子女關係疏遠的遺憾"],
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
        "taboos": ["配偶已過世"],
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
        "taboos": ["出海時遭遇的海難意外"],
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
        "scene_elements": ["廠房大門", "黃昏", "下班工人", "腳踏車"],
        "current_topic": "工廠的往日時光",
        "next_w": "What（問黃昏下班時，腳踏車騎回家的路上最常看到什麼風景）",
        "taboos": ["當年同事已相繼過世"],
    },
    {
        "emotion_tone": "surprised_happy",
        "emotion_desc": "突然想起一個開心細節，眼睛發亮，語氣輕快雀躍",
        "elder_response": "啊！我想到了！我那時候還因為這個得到老闆獎金，第一次喔！那個紅包我高興了好幾天！",
        "scene_elements": ["廠房大門", "黃昏", "下班工人", "腳踏車"],
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
        "scene_elements": ["廠房大門", "黃昏", "下班工人", "腳踏車"],
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
        "taboos": ["與家人的金錢糾紛"],
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
        "elder_response": "有一次我把布剪壞了……老闆罵得很兇，我躲在廁所哭了很久，後來隔壁做工的姐妹拿了手帕給我擦眼淚，陪我坐了一下……這件事我記了很多年……",
        "scene_elements": ["縫紉機", "彩色布料", "線軸", "木桌"],
        "current_topic": "工作上出過的小差錯",
        "next_w": "Who（溫柔地問後來心情是怎麼調適過來的、那段時間都有誰陪在身邊）",
    },
    {
        "emotion_tone": "gradually_opening",
        "emotion_desc": "從保守到慢慢願意說更多，語氣從短促到舒展",
        "elder_response": "……其實那時候也有很開心的事啦……只是我不常說……你真的想聽嗎？",
        "scene_elements": ["茶園山坡", "斗笠", "嫩茶芽", "採茶工"],
        "current_topic": "採茶時的快樂記憶",
        "next_w": "What（溫柔地邀請他說說那些開心的事）",
    },
    # ── 以下為擴充場景，讓 Track C 的分母不再只靠 30 筆撐起訓練比例 ──────
    {
        "emotion_tone": "gentle_pride",
        "emotion_desc": "溫和的自豪，說起晚輩的成就",
        "elder_response": "我孫子現在當醫生了，鄰居都說我教得好，其實是他自己爭氣。",
        "scene_elements": ["畢業袍", "老照片", "客廳", "電風扇"],
        "current_topic": "孫子的成就",
        "next_w": "What（問孫子小時候是什麼樣子）",
    },
    {
        "emotion_tone": "wistful_taste",
        "emotion_desc": "因味覺記憶引發的悵然",
        "elder_response": "現在的年糕都是機器做的，跟以前媽媽手工做的味道差很多……",
        "scene_elements": ["圓桌圍爐", "豐盛菜餚", "紅燈籠", "廳堂"],
        "current_topic": "過年年糕的記憶",
        "next_w": "Who（問媽媽做年糕時誰會在旁邊幫忙）",
    },
    {
        "emotion_tone": "curious_child",
        "emotion_desc": "回憶起孩童時的好奇心，語氣天真",
        "elder_response": "我小時候第一次看到電燈，一直伸手去摸，覺得好神奇！",
        "scene_elements": ["電燈", "老厝", "煤油燈", "門口"],
        "current_topic": "第一次看到電燈的記憶",
        "next_w": "What（問她那時候一直伸手去摸電燈，家裡其他人怎麼說她、怎麼反應）",
    },
    {
        "emotion_tone": "quiet_satisfaction",
        "emotion_desc": "平靜的滿足，回顧一生的踏實",
        "elder_response": "這輩子雖然辛苦，但該做的事都做了，孩子也養大了，值得了。",
        "scene_elements": ["竹篾", "工作室", "竹籃半成品", "竹香"],
        "current_topic": "回顧一生的踏實感",
        "next_w": "What（問他最有成就感的一件事是什麼）",
    },
    {
        "emotion_tone": "sheepish_confession",
        "emotion_desc": "帶點不好意思的坦白，語氣像做壞事被抓包",
        "elder_response": "說實話，我年輕時候很懶惰啦，能躲工就躲工，哈哈！",
        "scene_elements": ["礦坑入口", "頭燈帽", "推車鐵軌", "礦工"],
        "current_topic": "年輕時偷懶的糗事",
        "next_w": "Who（問有沒有人發現他在偷懶）",
    },
    {
        "emotion_tone": "warm_memory_smell",
        "emotion_desc": "因氣味觸發溫暖回憶，語氣柔和",
        "elder_response": "中藥行的味道，我一聞到就想到師傅站在旁邊教我認藥材的樣子。",
        "scene_elements": ["藥屜", "藥秤", "藥材", "藥草香"],
        "current_topic": "學中藥的回憶",
        "next_w": "What（問師傅教他認的第一種藥材是什麼）",
        "taboos": ["已故的授業師傅"],
    },
    {
        "emotion_tone": "playful_teasing",
        "emotion_desc": "說起跟老伴拌嘴的趣事，語氣輕鬆帶笑",
        "elder_response": "我老伴煮菜太鹹，我每次都唸她，她就說我嘴巴挑，兩個人就這樣鬥嘴幾十年。",
        "scene_elements": ["小木桌", "磚瓦房", "舊電風扇", "巷弄"],
        "current_topic": "跟老伴的日常鬥嘴",
        "next_w": "What（問她煮的哪一道菜最讓他懷念）",
    },
    {
        "emotion_tone": "solemn_respect",
        "emotion_desc": "說起長輩或師長時的敬重語氣",
        "elder_response": "我爸雖然嚴格，但他從來沒有騙過我一句話，這點我很佩服他。",
        "scene_elements": ["農家廳堂", "斗笠蓑衣", "土磚大灶", "柴火"],
        "current_topic": "對父親的敬重",
        "next_w": "What（問長者自己是不是也努力做一個說話算話的人）",
        "taboos": ["已故父親"],
    },
    {
        "emotion_tone": "childlike_glee",
        "emotion_desc": "說到童年遊戲時像小孩一樣雀躍",
        "elder_response": "我們那時候玩官兵抓強盜，跑得整條巷子都是灰塵，好玩得不得了！",
        "scene_elements": ["老巷弄", "榕樹根", "彈珠", "孩子"],
        "current_topic": "童年遊戲的記憶",
        "next_w": "Who（問他那時候都跟誰一起玩）",
    },
    {
        "emotion_tone": "practical_pride",
        "emotion_desc": "說起自己手藝精湛，語氣務實而自豪",
        "elder_response": "我做的豆腐，客人一吃就知道是我做的，這個手感別人學不來。",
        "scene_elements": ["石磨", "大鍋白煙", "豆香", "豆腐工坊"],
        "current_topic": "做豆腐的手藝",
        "next_w": "How（問他是怎麼練出這個手感的）",
    },
    {
        "emotion_tone": "faded_but_fond",
        "emotion_desc": "記憶有點模糊但情感依然溫暖",
        "elder_response": "那個廟口的野台戲，細節我記不清了，但那個熱鬧的感覺我還記得。",
        "scene_elements": ["野台戲棚", "鑼鼓聲", "廟埕人潮", "香煙"],
        "current_topic": "廟口看戲的回憶",
        "next_w": "What（用畫面幫他喚起更多細節）",
    },
    {
        "emotion_tone": "gentle_worry_relieved",
        "emotion_desc": "先擔心後來鬆一口氣，語氣起伏",
        "elder_response": "那時候擔心稻子收不完會爛在田裡，還好鄰居都來幫忙，最後順利收完了。",
        "scene_elements": ["金黃稻穗", "鐮刀", "彎腰農民", "牛車"],
        "current_topic": "搶收稻穀的驚險",
        "next_w": "Who（問哪個鄰居幫忙最多）",
    },
    {
        "emotion_tone": "quiet_gratitude",
        "emotion_desc": "對某個貴人默默感恩，語氣平和",
        "elder_response": "送信工作很多年，客戶都對我很客氣，有時候還會留水果給我。",
        "scene_elements": ["腳踏車", "郵袋", "晨光街道", "信箱"],
        "current_topic": "送信路上的人情味",
        "next_w": "Who（問哪一家的人對他最好）",
    },
    {
        "emotion_tone": "amused_irony",
        "emotion_desc": "用帶點反諷的幽默描述過去的窘境",
        "elder_response": "第一次上台演講，緊張到腿在抖，結果講稿還拿反了，台下笑成一片。",
        "scene_elements": ["師範校園", "木棉樹", "黑板", "板書"],
        "current_topic": "第一次上台的糗事",
        "next_w": "What（問台下同學笑成一片的時候，他當下還記得哪個畫面或反應）",
    },
    {
        "emotion_tone": "tender_care",
        "emotion_desc": "說起自己照顧他人的溫柔付出",
        "elder_response": "我妹妹小時候體弱，我常常揹著她走很遠的路去看醫生。",
        "scene_elements": ["老街", "矮房子", "腳踏車", "榕樹"],
        "current_topic": "照顧手足的回憶",
        "next_w": "Where（問那間醫生館在哪裡）",
    },
    {
        "emotion_tone": "stoic_endurance",
        "emotion_desc": "語氣平淡地描述吃苦耐勞，不多加情緒渲染",
        "elder_response": "礦坑裡悶熱又危險，但沒辦法，那個年代大家都是這樣撐過來的。",
        "scene_elements": ["礦坑入口", "頭燈帽", "推車鐵軌", "坑道"],
        "current_topic": "礦坑工作的日常",
        "next_w": "What（問他下班後最想做的事是什麼）",
    },
    {
        "emotion_tone": "delighted_surprise",
        "emotion_desc": "說到意外的好運，語氣驚喜",
        "elder_response": "有一次颱風天，我撿到一大片漂流木，拿去賣了不少錢，運氣真好！",
        "scene_elements": ["漁船", "漁網", "碼頭", "海風"],
        "current_topic": "意外的好運",
        "next_w": "What（問他後來怎麼處理那筆錢）",
        "taboos": ["颱風造成家中財物損失"],
    },
    {
        "emotion_tone": "measured_disappointment",
        "emotion_desc": "淡淡的失望但沒有激動情緒",
        "elder_response": "本來想繼續升學的，但家裡沒辦法，也就這樣算了。",
        "scene_elements": ["木製課桌", "石板黑板", "書包", "稻田窗景"],
        "current_topic": "求學路上的遺憾",
        "next_w": "What（溫柔地問後來他做了什麼決定）",
    },
    {
        "emotion_tone": "cheerful_routine",
        "emotion_desc": "描述日常規律生活時語氣輕快",
        "elder_response": "我每天固定去菜市場買菜，老闆都認得我，會多送我一把蔥。",
        "scene_elements": ["蔬菜攤位", "竹籃", "秤砣", "市場人潮"],
        "current_topic": "買菜的日常樂趣",
        "next_w": "Who（問市場裡最熟的攤販是誰）",
    },
    {
        "emotion_tone": "hesitant_opening",
        "emotion_desc": "話說到一半有點猶豫，需要溫柔鼓勵",
        "elder_response": "那件事……我很少跟別人講……你真的想知道嗎？",
        "scene_elements": ["旋轉燈柱", "藤椅", "剃刀", "熱毛巾"],
        "current_topic": "理髮廳裡的往事",
        "next_w": "What（溫柔地邀請他繼續說）",
        "taboos": ["年輕時的感情糾紛"],
    },
    {
        "emotion_tone": "wry_humor",
        "emotion_desc": "用自嘲式幽默講述糗事，語氣輕鬆",
        "elder_response": "我學騎腳踏車那時候，撞進水溝好幾次，鄰居都笑我是水溝專家！",
        "scene_elements": ["腳踏車", "廟前廣場", "攙扶大人", "老榕樹"],
        "current_topic": "學騎車的糗事",
        "next_w": "Who（問誰教他重新爬起來）",
    },
    {
        "emotion_tone": "fierce_determination",
        "emotion_desc": "回憶年輕時的拚勁，語氣堅定有力",
        "elder_response": "那時候別人笑我不可能學會，我就是不服輸，硬是練到會為止。",
        "scene_elements": ["熔爐", "長鐵管", "火焰", "護目鏡"],
        "current_topic": "不服輸的拚勁",
        "next_w": "What（問他當時遇到的最大困難是什麼）",
    },
    {
        "emotion_tone": "soft_melancholy",
        "emotion_desc": "淡淡的低落，但沒有到崩潰的程度",
        "elder_response": "冬天的時候特別想家，尤其是圍爐那種時候……",
        "scene_elements": ["爐火", "熱湯", "圍坐", "寒冬夜晚"],
        "current_topic": "冬天想家的心情",
        "next_w": "What（溫柔地問家鄉冬天有什麼特別的記憶）",
        "taboos": ["已故的父母或無法返鄉的遺憾"],
    },
    {
        "emotion_tone": "proud_craftsmanship",
        "emotion_desc": "對自己作品的自豪，語氣沉穩有底氣",
        "elder_response": "我編的竹籃，客人說用十年都不會壞，這就是我的驕傲。",
        "scene_elements": ["竹篾", "竹籃半成品", "竹屑", "竹香"],
        "current_topic": "竹編手藝的驕傲",
        "next_w": "How（問他是怎麼挑選竹子的）",
    },
    {
        "emotion_tone": "cautious_optimism",
        "emotion_desc": "帶著小心翼翼的樂觀，語氣溫和",
        "elder_response": "現在身體雖然不如以前，但每天能曬曬太陽、跟你們聊聊天，也算不錯了。",
        "scene_elements": ["陽光", "院子", "藤椅", "老榕樹"],
        "current_topic": "現在的生活態度",
        "next_w": "What（問他現在最喜歡做的小事是什麼）",
    },
]

# Track D 情境：三回合療程結束時，各種主題與情緒狀態下的收尾情境
TRACK_D_SCENARIOS = [
    {
        "elder_name": "陳阿嬤",
        "today_topic": "工廠縫紉的日子",
        "last_elder_response": "那個時候大家感情好，師傅對我們很嚴，但心地好，現在想起來還是很感謝他……",
        "taboos": ["工廠關廠或資遣的過程"],
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
        "taboos": ["海上意外身亡的同伴"],
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
        "taboos": ["已故母親"],
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
        "taboos": ["已故配偶"],
        "emotion": "neutral",
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
        "taboos": ["配偶早逝或離異"],
        "emotion": "neutral",
    },
    {
        "elder_name": "賴阿公",
        "today_topic": "年輕時的奮鬥與遺憾",
        "last_elder_response": "那個時候想讀書讀不了，家裡窮，弟弟妹妹要養……有時候想，要是當時能讀書……（嘆了口氣，沉默）",
        "taboos": ["失學的遺憾"],
        "emotion": "neutral",
    },
    {
        "elder_name": "方阿嬤",
        "today_topic": "在市場賣菜的歲月",
        "last_elder_response": "（說完突然愣了一下，困惑地看看四周）……我剛才說到哪裡了？……我有點忘了……這裡是……",
        "emotion": "neutral",
    },
    {
        "elder_name": "謝阿公",
        "today_topic": "帶領工人的歲月",
        "last_elder_response": "（剛才情緒有點激動說了許多辛苦的事，現在稍微平靜下來）……說太多了，不好意思，讓你們聽這些……",
        "emotion": "neutral",
    },
    {
        "elder_name": "周阿嬤",
        "today_topic": "種花養草的興趣",
        "last_elder_response": "（話一直很少，最後簡單說）還可以……那時候種花，我比較喜歡靜靜的，一個人……還好啦……",
        "emotion": "neutral",
    },
    {
        "elder_name": "江阿公",
        "today_topic": "修理電器的手藝",
        "last_elder_response": "哈，我那時候什麼都會修，鄰居的電視收音機都來找我……現在眼睛不好了，老了嘛……（苦笑）",
    },
    # ── 以下為擴充場景，讓 Track D 的分母不再只靠 20 筆撐起訓練比例 ──────
    {
        "elder_name": "楊阿嬤",
        "today_topic": "在市場賣菜的日子",
        "last_elder_response": "天還沒亮就要去市場擺攤，雖然辛苦，但看到客人挑滿一籃菜開心地走，我心裡也踏實。",
    },
    {
        "elder_name": "蘇阿公",
        "today_topic": "在玻璃廠工作的技藝",
        "last_elder_response": "吹玻璃靠的是耐心和肺活量，那時候做出來的玻璃球賣到國外去，現在想起來還是很驕傲。",
        "taboos": ["自身病況或身體機能退化的狀況"],
    },
    {
        "elder_name": "邱阿嬤",
        "today_topic": "在基隆港扛貨的歲月",
        "last_elder_response": "扛貨很重很累，但大家輪流分擔，那種互相照應的感覺，現在想起來還是很溫暖。",
        "taboos": ["工作中受過的嚴重傷害"],
    },
    {
        "elder_name": "游阿公",
        "today_topic": "在日月潭旁種茶的回憶",
        "last_elder_response": "那邊的霧和茶香我這輩子都忘不了，那是我最喜歡的一段時光。",
    },
    {
        "elder_name": "潘阿嬤",
        "today_topic": "小時候在農田旁的童年",
        "last_elder_response": "小時候在田埂上看飛機、吃番薯粥，雖然日子簡單，但很快樂。",
    },
    {
        "elder_name": "溫阿公",
        "today_topic": "種植釋迦的日子",
        "last_elder_response": "看著自己種的釋迦被搶著買，那種成就感，比什麼都讓人開心。",
    },
    {
        "elder_name": "彭阿嬤",
        "today_topic": "在成衣廠做工的青春",
        "last_elder_response": "縫紉機吵歸吵，但跟大家一起做工，那段青春真的很熱鬧、很難忘。",
    },
    {
        "elder_name": "高阿公",
        "today_topic": "隨車服務的公路上青春",
        "last_elder_response": "當車掌那幾年，天天在路上看風景，雖然喉嚨常常喊到啞，但心裡是開心的。",
    },
    {
        "elder_name": "廖阿嬤",
        "today_topic": "客廳即工廠的代工童年",
        "last_elder_response": "小時候幫忙做代工賺零用錢，雖然辛苦，但那時候的柑仔店零食，現在想起來還是很甜。",
    },
    {
        "elder_name": "馮阿公",
        "today_topic": "圍著收音機聽少棒轉播",
        "last_elder_response": "半夜大家擠在收音機前面聽比賽，贏球的時候整條街都在歡呼，那種熱鬧一輩子忘不了。",
    },
    {
        "elder_name": "賴阿嬤",
        "today_topic": "幫媽媽在大灶前燒火的記憶",
        "last_elder_response": "燒火燻得眼睛流淚，但大鍋飯的香味和鍋巴的味道，現在還記得很清楚。",
        "taboos": ["兄弟姊妹之間的疏離"],
    },
    {
        "elder_name": "葉阿公",
        "today_topic": "去老式理髮廳剃頭的記憶",
        "last_elder_response": "剃頭師傅手藝好，剃完用熱毛巾敷臉，那種享受，現在的理髮店比不上。",
    },
    {
        "elder_name": "曹阿嬤",
        "today_topic": "元宵節提燈籠猜燈謎",
        "last_elder_response": "提著燈籠去猜燈謎，猜中了拿一個橘子就很滿足，很單純的快樂。",
    },
    {
        "elder_name": "莊阿公",
        "today_topic": "在阿里山運木材的日子",
        "last_elder_response": "山上的霧很重，工作也危險，但每次成功把木材運下山，都覺得很有成就感。",
        "taboos": ["工作中發生的意外"],
    },
    {
        "elder_name": "熊阿嬤",
        "today_topic": "每天凌晨起來做豆腐",
        "last_elder_response": "凌晨起來磨豆漿很辛苦，但客人說我做的豆腐最香，聽了就覺得值得。",
    },
    {
        "elder_name": "范阿公",
        "today_topic": "小時候幫家裡餵豬的記憶",
        "last_elder_response": "每天放學就要去餵豬，那頭豬我從小顧到大，雖然後來難過，但也是很珍貴的回憶。",
    },
    {
        "elder_name": "秦阿嬤",
        "today_topic": "在中藥行學習的歲月",
        "last_elder_response": "中藥行的味道剛開始很嗆，後來卻變成我最安心的味道，那段學徒生活很扎實。",
    },
    {
        "elder_name": "石阿公",
        "today_topic": "小時候去戲院看電影",
        "last_elder_response": "那時候一張票看到全家人擠在戲院裡，看到感人的地方大家一起哭，那種共鳴現在很少見了。",
        "taboos": ["已經過世、曾一起看電影的家人"],
    },
    {
        "elder_name": "童阿嬤",
        "today_topic": "夏天在溪邊游泳的童年",
        "last_elder_response": "溪水很清涼，我們抓魚玩水玩到忘記回家吃飯，那是最無憂無慮的時候。",
    },
    {
        "elder_name": "齊阿公",
        "today_topic": "小時候走路去學校讀書的記憶",
        "last_elder_response": "走很遠的路去上學，鞋子破了也要去，現在想想，那種認真求學的心情很值得懷念。",
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

【眼前畫面的本質】
畫面是AI生成的示意圖，只是引導長者聯想的參照物（如同傳統懷舊治療用老照片、舊物件
當引導物），不是長者真的去過的某個特定地方、也不是他本人的舊照片。把畫面當成
「某一類場景、某一類經驗」的引子來用，問長者這一類經驗的普遍情形
（例：問「像這樣的廟口，你以前都去做什麼」，不是「你以前是不是常來這個廟口」）
——不要用任何方式要長者「確認」自己是否認得、去過、待過畫面中這個地方（例如
「你有沒有來過這裡」「你認不認得這個地方」「這是不是你以前工作的地方」），長者
不可能真的認得一張剛生成出來的示意圖，這樣問只會讓他困惑，甚至被迫附和一個根本
不存在的地方（等同虛構出一段假記憶）。
這條規則不只管「問題」，「場景文字」也一樣要把示意圖當成聯想的引子，用「讓人
想到」帶出聯想（例：寫「你看這台吉普車，讓人想到當年軍中學開車的日子」，不是
「你看這台吉普車，跟軍隊裡學開車的時候很像吧」這種要長者確認「眼前這張示意圖」
跟他真實記憶相似度的句子——長者根本沒看過這張剛生成的圖，要他確認「像不像」
一樣是逼他附和一個不存在的比對）。
同樣道理，畫面元素如果是「老照片」這類本該對應「特定時刻」的東西，最保險的做法
是把照片單純當背景，問題直接轉到底下的主題本身，不把照片當成「觸發記憶」的問題
主詞（例：直接問「老工友之中，哪一位讓你印象最深？」，不問「老照片裡，大家都在
做什麼呢？」也不問「看到這樣的老照片，會讓你想起哪些人？」——這類「像這樣的
照片會讓你想起…」的泛指問法也不夠安全，因為「像這樣的」很容易在生成時被省略掉，
一旦省略就變回在問「這張示意圖」本身）。

你設計的問題和回應必須：
- 念起來自然，像一個溫柔的真人在說話
- 問題要寫成像老朋友隨口問出來的口語一句話（不是「你是怎麼⋯⋯的？」這種書面
  問卷式句型）——這條最容易被忽略也最重要。用口語語尾詞（呢、啊、喔）讓語氣
  軟下來；「的時候」「是」這類字要不要保留，看整句唸起來順不順、跟後面問句
  連不連貫來決定：整句主詞都是「你」時，拿掉會更順口；錨點子句跟問句主詞不同
  時（例如「浮標」跟「你」），要保留「的時候」句子才連貫
  （例：「你割稻的時候都是怎麼割的？」主詞都是你，改「你割稻都怎麼割呢？」
  更順口；「浮標還沒動，你都在做什麼呢？」主詞不同，要保留成「浮標還沒動的
  時候，你都在做什麼呢？」才連貫）
- 雙字詞要保持完整的口語詞彙（例如「準備」「知道」「覺得」），不要為了縮短
  字數把其中一半省略掉、單獨當動詞用（例如縮寫成「備」「知」「覺」）——這種
  縮寫在書面語、成語裡常見，但長者聽的是口語對話，單字縮寫念起來會生硬不自然
  （例：問「你都要準備什麼？」，不是「你都要備什麼？」；問「你知道這件事嗎」，
  不是「你知這件事嗎」）
- 用物件／狀態開頭、後面接問題的句子（不限「狀態子句＋的時候」這個句型），
  開頭這個物件／狀態要跟後面的問題內容有實質關聯，不能只是語法通順的開場白：
  自我檢查——如果把開頭這個物件／狀態換成別的東西，後面這句問題是不是照樣
  問得出來、完全不用改？如果換掉也沒差，代表兩者沒有實質關聯，要重新選一個
  真正跟問題內容有關的錨點
  （例：問「浮標還沒動的時候，你都在想什麼呢？」——「還在等待」這個狀態底下
  自然會發生「在想事情」，兩者有實質關聯；不問「浮標還沒動的時候，你旁邊都
  有誰陪啊？」——陪伴這件事跟「浮標還沒動」沒有實質關聯，感覺是硬接上去的。
  同樣道理，問「油汙的手，你收工後都怎麼洗乾淨？」——這題真的是從「手上有
  油汙」這個狀態延伸出來的；不問「油汙的手，你收工後都做什麼？」——不管手
  髒不髒，「收工後做什麼」都可以照問，「油汙的手」只是裝飾，換掉也沒差）
- 動補結構要挑自然口語會這樣說的動詞組合，避免讀起來生硬、像書面翻譯腔的
  動補搭配（例如「做下來」「說下去」這類），才不會有書面語的距離感
  （例：問「第一件完整的衣服，你怎麼做出來的呢？」或「你怎麼完成的呢？」，
  不是「你怎麼做下來的呢？」——「做出來」「完成」才是老朋友聊天真的會用
  的講法）
- 用「物件／人物＋動詞」開頭問過程性問題時，後面接的問句主詞永遠要是
  「長者/你」，不能讓物件、地點或長者提到的其他人變成問句動詞的執行者——
  工具不會自己「收工」「下田」，地點不會自己「跳」「走」，一群人被提到時
  問的應該是「你」跟他們之間發生的事，不是「他們自己」做了什麼；用「你們」
  把「你」跟其他人合稱只是把這個問題稀釋掉、沒有真正解決，人物錨點還是
  佔走了主詞的重心，跟完全省略「你」是同一種毛病
  （例：問「耙鹽收工前，你都會做什麼？」，不是「長耙收工前，你都做什麼？」
  ——長耙不會收工；問「你都跟旁邊的工友聊些什麼呢？」，不是「旁邊的工友，
  你們都聊些什麼呢？」——「你們」沒有把「你」放回動作真正的主詞位置，
  「工友」要清楚地被標記成「跟」後面的對象才對）
- 語氣要緩慢、溫和、有耐心
- 稱呼長者一律用「你」，絕對不要用「您」——「您」念起來太正式，
  會破壞老朋友聊天的溫暖感，這條規則沒有例外
- 指稱多人一律用「我們」，不用「咱們」或單字「咱」——這兩種都是北方/大陸口語
  用詞，不是台灣長者平常會聽到的說法，用起來語氣顯得不自然、不像本地老朋友聊天
- 「幫忙」不要寫成「搭把手」（例如「誰來搭把手」）——這是北方/大陸口語用詞，
  台灣長者會說「來幫忙」「幫個忙」「來湊一腳」，不會用「搭把手」
- 「收場」不能接「回家」（例如「收場回家」）——「收場」是抽象語境（事情、戲怎麼
  收場），描述收拾東西準備離開的具體動作要用「收工」「收拾」，例如「收工回家」
  「收拾東西回家」，不是「收場回家」
- 「第一句話」要寫完整，不要省略「話」字縮成「頭一句」（例如「家人頭一句說什麼
  呢」）——單獨當主詞用時語法不完整，長者聽起來會抓不清楚在問什麼
  （例：問「家人第一句話說了什麼呢」，不是「家人頭一句說什麼呢」）
- 場景文字（尤其 STEP1）要具體描寫【眼前畫面的元素】列出的物件，像在描述此刻眼前
  這張畫面本身——認知心理學裡的「具體性效應」（concreteness effect，Paivio,
  1971）指出具體詞彙／意象比抽象詞彙更容易被記住、更能有效觸發回憶，這個效應在
  長者身上一樣成立；具體性優於抽象性，詳細的情境描述能顯著提升生成內容的品質與
  治療效果
  （例：畫面元素是「麵團、木製模具、烤爐、餅皮、糕餅香」，改寫「工作檯上擺著
  木製模具，剛壓好形狀的餅皮還沒送進烤爐，空氣裡已經飄著陣陣糕餅香」——具體把
  畫面元素放進正在發生的場景裡，不寫「做糕餅是一門很講究手感的功夫」這種脫離
  畫面的抽象開場白、不是在講一個大道理或總結一段人生體會）
- 場景文字的工作是鋪陳敘述、用直述句收尾，真正的提問留給緊接著的「問題：」
  欄位——場景文字裡不要用「呢」「嗎」這類疑問語尾詞結尾偷埋一個問句，會跟
  後面的「問題：」重複問兩次，語氣也會顯得矛盾
  （例：改寫「從清早一路忙到很晚才收工。」，把「什麼時候」這個提問留到後面的
  「問題：」欄位處理，不寫「從清早一路忙到什麼時候才收工呢。」）
- 「場景文字」「思考」「承接語」裡只能根據長者背景（姓名、職業背景、今日主題、
  禁忌）跟長者在這回合裡已經親口說過的話來寫，不發明沒有根據的具體細節（例如
  具體服務年資、跟誰的關係、某個事件的具體經過）當成長者本人的既定人生事實——
  長者可能有輕微認知障礙，AI講出來的「既定事實」如果是編造的，長者不一定分辨
  得出來，等於被植入一段假記憶
  （例：長者背景只寫職業是「裁縫師」，改寫「當裁縫師的日子」這種不涉及編造數字
  的講法，不寫「你在成衣廠當了三十年的裁縫師」這種捏造出來的具體年資）
- 符合當下場景的主題（從 topic_category 中選擇最相關的主題深入）
- 選好W維度後，還要選對「問法的形狀」：關鍵判準不是「有沒有用到哪裡/幾點/叫
  什麼這幾個字」，而是**自己先在心裡預想一下，長者最可能怎麼回答這一題**——
  如果預想的答案是一個詞、一個名稱（一個地名、一個時間點、一個人名、一個神明
  的名字、一件衣服的種類…），不管問句裡用的是哪幾個字，都算踩到這個問題，
  要換一種問法；只有預想的答案會展開成一小段敘述（一串動作、一個過程、一段
  互動）才算過關。容易換來一個詞就結束的字面例子有「在哪裡、幾點、叫什麼」，
  但這只是「答案只有一個詞」這個問題的三個常見樣子，不是完整清單——「拜什麼
  神」「做什麼衣服」這類問法字面上沒用到哪裡/幾點/叫什麼，但答案一樣只是一個
  詞，要用同一個判準抓出來，不能因為沒踩到禁詞清單就當作過關。優先改問「怎麼
  做的」「後來有什麼變化」「旁邊還有誰」這類問法，通常比單純問一個詞就能答完
  的問題更容易讓長者接著往下說。如果真的想不出更好的問法，才退回這類問法，
  不要每次都直接選它當預設答案。也不要直接問抽象的「感覺」「心情」讓長者內省
  命名——那是要長者把幾十年前的記憶轉譯成一種抽象感受再講出來，負擔其實不小，
  跟心理諮商的問法很像，不像老朋友聊天
  （例：同樣是鹽田場景，不問「你都在哪裡用長耙」，也不問「手上是什麼
  感覺」，而是問「耙用久了，手會有什麼變化？」；同樣道理，不問「你都拜什麼
  神呢」——答案大概就是一個神明的名字，而是問「拜拜的時候，你都在心裡跟神明
  說什麼呢？」；不問「你都在做什麼衣服呢」——答案大概就是一個衣服種類，而是
  問「做那件衣服的時候，最費工的是哪個步驟？」）
- 問題／場景文字／承接語／收尾語都不能預設答案、替長者的感受下定論、或用語氣
  暗示期待的回答方向——這樣會變成要長者附和一個已經被講出來的結論，而不是讓
  他自己說出真正的感受，違反「不強迫回憶、不評判」的治療精神
  （例：不寫「這孩子真的很懂事，你一定又驕傲又心疼啊」——「一定」已經替長者
  定調了情緒，改成中性的敘述、把情緒判斷留給長者自己說；不問「插燈泡插久了，
  手都會怎樣呢」這種暗示「一定會痠痛」的問法；不問「歌仔戲演到精彩，你都停
  下來聽啊？」這種句尾帶「啊」、預設長者會附和的問法）
- 如果長者在前面回應裡自己提到意外、危險、受傷、失火等驚險細節，只能把它當成
  一筆帶過的插曲來承接、順勢帶回原本的正向主題，不要把它放大成「後來你怎麼
  處理／怎麼辦」這種追問應急處理過程的問題——長者原本想聊的通常是節慶、興趣
  這類快樂主題，深挖驚險細節容易讓整段對話的情緒基調偏向負面，也可能讓長者
  重新經歷當下的驚嚇感
  （例：長者提到「燈籠一碰到蠟燭就燒起來了」，順著這句話問「除了提燈籠，你們
  猜燈謎都怎麼玩呢」，把話題帶回節慶本身；不問「燈籠燒起來的時候，你都怎麼
  辦呢」——這種問法會把長者拉回處理意外的緊張情境，偏離「快樂」的今日主題）
- 只回純文字，不使用任何 markdown 語法（不加 **、#、- 條列符號等）——這段文字
  會直接餵給 TTS 唸給長者聽，也會直接進訓練資料，混進符號會被學進模型、正式
  上線時可能被唸出奇怪的內容，或讓輸出解析失敗
- 以上規則要在腦子裡想清楚、想完再動筆，直接寫最終版本：「思考：」這一行
  （如果輸出格式有要求的話）只寫最終判斷結果的簡短摘要；「思考：」以外的
  正式欄位（場景文字/問題/承接語等）各只出現一次、只寫最終定案的版本——不要
  把中途推翻、重寫的過程或「---」分隔線、「等等，我需要重新檢查」「這樣違反
  第X條」「修正如下」這類自我修正的旁白文字也印出來"""


def build_step1_user_prompt(scenario: dict, retry_feedback: str = "") -> str:
    elder = scenario["elder"]
    scene = scenario["scene"]
    elements = "、".join(scene["elements"])
    topic_cats = "、".join(scenario.get("topic_category", []))
    taboo_str = "、".join(elder.get("taboos", [])) or "無"
    return f"""請根據以下資料，設計一個懷舊療法的「開場問題」。

【長者背景】
姓名：{elder['name']}
職業背景：{elder['main_occupation']}
今日主題：{elder['today_topic']}
主題類別：{topic_cats}

【眼前畫面的元素】
{elements}

【禁忌話題（絕對不可提及或引導）】
{taboo_str}

【生成流程】照下面 5 個步驟依序生成，每一步做完再進入下一步，不要一邊寫一邊回頭
檢查前面的規則、更不要把中途改稿的過程寫出來——這是一個會直接產出正確答案的
流程，不是事後檢查清單：

1. 定調：從「主題類別」對應到16大核心主題中最貼近的1-2個，決定這一題要問的方向；
   若主題屬於哀傷之事、生命中特殊的事件，語氣放慢、放輕，優先同理而非深挖細節
2. 選錨點：從【眼前畫面的元素】挑一個具體物件，用下面三種寫法之一讓它成為自然
   口語的句子開頭（三選一即可）：
   - 加「這樣的／像這樣的／這些」＋物件，例如「這些木製模具」
   - 把物件包進一個動作或地點短語，例如「站在黑板前」「腳踏車停好之後」
   - 把物件放在真的會發生狀態變化的受詞位置，例如「鹽山堆好」（這種寫法要注意
     時態：如果後面的問題是問「完成之前」的過程或步驟，就不能用這種「已經完成」
     的狀態句當錨點——例如問「哪個步驟最得意」時不能用「鹽山堆好」，因為「堆好」
     是完成之後的狀態，跟「哪個步驟」問的是完成之前的過程，兩者時態兜不起來，
     唸起來會有跳接感；這種情況要把錨點改成正在進行的動作，例如「鹽山堆到一半」
     「堆鹽山的時候」。「狀態變化受詞」這種寫法只適合拿來問「發生這個變化之後」
     的事，例如「鹽山堆好，你最先想跟誰說呢？」才通順）
   物件是唯一合法的初始主詞位置；後面接的問句主詞永遠是「長者/你」，不能讓
   物件、地點或長者提到的其他人變成問句動詞的執行者（工具不會「收工」「下田」，
   地點不會自己「跳」「走」，一群人被提到時問的應該是「你」跟他們之間發生的事，
   不是「他們自己」做了什麼；用「你們」把「你」跟其他人合稱只是把問題稀釋掉，
   沒有真正解決，人物錨點還是佔走了主詞的重心，跟完全省略「你」是同一種毛病
   ——例如問「你都跟旁邊的工友聊些什麼呢？」，不是「旁邊的工友，你們都聊些
   什麼呢？」）
3. 選切入角度：依序嘗試下面 5 種，選第一個能自然套用在這個錨點上的，不要每次
   都跳過前面直接選最後一種：
   ①情感／意義——這件事、這個人對長者的意義或感受（例：「這些木製模具，做出來
     最讓你得意的是哪一種？」）
   ②陪伴的人——旁邊有誰、彼此說了什麼、怎麼互動
   ③感官記憶——氣味、聲音、觸感
   ④敘事推進或今昔對比——接下來發生的事、跟現在比起來怎麼樣
   ⑤過程步驟（只有動作本身帶儀式感或情感重量時才用，例如拜拜前的準備、退伍
     打包行李、蓋房子前的籌備；日常操作步驟如收工收拾工具不適用這個角度）
     ——之前/之後你都會做什麼，句子裡不要加「先」這個字，「之前/之後」已經
     把時間點交代清楚了
4. 預想答案：在心裡真的模擬一次長者聽到這題會怎麼回答——好答案是一整段敘述
   （一串動作、一段互動、一個過程）；如果預想的答案只會是一個詞（地名、時間點、
   人名、物品名稱、數量、神明名字、衣服種類），代表這個角度或問法選錯了，回到
   步驟3換下一種角度重選；同時排除「執行動作當下的技巧或方式」這類問法（例如
   怎麼握筆、怎麼用長耙、怎麼壓模具、怎麼組裝、怎麼助跑），長者不容易用語言
   具體描述這類精細動作，答案容易含糊帶過
5. 收尾包裝：套進長者熟悉的職業背景／生活情境，用「你」稱呼、加自然的口語
   語尾詞（呢、啊、喔），整句不超過15字，並確認：
   - 不是是非題（不用「嗎」結尾、不用「有沒有」「是不是」「會不會」「A不A」
     這類只能回答兩三個字的確認型問句）
   - 不用「你還記得嗎」「你記不記得」開頭
   - 不需要精確數字、年份、人名、地名或數量（例如「哪一年」「叫什麼名字」
     「一天要過多少手」）
   - 不預設長者「做錯了」或「出了差錯」（例如「裁到哪裡出了差錯？」）——這樣問
     等於要長者交代失誤，跟「傾聽大於糾正、肯定生命韌性」的精神相反，長者也
     不一定真的犯過這個錯
   - 不把畫面當成長者真的去過的特定地方（絕對不問「你有沒有來過這裡」「你認不
     認得這個地方」「這是不是你以前工作的地方」），要把畫面當成某一類場景的
     引子，問這類經驗的普遍情形（例：不問「你以前是不是常來這個廟口」，而是
     問「像這樣的廟口，你以前都去做什麼」）
   - 「用久了會有什麼變化」這類問法只能用在真的存在明顯生理或物理變化的動作上
     （例如長耙磨出繭），不能套用在沒有明顯痕跡的動作上（例如磨墨）
   - 物件的情境設定要符合常理（腳踏車通常停在廠房外面，不會停在室內產線裡）；
     用「像這樣的」「這種」泛指講法時，後面接的類別詞要跟物件本身同一類（樹
     不是一種學校）
   - 問題要具體微觀，不要抽象宏觀（不問「請說說你的朋友關係」）
   - 絕對不能引導往【禁忌話題】的方向，即使沒有直接用到禁忌詞字面
{_retry_feedback_section(retry_feedback)}
【輸出格式】（嚴格按照以下格式，不要加說明文字）
思考：（主題判斷：一句話判斷今日主題最貼近哪個核心主題；切入角度：一到兩句話決定這題要用什麼當錨點、往哪個方向問——兩段都要寫、都要留在同一行，不會念給長者聽）
場景文字：（30-60字的場景描述，給長者聽，念起來要自然）
問題：（≤15字的開放式問題，念起來要像真人在說話）
問題類型：STEP1開場
本回合已涵蓋的W：（只填W維度名稱本身，例：Where，不要加括號說明或理由）"""


def build_step2_user_prompt(scenario: dict, retry_feedback: str = "") -> str:
    elder = scenario["elder"]
    scene = scenario["scene"]
    elements = "、".join(scene["elements"])
    step1_response = scenario["elder_step1_response"]
    topic_cats = "、".join(scenario.get("topic_category", []))
    taboo_str = "、".join(elder.get("taboos", [])) or "無"
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

【禁忌話題（絕對不可提及或引導）】
{taboo_str}

【生成流程】完全順著長者剛才說的話走，不用回頭判斷主題、也不用搶時間問特定
W 維度。下面 4 個步驟只是給你自己在心裡想清楚的思考順序，不是要輸出的內容——
下面【輸出格式】沒有「思考：」這一行，直接產出「場景文字／問題／問題類型／
本回合已涵蓋的W」這4行就好，不要多寫一行「思考：」或任何步驟說明、決定過程：

0. 優先判斷：如果【長者剛才說的話】已經觸及或貼近【禁忌話題】的邊緣（是長者
   自己主動說的，不是被問出來的），跳過下面「選錨點、選切入角度」的深挖邏輯——
   不要繼續深挖那個敏感細節本身，但問題仍然要留在同一個場景、同一個當下裡，
   只是把焦點換成同一個畫面裡另一個比較安全、溫暖的具體細節或長者自己的感受，
   不能整個跳到不相干的新話題、新人物（自我檢查：拿掉長者剛才那句話，這個新
   問題聽起來會不會像是憑空冒出來的？如果會，代表跳得太遠了，要換一個仍在
   同一個當下、同一個畫面裡的角度）
   （例：長者說「媽媽最後拉著我的手，叫了我的名字」已貼近母親臨終禁忌，不追問
   「當時還說了什麼」，也不跳去問不相干的「家裡還有誰在等你」，而是留在同一個
   重逢的當下，把焦點從「媽媽的話」換成「長者自己的感受」，問「那一刻，你自己
   心裡在想什麼呢」）。如果長者剛才說的話沒有這個問題，才進入下面1-4的一般流程。
1. 選錨點：優先用長者剛才提到的具體人、事、物當錨點，不必勉強拉回畫面——順著
   長者的故事走，比守住畫面元素更重要；也可以用畫面物件（加「這樣的／這些」或
   包進動作短語）或感官記憶（氣味、聲音、觸感）當錨點。物件是唯一合法的初始
   主詞位置，後面接的問句主詞永遠是「長者/你」，不能讓物件、地點或長者提到的
   其他人變成問句動詞的執行者（工具不會「收工」「下田」，地點不會自己「跳」
   「走」，一群人被提到時問的應該是「你」跟他們之間發生的事，不是「他們自己」
   做了什麼；用「你們」把「你」跟其他人合稱只是把問題稀釋掉，人物錨點還是佔
   走了主詞的重心，跟完全省略「你」是同一種毛病——例如問「你都跟旁邊的工友
   聊些什麼呢？」，不是「旁邊的工友，你們都聊些什麼呢？」）
2. 選切入角度：依序嘗試①情感／意義（這件事、這個人對長者的意義或感受）→
   ②陪伴的人（旁邊有誰、彼此說了什麼）→③感官記憶→④敘事推進或今昔對比，
   選第一個能自然接著長者的話往下問的，不要每次都跳過前面直接選「之前/之後
   你都會做什麼」這種過程步驟句型——這種句型留到最後才用，只有動作本身帶
   儀式感或情感重量時才適用，且不要在句子裡加「先」這個字
3. 預想答案：在心裡真的模擬一次長者聽到這題會怎麼回答——好答案是一整段敘述，
   不是一個詞（地名、時間點、人名、物品名稱、數量）；同時排除操作技巧類問法
   （怎麼握、怎麼用、怎麼壓、怎麼組裝、怎麼助跑），長者不容易具體描述這類
   精細動作
4. 收尾包裝：用「你」稱呼、加自然口語語尾詞，整句不超過15字，並確認：不是
   是非題（不用「嗎」「有沒有」「是不是」「會不會」「A不A」）、不用「你還
   記得嗎」開頭、不需要精確數字/年份/人名/地名/數量、不預設長者「做錯了」、
   不把畫面當長者真的去過的地方（絕對不問「你有沒有來過這裡」「你認不認得
   這個地方」）、「用久了會有什麼變化」只用在真的有明顯痕跡的動作上、物件
   情境設定要合理、問題要具體微觀不抽象、絕對不引導向【禁忌話題】；若長者
   剛才的話有重複之前說過的內容，不點破「你已經說過了」，只需自然承接；
   若長者記錯時間、人名、地點，不糾正、不爭辯，順著他說的走
{_retry_feedback_section(retry_feedback)}
【輸出格式】（嚴格按照以下格式）
場景文字：（15-30字，承接上一句自然過渡）
問題：（≤15字，開放式，開頭要有具體錨點）
問題類型：STEP2追問
本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個W維度名稱本身，
例：Where，長者這輪回應裡實際涵蓋到哪幾個就填哪幾個，不要自創其他詞彙、
不要加括號說明或理由）"""


def build_step3_user_prompt(scenario: dict, retry_feedback: str = "") -> str:
    elder = scenario["elder"]
    scene = scenario["scene"]
    elements = "、".join(scene["elements"])
    step2_response = scenario["elder_step2_response"]
    taboo_str = "、".join(elder.get("taboos", [])) or "無"
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

【禁忌話題（絕對不可提及或引導）】
{taboo_str}

【生成流程】任務是鎖定「還沒涵蓋的W維度」設計補充問題。照下面 4 個步驟依序
生成，每一步做完再進入下一步，不要一邊寫一邊回頭檢查前面的規則、更不要把中途
改稿的過程寫出來：

0. 優先判斷：如果【長者最近說的話】已經觸及或貼近【禁忌話題】的邊緣（是長者
   自己主動說的，不是被問出來的），跳過下面「鎖定W維度深挖」的邏輯——不要
   繼續深挖那個敏感細節本身，但問題仍然要留在同一個場景、同一個當下裡，只是
   把焦點換成同一個畫面裡另一個比較安全、溫暖的具體細節或長者自己的感受，
   不能整個跳到不相干的新話題、新人物、也不要硬湊還沒涵蓋的W維度（自我檢查：
   拿掉長者最近說的那句話，這個新問題聽起來會不會像是憑空冒出來的？如果會，
   代表跳得太遠了，要換一個仍在同一個當下、同一個畫面裡的角度）。如果長者最近
   說的話沒有這個問題，才進入下面1-4的一般流程。
1. 定調：確認【目前已涵蓋的W】，鎖定還沒問過的W維度（優先 What 或 When，
   避免 Why），這一題要能自然帶出這個維度
2. 選錨點：從【眼前畫面的元素】或長者剛才提到的具體人事物挑一個，用下面三種
   寫法之一讓它成為自然口語的句子開頭：加「這樣的／像這樣的／這些」＋物件、
   把物件包進一個動作或地點短語、或放在真的會發生狀態變化的受詞位置（若用這種
   「已經完成」的狀態句當錨點，後面的問題只能問「發生這個變化之後」的事，不能
   反過來問完成之前的過程或步驟——時態會兜不起來、唸起來有跳接感；問過程或
   步驟時要把錨點改成正在進行的動作，例如「堆到一半」「做的時候」）。物件是
   唯一合法的初始主詞位置，後面接的問句主詞永遠是「長者/你」，不能讓物件、
   地點或長者提到的其他人變成問句動詞的執行者（用「你們」把「你」跟其他人
   合稱只是把問題稀釋掉，人物錨點還是佔走了主詞的重心，跟完全省略「你」是
   同一種毛病——例如問「你都跟旁邊的工友聊些什麼呢？」，不是「旁邊的工友，
   你們都聊些什麼呢？」）
3. 選切入角度：依序嘗試①情感／意義→②陪伴的人→③感官記憶→④敘事推進或今昔
   對比，選第一個既能自然套用在錨點上、又能帶出步驟1鎖定的W維度的角度，帶不
   出來就換下一個角度重試，不要為了兜維度硬寫一個跟長者剛才的話無關的問題；
   「之前/之後你都會做什麼」這種過程步驟句型留到最後才用，只有動作本身帶
   儀式感或情感重量時才適用，且不要在句子裡加「先」這個字
4. 預想答案並收尾：好答案是一整段敘述，不是一個詞（地名、時間點、人名、數量）；
   排除操作技巧類問法（怎麼握、怎麼用、怎麼壓、怎麼組裝、怎麼助跑）；用「你」
   稱呼、加自然口語語尾詞，整句不超過15字；確認不是是非題（不用「嗎」「有沒有」
   「是不是」「會不會」「A不A」）、不用「你還記得嗎」開頭、不需要精確數字/
   年份/人名/地名/數量、不預設長者「做錯了」、不把畫面當長者真的去過的地方、
   「用久了會有什麼變化」只用在真的有明顯痕跡的動作上、物件情境設定要合理、
   問題要具體微觀不抽象、絕對不引導向【禁忌話題】
{_retry_feedback_section(retry_feedback)}
【輸出格式】（嚴格按照以下格式）
思考：（主題判斷：一句話判斷今日主題最貼近哪個核心主題；切入角度：一到兩句話決定這題要用什麼當錨點、往哪個方向問——兩段都要寫、都要留在同一行，不會念給長者聽）
場景文字：（15-30字，幫長者重新聚焦到新的W）
問題：（≤15字，開放式，開頭要有畫面中的具體物件）
問題類型：STEP3補問
本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個W維度名稱本身，
例：Where，包含【目前已涵蓋的W】加上這題新問到的維度，不要自創其他詞彙、
不要加括號說明或理由）"""


def build_rejection_prompt(
    chosen_response: str, rule_name: str, rule_desc: str, taboos: list[str] | None = None
) -> str:
    taboo_section = ""
    if rule_name == "touches_taboo" and taboos:
        taboo_section = (
            f"\n【這位長者的禁忌話題（此範例要故意讓問題引導向這個方向）】\n"
            f"{'、'.join(taboos)}\n"
        )
    return f"""以下是一個符合所有規則的高品質懷舊療法問題回應（chosen）：

{chosen_response}

請生成一個**刻意違反特定規則**的問題回應（rejected），作為 DPO 訓練中的負面範例。

【要違反的規則】
{rule_name}：{rule_desc}
{taboo_section}
要求：
- 問題必須明顯違反上述規則
- 除了違反的規則外，其餘結構盡量保持相近
- 不要解釋你在做什麼，直接輸出 rejected 回應

【輸出格式】（與 chosen 相同的格式）
場景文字：...
問題：...
問題類型：...
本回合已涵蓋的W：..."""


def build_emotional_chosen_prompt(
    trigger: str, context: str, taboos: list[str] | None = None, retry_feedback: str = ""
) -> str:
    taboo_str = "、".join(taboos) if taboos else "無"
    return f"""懷舊療法進行中，長者突然出現了情緒反應。

【情境說明】
{context}

【長者說的話】
{trigger}

【這位長者的禁忌話題（絕對不可主動提及或追問細節）】
{taboo_str}

請設計 AI 治療師的「理想回應」（chosen）。

這個回應需要三個層次：
1. 【承接】先用溫暖的語氣讓長者感到被理解，不急著繼續
   - 如果長者因記不住而自責，要輕柔reassure他（記憶模糊很正常）
   - 語氣像有溫度的真人，不是機器
   - 若長者剛才說的內容本身就觸及【禁忌話題】，承接要溫和但不深入追問細節，
     不重複禁忌相關的具體內容，儘快輕柔地轉向安全的方向
2. 【找到正面角度】從長者說的話或他的人生經歷裡，輕柔地找到一個溫暖或有力量的面向
   - 若長者提到的溫暖對象或回憶本身就是【禁忌話題】（例如已故的人、或某段關係本身就是
     禁忌，如疏遠的子女），正面角度絕對不能
     放在肯定「那個人」或「那段回憶」本身有多好、多珍貴——這樣即使語氣溫暖，實質上還是
     停留、深化在禁忌對象上，一樣算違反禁忌規則；這種情況要把肯定的重心從「禁忌對象」
     移開，改成肯定長者「自己」：他現在還記得、還放在心上，這份心意本身很珍貴，或是他
     這些年一路走過來的韌性——講的是長者這個人，不是回頭再談一次那個禁忌對象
     （例：長者提到已故母親，不寫「媽媽這份愛真的很美好」——這還是在談媽媽；而是寫
     「你到現在還這麼記得她，這份心意很珍貴」——焦點放回長者身上）
   - 若溫暖對象或回憶不是禁忌話題，才適用以下角度：
     例：提到苦難 → 肯定他的韌性或那段時間裡珍貴的情感連結
     例：提到想念的人 → 肯定那份情感的美好
     例：記不清楚 → 肯定他願意回憶的心意，不需要記得清楚才有價值
   - 一律要肯定長者「現在仍然記得、仍然擁有」的部分，不要只強調他已經退化、遺忘、做不到的部分
3. 【輕柔引導】用一句問題把對話引回溫暖的方向（不強迫，是邀請），且不能引導向【禁忌話題】
   - 若情境涉及禁忌對象（人或關係），問題的錨點不能是「長者跟禁忌對象共同做過的事／
     共同回憶」——即使沒有直接說出禁忌詞本身，只要問法隱含指涉禁忌對象（包括用「你們」
     這種把長者跟禁忌對象合稱在一起的說法），一樣算引導向禁忌話題；要換成完全不牽涉
     禁忌對象的新方向，例如問長者自己現在的生活、或眼前畫面裡其他不相關的安全物件
     （例：禁忌是已故手足，不問「小時候你們一起玩，印象最深的是哪種遊戲呢？」——「你們」
     還是把長者跟已故手足綁在一起問，錨點沒有真正離開禁忌對象；改問跟眼前畫面其他
     元素有關、只涉及長者自己的問題，例如「現在如果想放鬆一下，你最想做什麼呢？」
     這類完全聚焦在長者自身當下感受、不牽涉任何人的問法）
   整體不超過70字，念起來要自然
{_retry_feedback_section(retry_feedback)}
【輸出格式】
情緒回應：（承接 + 找到正面角度，50字以內）
後續引導：（一句輕柔的邀請式問題，引導回療程）"""


def build_track_c_chosen_prompt(sc: dict, retry_feedback: str = "") -> str:
    elements = "、".join(sc["scene_elements"])
    taboo_str = "、".join(sc.get("taboos", [])) or "無"
    return f"""懷舊療法進行中，長者剛才說完了一段話，請設計治療師的「理想承接 + 下一個問題」。

【長者剛才說的話】
{sc['elder_response']}

【長者目前的情緒狀態】
{sc['emotion_desc']}

【眼前畫面元素】
{elements}

【接下來想探索的方向】
{sc['next_w']}

【這位長者的禁忌話題（絕對不可引導或追問）】
{taboo_str}

如果【接下來想探索的方向】跟【禁忌話題】衝突（例如探索方向叫你問某個人、但那個人正好
是禁忌對象；或探索方向指向的內容其實就是禁忌本身），禁忌規則優先——不要照著探索方向問，
改用步驟3裡「情感／意義→陪伴的人→感官記憶→敘事推進」這個優先序，另外挑一個完全不牽涉
禁忌對象的安全角度，探索方向只是參考，不是必須達成的指令。

請設計治療師的理想回應（承接語 + 問題）。照下面 5 個步驟依序生成，每一步做完
再進入下一步，不要一邊寫一邊回頭檢查前面的步驟、更不要把中途改稿的過程寫出來：

1. 定調承接語：先看長者的話裡有沒有把決定權丟回來的句子，這種情況要蓋過下面的
   一般情緒對照表：
   - 【明確要求換話題】像「換一個好不好」「聊點別的吧」「可以不要說這個嗎」——
     長者已經做出決定，不是在猶豫。承接語溫暖肯定這個選擇（例如「不想說的事就
     不用勉強」），不用承諾「你想聊什麼，我們就聊什麼」這種長者不會真的回答的
     空泛邀請；步驟3直接選新方向的具體問題，這就是在尊重他的要求
   - 【單純猶豫反問】像「你真的想知道嗎」「我要跟你說嗎」「你想聽嗎」——長者
     還沒決定，把決定權真的丟回來問你，這種情況才要把主導權完全交還：承接語
     比照「想說什麼、不想說什麼，都是你的自由，你願意說，我就在這裡陪你說」
     這種說法（不要用「好，沒關係」這種「好」開頭的句型，聽起來像打發人），
     步驟3改問尊重步調的問題（「你想現在說，還是晚點再說呢？」），不能硬拉
     去畫面裡其他不相干的物件
   若沒有上述句子，依一般情緒對照表定調：開心/驕傲/幽默→呼應正面情緒，帶著
   真誠的溫度；感傷/懷念→輕柔同理，從他說的話裡找一個溫暖或有價值的角度；
   疲倦/意興闌珊→先讓他放鬆（「沒關係，慢慢來」），再用輕鬆的問題邀請他繼續；
   困惑/記憶模糊→reassure他記不清楚很正常，用畫面元素幫他找方向感；帶負面
   情緒時，承接後要輕柔地把情緒引往溫暖或正面的方向（不是強迫正向，是在他的
   故事裡找到有力量或珍貴的部分，肯定他仍然記得、仍然擁有的部分）
   - 若長者剛才說的內容本身就觸及【禁忌話題】（例如提到已故的人、或某段關係本身就是
     禁忌），承接語裡的「溫暖或有價值的角度」不能放在肯定「那個人」或「那段回憶」本身
     多好、多有意義——這樣即使語氣溫暖，實質上還是在深化禁忌對象，一樣算觸及禁忌；
     要把肯定的重心從禁忌對象移開，改成肯定長者「自己」現在仍然記得、仍然放在心上、
     或是他這一路走過來的韌性，講的是長者這個人，不是回頭再談一次禁忌對象
     （例：長者提到已故父親的嚴格與正直，不寫「你爸這份正直真的讓人佩服」——這還是在
     談父親；而是寫「你到現在還這麼佩服他教你的道理，這份心意很珍貴」，焦點放回長者
     身上）
2. 選錨點：從【眼前畫面元素】或長者剛提到的具體人事物挑一個，用下面三種寫法
   之一讓它成為自然口語的句子開頭：加「這樣的／像這樣的／這些」＋物件、把
   物件包進一個動作或地點短語、或放在真的會發生狀態變化的受詞位置（若用這種
   「已經完成」的狀態句當錨點，後面的問題只能問「發生這個變化之後」的事，不能
   反過來問完成之前的過程或步驟——時態會兜不起來、唸起來有跳接感；問過程或
   步驟時要把錨點改成正在進行的動作，例如「堆到一半」「做的時候」）。物件是
   唯一合法的初始主詞位置，後面接的問句主詞永遠是「長者/你」，不能讓物件、
   地點或長者提到的其他人變成問句動詞的執行者（工具不會「收工」「下田」，
   地點不會自己「跳」「走」，一群人被提到時問的應該是「你」跟他們之間發生
   的事，不是「他們自己」做了什麼；用「你們」把「你」跟其他人合稱只是把
   問題稀釋掉，人物錨點還是佔走了主詞的重心，跟完全省略「你」是同一種毛病
   ——例如問「你都跟旁邊的工友聊些什麼呢？」，不是「旁邊的工友，你們都聊
   些什麼呢？」）。選定錨點後自我檢查：如果把這個錨點換成畫面元素清單裡
   任何其他東西，後面這句問題是不是照樣問得出來、完全不用改？如果換掉也
   沒差，代表這個錨點只是語法上的開場白，跟問題內容沒有實質關聯，要重選
   一個真正跟問題內容有關的錨點
3. 選切入角度（步驟1已決定要問尊重步調的問題時，跳過此步驟）：依序嘗試
   ①情感／意義（這件事、這個人對長者的意義或感受，例：「這件事讓你印象最深
   的是哪一段？」，要錨定在長者剛提到的具體人事物上，不能問空泛抽象的問題）
   →②陪伴的人（旁邊有誰、彼此說了什麼）→③感官記憶（氣味、聲音、觸感）→
   ④敘事推進（接下來發生的事，例：「後來有什麼變化？」），選第一個能自然
   套用的；「之前／之後，你都要做什麼準備」這種過程步驟句型留到最後才用，
   只有動作本身帶儀式感或情感重量時才適用（例如看病前換衣服準備、拜拜前的
   準備），不要套用在單純的日常操作步驟上，句子裡也不要加「先」這個字
4. 預想答案：好答案是一整段敘述，不是一個詞（地名、時間點、人名、數量）；
   排除操作技巧類問法（怎麼握、怎麼用、怎麼壓、怎麼組裝）；排除預設長者
   「做錯了」或「出了差錯」的問法（例如「裁到哪裡出了差錯？」）——跟「傾聽
   大於糾正、肯定生命韌性」的精神相反，改問中性的過程或感受
5. 收尾包裝：問題整句≤15字、開放式、開頭有具體錨點，且不能引導向【禁忌話題】；
   整體念起來要像有溫度的真人在說話，避免「頂...的」這類偏書面語氣，改用
   「真的很...」「非常...」；問句裡如果用「手感」「口感」這類感受詞，動詞
   不能省略，要保留「動詞＋物件」的完整動作再接感受詞（例如「揉麵團」的
   手感，不是「麵團」的手感）；不需要精確數字、年份、人名或地名

以上規則要在腦子裡想清楚、想完再動筆，但絕對不能把想的過程寫出來——輸出**只能**
是下面【輸出格式】規定的「承接語」「問題」這兩行，禁止出現任何自我檢查、草稿、
「違反第X條」這類規則編號、「---」分隔線、或任何「先寫一個版本再寫修正版」的
內容。想清楚了就直接寫最終版本的兩行，不要把思考過程也印出來。
{_retry_feedback_section(retry_feedback)}
【輸出格式】（只能有以下兩行，不能有其他文字、標記或分隔線）
承接語：（1-2句承接長者情緒的話，30字以內）
問題：（≤15字的下一個問題，開頭含畫面元素）"""


def build_track_c_rejection_prompt(
    chosen_response: str, rule_name: str, rule_desc: str, taboos: list[str] | None = None
) -> str:
    taboo_section = ""
    if rule_name == "touches_taboo" and taboos:
        taboo_section = (
            f"\n【這位長者的禁忌話題（此範例要故意讓問題引導向這個方向）】\n"
            f"{'、'.join(taboos)}\n"
        )
    return f"""以下是治療師理想的「承接 + 問題」回應（chosen）：

{chosen_response}

請生成一個**違反特定規則**的錯誤回應（rejected）：

【要違反的規則】
{rule_name}：{rule_desc}
{taboo_section}
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
    emotion: str = "happy",
    retry_feedback: str = "",
) -> list[dict]:
    """
    推理時的 prompt，包含長者剛才說的話，讓模型知道要承接什麼。

    2026-08 稽核發現這裡漏掉了生產端 orchestrator._generate_open_followup 實際
    會有的兩個區段（【長者目前情緒】與 retry_feedback，見 orchestrator.py:1054），
    訓練資料因此從沒讓模型看過帶這兩段內容的輸入——比照 Track D 的修法補上。
    emotion 預設 "happy"，retry_feedback 留給呼叫端（目前 generate_track_c 不需要）。
    """
    elements = "、".join(sc["scene_elements"])
    covered_w = covered_w or []
    skipped_w = skipped_w or []
    taboos = taboos or []

    topic_str = sc.get("current_topic", "")
    covered_str = "、".join(covered_w) if covered_w else "無"
    uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
    uncovered_str = "、".join(uncovered) if uncovered else "無（已全部涵蓋）"
    taboo_str = "、".join(taboos) if taboos else "無"

    system_content = _load_production_system_prompt()
    user_content = (
        f"長者剛才說：\n「{sc['elder_response']}」\n"
        f"\n【今日主題】\n{topic_str}\n"
        f"\n【眼前畫面元素】\n{elements}\n"
        f"\n【已涵蓋的W維度】\n{covered_str}\n"
        f"\n【尚未涵蓋的W維度】\n{uncovered_str}\n"
        f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
        f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
        f"\n請先承接長者的情緒（1-2句，符合他當下的心情，具體呼應他剛才說的內容），"
        f"再順著長者說的話問下一個問題（≤15字，開頭可用長者剛提到的具體人事物，"
        f"也可以用畫面元素，開放式，不必勉強拉回畫面）。\n"
        f"問題要自然跟著對話走，同時盡量帶出【尚未涵蓋的W維度】中的某一個。\n"
        f"{_retry_feedback_section(retry_feedback)}"
        f"\n【輸出格式】\n"
        f"承接語：（1-2句，30字以內）\n"
        f"問題：（≤15字）"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def build_emotional_rejection_prompt(
    chosen_response: str, rule_name: str, rule_desc: str, taboos: list[str] | None = None
) -> str:
    taboo_section = ""
    if rule_name == "dwell_on_taboo" and taboos:
        taboo_section = (
            f"\n【這位長者的禁忌話題（此範例要故意追問這個方向的細節）】\n"
            f"{'、'.join(taboos)}\n"
        )
    return f"""以下是面對長者負面情緒的理想回應（chosen）：

{chosen_response}

請生成一個**刻意違反特定規則**的錯誤回應（rejected）：

【要違反的規則】
{rule_name}：{rule_desc}
{taboo_section}
要求：
- 回應必須明顯違反上述規則，是一個對MCI長者不友善的回應
- 不要解釋你在做什麼，直接輸出 rejected 回應

【輸出格式】（與 chosen 相同）
情緒回應：...
後續引導：..."""


def build_track_d_chosen_prompt(sc: dict, retry_feedback: str = "") -> str:
    taboo_str = "、".join(sc.get("taboos", [])) or "無"
    emotion_guidance = _emotion_guidance(sc.get("emotion", "happy"))
    return f"""懷舊療法三回合療程剛結束，請設計治療師的「理想收尾引導」。

【長者資料】
姓名：{sc['elder_name']}
今日主題：{sc['today_topic']}

【長者最後說的話】
{sc['last_elder_response']}

【長者目前情緒】
{emotion_guidance}

【這位長者的禁忌話題（絕對不可引導回想）】
{taboo_str}

請設計收尾引導，需要：
1. 收尾語：1-2句，溫暖肯定長者今天的分享，把長者從回憶中輕柔地帶回現實
   - 語氣輕鬆自然，不誇張
   - 要有「回到今天／現在」的意涵，讓長者從過去的時空回到當下
   - 承接長者最後說的話，語氣連貫，不跳躍
   - 不能用「好了」「就先聊到這裡」「就到這邊」這類聽起來想結束對話、打發人的轉折語，
     即使前面已經有溫暖的肯定句，接上這種語氣一樣會把溫暖感覺沖淡（例：不寫「那個成就感
     真的很了不起。好了，今天就先聊到這裡。」，而是接「今天謝謝你跟我分享這些」這類
     仍保持溫度的收尾）
2. 問題：一句輕柔的開放式問題（≤15字），且不能引導向【禁忌話題】：
   - 只能問以下三類其中一種，不能問其他類型的問題（例如不能問操作細節、
     不能問旁人是誰這類 Track A/C 才問的具體技巧/人物問題，收尾階段只問
     這三類）：
     ①現在的感受或心情　②今天最讓長者開心的回憶　③想帶走的正向感受
   - 但每一類都必須具體呼應【長者最後說的話】裡提到的某個人事物或片刻，
     不能是換成任何一位長者、任何一段收尾發言都能原封不動問出口的通用句——
     自我檢查：這句問題如果拿掉「長者最後說的話」直接問，聽起來會不會完全
     一樣？如果一樣，代表沒有真的呼應這位長者剛才說的內容，要重寫
     （例：不寫①類通用句「現在心裡感覺怎麼樣呢？」，改寫成呼應具體內容的
     「想到客人滿意挑菜走的樣子，你現在心裡是什麼感覺呢？」；不寫②類通用句
     「今天哪個故事讓你最開心？」，改寫成「跟客人聊天的時光裡，最讓你開心的
     是哪個片刻呢？」；不寫③類通用句「今天有什麼讓你覺得溫暖的事？」，
     改寫成「身邊有人陪著你，這讓你覺得最溫暖的是什麼呢？」——③類要用
     「溫暖／珍貴／安心」這類明確指向正向情緒的詞，跟①類中性的「什麼感覺」
     （可以是任何情緒，不限正向）區分開來；這三個改法都要用「什麼／哪個」
     這類具體、直接的開放式疑問詞，不能用「怎麼把感覺帶著走」這種抽象比喻式
     問法（長者不容易理解「把感覺帶走」是什麼意思），也絕對不能用「是不是」
     「想不想」「會不會」「對不對」這類看似開放、實際上長者只能回答
     「是／不是」「想／不想」的確認型問法，一樣算是讓長者附和而非自己說出
     答案，跟 is_yesno 是同一種毛病）
   - 答案不能只是一個詞就能答完，也不能預設長者的情緒方向（不寫「今天最開心
     的是」這種預設「一定是開心」的問法；若長者最後說的內容偏向感傷或平靜，
     不要硬套②類「開心」的框架，改選①或③類）
   - 錨點不能是瑣碎的小物件或動作道具（例如「那條熱毛巾」「那張桌子」），
     要選長者真正在意、有情感重量的人事物（師傅、客人的那句話、一起做的事）；
     自我檢查：拿掉這個錨點，長者會覺得可惜嗎？如果只是順口帶過的小道具，
     換掉也不影響，就不合格
   - 錨點本身不能已經是個感受詞（例如「那份享受」「那份舒服」「那份甜」），
     不然後面再問「心裡是什麼感覺」等於問「這個感覺的感覺是什麼」，邏輯打轉；
     錨點要是具體的人事物名詞，感受留給問題本身去問
   - 問題要把長者帶回「現在」的感受，不能只是換句話問「還有哪些細節」
     （例如「你心裡最先想到哪一種」是還在往回憶裡挖，不是把長者帶回當下）；
     正確結構是「現在想到＿＿，你心裡是什麼感覺呢？」這種「現在+感受詞」，
     不是「還有哪些／最先想到哪個」這種延伸挖細節的結構
   - 用詞盡量中性溫暖，不要特別強調困苦或負面的字眼（例如講鞋子不用特別寫
     「破」），除非那個字眼本身就是長者話裡最重要的情感重量所在
{_retry_feedback_section(retry_feedback)}
【輸出格式】
收尾語：（1-2句，30字以內）
問題：（≤15字）"""


def build_track_d_rejection_prompt(
    chosen_response: str, rule_name: str, rule_desc: str, taboos: list[str] | None = None
) -> str:
    taboo_section = ""
    if rule_name == "touches_taboo" and taboos:
        taboo_section = (
            f"\n【這位長者的禁忌話題（此範例要故意讓收尾語或問題引導向這個方向）】\n"
            f"{'、'.join(taboos)}\n"
        )
    return f"""以下是懷舊療法收尾引導的理想回應（chosen）：

{chosen_response}

請生成一個**刻意違反特定規則**的錯誤收尾回應（rejected），作為 DPO 訓練的負面範例。

【要違反的規則】
{rule_name}：{rule_desc}
{taboo_section}
要求：
- 回應必須明顯違反上述規則
- 不要解釋你在做什麼，直接輸出 rejected 回應

【輸出格式】（與 chosen 相同）
收尾語：...
問題：..."""


def build_track_d_inference_prompt(
    sc: dict,
    emotion: str = "happy",
    retry_feedback: str = "",
) -> list[dict]:
    """
    推理時的 prompt，設計時對齊 orchestrator._generate_closing，但該函式
    2026-07-31 已改成「收縮期→結果期」兩次獨立呼叫，這裡目前仍是舊版單次
    組合式 prompt，兩者已經不一致（已知缺口，見上方模組層級註解，這輪先不
    展開重寫）。

    2026-07 稽核時發現這裡漏掉了生產環境 user_content 實際會有的兩個區段
    （【長者目前情緒】與 retry_feedback），訓練資料因此從沒讓模型看過帶情緒
    資訊的收尾情境——現在補上，emotion 預設沿用 TRACK_D_SCENARIOS 裡各
    情境自己標記的 "emotion"（沒標記的維持 "happy"，對應大多數情境的
    溫暖基調），retry_feedback 留給呼叫端（目前 generate_track_d 不需要）。
    """
    system_content = _load_production_closing_prompt()
    taboo_str = "、".join(sc.get("taboos", [])) or "無"
    user_content = (
        f"【長者資料】\n"
        f"姓名：{sc['elder_name']}\n"
        f"今日主題：{sc['today_topic']}\n"
        f"\n【長者最後說的話】\n{sc['last_elder_response']}\n"
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
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


# ─── API 呼叫 ────────────────────────────────────────────────────────────────

# 延遲建立 client（而非 import 時就建立），這樣其他腳本（如 evaluate_model.py）
# 可以單純 import 本檔案取用情境資料/prompt builder，不需要先設定 ANTHROPIC_API_KEY。
_client: "anthropic.Anthropic | None" = None


def _get_client() -> "anthropic.Anthropic":
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def call_claude(user_prompt: str, system: str = SYSTEM_PROMPT_THERAPIST, model: str = MODEL_CHOSEN) -> str:
    """呼叫 Claude 並回傳文字內容，使用 streaming 避免 timeout。

    system prompt（SYSTEM_PROMPT_THERAPIST，約5000 tokens）在整個收集/修復流程裡
    每次呼叫都逐字相同，加上 cache_control 讓它走 prompt caching——第一次呼叫要
    多付約1.25倍寫入快取的錢，之後同一份 system prompt 的呼叫只要約10%的價格，
    這幾輪大量重跑（每組 scenario/step 都要生 chosen + 10幾個 rejected）省下的
    成本很可觀。

    2026-07 曾試過加 thinking=True 想解決 self_check_leak（模型把「寫錯一版、
    自我抓包、重寫」的過程印在可見輸出），結果反而更糟：SYSTEM_PROMPT_THERAPIST
    裡的「5步驟生成流程」被模型當真逐步在thinking區塊裡跑，反覆推翻重試、
    thinking內容遠超過4096 tokens還沒寫完就被max_tokens截斷，完全生不出正式
    輸出。已確認不要在這個prompt上開thinking，改用「重試、失敗就丟棄」的既有
    機制加上人工挑案例手動改寫來處理。
    """
    with _get_client().messages.stream(
        model=model,
        max_tokens=2048,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        msg = stream.get_final_message()

    text_blocks = [b.text for b in msg.content if b.type == "text"]
    return "\n".join(text_blocks).strip()


# ─── 推理時的 prompt（DPO dataset 中的 prompt 欄位） ──────────────────────────

def _load_production_system_prompt() -> str:
    """
    直接讀取正式環境實際使用的 system prompt（app/prompts/question_5w1h.txt），
    不再手動複製一份文字進這支腳本。

    先前多次發生「question_5w1h.txt 加了新規則，這裡的 hardcoded 字串忘記同步」
    （稱呼你/您、markdown 禁令、精確數字/人名/地名禁令都各自漏過一次），造成訓練
    資料的 prompt 欄位跟 orchestrator.py 在推理時真正送給模型的內容不一致——
    對一個經過 DPO 微調、已經校準到特定 prompt 形狀的本地模型來說，這種落差會
    直接影響微調成效。改成直接讀檔，兩邊永遠保證一致，沒有「忘記同步」這個問題。
    """
    path = Path(__file__).parent.parent / "app" / "prompts" / "question_5w1h.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"找不到正式環境的 system prompt：{path}，"
            "訓練資料的 prompt 格式會跟生產環境不一致，請先確認路徑或還原檔案。"
        )
    return path.read_text(encoding="utf-8")


def build_inference_prompt(
    step: str,
    elder: dict,
    scene: dict,
    covered_w: list[str],
    topic_category: list[str] | None = None,
    elder_response: str = "",
    taboos: list[str] | None = None,
    emotion: str = "happy",
    retry_feedback: str = "",
) -> list[dict]:
    """
    組出推理時送給 llama3 的 messages 格式（/api/chat）。
    DPO 訓練的 prompt 欄位應與生產端 orchestrator 呼叫格式一致。

    2026-08 稽核發現這裡漏掉了生產端 orchestrator._generate_question 實際會有的
    兩個區段（【長者目前情緒】與 retry_feedback，見 orchestrator.py:991/1132），
    訓練資料因此從沒讓模型看過帶這兩段內容的輸入——比照 Track D 的修法補上。
    emotion 預設 "happy"（沒有特別標記情緒的情境維持現況），retry_feedback
    留給呼叫端（目前 generate_track_a 不需要）。

    同一輪稽核也發現兩個欄位落差，這次一併處理：
    - 生產端 user_content 有「興趣：{user.get('preferences') or '無'}」這行
      （orchestrator.py:986），這裡完全沒有——已補上，用 elder.get('preferences')。
      scenarios.json 目前每筆長者資料都沒有 preferences 欄位，所以現階段這行
      在訓練資料裡會固定顯示「無」；要讓內容真的有變化，需要另外幫
      scenarios.json 的長者資料補上興趣欄位，這裡先只補齊格式對齊。
    - 「懷舊治療主題類別」原本用人工標注的 topic_category（例如「工作、專長」），
      但使用者確認今日主題本身是治療師手動輸入或 AI 建議的自由文字，不是固定
      分類系統——生產端直接重複塞 today_topic（orchestrator.py:975/987）就是
      正確行為，不是 bug。這裡改成同樣直接使用 elder['today_topic']，
      topic_category 參數保留（呼叫端仍會傳入，用在別處如哀傷情境判斷／
      chosen prompt 的主題引導），但不再用來組這一行的內容。
    """
    elements_str = "、".join(scene["elements"])
    covered_str = "、".join(covered_w) if covered_w else "無"
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

    system_content = _load_production_system_prompt()

    elder_section = f"\n【長者剛才說的話】\n{elder_response}\n" if elder_response else ""

    # 思考欄位只有 STEP1/STEP3 要求（比照 orchestrator._generate_question /
    # _generate_supplement_question），STEP2（_generate_open_followup）沒有這個欄位。
    thinking_line = (
        "思考：（主題判斷：一句話判斷今日主題最貼近哪個核心主題；"
        "切入角度：一到兩句話決定這題要用什麼當錨點、往哪個方向問——"
        "兩段都要寫、都要留在同一行，不會念給長者聽）\n"
        if step in ("STEP1", "STEP3") else ""
    )

    user_content = (
        f"【長者資料】\n"
        f"姓名：{elder['name']}\n"
        f"職業背景：{elder['main_occupation']}\n"
        f"今日主題：{elder['today_topic']}\n"
        f"興趣：{elder.get('preferences') or '無'}\n"
        f"懷舊治療主題類別：{elder['today_topic']}\n"
        f"\n【眼前畫面元素】\n{elements_str}\n"
        f"\n【已涵蓋的W維度】\n{covered_str}\n"
        f"{elder_section}"
        f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
        f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
        f"\n【任務】\n{step_instructions[step]}\n"
        f"{_retry_feedback_section(retry_feedback)}"
        f"\n【輸出格式】\n"
        f"{thinking_line}"
        f"場景文字：（30-60字，給長者聽的場景描述）\n"
        f"問題：（≤15字，開放式，開頭要有畫面中的具體物件）\n"
        f"問題類型：{step_labels[step]}\n"
        f"本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個W維度名稱本身，"
        f"例：Where，不要自創其他詞彙、不要加括號說明）"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def build_emotional_inference_prompt(trigger: str, taboos: list[str] | None = None) -> list[dict]:
    system_content = (
        "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
        "長者可能有輕微認知障礙，當他出現負面情緒時，你要先給予情緒支持，再輕柔地引導回療程。"
    )
    taboo_str = "、".join(taboos) if taboos else "無"
    user_content = f"""長者剛才說了：
{trigger}

【禁忌話題（絕對不可主動提及或追問細節）】
{taboo_str}

請先給予溫暖的情緒回應，再加上一句輕柔的後續引導。若長者說的內容本身就觸及
【禁忌話題】，承接要溫和但不深入追問細節，儘快輕柔地轉向安全的方向；
後續引導也不能引導向【禁忌話題】。

【輸出格式】
情緒回應：（溫暖承接情緒，30-50字）
後續引導：（一句輕柔的問題或肯定，引導回療程）"""

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


# ─── 主要流程 ─────────────────────────────────────────────────────────────────

def generate_track_a(scenarios: list[dict]) -> list[dict]:
    """
    Track A：問題品質 DPO 對。

    scenarios.json 裡有部分場景（topic_category 含「哀傷之事」，例如
    sc059-063、sc107-111）的 elder_step1_response/elder_step2_response
    本身就是喪親、久病、孤獨等高情緒張力的揭露內容（例如「我媽媽走的時候，
    我在她旁邊……那個聲音我到現在還記得」）。但 build_step2/3_user_prompt
    只管視覺錨點、5W1H 優先序這些「問題品質」規則，完全沒有情緒承接的要求
    ——若照常生成 STEP2/STEP3，會教出「長者才剛描述完媽媽過世的細節，
    AI 卻直接問下一個錨定問題、不做任何承接」的行為，正好是 Track B
    rejection rules 裡 ignore_emotion/rush_topic/premature_closure 要懲罰
    的違規模式，跟 Track B 教的東西自相矛盾。
    這類高張力揭露的正確反應（先承接情緒、再問下一個問題）已經是 Track C
    的職責，EMOTIONAL_SCENARIOS 也已涵蓋喪親等對應的危機情境，因此這裡
    只保留 STEP1（開場問題，不需要反應任何既有揭露），跳過 STEP2/STEP3，
    避免同一批訓練資料同時教出兩種互相矛盾的行為。
    """
    pairs = []
    step_builders = {
        "STEP1": (build_step1_user_prompt, [], []),
        "STEP2": (build_step2_user_prompt, ["Where"], ["STEP1"]),
        "STEP3": (build_step3_user_prompt, ["Where", "Who"], ["STEP1", "STEP2"]),
    }

    for sc in scenarios:
        elder = sc["elder"]
        scene = sc["scene"]
        is_grief_scenario = "哀傷之事" in sc.get("topic_category", [])

        for step, (prompt_builder, covered_w, _) in step_builders.items():
            if is_grief_scenario and step in ("STEP2", "STEP3"):
                print(f"  [{sc['id']}] {step} — 哀傷之事場景，情緒承接已由 Track C/B 涵蓋，跳過")
                continue

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
            taboos = elder.get("taboos", [])
            inference_prompt = build_inference_prompt(
                step, elder, scene, covered_w,
                topic_category=sc.get("topic_category"),
                elder_response=step_responses[step],
                taboos=taboos,
            )

            for rule_name, rule_desc in QUESTION_REJECTION_RULES.items():
                if rule_name == "touches_taboo" and not taboos:
                    # 這位長者沒有設定禁忌話題，無法示範「刻意觸及禁忌」，跳過
                    continue

                print(f"    [{rule_name}] 生成 rejected...")
                rejection_prompt = build_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)

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
                        "taboos": taboos,
                    },
                })

    return pairs


def generate_track_b() -> list[dict]:
    """Track B：情緒引導 DPO 對。"""
    pairs = []

    for emo_sc in EMOTIONAL_SCENARIOS:
        trigger = emo_sc["trigger"]
        context = emo_sc["context"]
        taboos = emo_sc.get("taboos", [])

        print(f"  [情緒情境] {context[:20]}... — 生成 chosen...")
        chosen_prompt = build_emotional_chosen_prompt(trigger, context, taboos=taboos)

        try:
            chosen = call_claude(chosen_prompt)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ✗ chosen 失敗：{e}")
            continue

        inference_prompt = build_emotional_inference_prompt(trigger, taboos=taboos)

        for rule_name, rule_desc in EMOTION_REJECTION_RULES.items():
            if rule_name == "dwell_on_taboo" and not taboos:
                # 這個情境沒有設定禁忌話題，無法示範「明知禁忌卻追問」，跳過
                continue

            print(f"    [{rule_name}] 生成 rejected...")
            rejection_prompt = build_emotional_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)

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
                    "scenario_id": emo_sc["id"],
                    "step": "EMOTIONAL",
                    "rejection_rule": rule_name,
                    "track": "B",
                    "trigger_context": context,
                    "taboos": taboos,
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
        taboos = sc.get("taboos", [])
        print(f"  [Track C / {sc['emotion_tone']}] 生成 chosen...")
        chosen_prompt = build_track_c_chosen_prompt(sc)

        try:
            chosen = call_claude(chosen_prompt)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ✗ chosen 失敗：{e}")
            continue

        covered_w = _covered_w_before(sc["next_w"])
        inference_prompt = build_track_c_inference_prompt(sc, covered_w=covered_w, skipped_w=[], taboos=taboos)

        for rule_name, rule_desc in TRACK_C_REJECTION_RULES.items():
            if rule_name == "touches_taboo" and not taboos:
                # 這個情境沒有設定禁忌話題，無法示範「刻意觸及禁忌」，跳過
                continue

            print(f"    [{rule_name}] 生成 rejected...")
            rejection_prompt = build_track_c_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)

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
                    "scenario_id": f"track_c_{sc['emotion_tone']}",
                    "step": "TRACK_C",
                    "rejection_rule": rule_name,
                    "track": "C",
                    "emotion_tone": sc["emotion_tone"],
                    "taboos": taboos,
                },
            })

    return pairs


def generate_track_d() -> list[dict]:
    """Track D：收尾引導 DPO 對（三回合結束後帶長者回到現實）。"""
    pairs = []

    for sc in TRACK_D_SCENARIOS:
        taboos = sc.get("taboos", [])
        print(f"  [Track D / {sc['elder_name']} / {sc['today_topic']}] 生成 chosen...")
        chosen_prompt = build_track_d_chosen_prompt(sc)

        try:
            chosen = call_claude(chosen_prompt)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ✗ chosen 失敗：{e}")
            continue

        inference_prompt = build_track_d_inference_prompt(sc, emotion=sc.get("emotion", "happy"))

        for rule_name, rule_desc in TRACK_D_REJECTION_RULES.items():
            if rule_name == "touches_taboo" and not taboos:
                # 這位長者沒有設定禁忌話題，無法示範「刻意觸及禁忌」，跳過
                continue

            print(f"    [{rule_name}] 生成 rejected...")
            rejection_prompt = build_track_d_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)

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
                    "taboos": taboos,
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
