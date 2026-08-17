"""
療程編排器(Orchestrator)。

實作懷舊療法完整狀態機：三回合、5W1H追蹤、開放訪談與補問路徑。

2026-08-17起三回合性質不同，不是三次一樣的生圖流程：round 1 才是下面第1、2
點描述的完整生圖流程；round 2 是自由追問（STEP2+STEP3，不生圖、不合成語音）；
round 3 是 closing（回縮期，不生圖、不合成語音，單輪問答就結束整場療程）。
round 2/3 各自的開場邏輯與跟既有「三回合結束後心得」機制的關係，見
start_round、_start_round2_free_followup、_start_round3_closing 的說明，
不在下面這份流程說明的範圍內（下面談的是 round 1 生圖前後的狀態機）。

=== 對話流程（round 1）===

1. 前端呼叫 start_round(round_number=1) 開始第一回合
   → 這時還沒有圖片：先判斷 today_topic 屬於16大主題分類的哪一類
     （_classify_topic_category）。scene_text（開場鋪墊）固定是通用開場語
     （_PRE_IMAGE_Q1_INTRO，「很高興今天能坐下來陪你聊聊天。」）；
     question（真正的問句，Q1）是「說到{today_topic}，」接依分類挑的邀請語
     （_PRE_IMAGE_Q1_INVITATIONS，例如「工作」是「我很想知道，你以前工作的
     日子是什麼樣子呢？」，本身已經是一句完整、能單獨回答的問題）；分類
     失敗就退回單純的 _PRE_IMAGE_Q1_FALLBACK_QUESTION。分類結果存進
     state["topic_category"]，長者若答得不夠具體、需要問 Q2 縮小範圍時直接
     複用，不用重複分類。state 的 last_question_type 是 "pre_image_q1"

2. 每次長者回答後，前端呼叫 process_response(elder_response, state)
   → 狀態機判斷下一步，回傳 action + 下一個問題 + 更新後的 state

   若上一題是 "pre_image_q1"（長者剛回答完生圖前的破冰問題）：
     → 回答已經有具體細節（_has_usable_detail）→ 直接當生圖記憶來源，生圖，
       回傳 action="image_reveal"（這時才第一次出現 image_path，但還沒問
       STEP1，見下方「出示圖片」說明）
     → 回答不夠具體但有回應 → 核對這句話有沒有涵蓋 Where/When/How/Why 任一
       維度，優先沿用 _has_usable_detail 已查過的地點/活動/時間證據（見
       _map_basic_checks_to_w，避免跟 _detect_pre_image_w_coverage 各自
       獨立判斷同一句話卻互相矛盾），三項都沒查到證據才呼叫
       _detect_pre_image_w_coverage 做完整判斷，分兩種情境（見
       _FIVE_W1H_BANK／get_scenario2_followup 說明）：
         情境1（完全答不出來、四維度都沒涵蓋）→ 複用 state["topic_
         category"]，問對應16大分類的域縮小範例句
         （_PRE_IMAGE_Q2_SCENARIO1_TEMPLATES，語氣是給兩個具體角度當
         範例、降低長者自己組織畫面描述的認知負擔），只問一輪
         情境2（有內容但缺特定維度）→ 呼叫 get_scenario2_followup() 直接
         問缺的那個維度，可能連續問到 _pre_image_q2_round_cap() 動態算出
         的輪數上限（依實際缺幾項W維度決定，2026-08-16改成動態，見該
         函式說明），或該問的維度都問完（回傳None）才停
       兩種情境的 state 都會把 last_question_type 變成 "pre_image_q2"，
       回傳 action="pre_image_followup"，用 state["pre_image_q2_scenario"]
       記錄是哪一種、process_response 收到回答時才知道要怎麼處理
     → 真的不想／不能答（_is_true_refusal：沉默逾時或明確講不知道/不記得，
       跟一般話題結束判斷用的 _is_quick_end 不同——單純的短回答不算，短
       回答一樣會落到上面「回答不夠具體」那條路，見該函式說明）→
       退回 RAG 記憶生圖
   若上一題是 "pre_image_q2"：
     → 情境1：Q2若真的拒答，把Q1+Q2合併後套嚴格把關（不夠具體才退回RAG
       記憶）；Q2若有實質內容，改用跟情境2同一套 get_scenario2_followup()
       邏輯，缺維度就從bank追加問（受 _pre_image_q2_round_cap() 動態輪數
       上限保護，2026-08-16新增，見該分支說明），問完或bank判定不需再問，
       都直接採信累積內容當生圖記憶來源，不再套嚴格把關
     → 情境2：長者這句回答先用 _detect_covered_w 更新 state["covered_w"]
       （跟STEP2背景追蹤共用同一份狀態，好處是這裡偵測到的W維度，STEP1/
       STEP3 之後會自動避開重複），再檢查輪數上限／get_scenario2_
       followup() 還有沒有下一個維度可問——有就繼續問（state 保持
       "pre_image_q2"），沒有就把累積的回答合併當生圖記憶來源，生圖
   （詳見 _start_scene_after_detail、_classify_topic_category、
   get_scenario2_followup）

   出示圖片（image_reveal）：生圖後不直接問 STEP1，先問固定的出示圖片問題
   （_IMAGE_REVEAL_SCENE_TEXT + _IMAGE_REVEAL_QUESTION，不經過LLM），讓長者
   看圖後說出第一反應。state 的 last_question_type 是 "image_reveal"。
   若上一題是 "image_reveal"：
     → 依長者反應生成承接語（圖與記憶相符/有差異但平靜/有差異且介意/情緒
       明顯-感動，見 _generate_image_reveal_reaction；情緒明顯-不安已經被
       更前面的 _detect_emotional_trigger 攔截走，不會走到這裡），承接語後
       只有長者這句話是在回答分類2追問的「哪裡不一樣」時（was_deferred，
       見該分支2026-08-16第二次稽核說明）才接 _IMAGE_REVEAL_TRANSITION，
       其餘情況（分類1/3/4未經分類2追問就直接命中）不接這句過渡句，再接
       STEP1 開場問題，回傳 action="scene_ready"

   其餘 action 說明：
     "open_followup"    → 話題豐富，繼續順著長者深入（有 scene_text + question）
     "ask_supplement_w" → 話題結束，切入未問的W維度（有 scene_text + question）
     "end_round"        → 本回合W全部覆蓋或跳過，前端用 next_round 呼叫 start_round
     "end_session"      → 療程結束（round 3 回答 closing 那題後觸發，見
                          _start_round3_closing／_end_action 說明）

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
import random
import re
import unicodedata
from pathlib import Path
from services.llm import LLMService
from services.image import OpenAIImageService
from services.rag_client import RealRAGClient
from services.user_profile_db import DBUserProfileClient
from privacy.deidentifier import Deidentifier
from safety.response_guard import guarded_generate
from safety.element_filter import filter_scene_elements

# 5W1H 涵蓋清單（Why 條件式使用、優先序最低）。補問時不是永遠照這個順序
# 掃第一個沒涵蓋的——5W是動態引導技術，不是必須照1到5走完的線性流程，
# 實際挑選見 _next_step_or_end（非Why維度隨機挑選，Why 仍最後才輪到）。
_W_ORDER = ["Where", "Who", "What", "When", "How", "Why"]

# _detect_covered_w 實測（2026-08-17）發現本地模型對「晚上」這類明確時間詞
# 常會同時標成 When 跟 What 的證據，即使 prompt 已經明講「同一段文字只能當
# 一個維度的證據」也還是會重現——_missing_from_checks 的重複證據去重邏輯
# 又剛好偏袒 _W_ORDER 排序在前的 What，把正確的 When 判定砍成「仍缺」，
# 長者已經回答過的時間又被重問一次。這份清單同時餵給 _W_DESC["When"]（prompt
# 定義）跟 _resolve_when_duplicate_evidence（跑在 _missing_from_checks 之前
# 的去重修正，見該函式），兩邊共用同一份字面詞才不會各自漂移（同一種「兩邊
# 各自 hardcode 忘改一邊」的坑，之前 DPO 用詞審查也踩過）。
_WHEN_KEYWORDS = [
    "什麼時候", "幾點", "早上", "中午", "下午", "傍晚", "晚上", "凌晨",
    "早餐", "午餐", "晚餐", "宵夜", "季節", "年份",
]

_W_DESC = {
    "Where": "地點（在哪裡、哪個地方、場所）",
    "Who":   "人物（誰、哪個人、關係）",
    "What":  "事物（什麼事、什麼東西、發生什麼事，不含時間點）",
    "When":  f"時間（{'、'.join(_WHEN_KEYWORDS)}、人生階段）",
    "How":   "方式（怎麼做、如何、過程、感受）",
    "Why":   "原因（為什麼、動機）",
}

_W_HINT = {
    "Where": "用 Where 角度問（哪裡、哪個地方、場所）",
    "Who":   "用 Who 角度問（誰、哪個人、關係）",
    "What":  "用 What 角度問（什麼事、什麼東西、發生什麼事）",
    "When":  "用 When 角度問（什麼時候、季節、人生階段）",
    "How":   "用 How 角度問（怎麼做、如何、過程、感受）",
    "Why":   "用 Why 角度問（為什麼、原因、動機）——僅在長者狀態良好時使用",
}

# 只剩 STEP1 用得到——STEP2（_generate_open_followup）、STEP3
# （_generate_supplement_question）都是自己inline寫死【任務】/問題類型文字，
# 沒有透過這兩個dict，原本放在這裡的 STEP2/STEP3 entry從沒被讀取過，是死碼，
# 2026-08稽核拿掉。STEP1原本的「優先問 Where 或 What」也一併拿掉——這句跟
# question_5w1h.txt STEP1流程步驟2的切入角度優先序（情感／意義→陪伴的人→
# 感官記憶→敘事推進或今昔對比→過程步驟，完全沒有Where/What優先的概念）互相
# 矛盾，混進【任務】欄位可能把模型導向字面地點/事物問法，跟系統prompt想要的
# 「情感意義優先」精神打架。
_STEP_TASKS = {
    "STEP1": "生成第一個【開場問題】，引導長者進入回憶",
}

_STEP_TYPE_LABEL = {
    "STEP1": "STEP1開場",
}

# Kinect 即時偵測情緒（app/routers/sensor.py 的 emotion_raw）→ 給 LLM 的語氣指引
_EMOTION_GUIDANCE = {
    "sad":     "長者目前情緒低落，請優先給予溫暖同理與正向肯定，語氣放柔，暫緩深入提問，可引導至輕鬆或正向的話題。",
    "angry":   "長者目前情緒焦躁不安，請先安撫情緒、語氣放緩，避免追問敏感或原因類問題。",
    "excited": "長者目前情緒較亢奮，維持溫暖但避免過度刺激。",
    "happy":   "長者情緒穩定，正常延續對話即可。",
    "neutral": "長者目前情緒不明確或平穩，語氣維持溫和平穩，避免貿然追問需要較多情緒能量的問題（如原因、動機類），先觀察長者反應再決定是否深入。",
}


def _emotion_guidance(emotion: str) -> str:
    # 情緒偵測不見得可靠（例如中風、面癱等生理因素會讓表情辨識失準），
    # 未知情緒保守地當作「不明確」處理，不要預設為 happy 就貿然深入提問。
    return _EMOTION_GUIDANCE.get(emotion, _EMOTION_GUIDANCE["neutral"])


# 單回合題數上限、補問上限：避免為了湊滿 5W1H 連環追問到底。
# 話題自然結束時，最多補問 _MAX_SUPPLEMENT_PER_ROUND 個未涵蓋的W就結束回合，
# 不強求六個W維度都要問過一輪。
_MAX_QUESTIONS_PER_ROUND = 5
_MAX_SUPPLEMENT_PER_ROUND = 2

# 生圖前情境2追問（見 _FIVE_W1H_BANK）最多問幾輪，避免長者還沒看到圖之前，
# 就被連環追問拖住。到上限或 get_scenario2_followup() 提早回傳 None（該問的
# 維度都已涵蓋或被排除）兩者任一成立就結束追問、進生圖。
#
# 2026-08-14：從2降到1——原本2輪是為了盡量把W維度問滿，但實測發現Q1已經
# 給出豐富內容時（例如同時涵蓋Who/What/How），第2輪還是會為了湊剩下那個
# 維度（通常是Why）硬多問一題，長者會覺得「怎麼一直問」。使用者要的只是
# 「一定再多答一題」，不是「把W問到全滿」，改成固定只追問一輪，問完不論
# 還缺哪些W都直接生圖。
#
# 2026-08-16：固定1輪又踩到另一個問題——Q1完全答不出來、實際缺到3個W
# 維度時，也只追問1輪，等於3項裡有2項永遠問不到，生圖素材長期不足。
# 改成動態決定：依 Q1（或情境1轉情境2那句）當下實際還缺幾個W維度，取
# 「缺幾項」跟這裡的上限兩者較小值（見 _pre_image_q2_round_cap）——只缺
# 一項的話問完那一項就直接生圖，不會為了湊滿輪數多問；缺到3項以上時，
# 這個常數就是最後的煞車，不會真的問到「全滿」讓長者覺得一直被追問。
_MAX_PRE_IMAGE_Q2_ROUNDS_CAP = 3


def _pre_image_q2_round_cap(covered_w: list[str]) -> int:
    """
    情境2（含情境1追問Q2後發現有實質內容、比照情境2邏輯追加問的那條
    路徑）這一輪最多能問幾輪，依當下 covered_w 還缺幾個 _PRE_IMAGE_
    PRIORITY_ORDER 維度動態算出，取「缺幾項」跟 _MAX_PRE_IMAGE_Q2_
    ROUNDS_CAP 兩者較小值（見上面 2026-08-16 說明）。理論上呼叫這裡時
    一定至少缺一項（不缺的話 get_scenario2_followup 會直接回傳 None，
    不會進到需要輪數上限的分支），保底 or 1 只是防禦寫法，不預期真的
    用到。
    """
    missing_count = len([d for d in _PRE_IMAGE_PRIORITY_ORDER if d not in covered_w])
    return min(missing_count, _MAX_PRE_IMAGE_Q2_ROUNDS_CAP) or 1


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
# 2026-08 改名：原本叫 _FALLBACK_SCENE_TEXT，但STEP1現在已經不產出場景文字了
# （見 _parse_step1_response），這個常數實際上只剩STEP2/STEP3的「承接語」情境
# 在用，取名還叫「SCENE_TEXT」會讓人誤以為只跟場景描述有關，改成語意更中性、
# 涵蓋「場景文字」跟「承接語」兩種角色的名稱。
_FALLBACK_TRANSITION_TEXT = "我們接著聊聊這個吧。"
_FALLBACK_CLOSING_TEXT = "謝謝你今天的分享，辛苦了。"
# 生圖前破冰問題固定分兩層：scene_text（開場語，念給長者聽的鋪墊，固定用
# _PRE_IMAGE_Q1_INTRO，不疊加任何東西）＋ question（長者真正要回答的問句，
# Q1 由 _build_pre_image_question 組出來）。
#
# 2026-08：原本是「Q1先問籠統的邀請語，答得不夠具體再追問Q2縮小範圍」兩題
# 分開問。中間試過改成「邀請語＋具體畫面提問」直接合併成一題問完，不再讓
# _has_usable_detail 的LLM判斷（不穩定，同一句話不同次呼叫可能給出不同
# 答案）決定要不要多問一題——但後來發現「畫面是什麼樣子」這種要求長者自己
#把記憶轉換成視覺化描述的問法，對可能有認知功能退化的長者來說負荷偏高，
# 比「發生了什麼事」「跟誰在一起」這種敘事性提問更難回答。改回兩題分開問
# 的架構，但 Q2 的問法也一併改成「像是A情境，或者B情境，你會想到什麼呢？」
# ——給兩個具體角度當範例讓長者挑一個接話，而不是要長者自己憑空生成一個
# 「畫面」，降低認知負荷但仍保留「引導長者講出具體內容」的效果。都是純
# 字串模板，不經過 LLM 生成，天然不會違反任何格式規則，不需要保底值
# （分類失敗時的完整保底句另見 _PRE_IMAGE_Q1_FALLBACK_QUESTION）。
#
# 2026-08-14：這句「A情境/B情境」範例式問法後來發現只適合「長者完全答不
# 出來」的情況——已經給了具體情境、只是缺特定維度（例如缺地點）的長者，
# 再聽一次選項菜單會像沒在聽。拆成情境1（_PRE_IMAGE_Q2_SCENARIO1_
# TEMPLATES，這裡描述的範例式問法，只用在答不出來時）／情境2
# （_FIVE_W1H_BANK＋get_scenario2_followup，有內容但缺維度時直接問缺的
# 維度），見 process_response 的 pre_image_q1／pre_image_q2 分支。
_PRE_IMAGE_Q1_INTRO = "很高興今天能坐下來陪你聊聊天。"
_PRE_IMAGE_Q1_QUESTION_TEMPLATE = (
    "說到{today_topic}，"
)
# 分類失敗（category是None，沒有對應的邀請語可用）時的完整保底句——跟
# _PRE_IMAGE_Q1_QUESTION_TEMPLATE 不一樣，這句本身就是完整、能單獨回答的
# 問題，不需要再接其他句子。
_PRE_IMAGE_Q1_FALLBACK_QUESTION = (
    "說到{today_topic}，你有什麼故事想跟我分享呢？像是想到一個人，或者一件事，都可以。"
)

# 懷舊治療16大主題分類（見「懷舊治療主題總覽」）。分類結果存進
# state["topic_category"]，長者答得不夠具體、需要問Q2縮小範圍時直接複用，
# 不用重複分類。
_TOPIC_CATEGORIES = [
    "童年經歷", "讀書求學", "家庭", "感情", "工作", "奮鬥經歷", "軍旅",
    "興趣", "專長", "印象最深刻的地方", "休閒", "節慶", "哀傷之事",
    "人生目標", "自我成就感", "生命中特殊的事件",
]

# 生圖前 Q1 依16大分類挑對應的邀請語，接在「說到{today_topic}，」後面組成
# 完整問句（見 _build_pre_image_question）。每句本身已經是一句完整、能
# 單獨回答的問題（以「呢？」收尾），不需要再接其他句子。語氣是主動好奇、
# 邀請長者分享，比中性鋪墊更溫暖。「軍旅」「哀傷之事」這兩類特別放軟語氣、
# 主動給長者退路（「如果你願意」「不方便說的部分可以跳過」），哀傷之事這句
# 刻意跟其他句子的「邀請」語氣做區隔（更輕、更像接話而非開場邀請）。
_PRE_IMAGE_Q1_INVITATIONS = {
    "童年經歷":           "我一直很好奇，你小時候的生活是什麼樣子呢？",
    "讀書求學":           "我很想知道，你以前上學的日子是什麼樣子呢？",
    "家庭":               "我很想知道，你家裡的故事、跟家人相處的日子是什麼樣子呢？",
    "感情":               "我很好奇，你當年是怎麼認識另一半的呢？",
    "工作":               "我很想知道，你以前工作的日子是什麼樣子呢？",
    "奮鬥經歷":           "這一生有沒有什麼特別不容易、但你撐過來的事呢？",
    "軍旅":               "如果你願意，我很想聽你說說看，當兵從軍那段日子是什麼樣子呢？不方便說的部分可以跳過。",
    "興趣":               "你平常喜歡做些什麼事情，讓自己開心呢？",
    "專長":               "你有沒有什麼特別拿手的本事呢？",
    "印象最深刻的地方":   "這一生有沒有哪個地方，讓你特別難忘呢？",
    "休閒":               "你以前閒暇的時候，都喜歡做什麼呢？",
    "節慶":               "我很好奇，你以前都是怎麼過節的呢？",
    "哀傷之事":           "如果你願意，我很想聽你說說看，他平常的樣子，或者你們相處的時候，是什麼樣子呢？",
    "人生目標":           "這一生有沒有什麼特別想完成的心願呢？",
    "自我成就感":         "這一生最讓自己驕傲的一件事，是什麼呢？",
    "生命中特殊的事件":   "這一生有沒有什麼特別難忘、印象深刻的事情呢？",
}

# 生圖前 Q2・情境1（Q1完全答不出來、答非所問，_detect_pre_image_w_coverage
# 核對Where/When/How/Why四維度都找不到證據）：給兩個具體情境當範例、讓長者
# 挑一個接話，不是要長者自己憑空組織出一個「畫面」——降低認知負荷，但仍能
# 引導出足夠具體的內容當生圖來源。只問一輪，不像情境2（_FIVE_W1H_BANK／
# get_scenario2_followup）那樣會依 covered_w 連續追問缺的維度——長者這時候
# 連個切入點都沒有，給選項幫他縮小範圍才是重點，不是精準補齊某個W維度。
#
# 2026-08-14：原本是Q1答得不夠具體就一律問這句（不分「完全答不出來」跟
# 「有內容但缺維度」），對已經有具體情境、只是沒提到特定維度（例如已經講了
# 「跟同事一起加班」，只差地點）的長者來說，這句「像是A情境或B情境」等於
# 沒在聽，長者剛給的具體情境被晾在一邊、又被迫重新從頭選一個情境。改成
# 兩種情境分流：這句範例式問法只留給「真的完全答不出來」用，「有內容但缺
# 維度」改用 get_scenario2_followup() 直接問缺的那個維度，不重複給範例。
_PRE_IMAGE_Q2_SCENARIO1_TEMPLATES = {
    "童年經歷":           "像是玩耍的時候，或者跟家人在一起的時候，你會想到什麼呢？",
    "讀書求學":           "像是上課的時候，或者跟同學相處的時候，你會想到什麼呢？",
    "家庭":               "像是跟孩子相處，或者一家人團聚的時候，你會想到什麼呢？",
    "感情":               "像是剛認識的時候，或者籌備婚禮的時候，你會想到什麼呢？",
    "工作":               "像是剛開始工作，或者做得最上手的時候，你會想到什麼呢？",
    "奮鬥經歷":           "像是最難熬的時候，或者後來撐過去的時候，你會想到什麼呢？",
    "軍旅":               "像是受訓的時候，或者跟同袍相處的時候，你會想到什麼呢？不方便說也沒關係。",
    "興趣":               "像是自己一個人的時候，或者跟人一起的時候，你會想到什麼呢？",
    "專長":               "像是剛學會的時候，或者做得最順手的時候，你會想到什麼呢？",
    "印象最深刻的地方":   "像是那裡的景色，或者在那裡發生的事，你會想到什麼呢？",
    "休閒":               "像是跟朋友一起，或者自己一個人的時候，你會想到什麼呢？",
    "節慶":               "像是準備過節的時候，或者過節當天，你會想到什麼呢？",
    "哀傷之事":           "像是他平常的樣子，或者你們相處的時候，你會想到什麼呢？",
    "人生目標":           "像是達成之後的生活，或者身邊的人，你會想到什麼呢？",
    "自我成就感":         "像是完成的那一刻，或者旁人的反應，你會想到什麼呢？",
    "生命中特殊的事件":   "像是當時的場景，或者身邊的人，你會想到什麼呢？",
}

# 生圖前 Q2・情境2（Q1有內容、但缺特定W維度，_detect_pre_image_w_coverage
# 核對出至少一個維度有證據）：不再給範例讓長者選，直接問缺的那個維度——
# 長者已經給了具體情境，範例式問法在這裡顯得多餘、像沒在聽。
#
# 只收 Where/When/How/Why 四個維度（_PRE_IMAGE_PRIORITY_ORDER），不含
# Who/What：Q2 階段的目的是把Q1已經給的具體情境補到能生圖的程度，不是完整
# 走一輪5W1H——Who/What 這兩維度交給生圖後的 STEP1/2/3（_W_ORDER）補問即可。
#
# Why 排最後：跟 _W_ORDER（「Why 條件式使用、優先序最低」）同一個理由——
# Where／How 這種具體可觀察的維度對生圖來說比 Why 更關鍵、也更容易問到
# 明確答案，優先排在 Why 前面，讓有限的追問輪數優先花在更可能問出
# 「生得出圖」內容的維度上。
#
# granularity "theme"：整個主題共用一份 fields／excluded_fields。
# granularity "sub_item"：主題底下還要再分子項目（例如「童年經歷」的
# 「威權教育」跟「物質生活佳」該排除的欄位不一樣），子項目由
# _classify_pre_image_sub_item 判斷，只在情境2成立時才分類一次（見該函式
# 說明），不在 Q1 階段預先分類——多數Q1回答會落在「已經夠具體直接生圖」或
# 「情境1」，用不到子項目，沒必要每次都多付一次LLM呼叫成本。
#
# excluded_fields：這個主題／子項目結構上就不適合問的維度（例如「物質生活
# 佳」是家裡整體環境的敘事，沒有單一「地點」；「座右銘」只有Why有意義）。
# get_scenario2_followup() 會連同 covered_w 一起排除，不會問到這些欄位，
# 也不需要另外維護一組地點專用句庫。
_PRE_IMAGE_PRIORITY_ORDER = ["Where", "When", "How", "Why"]

_FIVE_W1H_BANK = {
    "童年經歷": {
        "granularity": "sub_item",
        "sub_items": {
            "威權教育": {
                "excluded_fields": [],
                "fields": {
                    "Where": {"variants": ["那是在家裡的哪裡呢？"]},
                    "When": {"variants": ["那是白天，還是吃過晚飯後的時間呢？"]},
                    "How": {"variants": ["當時的規矩是什麼樣子呢？", "那時候，通常是怎麼管教的呢？"]},
                    "Why": {"variants": ["這段記憶裡，你最想記住的是哪一部分？", "說起這段，最讓你印象深的是什麼？"]},
                },
            },
            "物質生活佳": {
                "excluded_fields": ["Where"],
                "fields": {
                    "When": {"variants": ["那是過年才有的，還是平常也有呢？"]},
                    "How": {"variants": ["那時候，家裡的生活是什麼樣子呢？"]},
                    "Why": {"variants": ["這段生活裡，你最想記住的是哪一部分？"]},
                },
            },
            "升學": {
                "excluded_fields": [],
                "fields": {
                    "Where": {"variants": ["那是在哪裡呢？"]},
                    "When": {"variants": ["那是開學的時候，還是考試前後呢？"]},
                    "How": {"variants": ["那時候，家裡對你唸書的看法是什麼樣子呢？"]},
                    "Why": {"variants": ["這件事裡，你最想記住的是哪一部分？"]},
                },
            },
            "童玩經驗": {
                "excluded_fields": [],
                "fields": {
                    "Where": {"variants": ["那是在什麼地方玩呢？"]},
                    "When": {"variants": ["那個時候，是放學後，還是假日的時候呢？"]},
                    "How": {"variants": ["當時是怎麼玩起來的呢？"]},
                    "Why": {"variants": ["這段回憶裡，你最想記住的是哪一個畫面？"]},
                },
            },
        },
    },

    "讀書求學": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["那是在哪個學校呢？", "那個時候，是在哪裡上學呢？"]},
            "When": {"variants": ["那是白天上課，還是晚自習的時候呢？", "那是開學不久，還是快放假的時候呢？"]},
            "How": {"variants": ["當時是怎麼上課或相處的呢？", "那時候，你們都是怎麼相處的呢？"]},
            "Why": {"variants": ["這段經驗裡，你最想記住的是哪一部分？", "說起這段求學的日子，最讓你難忘的是什麼？"]},
        },
    },

    "家庭": {
        "granularity": "sub_item",
        "sub_items": {
            "養兒育女": {
                "excluded_fields": ["Where"],
                "fields": {
                    "When": {"variants": ["那是白天忙家務的時候，還是晚上哄孩子睡覺的時候呢？"]},
                    "How": {"variants": ["那時候，你都是怎麼照顧孩子的呢？"]},
                    "Why": {"variants": ["這段日子裡，你最想記住的是哪一部分？", "養孩子的過程，最讓你難忘的是什麼？"]},
                },
            },
            "晚輩孝順": {
                "excluded_fields": ["Where"],
                "fields": {
                    "When": {"variants": ["那是過節團聚的時候，還是平常的日子呢？"]},
                    "How": {"variants": ["那時候，是怎麼樣的情形呢？"]},
                    "Why": {"variants": ["這段回憶裡，你最想記住的是哪一部分？", "說起這件事，最讓你感動的是什麼？"]},
                },
            },
            "家庭衝突": {
                "excluded_fields": ["When", "Where", "How"],
                "fields": {
                    "Why": {"variants": ["現在想起來，有沒有什麼和好的片刻，讓你印象深刻呢？"]},
                },
            },
            "家庭組成": {
                "excluded_fields": [],
                "fields": {
                    "Where": {"variants": ["那是在什麼地方呢？"]},
                    "When": {"variants": ["那是白天，還是一家人晚上聚在一起的時候呢？"]},
                    "How": {"variants": ["那時候，是怎麼樣的情形呢？"]},
                    "Why": {"variants": ["這段回憶裡，你最想記住的是哪一部分？"]},
                },
            },
        },
    },

    "感情": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["那是在哪裡呢？", "那個時候，你們常去的地方是哪裡呢？"]},
            "When": {"variants": ["那是白天約會，還是晚上見面的時候呢？", "那是什麼季節的事呢？"]},
            "How": {"variants": ["當時的心情是什麼樣子呢？", "那時候，你們是怎麼相處的呢？"]},
            "Why": {"variants": ["這段感情裡，你最想記住的是哪一個畫面？", "說起這段，最讓你難忘的是什麼？"]},
        },
    },

    "工作": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["那是在哪裡呢？", "那個時候，工作的地方是什麼樣子呢？"]},
            "When": {"variants": ["那是白天上班的時候，還是加班到很晚呢？", "那是忙季，還是比較清閒的時候呢？"]},
            "How": {"variants": ["當時是怎麼做到的呢？", "那時候，你都是怎麼處理的呢？"]},
            "Why": {"variants": ["這件事裡，你最想記住的是哪一部分？", "說起這段工作經驗，最讓你難忘的是什麼？"]},
        },
    },

    "奮鬥經歷": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["那是在什麼地方呢？", "那個時候，是在哪裡發生的呢？"]},
            "When": {"variants": ["那是什麼時候的事呢？", "那是白天，還是晚上發生的呢？"]},
            "How": {"variants": ["後來是怎麼撐過去的呢？", "那時候，你是怎麼熬過來的呢？"]},
            "Why": {"variants": ["這段經歷裡，你最想記住的是哪一個片刻？", "這段日子，最讓你放不下的是什麼？"]},
        },
    },

    "軍旅": {
        "granularity": "sub_item",
        "sub_items": {
            "戰爭經驗": {
                "excluded_fields": ["How"],
                "fields": {
                    "Where": {"variants": ["那個時候，是在哪裡呢？"]},
                    "When": {"variants": ["那是白天，還是晚上發生的呢？"]},
                    "Why": {"variants": ["如果你願意，這段記憶對你來說是什麼樣的感覺？"]},
                },
            },
            "隨政府遷台": {
                "excluded_fields": ["How"],
                "fields": {
                    "Where": {"variants": ["那個時候，是在哪裡發生的呢？"]},
                    "When": {"variants": ["那是白天，還是晚上出發的呢？"]},
                    "Why": {"variants": ["如果你願意，這段經歷對你來說是什麼樣的感覺？"]},
                },
            },
            "當軍人過程": {
                "excluded_fields": ["How"],
                "fields": {
                    "Where": {"variants": ["那個時候，是在哪裡呢？"]},
                    "When": {"variants": ["那是白天出操，還是晚上站哨的時候呢？"]},
                    "Why": {"variants": ["這段當兵的日子，如果你想聊，特別在哪裡呢？"]},
                },
            },
            "軍事教育": {
                "excluded_fields": ["How"],
                "fields": {
                    "Where": {"variants": ["那個時候，是在哪裡受訓的呢？"]},
                    "When": {"variants": ["那是白天訓練，還是晚上的時候呢？"]},
                    "Why": {"variants": ["這段訓練的日子，如果你想聊，特別在哪裡呢？"]},
                },
            },
        },
    },

    "興趣": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["那通常是在哪裡呢？", "那個時候，都在哪裡進行呢？"]},
            "When": {"variants": ["那通常是什麼時候呢？", "那個時候，通常是什麼時間做這件事呢？"]},
            "How": {"variants": ["通常是怎麼進行的呢？", "那時候，你都是怎麼做的呢？"]},
            "Why": {"variants": ["這件事裡，最讓你放不下的是哪一部分？", "說起這個興趣，最讓你著迷的是什麼？"]},
        },
    },

    "專長": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["那是在哪裡的事呢？", "那個時候，是在哪裡學的呢？"]},
            "When": {"variants": ["那讓你想到是什麼時候的事？", "那通常是白天，還是晚上練習的呢？"]},
            "How": {"variants": ["是怎麼學會、怎麼做到的呢？", "那時候，你是怎麼練成的呢？"]},
            "Why": {"variants": ["這項本事裡，你最想記住的是哪一部分？", "說起這項本領，最讓你驕傲的是什麼？"]},
        },
    },

    "印象最深刻的地方": {
        "granularity": "theme",
        "excluded_fields": ["How"],
        "fields": {
            "Where": {"variants": ["那確切是哪裡呢？", "那個時候，那個地方是什麼樣子呢？"]},
            "When": {"variants": ["大概是什麼時候去的呢？", "那個時候，是什麼季節或時期呢？"]},
            "Why": {"variants": ["這個地方裡，你最想記住的是哪一個畫面？", "說起這個地方，最讓你難忘的是什麼？"]},
        },
    },

    "休閒": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["那是在哪裡呢？", "那個時候，都去哪裡呢？"]},
            "When": {"variants": ["那通常是什麼時候呢？", "那個時候，通常是什麼時間去呢？"]},
            "How": {"variants": ["當時的氣氛或心情是怎麼樣的呢？", "那時候，通常是怎麼樣的情形呢？"]},
            "Why": {"variants": ["這件事裡，你最想記住的是哪一部分？", "說起這段休閒時光，最讓你懷念的是什麼？"]},
        },
    },

    "節慶": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["是在什麼地方呢？", "那個時候，是在哪裡呢？"]},
            "When": {"variants": ["那通常會是在什麼時段呢？", "大概什麼時候會這麼做呢？"]},
            "How": {"variants": ["你們家通常是怎麼過的呢？", "那時候，都是怎麼準備的呢？"]},
            "Why": {"variants": ["這個節日裡，你最想記住的是哪一個畫面？", "說起這個節日，最讓你懷念的是什麼？"]},
        },
    },

    "哀傷之事": {
        "granularity": "sub_item",
        "sub_items": {
            "親人死亡": {
                "excluded_fields": ["When", "Where", "How"],
                "fields": {
                    "Why": {"variants": ["這段回憶對你來說，特別珍貴的地方是什麼？", "想起{person}，最讓你感到溫暖的是什麼呢？"], "requires": "named_person"},
                },
            },
            "擾人疾病": {
                "excluded_fields": ["When", "Where", "How"],
                "fields": {"Why": {"variants": ["這段日子裡，有沒有什麼讓你撐過來的片刻呢？"]}},
            },
            "人生缺憾": {
                "excluded_fields": ["When", "Where", "How"],
                "fields": {"Why": {"variants": ["這件事對你來說，最放在心上的是什麼？"]}},
            },
            "寂寞沒人陪": {
                "excluded_fields": ["When", "Where", "How"],
                "fields": {"Why": {"variants": ["現在的日子裡，有沒有什麼時刻，讓你覺得比較自在呢？"]}},
            },
        },
    },

    "人生目標": {
        "granularity": "sub_item",
        "sub_items": {
            "心中願望": {
                "excluded_fields": ["When", "How"],
                "fields": {
                    "Where": {"variants": ["你想像中，那會是在哪裡呢？"]},
                    "Why": {"variants": ["這個心願對你來說，特別在哪裡？"]},
                },
            },
            "金錢掌控權": {
                "excluded_fields": ["When", "Where", "How"],
                "fields": {"Why": {"variants": ["這件事對你來說，特別在哪裡？"]}},
            },
            "座右銘": {
                "excluded_fields": ["When", "Where", "How"],
                "fields": {"Why": {"variants": ["這句話對你來說，特別在哪裡？"]}},
            },
            "兒女成就": {
                "excluded_fields": ["When", "Where", "How"],
                "fields": {"Why": {"variants": ["這件事對你來說，特別在哪裡？"]}},
            },
        },
    },

    "自我成就感": {
        "granularity": "theme",
        "excluded_fields": [],
        "fields": {
            "Where": {"variants": ["那是在哪裡達成的呢？", "那個時候，是在哪裡完成的呢？"]},
            "When": {"variants": ["那是什麼時候達成的呢？", "那是白天，還是晚上發生的呢？"]},
            "How": {"variants": ["當時是怎麼做到的呢？", "那時候，你是怎麼完成的呢？"]},
            "Why": {"variants": ["這件事裡，最讓你驕傲的是哪一部分？", "說起這件事，最讓你自豪的是什麼？"]},
        },
    },

    "生命中特殊的事件": {
        "granularity": "theme",
        "excluded_fields": ["How"],
        "fields": {
            "Where": {"variants": ["那是在哪裡發生的呢？", "那個時候，是在什麼地方呢？"]},
            "When": {"variants": ["那是什麼時候發生的呢？", "那是白天，還是晚上發生的呢？"]},
            "Why": {"variants": ["這件事裡，你最想記住的是哪一部分？", "說起這件事，最讓你難忘的是什麼？"]},
        },
        # 涉及228等政治敏感內容時，orchestrator 需動態把 excluded_fields 擴充為排除 Why，並加註「不方便說也沒關係」
    },
}

# {person} 佔位符代換用（見上方「哀傷之事」>「親人死亡」sub_item）：長者
# 資料庫（app/services/user_profile_db.py）目前沒有登記「懷念對象稱謂」的
# 欄位，只能從長者Q1（或累積的Q1+情境2追問）原話裡抓稱謂關鍵字，抓不到就
# 保底用「他」。絕對不能讓 {person} 這個字面字串外流到輸出——會被TTS直接
# 唸成英文字母，比隨便一個代稱都更突兀。
_PERSON_TERM_RE = re.compile(
    "媽媽|爸爸|母親|父親|先生|太太|老伴|阿嬤|阿公|奶奶|爺爺|外婆|外公|"
    "哥哥|姊姊|弟弟|妹妹|兒子|女兒|老公|老婆|丈夫|妻子|媽|爸"
)


def _extract_named_person(text: str) -> str:
    match = _PERSON_TERM_RE.search(text)
    return match.group(0) if match else "他"


def get_scenario2_followup(
    theme: str | None,
    sub_item: str | None,
    covered_w: list[str],
    named_person: str = "",
) -> str | None:
    """
    生圖前 Q2・情境2專用：依 _PRE_IMAGE_PRIORITY_ORDER 找下一個該問的W維度，
    直接複用 _FIVE_W1H_BANK 裡已經核對過、排除過不適用欄位的那份題庫——
    每個主題／子項目該排除什麼，題庫裡已經寫好了（excluded_fields），不需要
    另外維護一組地點專用句庫，也不會再出現「這個主題問地點問不通」的狀況。

    covered_w：目前已經涵蓋的W維度（來自 _detect_pre_image_w_coverage 對
    Q1的核對，或後續每一輪 _detect_covered_w 對長者回答的更新），已涵蓋的
    欄位跳過不問。

    回傳 None 代表該問的都問完了（扣掉 excluded_fields、covered_w 後，
    _PRE_IMAGE_PRIORITY_ORDER 已經沒有欄位可問）——呼叫端據此結束情境2的
    追問迴圈，進生圖。theme 不在題庫裡、或 sub_item 分類失敗（該主題是
    sub_item granularity 卻拿不到有效值）時，同樣回傳 None，不勉強瞎猜。
    """
    theme_data = _FIVE_W1H_BANK.get(theme) if theme else None
    if not theme_data:
        return None
    if theme_data["granularity"] == "sub_item":
        field_data = theme_data.get("sub_items", {}).get(sub_item)
    else:
        field_data = theme_data
    if not field_data:
        return None

    excluded = set(field_data.get("excluded_fields", []))
    fields = field_data.get("fields", {})
    for w in _PRE_IMAGE_PRIORITY_ORDER:
        if w in excluded or w in covered_w:
            continue
        field = fields.get(w)
        variants = field.get("variants") if field else None
        if not variants:
            continue
        question = random.choice(variants)
        if field.get("requires") == "named_person":
            question = question.format(person=named_person or "他")
        return question
    return None


def _build_pre_image_question(today_topic: str, category: str | None) -> str:
    """
    組生圖前破冰問題 Q1（見上方 _PRE_IMAGE_Q1_INTRO 一帶的說明）：「說到
    {today_topic}，」接分類對應的邀請語，本身已經是一句完整的問題。是否
    追問 Q2 由 process_response 依 _has_usable_detail 的判斷另外決定，不在
    這裡處理。分類失敗（category是None，或分類結果不在 _PRE_IMAGE_Q1_
    INVITATIONS 裡）時退回完整的 _PRE_IMAGE_Q1_FALLBACK_QUESTION，不勉強
    拼湊。
    """
    if category and category in _PRE_IMAGE_Q1_INVITATIONS:
        prefix = _PRE_IMAGE_Q1_QUESTION_TEMPLATE.format(today_topic=today_topic)
        return f"{prefix}{_PRE_IMAGE_Q1_INVITATIONS[category]}"
    return _PRE_IMAGE_Q1_FALLBACK_QUESTION.format(today_topic=today_topic)


# 生圖完成、圖片第一次出現時的固定「出示圖片」開場白＋問題——不預先打預防針，
# 單純交代這張圖是怎麼來的、留白讓長者自己反應，不用引導語暗示長者該有什麼
# 感覺。是純字串模板，不經過 LLM 生成。
#
# 注意這裡故意把畫面講成「圖畫」「照你說的話畫出來的」——這跟 STEP1/2/3 那組
# scene_text 規則裡「不能把畫面講成一幅畫/示意圖」（見 scene_text_frames_as_
# artwork）是不同情境：那條規則是為了讓 5W1H 追問時長者能沉浸式回想，不要有
# 「這只是一幅畫」的疏離感；這裡剛好相反，是唯一一個刻意誠實、後設地告訴長者
# 「這是AI依你說的話畫出來的示意圖」的時刻，之後才會轉回沉浸式的 5W1H 語氣。
_IMAGE_REVEAL_SCENE_TEXT = "我把你剛剛說的故事畫成一張圖了，想給你看看。"
_IMAGE_REVEAL_QUESTION = "你看看這張圖，想到什麼都可以跟我說。"

# 承接語後、進入 STEP1 前的過渡句，見 _generate_image_reveal_reaction。
# 2026-08-16（第二次稽核）：內容是在謝長者「說了這麼多」，只有長者這句話是
# 在回答分類2追問的「哪裡不一樣」時才對得上，process_response 的 image_
# reveal 分支用 was_deferred 判斷要不要接這句話——不是每次出示圖片反應
# 都會接，見該分支說明。
_IMAGE_REVEAL_TRANSITION = "謝謝你跟我說這麼多，讓我更了解你記得的畫面是什麼樣子了。"

# 長者看完圖沒有特別反應（quick_end）時的固定過渡句，取代承接語，直接接上
# STEP1 問題，見 process_response 的 image_reveal 分支。
_IMAGE_REVEAL_QUICK_END_ACK = "沒關係，那我們來聊聊，"

# Unity 端問題撥放完30秒沒按麥克風時送出的合成 marker（GameController.cs
# AutoSubmitNoResponse），不是長者真的說的話，不該拿去問 LLM 有沒有情緒訊號。
_NO_RESPONSE_MARKER = "（長者未回應）"

# 長者明確表示放棄／答不出來的關鍵字，_is_quick_end 跟 _is_true_refusal
# 共用同一份清單，避免兩處各自維護一份、改一邊漏改另一邊。
_GIVE_UP_KEYWORDS = ["不記得", "不知道", "忘了", "忘記了", "不清楚", "沒印象"]

def _strip_leaked_brackets(text: str) -> str:
    return _LEAK_BRACKET_RE.sub("", text).strip()


# STEP3補問保底問句：target_w 已知時，比起完全通用的「讓你想到什麼？」，
# 用對應W維度的通用問法更貼近這一題原本想問的方向（同樣是保底，能中的還是
# 比不中的好）。刻意不收 Why——來源模板本身也沒給 Why 的通用問法，Why 涉及
# 意義/動機，通用句容易問得空泛，交給 _element_fallback 的預設保底句就好。
_W_FALLBACK_QUESTION = {
    "Who":   "那個時候，還有誰跟你在一起呢？",
    "When":  "那大概是什麼時候的事呢？",
    "Where": "那是在什麼地方呢？",
    "What":  "那時候還有什麼讓你印象深刻的地方？",
    "How":   "當時是怎麼樣的情形呢？",
}

# 五感通用保底問句本體，供 _TOPIC_SENSE_FALLBACK 組裝，也避免同一句文字散落
# 各處、改一次要找好幾個地方。
_SENSE_QUESTION = {
    "視覺": "那時候，天氣或光線是什麼樣子呢？",
    "聽覺": "那個時候，有沒有什麼聲音，讓你印象特別深呢？",
    "嗅覺": "那個時候，空氣中有沒有什麼味道呢？",
    "味覺": "那時候，有沒有嚐到什麼味道呢？",
    "觸覺": "那時候，有沒有摸到或感覺到什麼呢？",
}

# STEP1/STEP2保底問句：依16大主題類別（_TOPIC_CATEGORIES）挑一個最貼近的
# 感官通用問法，取代完全籠統的「XX讓你想到什麼？」（這句本身就是question_5w1h.
# txt明文禁止的「籠統聯想類」問法）。挑選依據跟question_5w1h.txt【提問規則】
# 「五感當切入角度」那條挑選原則同一套邏輯（2026-08使用者確認）：
#   - 童年經歷／休閒：視覺、聽覺、觸覺都適合，取視覺代表（天氣/光線最通用）
#   - 讀書求學：聽覺、嗅覺為主（上課鐘聲、朗讀聲、粉筆或書本的氣味），取聽覺代表
#   - 家庭／節慶：嗅覺、味覺為主（飯菜香、年糕味這類），取嗅覺代表
#   - 感情：嗅覺、聽覺為主（喜宴菜香、鞭炮聲、音樂），取嗅覺代表
#   - 工作／軍旅：聽覺、觸覺為主（哨聲、制服材質這類），取聽覺代表
#   - 興趣：長者實際講的興趣內容差異太大（唱歌→聽覺，種花→嗅覺/觸覺，看報紙→
#     視覺），無法保底時預先鎖定單一感官，取視覺當保守通用預設
#   - 專長：觸覺、聽覺為主（操作時的手感、伴隨的聲音，例如算盤聲），取觸覺代表
#   - 奮鬥經歷：觸覺、視覺為主（身體疲憊/環境冷熱、周遭場景樣貌），跟工作類接近
#     但個案差異大，取視覺代表
#   - 印象最深刻的地方：視覺、聽覺、嗅覺都適合（場景沉浸型），取視覺代表
#   - 自我成就感：聽覺、視覺為主（旁人的稱讚或反應、成果呈現出來的樣子），取
#     聽覺代表
#   - 哀傷之事／人生目標：刻意不給——哀傷之事怕把注意力拉回具體場景細節，
#     人生目標是尚未發生的想像場景，感官提問容易變成逼長者憑空編造細節，兩者
#     理由不同但結論一樣是不用感官，這裡不建立對應項目，讓 _element_fallback
#     自然退回畫面錨定的保底句
#   - 生命中特殊的事件：往事性質差異可能很大（一般往事 vs. 政治敏感等沉重
#     事件），fallback沒有語意判斷能力、無法像LLM一樣依內容臨時判斷，保底時
#     一律從嚴，比照哀傷之事不給感官項目，不建立對應項目
_TOPIC_SENSE_FALLBACK = {
    "童年經歷":         _SENSE_QUESTION["視覺"],
    "讀書求學":         _SENSE_QUESTION["聽覺"],
    "家庭":             _SENSE_QUESTION["嗅覺"],
    "感情":             _SENSE_QUESTION["嗅覺"],
    "工作":             _SENSE_QUESTION["聽覺"],
    "奮鬥經歷":         _SENSE_QUESTION["視覺"],
    "軍旅":             _SENSE_QUESTION["聽覺"],
    "興趣":             _SENSE_QUESTION["視覺"],
    "專長":             _SENSE_QUESTION["觸覺"],
    "印象最深刻的地方": _SENSE_QUESTION["視覺"],
    "休閒":             _SENSE_QUESTION["視覺"],
    "節慶":             _SENSE_QUESTION["嗅覺"],
    "自我成就感":       _SENSE_QUESTION["聽覺"],
}


def _element_fallback(
    scene_elements: list[str],
    target_w: str | None = None,
    topic_category: str | None = None,
    with_covered_w: bool = True,
) -> dict:
    """
    guarded_generate 重試多次仍違規時的最終保底值。之前保底句是完全通用、跟
    這次情境無關的固定字串（「現在心裡在想些什麼呢？」）——2026-08 用真實
    pipeline 實測發現：加了 too_long 等格式規則檢查後，落到這個保底句的比例
    不低（單場測試將近一半），代表有不小比例的回合長者聽到的問題其實跟眼前
    這次的畫面、記憶完全無關。這裡改成至少帶著這次真正的畫面元素組出保底句，
    純字串組合、保證合規，不用再多呼叫一次LLM，不影響延遲。

    target_w: STEP3補問才會傳，known時優先用 _W_FALLBACK_QUESTION 對應的
    W維度通用問法。
    topic_category: 依 _classify_topic_category 分類出的16大主題類別，target_w
    沒有對應項目時（例如target_w="Why"、或STEP2 open_followup根本沒有target_w）
    退回 _TOPIC_SENSE_FALLBACK 依主題挑的感官通用問法；兩者都沒對應項目時，才
    退回原本 scene_elements 組出來的保底句。
    """
    elements_str = _natural_join(scene_elements or [])
    first = scene_elements[0] if scene_elements else None
    fallback = {
        "scene_text": (
            f"眼前的畫面裡有{elements_str}，我們換個方向聊聊吧。"
            if elements_str else _FALLBACK_TRANSITION_TEXT
        ),
        "question": (
            _W_FALLBACK_QUESTION.get(target_w)
            or _TOPIC_SENSE_FALLBACK.get(topic_category)
            or (f"{first}，讓你想到什麼？" if first else _FALLBACK_QUESTION)
        ),
    }
    if with_covered_w:
        fallback["covered_w"] = []
    return fallback


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


_STYLE_ONLY_FRAGMENTS_RE = re.compile(
    r"watercolor painting style|nostalgic warm tones|warm high-contrast palette|"
    r"warm tones|high-contrast palette|"
    r"avoid adjacent blue-green-purple tones|no text|"
    r"no people, empty of any human figures",
    re.IGNORECASE,
)


# evidence／source_text 逐字比對前先拿來正規化標點寬度用（見 _is_real_
# evidence 2026-08-14 那條變更紀錄）。NFKC 只涵蓋「全形拉丁標點」區塊
# （U+FF00-FFEF，例如全形逗號／驚嘆號／冒號），涵蓋不到中文原生標點
# （。「」『』、這些不在該區塊，NFKC 不會動它們），要另外手動對應。
_PUNCT_WIDTH_MAP = str.maketrans({
    "，": ",", "。": ".", "、": ",", "！": "!", "？": "?",
    "：": ":", "；": ";", "（": "(", "）": ")",
    "「": '"', "」": '"', "『": '"', "』": '"',
})


def _normalize_punct_width(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_PUNCT_WIDTH_MAP)


# _is_real_evidence 用來拆分「模型把多個真證據用連接詞黏成一段回傳」的
# evidence（見該函式 2026-08-16 說明），只切分常見的列舉型連接符號，不切
# 「的」「和」「跟」這類可能是內容本身一部分的字。
_EVIDENCE_DELIMITER_RE = re.compile(r"[、，,／/]")


def _is_real_evidence(evidence: str | None, source_text: str) -> bool:
    """
    判斷「逐項列證據」模式（見 _find_missing_memory_items）拿到的 evidence
    是不是「真的對應到具體內容」的證據，不是空字串、「找不到」、或隨手抓
    的樣板字。

    2026-08-14 實測發現：光是「evidence 非空、不等於找不到」還不夠嚴謹——
    模型有時候會把每張圖固定都會有的畫風/色調樣板字（例如"no text"，見
    _STYLE_ONLY_FRAGMENTS_RE／_strip_style_descriptors）直接當成不相關
    項目（例如「哥哥剝柚子」「狗追逐」）的證據硬湊，這種evidence不是
    「找不到」三個字，逃過原本的過濾，但明顯不是真的證據——樣板字每張
    圖都會出現，不可能是任何特定項目的專屬視覺內容。這裡額外擋掉：
    (1) evidence 整段剛好就是畫風/色調樣板字本身；(2) evidence 根本沒有
    真的出現在 source_text 裡（防止純粹捏造的證據文字）。

    2026-08-14（第二次）：測 _detect_pre_image_w_coverage 時發現本機模型
    有時會把長者原話整句複製當證據，內容完全正確，卻只是把全形標點抄成
    半形（例如長者原話句尾是「。」，模型抄成「.」）——逐字substring比對
    因為這一個標點字元不同就整句判定「沒有真的出現在原文」，把一個對的
    證據錯殺。substring 比對前先用 _normalize_punct_width 把兩邊的標點
    寬度都正規化再比，只影響標點符號，不影響中文字本身的比對嚴謹度。

    2026-08-14（第三次）：修完上面那條才發現本機模型對「Why（原因/意義）」
    這種抽象、沒有固定詞類對應的維度，經常整句照抄長者原話當證據交差
    （不管長者有沒有真的講出原因/意義都一樣）——跟地點/人物/活動這種有
    明確詞類、抓不到就老實回「找不到」的具體維度行為不一樣。這種evidence
    通過substring比對（畢竟真的是原文的一部分），卻不是prompt要求的
    「具體片段」，是模型在「找不到適合的短語」時的偷懶策略。加一道長度
    比例檢查：evidence長度佔source_text長度太高比例（>0.8）、且
    source_text本身不算太短（>=8字，太短的原話本來就可能整句就是唯一
    能圈出來的片段，不該被這條規則誤傷）時，視為「整句交差」不算真證據。

    2026-08-15：長度門檻原本寫 >10（嚴格大於），實測發現長者一句10個字
    整的短回答（例如「我會跟家人一起吃月餅」）剛好卡在邊界外，這道防呆
    完全沒觸發，讓模型整句照抄當四個維度共用證據的偷懶案例直接放行——
    10字左右的短句在長者回答裡很常見，不是稀有邊界情況。改成 >=8，
    往下多收幾個字長度，縮小這個「剛好卡在門檻外」的縫隙。

    2026-08-16：實測 _retry_short_phrase_evidence（要求2-4字單一關鍵詞）
    發現本機模型遇到長者一句話裡其實有兩件真事（例如「吃月餅」跟「做
    柚子帽」）時，不會照格式只挑一個，而是把兩個都塞進同一個evidence、
    用頓號接起來回傳「吃月餅、做柚子帽」——這個頓號是模型自己加的連接
    詞，不是原文本來就有的字元（長者的話是先經過STT轉出來的，口語斷句
    本來就跟書面頓號對不太上），逐字整段比對找不到這個連接詞就整組判定
    沒找到，把兩個其實都真實存在的內容一起錯殺。加一道退路：整段比對
    失敗、但evidence裡有頓號/逗號這類分隔符時，拆開來看，只要拆出來的
    每一段各自都是原文的逐字子字串，就算數——不要求連接詞本身也要在
    原文裡找到對應，只驗證分隔符兩側的實質內容。
    """
    evidence = (evidence or "").strip()
    if not evidence or evidence == "找不到":
        return False
    if _STYLE_ONLY_FRAGMENTS_RE.fullmatch(evidence):
        return False
    norm_source = _normalize_punct_width(source_text.lower())
    if _normalize_punct_width(evidence.lower()) not in norm_source:
        segments = [s.strip() for s in _EVIDENCE_DELIMITER_RE.split(evidence) if s.strip()]
        is_real_by_segments = len(segments) >= 2 and all(
            _normalize_punct_width(seg.lower()) in norm_source for seg in segments
        )
        if not is_real_by_segments:
            return False
    if _is_whole_sentence_copy(evidence, source_text):
        return False
    return True


def _is_whole_sentence_copy(evidence: str, source_text: str) -> bool:
    """
    _is_real_evidence 用的「整句交差」判斷本體，抽成獨立函式讓
    _whole_sentence_copy_items 可以重用同一套長度比例規則來找出「哪些
    維度」是這個模式，不只是「有沒有任何維度」。呼叫端必須先確認
    evidence 不是空字串/「找不到」/純樣板字/真的存在於原文，這裡只管
    長度比例本身。
    """
    return len(source_text) >= 8 and len(evidence) / len(source_text) > 0.8


def _whole_sentence_copy_items(
    checks: list, key: str, source_text: str
) -> list[str]:
    """
    找出 checks 裡「證據真的存在於原文、但因整句抄被 _is_real_evidence
    判定不算數」的項目清單，用來決定要對哪幾項重試短語格式。

    2026-08-15：原本 _all_evidence_is_whole_sentence 是「全部非空證據都要
    整句抄」才觸發重試，實測發現長者原話「我都會跟家人在車庫烤肉」這種
    案例，模型「活動」抽對了短語（不等於整句），只有「地點」偷懶整句抄
    ——不符合「全部都整句」的條件，重試完全沒觸發，「地點」這項答對的
    內容就這樣被錯殺沒有補救機會（見 STT 準確度那次討論，STT 造成的
    短句只會讓這個誤判更常發生，不是新增的獨立問題）。改成逐項判斷，
    只挑出真正符合「整句交差」訊號的項目個別重試，本來就答對（短語）的
    項目不受影響，也不用再要求「全部都整句」這個過嚴的觸發條件。
    """
    items = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        item = c.get(key)
        evidence = (c.get("evidence") or "").strip()
        if not item or not evidence or evidence == "找不到":
            continue
        if _STYLE_ONLY_FRAGMENTS_RE.fullmatch(evidence):
            continue
        if _normalize_punct_width(evidence.lower()) not in _normalize_punct_width(source_text.lower()):
            continue
        if _is_whole_sentence_copy(evidence, source_text):
            items.append(item)
    return items


def _resolve_when_duplicate_evidence(checks: list) -> list:
    """
    _detect_covered_w 專用，跑在 _missing_from_checks 之前。見 _WHEN_KEYWORDS
    定義處的說明：本地模型常把「晚上」這類時間詞同時標成 When 跟另一個維度
    （通常是 What）的證據，_missing_from_checks 的重複證據去重邏輯是「先出現
    的維度贏」，_W_ORDER 裡 What 排在 When 前面，結果永遠是 When 被當重複
    砍掉——跟長者的話明明是在回答「什麼時候」矛盾。

    這裡只處理「When 的證據本身命中 _WHEN_KEYWORDS 明確時間詞」這個已證實
    的案例：確定是時間詞的話，把其他維度對同一段文字的重複主張清成
    「找不到」，讓 When 保留這段證據、不被去重邏輯誤殺。其餘維度之間的
    證據重複（目前沒有實測案例）維持原本 _missing_from_checks 的處理方式
    不動，不在這裡預先猜測規則。
    """
    when_checks = [c for c in checks if isinstance(c, dict) and c.get("dimension") == "When"]
    if not when_checks:
        return checks
    when_evidence = (when_checks[0].get("evidence") or "").strip()
    if when_evidence in ("", "找不到") or not any(kw in when_evidence for kw in _WHEN_KEYWORDS):
        return checks
    return [
        {**c, "evidence": "找不到"}
        if isinstance(c, dict) and c.get("dimension") != "When"
        and (c.get("evidence") or "").strip() == when_evidence
        else c
        for c in checks
    ]


def _backstop_when_evidence(checks: list, elder_response: str) -> list:
    """
    _detect_covered_w 專用，跟 _resolve_when_duplicate_evidence 處理的是
    另一種失效模式：2026-08-17 實測發現本地模型對「晚餐時間」這類時間詞，
    即使 _W_DESC["When"] 已經明列（見 _WHEN_KEYWORDS），有時不是重複標到
    別的維度，而是直接對 When 回「找不到」，完全沒偵測到——即使長者原話
    裡的時間詞比 _resolve_when_duplicate_evidence 能處理的案例（同一段文字
    同時被標到兩個維度）更明確，模型還是漏判。這種「該有的證據完全沒找到」
    (vs「找到了但標錯維度」)，靠繼續加提示詞說服模型效果有限（image_reveal
    分類那邊已經加過一次「以語意為準」的通用規則、这里的失效模式不一样：
    不是清單沒列到新詞，是清單列了模型還是沒找到），改用規則直接補：長者
    原話裡只要出現 _WHEN_KEYWORDS 任一個詞、而 When 這一項 LLM 回「找不到」
    或空白，就直接用比對到的關鍵詞當證據覆蓋回去，不用再賭模型這次判斷
    準不準。
    """
    when_check = next(
        (c for c in checks if isinstance(c, dict) and c.get("dimension") == "When"), None
    )
    if when_check is None:
        return checks
    existing = (when_check.get("evidence") or "").strip()
    if existing not in ("", "找不到"):
        return checks
    matched = next((kw for kw in _WHEN_KEYWORDS if kw in elder_response), None)
    if not matched:
        return checks
    return [
        {**c, "evidence": matched} if c is when_check else c
        for c in checks
    ]


def _merge_retried_checks(checks: list, retried: list, key: str) -> list:
    """
    把 _retry_short_phrase_evidence 針對特定維度重問到的結果，覆蓋回原本
    checks 對應的項目（其餘沒重試的項目維持原樣），給 _has_usable_detail／
    _detect_pre_image_w_coverage 共用。
    """
    retried_map = {
        c.get(key): c for c in retried if isinstance(c, dict) and c.get(key)
    }
    return [
        retried_map.get(c.get(key), c) if isinstance(c, dict) else c
        for c in checks
    ]


def _missing_from_checks(
    items: list[str], checks: list, key: str, source_text: str
) -> list[str]:
    """
    比對 items 清單跟 LLM 逐項核對回傳的 checks，判斷哪些項目算漏掉，給
    _find_missing_memory_items 用。

    2026-08-14 實測（10項清單一次核對）發現兩種 _is_real_evidence 單獨
    擋不住的漏網：
    (1) 有些 item 在 checks 陣列裡完全沒有對應的 entry——模型直接漏掉
        沒檢查（10項只回9筆），不是給了無效證據，是連檢查都沒做，這種
        「憑空消失」不會被任何 evidence 內容過濾攔到，只能靠比對 item
        清單跟 checks 涵蓋的 item 是否一致才抓得到。
    (2) evidence 文字整段跟前面某一項重複——模型偷懶把別項的證據複製
        貼上湊數（例如「哥哥剝柚子」「姊姊削蘋果」分別被套用「小孩子
        提燈籠」「打麻將」的證據句），這段文字確實真實存在於
        source_text，能通過 _is_real_evidence 的substring檢查，但明顯
        不是這個item專屬的證據。同一段證據不太可能同時是兩個不同細節
        的專屬視覺內容，重複使用一律當作漏掉處理。
    """
    checked: dict[str, str] = {}
    for c in checks:
        if isinstance(c, dict) and c.get(key):
            checked[c[key]] = (c.get("evidence") or "").strip()

    missing = []
    seen_evidence: set[str] = set()
    for item in items:
        evidence = checked.get(item)
        if evidence is None:
            missing.append(item)
            continue
        evidence_norm = evidence.lower()
        if evidence_norm in seen_evidence or not _is_real_evidence(evidence, source_text):
            missing.append(item)
            continue
        seen_evidence.add(evidence_norm)
    return missing


# _check_pre_image_basic_dims（地點/活動/時間）跟 _detect_pre_image_w_
# coverage（Where/When/How/Why）查的是同一句長者回答，這份對照表讓
# _map_basic_checks_to_w 把前者已經查證過的證據直接轉成後者的維度名稱、
# 重複使用，不用為了套用不同措辭的四維度框架就重新問一次LLM。
_PRE_IMAGE_BASIC_DIM_MAP = {"地點": "Where", "活動": "How", "時間": "When"}


def _map_basic_checks_to_w(checks: list, source_text: str) -> list[str]:
    """
    把 _check_pre_image_basic_dims 查到的地點/活動/時間 checks 映射成
    Where/How/When，給 process_response 的 pre_image_q1 分支判斷情境1/2
    時優先沿用，不用另外對 _detect_pre_image_w_coverage 的四維度框架重新
    問一次LLM。

    2026-08-16：稽核發現 _has_usable_detail（地點/活動/時間）跟
    _detect_pre_image_w_coverage（Where/When/How/Why）是兩個完全獨立、
    用不同措辭框架各自判斷的 LLM 呼叫——同一句長者回答，前者可能已經
    查到「活動」的證據，後者卻可能判定對應的「How」找不到（LLM判斷本身
    非決定性，換一套維度措辭問更容易得出不同答案）。process_response
    原本在 _has_usable_detail 判定「不夠具體」後，一律另外呼叫
    _detect_pre_image_w_coverage 決定情境1/2，等於把前者已經查到的證據
    整個丟掉重問一次，遇到上述矛盾就會把長者剛剛答過的內容當作沒答，
    錯把情境2（該直接問缺的維度）判成情境1（退回通用範例問法）。

    只回傳「有真的查到證據」的維度（Why 沒有對應的基本維度，一律不在
    這裡出現）；只要這裡回傳非空list，就代表已經有可信的證據可以直接
    判定情境2，不需要再呼叫 _detect_pre_image_w_coverage。三項都沒查到
    證據（回傳空list）時，才需要呼叫 _detect_pre_image_w_coverage 做完整
    的四維度判斷（含 Why），因為這種情況代表 _check_pre_image_basic_dims
    這套框架完全沒查到東西，換一套框架可能查得到，或是Q1真的什麼都沒答。
    """
    covered = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        mapped = _PRE_IMAGE_BASIC_DIM_MAP.get(c.get("dimension"))
        if mapped and _is_real_evidence(c.get("evidence"), source_text):
            covered.append(mapped)
    return covered


# _strip_people_clauses 用的人物關鍵字——涵蓋常見的人物名詞（man/woman/
# child/grandmother...），比對到就連同前面的冠詞（a/an/the）跟緊接在後面
# 的「聚在一起」類動詞（gathered/chatting/laughing）一起拿掉，只刪掉
# 這個詞本身，不刪掉整個逗號子句。2026-08-14：原本靠問LLM「這段話有沒有
# 描述到人物」逐次偵測＋重試，實測發現模型對「a family gathered」這種
# 間接說法會判斷「找不到」（temperature=0跟0.7都一樣，不是隨機性問題，
# 是模型對這種說法沒有辨識出是在描述人物）——換句話說，靠模型「事後判斷
# 有沒有畫到人」不可靠，改成在組合階段就用關鍵字比對直接刪詞，不依賴
# 模型自己判斷／自己記得拿掉。
#
# 一開始試過整段子句砍掉（跟 _strip_style_descriptors 濾畫風用詞同一套
# 做法），實測發現本機模型常把場景/地點資訊跟人物詞擠在同一個逗號子句裡
# （例如「1980s Taiwan family in a garage」「a man grilling on a
# barbecue」），整句砍掉會連「in a garage」「grilling on a barbecue」這種
# 真正該保留的場景/動作內容也一起消失。改成只刪掉人物詞本身（含前面的
# 冠詞、後面緊接的聚會動詞），把子句裡其餘的場景/動作內容留下來。
_PEOPLE_FRAGMENTS_RE = re.compile(
    r"\b(?:a|an|the)?\s*(?:"
    r"man|men|woman|women|boy|girl|boys|girls|child|children|kids?|"
    r"grandmother|grandfather|grandma|grandpa|grandparents|"
    r"mother|father|mom|dad|parents|"
    r"family(?:\s+members?)?|families|sister|brother|siblings?|aunt|uncle|cousin|"
    r"friend|friends|neighbor|neighbors|colleague|colleagues|classmate|classmates|"
    r"people|person|folks|crowd|figures?|narrator|elderly|elder|"
    r"someone|somebody|anybody|anyone|everyone|everybody"
    r")\b(?:'s)?\s*(?:gathered|gathering|chatting|laughing)?\s*",
    re.IGNORECASE,
)

# 刪完人物詞後，如果整個逗號子句只剩下介系詞（例如「surrounded by family
# members」刪掉「family members」後只剩「surrounded by」），這種殘留的
# 懸空片語也要整段丟掉，不留下語意不完整的子句。
_DANGLING_PREPOSITION_RE = re.compile(
    r"^(?:surrounded by|with|by|among|near|beside|next to|around)$",
    re.IGNORECASE,
)

# 2026-08-15：光刪掉懸空介系詞本身還不夠——如果介系詞後面接的是動名詞
# （例如「surrounded by family members eating」刪掉「family members」後
# 剩「surrounded by eating」），介系詞沒有懸空、但後面的動名詞片語沒有
# 主詞，一樣讀起來破碎不通順，要整段丟掉，不只是刪詞而已。
_ORPHANED_GERUND_RE = re.compile(
    r"^(?:surrounded by|with|by|among|near|beside|next to|around)\s+\w+ing\b",
    re.IGNORECASE,
)

# 2026-08-15：懸空介系詞不只會出現在子句開頭（整句只剩介系詞），也會
# 出現在子句尾端（例如「1980s Taiwan backyard barbecue with family」刪掉
# 「family」後剩「1980s Taiwan backyard barbecue with」，句尾留下沒接
# 受詞的介系詞）——這種情況不能整句丟掉（前面還有場景內容要留），只把
# 尾端這個孤兒介系詞刪掉即可。
_TRAILING_DANGLING_PREPOSITION_RE = re.compile(
    r"\s+(?:surrounded by|with|by|among|near|beside|next to|around)$",
    re.IGNORECASE,
)


def _strip_people_clauses(image_prompt: str) -> str:
    """
    只在長者原話沒有特別點名任何具體人物時使用（見
    _translate_detail_to_image_prompt 呼叫處）：把 image_prompt 裡提到
    人物的詞（含冠詞、所有格's、緊接的聚會動詞）刪掉，保留子句裡其餘的
    場景/動作內容；刪完變成懸空介系詞、或介系詞後面接著沒有主詞的動名詞
    片語的子句，整段丟掉。保證送去生圖 API 的內容不會出現人物——不管模型
    自己生成時有沒有偷偷加人、也不管事後有沒有判斷出「這裡有畫人」，直接
    在組合階段用關鍵字比對處理，不依賴模型的判斷或聽話程度。

    2026-08-15 實測發現：只刪人物詞本身，會留下三種讀起來破碎的殘留——
    (1) 所有格「woman's birthday」刪掉「woman」剩「's birthday」，變成
    「middle-aged 's birthday」這種孤兒所有格；(2)「surrounded by family
    members eating」刪掉「family members」剩「surrounded by eating」，
    介系詞後面接著沒有主詞的動名詞，讀起來像沒寫完的句子；(3)「backyard
    barbecue with family」刪掉「family」剩「backyard barbecue with」，
    句尾留下沒接受詞的孤兒介系詞。改成正則多吃掉緊接的's，對「介系詞+
    動名詞」這種殘留整段子句丟掉，句尾孤兒介系詞則只刪掉介系詞本身、
    保留前面的場景內容。
    """
    if not image_prompt:
        return image_prompt
    stripped = _PEOPLE_FRAGMENTS_RE.sub(" ", image_prompt)
    stripped = re.sub(r"\s{2,}", " ", stripped)
    parts = [p.strip() for p in stripped.split(",")]
    kept = []
    for p in parts:
        if not p or len(p) <= 2:
            continue
        if _DANGLING_PREPOSITION_RE.match(p) or _ORPHANED_GERUND_RE.match(p):
            continue
        p = _TRAILING_DANGLING_PREPOSITION_RE.sub("", p).strip()
        if p:
            kept.append(p)
    return ", ".join(kept)


def _add_no_people_directive(image_prompt: str, chinese: bool = False) -> str:
    """
    _start_scene_after_detail 呼叫處專用，緊接在 _strip_people_clauses
    後面用：光是 prompt 文字裡沒有人物詞，實測發現生圖模型（gpt-image-2）
    看到「grilling on a barbecue」這類本身就隱含「有人在做」的動作描述，
    還是會自己腦補畫出人物——這已經不是prompt文字有沒有提到人的問題，是
    生圖模型自己的推論/想像層級，不是拿掉人物詞就能擋住的。改成額外加
    一句明確的排除指示（不是「沒提到人」，是「明講不要有人」），插在固定
    的畫風/色調片語之前，讓它算進場景指示，不被 _strip_style_descriptors
    濾掉。找不到這個標記時（理論上不會發生，image_prompt 格式是固定的，
    這裡是防禦性寫法）就直接接在句尾。

    chinese: image_prompt 的語言。is_direct_detail（長者原話直接翻譯，見
    _start_scene_after_detail 呼叫處）路徑的 image_prompt 是中文（見
    _translate_detail_to_image_prompt 2026-08-15 全中文版說明），固定色調
    片語是「懷舊溫暖色調」（在 prompt 開頭）；RAG／anchor 路徑
    （_build_plan_image_prompt）仍然是英文，固定色調片語是"nostalgic warm
    tones"（在 prompt 結尾）。原本這裡不分語言一律寫死英文指示，會在中文
    路徑的整段中文 prompt 裡混進一句英文，兩種語言各自對應自己的指示文字
    跟插入點 marker，不要混用。
    """
    if chinese:
        directive = "不要出現任何人物、不要有人形，"
        marker = "懷舊溫暖色調"
        idx = image_prompt.find(marker)
        if idx == -1:
            return image_prompt.rstrip().rstrip("，") + "，" + directive.rstrip("，")
        return image_prompt[:idx] + directive + image_prompt[idx:]
    directive = "no people, empty of any human figures, "
    marker = "nostalgic warm tones"
    idx = image_prompt.find(marker)
    if idx == -1:
        return image_prompt.rstrip().rstrip(",") + ", " + directive.rstrip(", ")
    return image_prompt[:idx] + directive + image_prompt[idx:]


def _strip_style_descriptors(image_prompt: str) -> str:
    """
    image_prompt 是 _plan_image 送去 Stability AI 的英文生圖描述，開頭固定是
    「watercolor painting style」，常常還帶「nostalgic warm tones」「no text」
    這類畫風/色調指令——這些是給 Stability AI 的生圖技術參數，不是場景內容。

    2026-08：原本讓問題生成的模型自己讀到這整段英文、自行忽略畫風用詞（見
    _composition_section 的指示），實測不可靠——模型曾把「watercolor painting
    style, 1970s Taiwan coal mine...」直接翻譯成「一幅水彩畫描繪了1970年代
    台灣煤礦場景」，用後設視角把畫面講成「這是一幅畫」，跟長者拉開距離。改成
    在傳給問題生成步驟之前，先用逗號分段濾掉這些已知的畫風/色調片語，只留下
    真正描述場景內容的部分，不再指望模型自己守住「請忽略」這句提醒——這是
    從源頭消除誘因，比事後靠一句提示語更可靠。
    """
    if not image_prompt:
        return image_prompt
    parts = [p.strip() for p in image_prompt.split(",")]
    kept = [p for p in parts if p and not _STYLE_ONLY_FRAGMENTS_RE.search(p)]
    return ", ".join(kept)


def _composition_section(scene_composition: str) -> str:
    """
    scene_composition 這裡放的是 _plan_image 送去 Stability AI 的 image_prompt
    原文（英文，已經用 _strip_style_descriptors 濾掉畫風/色調片語），不是另外
    請 LLM 生成一份中文描述——2026-08 原本讓 LLM 在規劃圖片的同一次回應裡多
    生成一個中文 scene_composition 欄位，實測發現這個欄位常常跟 elements 對
    不上（同一次回應裡兩個各自生成的欄位，本地弱模型顧不好兩邊一致），還得
    額外加一致性檢查、重試，仍不保證對齊。image_prompt 才是生圖當下唯一真正
    決定畫面長相的描述，直接原文轉傳給問題生成步驟，不會有「兩份描述互相
    打架」的問題，也不用再多一次 LLM 生成。若不提供，模型會把元素清單當成
    互不相干的詞袋，自己亂兜空間關係（例如「坐在木桌前的腳踏車上」），生出
    不合理的畫面。
    """
    if not scene_composition:
        return ""
    return (
        f"\n【這些元素在畫面裡實際的擺放/動作關係（生圖當下的原始描述，英文）】\n"
        f"{scene_composition}\n"
        f"（這段英文是生圖時實際使用的描述，如果問題或承接語要提到這些元素之間的"
        f"空間或動作關係，要以這段內容為準，不要自己另外想像一個不同的組合方式）\n"
    )


def _load_prompt(filename: str) -> str:
    """
    從 app/prompts/ 讀取 prompt 模板，找不到就回傳空字串。

    question_5w1h.txt（_generate_question／_generate_open_followup／
    _generate_supplement_question 共用）的【承接語規則】設計目的，是讓長者
    透過 AI 生成的圖回想過去，具體運作方式是一個三段式漏斗，之後新增或調整
    那組規則時，都用這個原則判斷合不合理（2026-08 從 prompt 本文搬到這裡
    ——這段是寫給未來維護 prompt 的人看的設計理念，不是要模型照做的指令，
    留在 prompt 裡只會佔用模型的注意力額度，不影響它的實際輸出）：
      1. 圖片本身負責「schema活化」——生成圖片時選的是同年代、同職業背景的人
         普遍會有印象的通俗元素，不是長者的真實地點，作用是喚起長者腦中對應
         那個年代／職業／場景的一整套熟悉印象，不是要長者辨認「這張照片」本身
      2. 出示圖片那一刻的「雙重編碼鋪墊」現在由固定過渡句負責（見
         _IMAGE_REVEAL_SCENE_TEXT 等常數，不是 LLM 生成）——長者同時看到、
         聽到同一組具體物件，加深 schema 被活化的強度（dual-coding：視覺＋
         語音兩個管道疊加，記憶痕跡比單一管道更容易被觸發）；STEP2/STEP3的
         「承接語」接續的是不同的鋪墊功能——具體呼應長者剛才說的話（不是
         重新描寫畫面），讓問題感覺是順著對話自然接下去、不是憑空冒出來，
         鋪墊的素材來源從「畫面元素」換成「長者自己說過的話」
      3. 問題負責「回想動作本身」——問的不是畫面裡有什麼，而是長者自己那個
         年代／職業裡「你的版本是怎樣」，長者要主動把被畫面喚起的 schema
         填進自己真實的細節、事件、情感，這一步才是懷舊治療真正發生的地方
    承接語自己不需要、也不應該試圖直接達成「回想」的效果——它的成敗判準是
    「有沒有做好活化與鋪墊，讓緊接著的問題更容易被回答」，不是這句話本身寫得
    夠不夠感人、夠不夠有畫面感。
    """
    path = Path(__file__).parent / "prompts" / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


class TherapyOrchestrator:
    """指揮所有 service，實作懷舊療法完整狀態機。"""

    def __init__(
        self,
        llm: LLMService,
        image: OpenAIImageService,
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
        carryover: dict | None = None,
    ) -> dict:
        """
        開始回合 n 的第一步。

        2026-08-17起三回合改成不同性質：
          round 1：原本的生圖流程，問長者一句生圖前的破冰問題，這時還沒有圖片
                   （細節見下方原本的說明）。
          round 2：自由追問（STEP2+STEP3），不生圖、不合成語音（STT仍照常）。
                   延續 round 1 最後定案的畫面元素／話題，第一題直接當作
                   STEP2 開放追問呼叫，見 _start_round2_free_followup。
          round 3：closing（回縮期：從過去回到現實，將情緒引導回正向），
                   不生圖、不合成語音（STT仍照常）。內容沿用 closing.txt／
                   _generate_closing，只是觸發時機提前到「開始第三回合」
                   （而不是像現行三回合結束後心得那樣，等三回合真的跑完才問）。
                   長者回答這題後，process_response 直接視為本回合（也是整場
                   療程）結束，交給既有 _end_action 的 current_round>=3 分支
                   （原本用來產生「心得」問題那段，這次改動刻意保留不動，
                   見該處說明），因此長者實際上會連續被問兩次「回縮期」風格
                   的問題——這是目前刻意接受的行為，「心得」回合之後會再
                   另外重新設計，不在這次改動範圍內。

        round 2/3 開場都需要上一回合結束時的內容（round 2 需要 round 1 最後的
        畫面元素/話題與長者最後一句話；round 3 需要 round 2 最後一句話）才能
        承接得上，但前端 round 邊界會捨棄 state（見 Unity GameController.
        StartRound：呼叫 /session/round 不帶 state），所以由呼叫端
        （app/routers/session.py）在每回合最後一次 respond 時把需要的內容存進
        Redis，下一回合開始時讀出來、包成 carryover 傳進來。carryover 為 None
        或缺欄位時（例如 Redis 過期、或不是正常三回合序列）都有保底處理，
        不會拋例外。

        2026-08：原本這裡會立刻用 RAG 記憶＋長者資料規劃並生成場景圖，長者從頭
        到尾沒機會在生圖前先說出這次真正想聊的細節，畫面內容完全跟長者這次的
        回答無關。改成先問長者一句破冰問題 Q1（_build_pre_image_question：
        「說到{today_topic}，」接依16大主題分類挑的邀請語，見該函式與檔案
        開頭流程說明）。分類用 _classify_topic_category，這裡一開始就跑，
        分類結果存進 state["topic_category"]，之後若長者答得不夠具體、需要
        問 Q2 縮小範圍時直接複用，不用重複分類。長者回答後（process_response
        收到 last_question_type=="pre_image_q1" 時）才判斷細節夠不夠具體，
        最後才呼叫 _start_scene_after_detail 規劃並生成圖片——長者親口說的
        細節優先當生圖元素來源，RAG 記憶只在長者沒給出可用細節時才當 fallback，
        見該函式說明。

        Args:
            topic_override: 治療師啟動療程時手動輸入的今日主題，蓋過
                Patient.scene_weights 推導出的預設主題。同一場療程的 round 2/3
                應沿用同一個值，由呼叫端（app/routers/session.py）從 Redis
                session meta 讀出後重新傳入。
            carryover: round 2/3 專用，見上方說明；round 1 不使用。

        Returns dict 含：
          user_name, today_topic, scene_text, scene_elements（空list，還沒生圖）,
          image_path（空字串，還沒生圖）, question, memories_used（空list）,
          state  ← 傳給下一輪 process_response 用；last_question_type 是
                   "pre_image_q1"，長者回答這題後才會真的生圖、生成 STEP1 問題
                   （或先問 Q2 縮小範圍，見本檔頂部流程說明）
        """
        print(f"[Orchestrator] ── 回合 {round_number} 開始 ──")

        user = await self.user_profile.get_user(user_id)
        if not user:
            raise ValueError(f"找不到使用者: {user_id}")
        if topic_override:
            user = {**user, "today_topic": topic_override}
        print(f"  → {user['name']}，主題: {user['today_topic']}")

        if round_number == 2:
            return await self._start_round2_free_followup(user, user_id, session_id, carryover)
        if round_number == 3:
            return await self._start_round3_closing(user, user_id, session_id, carryover)

        # RAG 撈回憶提前到這裡（回合一開始就撈），結果存進 state，供
        # _start_scene_after_detail 需要當 fallback 時直接讀，不用等到那時候
        # 才發查詢、讓長者多等一次 RAG 往返。_retrieve_candidate_memories 本身
        # 邏輯不變（撈3筆候選、依 round_number 挑一筆），只是呼叫時機提前。
        cached_rag_memories = await self._retrieve_candidate_memories(user, round_number)

        category = await self._classify_topic_category(user["today_topic"])
        scene_text = _PRE_IMAGE_Q1_INTRO
        question = _build_pre_image_question(user["today_topic"], category)
        print(f"  → 主題分類: {category!r}，開場語: {scene_text}，生圖前破冰問題: {question}")

        state = {
            "user_id": user_id,
            "session_id": session_id,
            "round": round_number,
            "scene_elements": [],
            "scene_composition": "",
            "covered_w": [],
            "skipped_w": [],
            "last_question_type": "pre_image_q1",
            "last_w_asked": "",
            "question_count": 0,   # 生圖前破冰問題不算進本回合 5W1H 題數上限
            "supplement_count": 0,
            "topic_category": category,  # 開場已分類過，後續問題生成需要時直接複用，不重複分類
            "cached_rag_memories": cached_rag_memories,  # 開場已撈過，fallback需要時直接讀
        }

        return {
            "user_name": user["name"],
            "today_topic": user["today_topic"],
            "scene_text": scene_text,
            "scene_elements": [],
            "image_path": "",
            "question": question,
            "memories_used": [],
            "state": state,
        }

    async def _start_round2_free_followup(
        self, user: dict, user_id: str, session_id: str, carryover: dict | None,
    ) -> dict:
        """
        第二回合開場：自由追問（STEP2+STEP3），不生圖、不合成語音。

        沒有新畫面可以當錨點，第一題直接沿用 round 1 收尾時的畫面元素／構圖／
        生圖前訪談內容（carryover），把 round 1 最後一句話當「長者剛才說」，
        原封不動呼叫既有的 STEP2 開放追問生成函式（_generate_open_followup）
        ——跟本回合之後每一題用的是同一支函式、同一套規則，只是第一次呼叫的
        時機提前到開場，不用另外設計一份新的開場提示詞。

        covered_w 每回合重新歸零（見 state 初始化），不沿用 round 1 涵蓋過的
        W——round 2 是新的一輪 5W1H 追蹤，結束條件（全部涵蓋／單回合題數上限）
        沿用既有機制，見 process_response 對 last_question_type not in
        ("pre_image_q1","pre_image_q2","image_reveal") 的預設分支。
        """
        carryover = carryover or {}
        scene_elements = carryover.get("scene_elements") or []
        scene_composition = carryover.get("scene_composition", "")
        pre_image_detail = carryover.get("pre_image_detail", "")
        topic_category = carryover.get("topic_category")
        last_elder_response = carryover.get("last_elder_response", "")
        emotion = carryover.get("emotion") or "happy"

        result = await guarded_generate(
            self._generate_open_followup,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            max_retry=3,
            fallback=_element_fallback(
                scene_elements, topic_category=topic_category, with_covered_w=False,
            ),
            user=user, scene_elements=scene_elements, covered_w=[], skipped_w=[],
            elder_response=last_elder_response, emotion=emotion,
            scene_composition=scene_composition, pre_image_detail=pre_image_detail,
        )

        state = {
            "user_id": user_id,
            "session_id": session_id,
            "round": 2,
            "scene_elements": scene_elements,
            "scene_composition": scene_composition,
            "covered_w": [],
            "skipped_w": [],
            "last_question_type": "open",
            "last_w_asked": "",
            "question_count": 1,
            "supplement_count": 0,
            "topic_category": topic_category,
            "cached_rag_memories": [],
            "pre_image_q1_answer": "",
            "pre_image_detail": pre_image_detail,
            "image_reveal_deferred": False,
        }
        return {
            "user_name": user["name"],
            "today_topic": user["today_topic"],
            "scene_text": result["scene_text"],
            "scene_elements": scene_elements,
            "image_path": "",
            "question": result["question"],
            "memories_used": [],
            "state": state,
        }

    async def _start_round3_closing(
        self, user: dict, user_id: str, session_id: str, carryover: dict | None,
    ) -> dict:
        """
        第三回合開場：closing（回縮期），不生圖、不合成語音，單輪問答就結束
        本回合（也就是結束整場療程）。

        內容直接沿用 closing.txt／_generate_closing——跟現行「三回合結束後
        自動接一句心得引導」用的是同一份提示詞、同一支函式，這裡只是把觸發
        時機提前到「開始第三回合」本身，不是等第三回合跑完才問。長者回答這題
        後，process_response 會直接把它交給 _end_action 的 current_round>=3
        分支結束整場療程——那段邏輯（含它會再呼叫一次 _generate_closing 產生
        「心得」問題）這次刻意保留不動，所以長者會連續被問兩次同類型的收尾
        問題，是暫時可接受的重複，等心得回合另外重新設計時再處理。
        """
        carryover = carryover or {}
        last_elder_response = carryover.get("last_elder_response", "")
        emotion = carryover.get("emotion") or "happy"

        closing = await guarded_generate(
            self._generate_closing,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            max_retry=3,
            text_keys=("closing_text", "question"),
            fallback={
                "closing_text": "謝謝你今天的分享，辛苦了。",
                "question": "現在心裡在想些什麼呢？",
            },
            user=user, elder_response=last_elder_response, emotion=emotion,
        )

        state = {
            "user_id": user_id,
            "session_id": session_id,
            "round": 3,
            "scene_elements": [],
            "scene_composition": "",
            "covered_w": [],
            "skipped_w": [],
            "last_question_type": "round3_closing",
            "last_w_asked": "",
            "question_count": 1,
            "supplement_count": 0,
            "topic_category": None,
            "cached_rag_memories": [],
            "pre_image_q1_answer": "",
            "pre_image_detail": "",
            "image_reveal_deferred": False,
        }
        return {
            "user_name": user["name"],
            "today_topic": user["today_topic"],
            "scene_text": closing["closing_text"],
            "scene_elements": [],
            "image_path": "",
            "question": closing["question"],
            "memories_used": [],
            "state": state,
        }

    async def _retrieve_candidate_memories(self, user: dict, round_number: int) -> list[dict]:
        """
        RAG 撈長者過去記憶，回傳這次要餵給 _plan_image 的單筆記憶。在 start_round
        一開始就呼叫（不用等長者回答完才發查詢），結果存進 state["cached_rag_
        memories"]；長者沒有給出可用細節時，_start_scene_after_detail 直接讀
        這個快取當生圖記憶來源的 fallback，不會再呼叫這支函式第二次。

        撈 limit=3 筆候選（同一場療程3回合的查詢字串都一樣，撈回來的候選名單
        大致相同），但每次只會挑其中一筆單獨餵給 _plan_image——2026-08 實測發現
        多筆記憶疊加組成 memory_section 時，就算每筆單獨都在安全長度內，疊加
        起來還是會讓模型完全失焦、退化成「公園野餐」這種通用內容，所以送進
        _plan_image 的永遠只有一筆。用 round_number 輪流挑不同筆，三回合各自
        扣住不同的真實記憶，比每回合都固定挑最高分那一筆更能避免三張圖內容
        太相似。retrieve_memories 失敗時會丟例外，只在「真的失敗」（例如
        Ollama/RAG服務還沒起來）時才等3秒重試一次；「這位長者本來就還沒有任何
        記憶」會正常回傳空list、不會進到這個except。
        """
        try:
            candidate_memories = await self.rag.retrieve_memories(
                user_id=user["user_id"],
                query=f"{user['today_topic']} {user['main_occupation']}",
                limit=3,
            )
        except Exception as e:
            import asyncio as _asyncio
            print(f"  → [RAG] 第一次失敗（{e}），等 3 秒後 retry...")
            await _asyncio.sleep(3)
            try:
                candidate_memories = await self.rag.retrieve_memories(
                    user_id=user["user_id"],
                    query=f"{user['today_topic']} {user['main_occupation']}",
                    limit=3,
                )
            except Exception as e2:
                print(f"  → [RAG] 重試仍失敗（{e2}），這回合當作沒有記憶處理")
                candidate_memories = []

        return (
            [candidate_memories[(round_number - 1) % len(candidate_memories)]]
            if candidate_memories else []
        )

    async def _start_scene_after_detail(
        self, user: dict, state: dict, elder_detail: str,
        detail_is_usable: bool | None = None,
    ) -> dict:
        """
        長者回答完生圖前的破冰問題（Q1，若不夠具體則再追問 Q2）後：決定這次
        生圖要用的記憶來源、生圖，再出示圖片＋留白讓長者說出第一反應（真正的
        STEP1 開場問題延後到長者回答完這句「出示圖片」之後才生成，見
        _generate_image_reveal_reaction）——這段邏輯是原本 start_round 生圖
        那半段搬過來的，只是現在要等長者答完 Q1（或 Q1+Q2）才會觸發（見
        process_response 的 pre_image_q1／pre_image_q2 分支、本檔頂部流程
        說明）。

        elder_detail 非空、且內容有具體細節可用（見 _has_usable_detail）：長者
        剛才親口說的細節（Q1單獨一句，或Q1+Q2合併）優先當這次生圖的記憶來源，
        比 RAG 撈回來的舊記憶更即時、更貼近長者這次真正想聊的畫面。
        elder_detail 為空（長者不知道／跳過／回應太短，process_response 呼叫
        這裡之前已用 _is_quick_end 判斷過）、或雖然有回答但內容空洞（例如
        「還好啦」「有喔，很多回憶」這種沒有任何具體地點/人物/物件/活動、
        _is_quick_end 抓不到但也生不出東西的回答）：退回原本以 RAG 記憶＋
        長者資料為主的生圖方式，RAG 記憶已經在 start_round 一開始就撈好、
        存進 state["cached_rag_memories"]，這裡直接讀，不用重新查一次。

        2026-08：原本只要 elder_detail 非空就直接當記憶來源，沒有檢查內容
        本身夠不夠具體——_is_quick_end 只看「少於5字」或「不記得/不知道」這類
        放棄關鍵字，抓不到「有講話但沒講出任何具體東西」這種情況。_plan_image
        在有記憶輸入時的規則是「只從記憶內容延伸、不要新增回憶沒提到的細節」，
        遇到空洞的記憶文字時，要嘛生不出像樣的畫面元素、要嘛被迫違反這條規則
        自己亂編，兩者都比退回 RAG 記憶差，所以補上這道檢查。

        detail_is_usable: process_response 的 pre_image_q1 分支在呼叫這裡之前，
        往往已經用 _has_usable_detail 判斷過同一句 elder_detail（用來決定要不要
        追問Q2／要不要略過Q2直接生圖），呼叫端已經知道答案時直接把結果傳進來，
        這裡就不用再對同一句話重新問一次LLM。2026-08稽核發現：不傳的話這裡會
        用同一句話再呼叫一次 _has_usable_detail，因為是LLM判斷（非決定性），
        兩次呼叫可能給出不同答案——外層判定「夠具體」才呼叫這裡，這裡卻判定
        「不夠具體」，臨時又退回RAG記憶，跟呼叫端當下印出的log訊息自相矛盾，
        也多浪費一次LLM往返延遲。留 None（預設）給還沒判斷過的呼叫端（例如
        pre_image_q2 合併Q1+Q2的情況），維持原本行為。
        """
        session_id = state["session_id"]
        round_number = state["round"]

        if detail_is_usable is None:
            detail_is_usable = bool(elder_detail) and await self._has_usable_detail(elder_detail)

        is_direct_detail = bool(elder_detail) and detail_is_usable
        if is_direct_detail:
            memories = [{"summary": elder_detail}]
            # 長者這句生圖前的訪談回答通常已經自然帶出幾個W維度（例如提到
            # 誰、在哪裡、發生什麼事），不該生完圖就把這些內容丟掉、讓後面
            # 的5W1H流程從零開始重問一次——用跟 STEP2 背景追蹤同一套判斷
            # （_detect_covered_w），把這裡偵測到的W維度接續進 state，STEP1/
            # STEP3 就會自動避開已經聊過的方向。
            pre_image_covered_w = await self._detect_covered_w(elder_detail, [])
            print(f"  → 生圖前訪談已自然涵蓋 W: {pre_image_covered_w}")
        else:
            # 退回 RAG 記憶時，elder_detail 不是這次生圖真正的內容來源
            # （太空洞或長者沒回答），不能拿它來判斷涵蓋了哪些W，也沒有
            # 實質內容可以在下面存進 state 給 quick_end 之後呼應。
            # RAG 已經在 start_round 一開始就撈過、存進 state["cached_rag_
            # memories"]，這裡直接讀，不用再發一次查詢讓長者多等一輪。
            memories = state.get("cached_rag_memories", [])
            pre_image_covered_w = []
            elder_detail = ""

        # 脫敏要在這裡做（送進LLM規劃畫面之前），不能只在下面對LLM吐出來的
        # image_prompt做——deidentifier.desensitize_text的regex（中文姓名/
        # 年份/鄉鎮地名）全部是針對中文字元設計的，image_prompt是LLM生成的
        # 英文，這些regex對英文文字完全比對不到；taboos字串比對也一樣，
        # taboos是長者原始中文禁忌詞，image_prompt是英文，plain string
        # replace同樣配不到。也就是說「只脫敏英文輸出」等於沒真的脫敏——
        # 長者原話裡如果講到真實姓名/禁忌對象，LLM大概率會把這些內容翻譯
        # 進英文image_prompt，脫敏regex完全攔不住。真正有效的做法是在
        # memories（長者原話或RAG回憶摘要，都是中文）送進LLM之前先脫敏，
        # 讓敏感內容從源頭就不會進入LLM的輸入，自然也不會被翻譯進輸出。
        memories = [
            {
                **m,
                "summary": self.deidentifier.desensitize_text(
                    m.get("summary", m.get("text", "")), taboos=user["taboos"],
                ),
            }
            for m in memories
        ]

        # 長者這回合親口給的直接素材：跳過 _plan_image 那套「先抽成elements
        # 清單、LLM再自由重新想像畫面」的流程，image_prompt直接從長者原話
        # 翻譯重構（見 _plan_image_from_detail 說明）——elements抽象化會弄丟
        # 原話裡的空間/相對位置關係，直接翻譯才保留得住。RAG回憶／anchor
        # 自由發想這兩種情況本來就沒有「長者剛講的原話」可以直接翻譯，維持
        # 原本 _plan_image 那套流程。
        if is_direct_detail:
            image_plan = await self._plan_image_from_detail(user, memories[0]["summary"])
        else:
            image_plan = await self._plan_image(user, memories=memories)
        print(f"  → 圖片元素: {image_plan['elements']}")
        print(f"  → [DEBUG] image_prompt（LLM原始輸出）: {image_plan['image_prompt']}")
        # 濾掉畫風/色調片語後才傳給問題生成步驟，Stability AI 那邊還是用完整的
        # image_plan["image_prompt"]（見下面 safe_prompt），兩者用途不同不能共用。
        clean_composition = _strip_style_descriptors(image_plan["image_prompt"])

        if is_direct_detail:
            # 2026-08-15：長者原話已經在組合image_prompt之前單獨脫敏過
            # （見上面 memories 的處理），這裡的image_prompt本身是中文
            # （_translate_detail_to_image_prompt 改成直接送中文，不再
            # 翻譯成英文）——如果再對整段image_prompt跑一次完整的
            # desensitize_text，會連我們自己寫的固定中文指令也被掃到：
            # 「1980年代」被_remove_years誤判成確切年份換成「從前」，
            # 「段落」「任何」被_remove_chinese_names的姓氏規則（「段」
            # 「任」剛好都是清單裡的姓）誤判成人名換成「某人」。固定
            # 指令不是使用者輸入，不需要也不能再套用這套針對中文個資
            # 設計的正則，這裡只保留防禦性的taboo原字串比對（純substring
            # replace，不會誤傷不相關文字）。
            safe_prompt = image_plan["image_prompt"]
            for taboo in user["taboos"]:
                safe_prompt = safe_prompt.replace(taboo, "")
        else:
            # 上面已經在源頭（中文memories）脫敏過，這裡對英文image_prompt
            # 再做一次只是defense-in-depth的最後防線（例如LLM漏翻、殘留
            # 中文片段時還能擋一次taboos的原字串比對），不能單獨依賴這
            # 一步。_plan_image（RAG／anchor路徑）的輸出仍然是英文，這套
            # 針對中文個資設計的正則對英文文字是安全的no-op，不會誤傷。
            safe_prompt = self.deidentifier.desensitize_text(
                image_plan["image_prompt"], taboos=user["taboos"]
            )
        # 人物一律清空：_build_plan_image_prompt 的規則已經要求不生成任何
        # 人物元素，這裡是事後最後一道防線——純文字指示不可靠，實測發現
        # gpt-image-2 光看到隱含「有人在做」的動作描述（例如grilling on a
        # barbecue）還是會自己腦補畫出人物，見 _add_no_people_directive
        # 說明。
        safe_prompt = _strip_people_clauses(safe_prompt)
        safe_prompt = _add_no_people_directive(safe_prompt, chinese=is_direct_detail)
        print(f"  → [DEBUG] safe_prompt（去識別化後，實際送給Stability）: {safe_prompt}")
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

        # 圖生成後不直接問 STEP1，先出示圖片、留白讓長者自己反應——長者這句
        # 反應要等 process_response 收到 last_question_type=="image_reveal"
        # 時才處理（分類反應、生成承接語，再接上真正的 STEP1 開場問題，見
        # _generate_image_reveal_reaction）。這裡先把 scene_elements/
        # scene_composition 存進 state，因為 STEP1 問題生成延後到那時候才做，
        # 仍然需要這兩個值。
        new_state = {
            **state,
            "scene_elements": image_plan["elements"],
            "scene_composition": clean_composition,
            "covered_w": pre_image_covered_w,
            # 長者這句生圖前訪談的原話存進 state，撐過 image_reveal 這一輪——
            # 如果長者看完圖沒有給出真正的反應（quick_end），下一步還是要能
            # 具體呼應這句話，不能只接一句跟內容無關的固定過渡句（見
            # process_response 的 image_reveal quick_end 分支、
            # _generate_quick_end_recap）。
            "pre_image_detail": elder_detail,
            "skipped_w": state["skipped_w"],
            "last_question_type": "image_reveal",
            "last_w_asked": "",
            "question_count": 0,   # 出示圖片這一題不算進本回合 5W1H 題數上限，理由同生圖前 Q1/Q2
            "supplement_count": 0,
        }
        return {
            "action": "image_reveal",
            "scene_text": _IMAGE_REVEAL_SCENE_TEXT,
            "question": _IMAGE_REVEAL_QUESTION,
            "image_path": image_path,
            "state": new_state,
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

        # ── 第三回合（closing）是單輪問答，長者這句回答直接結束本回合／整場
        # 療程，交給既有 _end_action 的 current_round>=3 分支（見
        # _start_round3_closing 說明，這段刻意不做 5W1H／情緒觸發等一般判斷）──
        if state["last_question_type"] == "round3_closing":
            return await self._end_action(state, user, elder_response, emotion)

        covered_w        = list(state["covered_w"])
        skipped_w        = list(state["skipped_w"])
        last_type        = state["last_question_type"]
        last_w           = state.get("last_w_asked", "")
        scene_els        = state["scene_elements"]
        scene_comp       = state.get("scene_composition", "")
        question_count   = state.get("question_count", 1)
        supplement_count = state.get("supplement_count", 0)

        print(f"[Orchestrator] process_response | round={state['round']} "
              f"last={last_type} covered={covered_w} skipped={skipped_w} "
              f"questions={question_count} supplements={supplement_count}")

        # ── 情緒觸發偵測（Track B，必須跑在 _is_quick_end 之前）─────
        # _is_quick_end 用「不記得／不知道／忘了」關鍵字判斷放棄話題，但這類字面
        # 也會出現在自責/沮喪的情緒表達裡（例如「我不記得了，都忘了，腦子越來越
        # 差了……」），如果先跑 quick_end，這類句子會被誤判成單純放棄話題，
        # 跳過情緒支持直接進下一題——正好是 Track B 的 ignore_emotion／rush_topic
        # 規則要懲罰的行為，所以這個判斷必須放在最前面。
        is_emotional_trigger = (
            False if elder_response.strip() == _NO_RESPONSE_MARKER
            else await self._detect_emotional_trigger(elder_response)
        )
        print(f"  → 情緒觸發偵測: {is_emotional_trigger}")
        if is_emotional_trigger:
            await self.rag.save_memory(
                user_id=user_id,
                session_id=state["session_id"],
                text=elder_response,
                emotion=emotion,
            )
            result = await guarded_generate(
                self._generate_emotional_response,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=3,
                text_keys=("emotional_text", "question"),
                fallback={
                    "emotional_text": "謝謝你願意說這些，我在這裡陪著你。",
                    "question": _FALLBACK_QUESTION,
                },
                user=user, elder_response=elder_response,
            )
            # 情緒支持不受 _MAX_QUESTIONS_PER_ROUND 限制：即使本回合題數已達上限，
            # 偵測到情緒觸發仍優先回應，不直接跳去收尾——情緒支持優先於節奏控制。
            # question_count 仍要 +1，下一輪正常判斷時題數已達上限就會照常收尾。
            new_state = {
                **state,
                "covered_w": covered_w,
                "skipped_w": skipped_w,
                # 若這句情緒觸發發生在生圖前後的過渡階段（pre_image_q1／
                # pre_image_q2／image_reveal），代表長者還沒真的給出生圖細節、
                # 或圖片剛出示但還沒走到「反應承接」——這裡要維持原本的階段，
                # 讓長者聽完情緒支持、回答下一句「後續引導」時，process_response
                # 仍會走對應的分支（觸發生圖、問Q2、或反應承接接STEP1），不會
                # 誤標成一般 STEP2 開放追問（那個分支預期已經有 scene_elements
                # 可用，image_reveal 階段雖然已經有 scene_elements，但這句
                # 回應是在反應圖片本身，不是在自由聊天，仍要走專屬分支）。
                # pre_image_q2 階段的 state["pre_image_q1_answer"] 會透過上面
                # 的 **state 展開自然保留，不會被這裡蓋掉。
                "last_question_type": (
                    last_type
                    if last_type in ("pre_image_q1", "pre_image_q2", "image_reveal")
                    else "emotional_support"
                ),
                "last_w_asked": "",
                "question_count": question_count + 1,
                "supplement_count": supplement_count,
            }
            return {
                "action": "emotional_support",
                "scene_text": result["emotional_text"],
                "question": result["question"],
                "state": new_state,
            }

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

        # ── 生圖前破冰問題階段：Q1 答得夠具體就直接生圖，不夠具體才追問 Q2 ──
        # 必須放在補問路徑／STEP2 W偵測之前——這個階段 state["scene_elements"]
        # 還是空的，沒有圖可以問，下面那些邏輯都預期已經有圖才能正常運作。
        if last_type == "pre_image_q1":
            # 這裡故意不用上面算好的 quick_end（_is_quick_end），改用範圍更
            # 窄的 _is_true_refusal——quick_end 把「字數<5的短回答」（例如
            # 「有喔」「會啊」，長者真的有回答，只是講得短）跟「長者真的不想
            # 答」一視同仁，會讓短回答直接跳過Q2、退回RAG記憶，長者其實還
            # 沒機會在Q2多說一點。只有真的沉默逾時或明確講不知道/不記得，
            # 才不用再多問一題。短回答會落到下面 _has_usable_detail 判斷，
            # 通常判NO、自然會進到問Q2那條路，不需要在這裡特別處理。
            elder_detail = "" if self._is_true_refusal(elder_response) else elder_response
            if not elder_detail:
                # 長者真的不想／不能答，不用再多問Q2，直接退回RAG記憶生圖。
                return await self._start_scene_after_detail(user, state, "")
            basic_checks = await self._check_pre_image_basic_dims(elder_detail)
            has_usable_detail = await self._has_usable_detail(elder_detail, checks=basic_checks)
            if has_usable_detail:
                # 已經夠具體，不逼長者多答一題。把剛剛判斷過的結果直接傳下去，
                # 不讓 _start_scene_after_detail 對同一句話重新問一次LLM——
                # 那是非決定性判斷，兩次呼叫可能得出不同答案，見該函式
                # detail_is_usable 參數的說明。
                return await self._start_scene_after_detail(
                    user, state, elder_detail, detail_is_usable=True,
                )
            # 有回應但不夠具體 → 分類已經在 start_round 開場時做過、存在
            # state["topic_category"]，這裡直接複用，不用重複分類。分類失敗
            # （當時 LLM 輸出不在16個名稱裡，topic_category 是 None）就不
            # 勉強瞎猜，直接拿Q1這句（哪怕不夠具體）去生圖，好過卡住問不出Q2。
            category = state.get("topic_category")
            if not category or category not in _FIVE_W1H_BANK:
                print(f"  → 主題分類失敗（{category!r}），略過Q2直接生圖")
                return await self._start_scene_after_detail(
                    user, state, elder_detail, detail_is_usable=has_usable_detail,
                )
            # 情境1 vs 情境2：核對Q1這句話有沒有涵蓋Where/When/How/Why任一
            # 維度，決定要給範例縮小範圍（情境1），還是直接問缺的維度
            # （情境2，見 get_scenario2_followup）。優先沿用上面 _has_
            # usable_detail 已經查過的地點/活動/時間證據（見
            # _map_basic_checks_to_w），只有三項都沒查到證據時，才另外
            # 呼叫 _detect_pre_image_w_coverage 做完整四維度判斷（含
            # Why）——避免同一句話用不同維度框架各問一次LLM，兩次獨立、
            # 非決定性的判斷互相矛盾，把已經查到的內容誤判成沒答，錯把
            # 情境2 判成情境1。
            covered_w = _map_basic_checks_to_w(basic_checks, elder_detail)
            if not covered_w:
                covered_w = await self._detect_pre_image_w_coverage(elder_detail)
            if not covered_w:
                q2_question = _PRE_IMAGE_Q2_SCENARIO1_TEMPLATES.get(category)
                if not q2_question:
                    return await self._start_scene_after_detail(
                        user, state, elder_detail, detail_is_usable=has_usable_detail,
                    )
                print(f"  → 主題分類: {category}，情境1（完全答不出來）追問Q2: {q2_question}")
                new_state = {
                    **state,
                    "last_question_type": "pre_image_q2",
                    "pre_image_q1_answer": elder_detail,
                    "pre_image_q2_scenario": 1,
                }
                return {
                    "action": "pre_image_followup",
                    "scene_text": "",
                    "question": q2_question,
                    "state": new_state,
                }
            # 情境2：Q1已經有內容，只缺特定維度，直接問缺的那個維度，不重複
            # 給範例。sub_item只在這裡才分類一次（見 _classify_pre_image_
            # sub_item 說明），之後每一輪迴圈都複用同一個值，不重新分類。
            theme_data = _FIVE_W1H_BANK[category]
            sub_item = None
            if theme_data["granularity"] == "sub_item":
                sub_item = await self._classify_pre_image_sub_item(category, elder_detail)
            named_person = _extract_named_person(elder_detail)
            q2_question = get_scenario2_followup(category, sub_item, covered_w, named_person)
            if not q2_question:
                # 該問的維度都已經在Q1涵蓋或被排除，不用再多問一題。
                return await self._start_scene_after_detail(
                    user, state, elder_detail, detail_is_usable=has_usable_detail,
                )
            print(f"  → 主題分類: {category}（子項目: {sub_item}），"
                  f"情境2（缺維度）追問Q2: {q2_question}，已涵蓋: {covered_w}")
            new_state = {
                **state,
                "last_question_type": "pre_image_q2",
                "pre_image_q1_answer": elder_detail,
                "pre_image_q2_scenario": 2,
                "pre_image_sub_item": sub_item,
                "covered_w": covered_w,
                "pre_image_q2_round": 1,
                "pre_image_q2_max_rounds": _pre_image_q2_round_cap(covered_w),
            }
            return {
                "action": "pre_image_followup",
                "scene_text": "",
                "question": q2_question,
                "state": new_state,
            }

        if last_type == "pre_image_q2":
            # Q1+Q2 合併當生圖記憶來源；Q2 沒回答就只用Q1那句。這裡跟上面
            # pre_image_q1 分支同理，用 _is_true_refusal 而不是 quick_end——
            # Q2 答得短（例如「廟口」）依然是真實內容，合併進Q1那句可能就
            # 剛好湊出可用細節，不該因為單獨這句話字數<5就整句丟掉。
            # （_start_scene_after_detail 內部的 _has_usable_detail 檢查仍會
            # 判斷合併後的內容夠不夠具體，不夠具體會自動退回RAG記憶，這裡
            # 不用重複判斷一次。）
            q1_answer = state.get("pre_image_q1_answer", "")
            q2_answer = "" if self._is_true_refusal(elder_response) else elder_response
            combined = f"{q1_answer} {q2_answer}".strip()
            scenario = state.get("pre_image_q2_scenario", 1)
            if scenario != 2:
                # 2026-08-16：原本情境1的Q2不管答得有沒有內容，一律只套用
                # 嚴格三維度把關（地點+活動+時間都要有），沒過就整個放棄
                # 退回RAG記憶生圖——但長者這句Q2常常是有實質內容的回答
                # （不是拒答），只是剛好沒把三個維度都講到（例如只講了
                # 活動，沒講地點/時間），嚴格把關一沒過，這句本來可用的
                # 內容就整個浪費掉、還會生出跟長者剛才回答無關的英文
                # RAG畫面。改成跟情境2共用同一套「缺什麼就從bank精準問
                # 什麼」邏輯：只在Q2真的有內容（q2_answer非空）時才啟用，
                # 追加問缺的維度，受 _pre_image_q2_round_cap 同一套動態
                # 輪數上限保護（依這裡算出的 covered_w 缺幾項動態決定，
                # 不會無限問下去）；bank判定沒有更多可問的維度，或分類
                # 失敗，就直接採信目前累積內容當生圖來源，不再套用嚴格
                # 把關。
                category = state.get("topic_category")
                theme_data = _FIVE_W1H_BANK.get(category) if q2_answer else None
                if theme_data:
                    covered_w = await self._detect_pre_image_w_coverage(combined)
                    sub_item = None
                    if theme_data["granularity"] == "sub_item":
                        sub_item = await self._classify_pre_image_sub_item(category, combined)
                    named_person = _extract_named_person(combined)
                    next_question = get_scenario2_followup(
                        category, sub_item, covered_w, named_person,
                    )
                    if next_question:
                        print(f"  → 情境1追問Q2有實質內容但仍缺維度，追加問一次: "
                              f"{next_question}，已涵蓋: {covered_w}")
                        new_state = {
                            **state,
                            "last_question_type": "pre_image_q2",
                            "pre_image_q1_answer": combined,
                            "pre_image_q2_scenario": 2,
                            "pre_image_sub_item": sub_item,
                            "covered_w": covered_w,
                            "pre_image_q2_round": 1,
                            "pre_image_q2_max_rounds": _pre_image_q2_round_cap(covered_w),
                        }
                        return {
                            "action": "pre_image_followup",
                            "scene_text": "",
                            "question": next_question,
                            "state": new_state,
                        }
                    print(f"  → 情境1追問Q2已有實質內容，bank判定不需再追問，"
                          f"已涵蓋: {covered_w}")
                    return await self._start_scene_after_detail(
                        user, state, combined, detail_is_usable=True,
                    )
                return await self._start_scene_after_detail(user, state, combined)
            # 情境2：Q1當初能進情境2，代表至少已經涵蓋一個W維度（見
            # _detect_pre_image_w_coverage），不是空話——不管這一輪是長者
            # 拒答提早結束，還是問完該問的維度／到輪數上限正常結束，都直接
            # 採信累積內容當生圖來源，不再讓 _has_usable_detail 那套「一定
            # 要有地點」的嚴格組合把關重新judge一次。
            #
            # 2026-08-14：原本讓 _start_scene_after_detail 自動判斷（跟情境1
            # 同一套），實測發現優先序若把「地點」排太後面、輪數上限用完時
            # 沒問到「地點」，就會因為缺這一項把長者聊了兩輪的內容整個丟棄、
            # 退回不相關的RAG/anchor生圖——情境2既然已經是「直接問缺的維度」
            # 這種更精準的做法，就不該再套用同一套通用組合把關。已經把
            # _PRE_IMAGE_PRIORITY_ORDER 的 Where 移到 Why 前面降低問不到的
            # 機率，這裡再把最終把關放寬做雙重保險。
            if not q2_answer:
                return await self._start_scene_after_detail(
                    user, state, combined, detail_is_usable=True,
                )
            # 長者答了這一輪，先更新covered_w（跟STEP2背景追蹤共用同一套
            # 判斷_detect_covered_w、同一份state["covered_w"]狀態，好處是
            # 這裡偵測到的W維度，之後STEP1/STEP3會自動避開重複），再看還
            # 有沒有下一個維度可問、有沒有到輪數上限。
            covered_w = list(state.get("covered_w", []))
            newly_covered = await self._detect_covered_w(q2_answer, covered_w)
            for w in newly_covered:
                if w not in covered_w:
                    covered_w.append(w)
            round_count = state.get("pre_image_q2_round", 1)
            max_rounds = state.get("pre_image_q2_max_rounds", 1)
            next_question = None
            if round_count < max_rounds:
                category = state.get("topic_category")
                sub_item = state.get("pre_image_sub_item")
                named_person = _extract_named_person(combined)
                next_question = get_scenario2_followup(category, sub_item, covered_w, named_person)
            if not next_question:
                print(f"  → 情境2追問結束，已涵蓋: {covered_w}（共{round_count}輪）")
                new_state = {**state, "covered_w": covered_w}
                return await self._start_scene_after_detail(
                    user, new_state, combined, detail_is_usable=True,
                )
            print(f"  → 情境2第{round_count + 1}輪追問: {next_question}，已涵蓋: {covered_w}")
            new_state = {
                **state,
                "last_question_type": "pre_image_q2",
                "pre_image_q1_answer": combined,
                "pre_image_q2_scenario": 2,
                "covered_w": covered_w,
                "pre_image_q2_round": round_count + 1,
            }
            return {
                "action": "pre_image_followup",
                "scene_text": "",
                "question": next_question,
                "state": new_state,
            }

        # ── 出示圖片階段：長者剛看完圖說出第一反應，依反應承接後才問 STEP1 ──
        # 「情緒明顯（不安）」這類已經被最前面的 _detect_emotional_trigger
        # 攔截走了，不會走到這裡；這裡處理的是其餘3類（相符/有差異但平靜/
        # 有差異且介意）＋「情緒明顯（感動）」，見 _generate_image_reveal_reaction。
        if last_type == "image_reveal":
            if quick_end:
                # 長者沒有特別想法／沒回應（例如「沒有」「還好」），沒有真正
                # 的反應可以分類承接，硬套4類反應之一容易答非所問（例如把
                # 「沒有」誤判成「圖跟記憶相符」）。
                pre_image_detail = state.get("pre_image_detail", "")
                if pre_image_detail:
                    # 生圖前的Q1/Q2其實有實質內容，不能因為長者對圖片本身
                    # 沒反應，就接一句完全跟內容無關的固定過渡句——改用
                    # _generate_quick_end_recap 具體呼應 pre_image_detail，
                    # 再接上STEP1開場問題。
                    result = await guarded_generate(
                        self._generate_quick_end_recap,
                        taboo_words=user["taboos"],
                        llm_service=self.llm,
                        max_retry=3,
                        text_keys=("reaction_text", "question"),
                        fallback={
                            "reaction_text": _IMAGE_REVEAL_QUICK_END_ACK,
                            "question": f"{scene_els[0]}，讓你想到什麼？" if scene_els else _FALLBACK_QUESTION,
                            "covered_w": [],
                        },
                        user=user, scene_elements=scene_els, pre_image_detail=pre_image_detail,
                        scene_composition=scene_comp, covered_w=covered_w, emotion=emotion,
                    )
                    print(f"  → quick_end呼應生圖前內容: {result['reaction_text']}｜"
                          f"STEP1問題: {result['question']}（自報W: {result.get('covered_w', [])}，"
                          f"不採信，見下方covered_w說明）")
                    # covered_w 刻意維持不變，不採信這句「本回合已涵蓋的W」——長者
                    # 都還沒回答這句STEP1問題，這裡只是LLM對自己剛寫出的問題的
                    # 自我標記，不是長者真的講過的內容。長者答完後，process_response
                    # 一般分支的背景追蹤（_detect_covered_w，見下方「STEP2：自由對話
                    # 中背景追蹤W覆蓋」區塊）會用長者的真實回答重新判斷涵蓋了哪些W，
                    # 不需要在這裡搶先記錄，也不該搶先記錄——搶先記錄的話，那個W會被
                    # 誤判成「已檢查過」，背景追蹤看到已經在covered_w裡就會跳過重新
                    # 驗證，即使長者根本沒答到也不會被抓出來。
                    new_state = {
                        **state,
                        "covered_w": covered_w,
                        "skipped_w": skipped_w,
                        "last_question_type": "step1",
                        "last_w_asked": "",
                        "question_count": 1,
                        "supplement_count": 0,
                        "image_reveal_deferred": False,  # 見下面分類2分支的說明
                    }
                    return {
                        "action": "scene_ready",
                        "scene_text": result["reaction_text"],
                        "question": result["question"],
                        "state": new_state,
                    }
                # pre_image_detail 是空的（Q1/Q2也沒講出什麼有用內容，生圖時
                # 已經退回RAG記憶）：沒有實質內容可以具體呼應，維持原本的
                # 固定中性過渡句（_IMAGE_REVEAL_QUICK_END_ACK）接上 STEP1
                # 問題。STEP1開場在 question_5w1h.txt 裡本來就不產出場景
                # 文字（畫面一律由別處負責出示），這裡的固定過渡句取代的
                # 正是那個位置，直接用一般 STEP1 就好。
                q = await guarded_generate(
                    self._generate_question,
                    taboo_words=user["taboos"],
                    llm_service=self.llm,
                    max_retry=3,
                    text_keys=("question",),
                    fallback={
                        "question": f"{scene_els[0]}，讓你想到什麼？" if scene_els else _FALLBACK_QUESTION,
                        "covered_w": [],
                    },
                    step="STEP1",
                    user=user,
                    scene_elements=scene_els,
                    covered_w=covered_w,
                    scene_composition=scene_comp,
                    emotion=emotion,
                )
                # covered_w 刻意維持不變——理由同上面 quick_end 呼應 pre_image_
                # detail 那條路徑：q["covered_w"] 只是LLM對自己剛寫出的STEP1
                # 問題的自我標記，長者還沒回答，不能當成已驗證的涵蓋紀錄，交給
                # 長者答完後的背景追蹤（_detect_covered_w）處理即可。
                print(f"  → STEP1問題: {q['question']}（自報W: {q['covered_w']}，"
                      f"不採信，見上方covered_w說明）")
                new_state = {
                    **state,
                    "covered_w": covered_w,
                    "skipped_w": skipped_w,
                    "last_question_type": "step1",
                    "last_w_asked": "",
                    "question_count": 1,
                    "supplement_count": 0,
                    "image_reveal_deferred": False,  # 見下面分類2分支的說明
                }
                return {
                    "action": "scene_ready",
                    "scene_text": _IMAGE_REVEAL_QUICK_END_ACK,
                    "question": q["question"],
                    "state": new_state,
                }
            pre_image_detail = state.get("pre_image_detail", "")
            # was_deferred 要在呼叫LLM之前就先算好——代表長者這句話是不是在
            # 回答分類2追問的「哪裡不一樣」，除了下面決定要不要接
            # _IMAGE_REVEAL_TRANSITION，也要傳給 _generate_image_reveal_
            # reaction 讓它知道「這是追問過一次之後的回答」（見下面2026-08-17
            # 稽核說明），必須在呼叫前算好才能當參數傳入。
            was_deferred = bool(state.get("image_reveal_deferred"))
            result = await guarded_generate(
                self._generate_image_reveal_reaction,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=3,  # 理由同其他生成呼叫：多幾次嘗試換更高機率避開保底句
                text_keys=("reaction_text", "question"),
                # 2026-08稽核：問題太長（too_long）等只跟question有關的違規，
                # 常常在承接語（4類反應分類）已經正確判斷、通過所有檢查的情況
                # 下發生——沒有這個參數，guarded_generate 會把整包（承接語＋
                # 問題）丟掉重新生成，讓已經寫對的承接語也跟著陪葬，實測發現
                # 這種情況並不少見。見 _regenerate_image_reveal_question、
                # guarded_generate 的 question_only_retry_fn 參數說明。
                question_only_retry_fn=self._regenerate_image_reveal_question,
                # _element_fallback 回傳的 dict 是 scene_text/question 這組 key，
                # 這裡 text_keys 換成 reaction_text，不能直接沿用，否則 fallback
                # 真的觸發時 result['reaction_text'] 會 KeyError。
                fallback={
                    # 留空：scene_text 組裝時 was_deferred 成立時會在
                    # reaction_text 後面接一次 _IMAGE_REVEAL_TRANSITION（見
                    # 該常數說明），fallback 這裡不能重複塞同樣的過渡句，
                    # 否則長者會聽到同一句話唸兩次。
                    "reaction_text": "",
                    "question": f"{scene_els[0]}，讓你想到什麼？" if scene_els else _FALLBACK_QUESTION,
                    "covered_w": [],
                },
                user=user, scene_elements=scene_els, elder_response=elder_response,
                scene_composition=scene_comp, covered_w=covered_w,
                pre_image_detail=pre_image_detail, emotion=emotion,
                already_deferred=was_deferred,
            )
            print(f"  → 出示圖片反應分類: {result.get('classification', '')!r}"
                  f"（依據: {result.get('judgment_evidence', '')!r}）")
            # 2026-08-17稽核：這個分支（生圖後第一反應／哪裡不一樣追問）先前
            # 完全沒呼叫 _detect_covered_w——長者這裡如果講出具體的地點/時間/
            # 人物（例如分類3的例句「烤肉的地方是在前面」，或分類4提到已故的
            # 阿嬤），這些內容不會被記進covered_w，之後的STEP3補問還可能問到
            # 同一個W維度，讓長者覺得AI沒在聽。跟STEP2背景追蹤（見下方「STEP2：
            # 自由對話中背景追蹤W覆蓋」區塊）用同一支函式補上，兩個分支
            # （分類2首次追問、正常進STEP1）都要看得到更新後的 covered_w。
            newly_covered = await self._detect_covered_w(elder_response, covered_w)
            for w in newly_covered:
                if w not in covered_w:
                    covered_w.append(w)
            if newly_covered:
                print(f"  → 出示圖片反應自然涵蓋 W: {newly_covered}，covered={covered_w}")
            # 2026-08-16稽核：分類2（有差異但長者還沒具體講出哪裡不一樣）的
            # 承接語原則是「好奇追問哪裡不一樣」——承接語本身就是一句要長者
            # 回答的問題。但下面的正常流程固定會在承接語後面接一句過渡句
            # 「謝謝你跟我說這麼多」＋另一個完全不相關的STEP1新問題，導致
            # 長者根本沒機會回答「哪裡不一樣」，就先被提前道謝、又被問了
            # 別的問題（實測案例：長者說「還好」，AI回「可以多說說看嗎。
            # 謝謝你跟我說這麼多...你們家阿公負責什麼？」）。
            #
            # 分類2這一輪改成只問「哪裡不一樣」，不接過渡句、不接STEP1問題，
            # state 留在 image_reveal（不轉去 step1），下一輪長者回答「哪裡
            # 不一樣」時會重新進到這個分支、用他的回答再分類一次——通常會
            # 變成分類3（已經具體講出差異）或4（講出來變得有情緒），到那時
            # 才真正接過渡句＋STEP1問題，順序就對了：長者先回答「哪裡不
            # 一樣」→ 謝謝你跟我說這麼多 → STEP1問題（使用者2026-08-16確認
            # 要的順序）。
            #
            # image_reveal_deferred 只擋一次：避免長者第二次還是講得很籠統
            # （分類又是2）時無限追問下去——第二次不管分類結果是什麼，都
            # 一律往下走正常流程，把這輪當作已經問過一次「哪裡不一樣」結束。
            # 這個欄位必須宣告在 routers/session.py 的 SessionState pydantic
            # model 裡才能透過 API 往返存活，否則會被 FastAPI 驗證silently
            # 丟棄，導致每次都判斷成「沒追問過」而無限循環（session.py 裡
            # question_count/pre_image_q1_answer 等欄位都因為漏宣告踩過同一個
            # 坑，見那些欄位上方的說明）。
            if result.get("classification") == "2" and not was_deferred:
                print(f"  → 分類2：承接語本身是問題，先問「哪裡不一樣」，"
                      f"暫不接STEP1問題: {result['reaction_text']!r}")
                new_state = {
                    **state,
                    # 2026-08-17稽核：這裡先前沒有更新 covered_w，上面新加的
                    # _detect_covered_w 偵測結果會直接遺失（**state 展開的是
                    # 呼叫前的舊值，不是這裡的區域變數）——長者這句被追問前的
                    # 反應如果剛好帶到W內容，會在這裡憑空消失。
                    "covered_w": covered_w,
                    "last_question_type": "image_reveal",
                    "image_reveal_deferred": True,
                }
                return {
                    "action": "image_reveal_followup",
                    "scene_text": "",
                    "question": result["reaction_text"],
                    "state": new_state,
                }
            if result.get("classification") == "2" and was_deferred:
                # 2026-08-17稽核：長者已經被追問過一次「哪裡不一樣」，這次
                # 分類仍然是2——依「只擋一次」設計（見上方說明）不能再追問
                # 一次，必須往下走進STEP1。但分類2的承接語本質上是一句要
                # 長者回答的問題（例如「哪裡不一樣呢，可以多說一點嗎」），
                # 如果直接沿用，下面會變成「[問句]謝謝你跟我說這麼多...
                # [新的STEP1問題]」——問完馬上道謝、又問下一題，長者根本沒
                # 機會回答第一個問題，語意完全不通，這是實測發現的真實bug。
                # 不能只靠加提示詞讓模型自己避開——本函式其他稽核筆記已經
                # 多次證實本地8B量化基底模型對這類細節指示不穩定，改用跟
                # 分類3同一種語氣的固定句子（下方【任務】分類3的官方例句），
                # 保證這裡一定不是問句，不賭這次LLM會不會照做。
                print(f"  → 已追問過一次仍是分類2，改用固定的分類3語氣承接語"
                      f"（原始: {result['reaction_text']!r}）")
                result = {
                    **result,
                    "reaction_text": (
                        "這張圖確實沒辦法把每個細節都畫得剛剛好，"
                        "聽你這樣說，你記得的畫面比圖裡的還要豐富。"
                    ),
                }
            # 2026-08-16稽核：上面那層連續3次都違規（例如照抄範例、編造判斷
            # 依據）會退回完全通用的固定保底句，跟長者剛才說的話完全無關——
            # 但如果生圖前的Q1/Q2（pre_image_detail）其實有實質內容，不該就
            # 這樣浪費掉。這裡改用跟上面 quick_end 分支同一支
            # _generate_quick_end_recap 當第二層保底：那支函式任務更單純
            # （只呼應pre_image_detail+問STEP1，不用再賭一次4類分類），成功
            # 機率比再重試一次完整的分類任務高，退回的內容至少還貼著長者剛才
            # 講過的東西，好過完全通用的「{畫面元素}，讓你想到什麼？」。
            # reaction_text 判斷式空字串只有 guarded_generate 真的退回上面那組
            # fallback 才會發生（_parse_image_reveal_response 保證正常解析出
            # 的 reaction_text 一定非空，見該函式保底句說明），可以用來判斷
            # 第一層是否真的落到保底。
            if not result["reaction_text"] and pre_image_detail:
                print("  → 出示圖片承接連續違規、已退回第一層保底，"
                      "改用pre_image_detail呼應當第二層保底")
                result = await guarded_generate(
                    self._generate_quick_end_recap,
                    taboo_words=user["taboos"],
                    llm_service=self.llm,
                    max_retry=3,
                    text_keys=("reaction_text", "question"),
                    fallback={
                        "reaction_text": _IMAGE_REVEAL_QUICK_END_ACK,
                        "question": f"{scene_els[0]}，讓你想到什麼？" if scene_els else _FALLBACK_QUESTION,
                        "covered_w": [],
                    },
                    user=user, scene_elements=scene_els, pre_image_detail=pre_image_detail,
                    scene_composition=scene_comp, covered_w=covered_w, emotion=emotion,
                )
            print(f"  → 出示圖片承接: {result['reaction_text']}｜STEP1問題: {result['question']}"
                  f"（自報W: {result.get('covered_w', [])}，不採信，見上方covered_w說明）")
            # 2026-08-16稽核（第二次）：_IMAGE_REVEAL_TRANSITION「謝謝你跟我說
            # 這麼多」這句話，內容上是在謝長者剛才具體講了不少——只有 was_
            # deferred（長者這句話是在回答分類2追問的「哪裡不一樣」）成立時
            # 才對得上，長者是真的多說了一段。分類1（簡短肯定，例如「對，很
            # 像」）、分類4（簡短的情緒反應）如果是第一輪直接命中、沒有經過
            # 分類2追問，長者根本沒有「說這麼多」，接這句話文不對題。分類3
            # 若是第一輪就直接講出具體差異（不是回答分類2追問），一樣沒有
            # was_deferred，不接這句話；只有透過分類2追問後才「說了這麼多」
            # 的情況才接，跟上面 was_deferred 的判斷是同一件事。
            transition = _IMAGE_REVEAL_TRANSITION if was_deferred else ""
            # covered_w 這裡帶的是上面 _detect_covered_w 更新過的版本（長者
            # 這句反應／哪裡不一樣追問裡自然涵蓋的W），不是LLM自報的那份
            # ——LLM在result["covered_w"]裡自己標記的是「它剛寫的STEP1問題
            # 涵蓋哪些W」，那份仍然不採信，理由同上面兩條STEP1路徑。
            new_state = {
                **state,
                "covered_w": covered_w,
                "skipped_w": skipped_w,
                "last_question_type": "step1",
                "last_w_asked": "",
                "question_count": 1,  # STEP1 開場問題算本回合第 1 題（出示圖片那題不算）
                "supplement_count": 0,
                # 重置回 False：不管這輪是分類2追問過一次後走到這裡、還是
                # 一開始就不是分類2，進了STEP1都代表這次image_reveal已經
                # 結束，下次（下一回合）重新出示圖片時不該繼承這次的追問
                # 記錄，見上面 image_reveal_deferred 的說明。
                "image_reveal_deferred": False,
            }
            return {
                "action": "scene_ready",
                "scene_text": f"{result['reaction_text']}{transition}",
                "question": result["question"],
                "state": new_state,
            }

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
                    question_count, supplement_count, scene_composition=scene_comp,
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
                # _generate_open_followup 回傳沒有 covered_w 這個 key，跟 STEP1/STEP3
                # 用的 _generate_question/_generate_supplement_question 不一樣。
                fallback=_element_fallback(
                    scene_els, topic_category=state.get("topic_category"), with_covered_w=False,
                ),
                user=user, scene_elements=scene_els, covered_w=covered_w,
                skipped_w=skipped_w, elder_response=elder_response, emotion=emotion,
                scene_composition=scene_comp, pre_image_detail=state.get("pre_image_detail", ""),
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
                question_count, supplement_count, scene_composition=scene_comp,
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
        question_count: int = 1,
        supplement_count: int = 0,
        elder_response: str = "",
        scene_composition: str = "",
    ) -> dict:
        result = await guarded_generate(
            self._generate_supplement_question,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            max_retry=3,  # 理由同 STEP1 呼叫處：多幾次嘗試換更高機率避開保底句
            fallback=_element_fallback(
                scene_els, target_w=target_w, topic_category=state.get("topic_category"),
            ),
            user=user, scene_elements=scene_els, covered_w=covered_w, target_w=target_w,
            emotion=emotion, elder_response=elder_response, scene_composition=scene_composition,
            pre_image_detail=state.get("pre_image_detail", ""),
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
            # 心得環節（開場邀請語／承接語／收尾肯定語）2026-08-17起改成純規則
            # 模板（app/services/closing_templates.py），不再叫LLM，也不需要
            # user/taboo——scene_text/question 留空，由 app/routers/session.py
            # 讀 Redis 存的本場療程主題分類後，用 build_closing_invitation 填入，
            # 見該檔 session_respond 對 action=="end_session" 的處理。
            return {
                "action": "end_session",
                "scene_text": "",
                "question": "",
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
        scene_composition: str = "",
    ) -> dict:
        """
        話題結束後：最多補問 _MAX_SUPPLEMENT_PER_ROUND 個未涵蓋的W，或結束回合。
        Why 需長者狀態良好才問。

        5W1H 覆蓋度只用來決定「補問哪一個W」，不是「六個維度都要問過才能結束」——
        補問次數或本回合題數一旦超過上限，就算還有W沒問到，也直接自然收尾，
        避免對話變成連環打勾清單。

        目標W的選擇不是永遠照 _W_ORDER 固定順序掃第一個沒涵蓋的——那樣等於
        機械化地把5W1H當成必須照1到5走完的線性流程。改成在非Why的未涵蓋
        維度裡隨機挑一個，Why仍維持最低優先序（只有其他都涵蓋時才輪到，
        且仍要通過長者狀態良好判斷）。
        """
        uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
        if not uncovered:
            return await self._end_action(state, user, elder_response, emotion)

        # 懷舊治療重點是長者的成就感／愉悅感，不是把5W1H打勾湊滿——情緒已經
        # 正向、且本回合已經補問過至少一次，代表這一輪已經達到效果，優先
        # 自然收尾，不用為了湊剩下的W硬多問一題。
        if supplement_count >= 1 and emotion in ("happy", "excited"):
            print(f"  → 長者情緒{emotion}且已補問過，優先收尾（成就感優先於題數）")
            return await self._end_action(state, user, elder_response, emotion)

        if (
            supplement_count >= _MAX_SUPPLEMENT_PER_ROUND
            or question_count >= _MAX_QUESTIONS_PER_ROUND
        ):
            print(f"  → 補問已達上限（{supplement_count}/{_MAX_SUPPLEMENT_PER_ROUND}），話題自然結束")
            return await self._end_action(state, user, elder_response, emotion)

        non_why = [w for w in uncovered if w != "Why"]
        if non_why:
            next_w = random.choice(non_why)
        else:
            elder_state_good = await self._check_elder_state_good(elder_response, emotion)
            print(f"  → Why 長者狀態良好: {elder_state_good}")
            if not elder_state_good:
                skipped_w.append("Why")
                return await self._end_action(state, user, elder_response, emotion)
            next_w = "Why"

        return await self._ask_supplement(
            user, scene_els, covered_w, skipped_w, next_w, state, emotion,
            question_count, supplement_count,
            elder_response=elder_response, scene_composition=scene_composition,
        )

    # ══════════════════════════════════════════════════════════════
    # 私有：LLM 判斷
    # ══════════════════════════════════════════════════════════════

    async def _detect_emotional_trigger(self, elder_response: str) -> bool:
        """
        判斷長者這句話有沒有透露出需要優先安撫的情緒訊號（對齊 dpo/collect_data.py
        的 Track B／EMOTIONAL_SCENARIOS：提到已故親人、自責記憶力衰退、痛苦往事、
        突然情緒低落等）。這類情況要先給情緒支持，不能直接當一般話題結束或延續處理。

        必須明確排除「單純不知道/不記得（沒有情緒起伏）」——這種情況本來就該讓
        _is_quick_end 的關鍵字判斷接手。兩者的判準若混在一起，會漏掉像
        「我不記得了，我都忘了，我腦子越來越差了……」這種同時包含「不記得/忘了」
        字面、但其實是自責沮喪而非單純放棄話題的情況（Track B 情境 b002 就是
        這個模式）。這個判斷必須跑在 _is_quick_end 之前，不然這類句子會先被
        關鍵字判斷攔截成單純的放棄話題，跳過情緒支持直接進下一題。
        """
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            "請判斷長者這句話有沒有透露出需要優先安撫的情緒訊號，例如：\n"
            "- 提到已故的親人或痛苦的死別經驗\n"
            "- 因為記憶力衰退、做不到某件事而自責、沮喪\n"
            "- 描述過去的苦難、創傷或不想多談的痛苦經歷\n"
            "- 情緒突然低落、哽咽、聲音變小、話說到一半停住\n"
            "YES：有上述情緒訊號，需要先安撫再繼續。\n"
            "NO：只是正常回答問題、平靜陳述，或單純不知道/不記得（沒有情緒起伏）。\n"
            "只回 YES 或 NO，不要任何說明。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        return raw.strip().upper().startswith("Y")

    async def _decide_topic_continuation(
        self, elder_response: str, user: dict, scene_elements: list
    ) -> bool:
        """
        判斷長者的回應是否值得繼續順著深入。

        2026-08-14：原本一次性問 LLM「值得繼續深入嗎」直接吐 YES/NO——跟
        _has_usable_detail 稽核前的舊寫法同一種毛病：8B量化基底模型對這種
        整體判斷會穩定漏判/誤判，容易被「有講到具體人物」這種表面訊號
        帶走判成YES，即使長者這句話其實已經是一個完整、帶著情緒收尾感的
        小結局（實測案例：長者說「都是奶奶跟媽媽在準備的哈哈哈」，內容
        雖然有人物，但語氣是笑著收尾，被舊寫法誤判成還要追問）。改成
        逐項列證據，模式同 _has_usable_detail：要求模型明確指出「如果要
        繼續深入，可以往哪個方向延伸」，並附上支撐這個延伸方向的原文
        證據，找不到具體證據就必須明講「找不到」——比「這句話有內容就
        隨口判YES」更難含糊帶過。
        """
        directions = ["情感／意義", "陪伴的人的互動", "感官細節", "接下來發生的事"]
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            "請逐一檢查這段話，能不能自然延伸出更多值得追問的內容，分成"
            "「情感／意義」「陪伴的人的互動」「感官細節」「接下來發生的事」"
            "四個延伸方向，各自找出這段話裡有沒有可以往下追問的具體線索"
            "當作證據。evidence必須是逐字從長者原話裡複製出來的片段，一個"
            "字都不能改寫，也不能複製這句指示本身的文字當證據。如果這段話"
            "已經把某個方向講完整了（例如已經直接給出結論、帶有笑聲等收尾"
            "語氣，沒有懸而未答的細節），或這段話裡根本沒有這個方向的"
            "線索，evidence欄位就必須填「找不到」這三個字，不要為了湊答案"
            "硬找不相關的片段當證據。\n"
            "回傳一個JSON物件，格式：\n"
            "{\"checks\": ["
            "{\"direction\": \"情感／意義\", \"evidence\": \"對應的原文片段，或"
            "「找不到」\"}, "
            "{\"direction\": \"陪伴的人的互動\", \"evidence\": \"...\"}, "
            "{\"direction\": \"感官細節\", \"evidence\": \"...\"}, "
            "{\"direction\": \"接下來發生的事\", \"evidence\": \"...\"}"
            "]}\n"
            "checks陣列一定要包含這四項，不能省略。只回JSON，不要任何說明"
            "文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        checks = result.get("checks", [])
        missing = _missing_from_checks(directions, checks, "direction", elder_response)
        covered = [d for d in directions if d not in missing]
        print(f"  → 話題延伸方向核對，可延伸: {covered}，找不到: {missing}")
        return bool(covered)

    async def _check_pre_image_basic_dims(self, elder_response: str) -> list[dict]:
        """
        「地點／活動／時間」逐項核對本體，從 _has_usable_detail 抽出來，讓
        process_response 的 pre_image_q1 分支可以在判斷情境1/2時直接複用
        這份已查證的 checks（見 _map_basic_checks_to_w），不用再對
        _detect_pre_image_w_coverage 的 Where/When/How/Why 四維度框架重新
        問一次 LLM。

        2026-08-14：原本是一次性問 LLM「有沒有任兩項同時出現」直接吐
        YES/NO——這正是這次稽核（見 _find_missing_memory_items 說明）
        反覆踩到的不可靠模式，8B量化
        基底模型對這種整體判斷會穩定漏判，不是溫度問題。改成逐一核對
        「地點」「活動」「時間」三個維度，每項都要求標出對應的原文
        具體片段當證據，找不到就必須明講「找不到」，再用共用的
        _missing_from_checks／_is_real_evidence 判斷哪些維度真的有覆蓋
        （擋掉樣板字硬套、證據重複挪用、整項憑空消失這幾種已知漏洞），
        比直接問「有沒有任兩項」更難含糊帶過。
        """
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            "請逐一檢查「地點」「活動」「時間」這三個維度，在這段話裡找出"
            "對應的具體片段當作證據。時間不用精確到年份/日期，只要是籠統"
            "的時間點或時段（例如「小時候」「晚上」「過年的時候」）就算數。"
            "無論哪個維度，evidence都必須是逐字從長者原話裡複製出來的"
            "片段，一個字都不能改寫或替換成其他說法，即使意思一樣也不行，"
            "也不能直接複製這句指示本身的文字當證據。如果這段話裡真的沒有"
            "這個維度，evidence欄位就必須填「找不到」這三個字，不要為了"
            "湊答案硬找不相關的片段當證據。\n"
            "回傳一個JSON物件，格式：\n"
            "{\"checks\": ["
            "{\"dimension\": \"地點\", \"evidence\": \"對應的原文片段，或"
            "「找不到」\"}, "
            "{\"dimension\": \"活動\", \"evidence\": \"...\"}, "
            "{\"dimension\": \"時間\", \"evidence\": \"...\"}"
            "]}\n"
            "checks陣列一定要包含地點、活動、時間三項，不能省略。只回"
            "JSON，不要任何說明文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        checks = result.get("checks", [])
        print(f"  → [DEBUG] _has_usable_detail 原始 checks: {checks}")
        # 2026-08-14：實測發現本機8B量化模型有時會偷懶把「整句原文」照抄當
        # 證據（例如「我都會跟家人在車庫烤肉」這種本來就短的句子），不是真的
        # 找不到——這種證據會被 _is_real_evidence 的「整句交差」規則正確擋
        # 下，但擋下來的後果是明明有地點內容，卻被判定成缺，逼長者多答一題
        # 其實不需要的Q2。
        # 2026-08-15：原本要求「三個維度都整句抄」才重試，實測發現「活動」
        # 抽對短語、只有「地點」整句抄這種混合案例完全不觸發重試，答對的
        # 「地點」就這樣被錯殺沒有補救機會（見 _whole_sentence_copy_items
        # 說明）。改成逐項判斷，只把真的疑似整句交差的維度單獨挑出來，用
        # 更受限的格式（限定2-4字關鍵詞，不能整句照抄）重問一次，給模型一
        # 次機會把短語真的挑出來，其他已經答對短語的維度不受影響。
        retry_items = _whole_sentence_copy_items(checks, "dimension", elder_response)
        if retry_items:
            print(f"  → {retry_items} 證據疑似整句照抄（偷懶交差），改用短語格式單獨重問")
            retried = await self._retry_short_phrase_evidence(
                elder_response, retry_items, "dimension",
            )
            print(f"  → 短語格式重問結果: {retried}")
            checks = _merge_retried_checks(checks, retried, "dimension")
        return checks

    async def _has_usable_detail(
        self, elder_response: str, checks: list[dict] | None = None,
    ) -> bool:
        """
        判斷長者回答生圖前引導問題時，內容裡有沒有同時包含「地點＋活動＋
        時間」三項，足夠當這次生圖的記憶來源（見 _start_scene_after_
        detail）。

        跟 _is_quick_end 不一樣：_is_quick_end 只看字數/放棄關鍵字，抓不到
        「有講話、字數也夠，但內容不足以撐出一個場景」這種回答——這裡要求
        三個維度都要有（不是任一項單一維度、也不是兩項任選就算數）。

        2026-08：原本只承認「地點＋活動」「人物＋地點」這兩種組合，一度
        放寬成「地點/活動/人物」三項任兩項同時出現就算YES。

        2026-08-14（第一次）：改回只認「地點＋活動」「人物＋地點」——一定
        要有地點，另外搭配活動或人物任一項才算數。

        2026-08-14（第二次）：改成「地點＋活動＋時間」三項都要有才算數，
        不再收「人物」這個維度——跟 _PRE_IMAGE_PRIORITY_ORDER／
        _FIVE_W1H_BANK 同步改成 Where/When/How/Why 之後，這裡也一併對齊，
        缺任何一項（地點、活動、或時間）都不算夠具體，退回讓 process_
        response 走情境1/2那套精準補維度的流程，用 _FIVE_W1H_BANK 問缺的
        那個維度，不是直接退回RAG記憶——只有 _is_true_refusal 判定的真拒答
        才會退回RAG記憶fallback，見 process_response 的 pre_image_q1 分支。

        checks: process_response 若已經呼叫過 _check_pre_image_basic_dims
        （例如要接著判斷情境1/2、需要重用同一份 checks 給
        _map_basic_checks_to_w），直接把結果傳進來，不用對同一句話重新
        問一次 LLM——同一句話兩次獨立呼叫是非決定性判斷，可能給出不同
        答案。留 None（預設）給還沒查過的呼叫端，維持原本行為。
        """
        dimensions = ["地點", "活動", "時間"]
        if checks is None:
            checks = await self._check_pre_image_basic_dims(elder_response)
        missing = _missing_from_checks(dimensions, checks, "dimension", elder_response)
        covered = [d for d in dimensions if d not in missing]
        print(f"  → 生圖前訪談內容維度核對，涵蓋: {covered}，缺: {missing}")
        # 地點＋活動＋時間三項都要有才算數，缺一就不算夠具體。
        return "地點" in covered and "活動" in covered and "時間" in covered

    async def _retry_short_phrase_evidence(
        self, elder_response: str, dimensions: list[str], key: str,
    ) -> list[dict]:
        """
        _has_usable_detail／_detect_pre_image_w_coverage 共用的重試路徑，
        只在第一次逐項列證據時被 _whole_sentence_copy_items 判定「疑似整句
        照抄」的維度才會用到，只重問那幾項（不是每次都重問全部維度）。
        限定每個維度只能回2-4字的關鍵詞，不能整句照抄，逼模型真的從原文裡
        挑出對應的短語，而不是重複貼上整句話當交差。

        dimensions 可以是中文（地點/活動/時間）或英文（Where/When/How/
        Why）維度名稱，兩邊呼叫端沿用各自原本 prompt 裡的名稱，不用另外
        維護一份翻譯對照表。
        """
        dim_list = "、".join(f"「{d}」" for d in dimensions)
        checks_format = ", ".join(
            f'{{"{key}": "{d}", "evidence": "2-4字關鍵詞，或「找不到」"}}'
            for d in dimensions
        )
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            f"請針對{dim_list}這{len(dimensions)}個維度，各自從這段話裡挑出"
            "對應的關鍵詞，每個關鍵詞限定2-4個字、必須是原文裡真的出現過的"
            "詞語，不能整句照抄、也不能改寫。如果這段話裡真的沒有對應的"
            "內容，evidence欄位就填「找不到」。\n"
            "回傳一個JSON物件，格式：\n"
            f"{{\"checks\": [{checks_format}]}}\n"
            f"checks陣列一定要包含{dim_list}這{len(dimensions)}項，不能省略。"
            "只回JSON，不要任何說明文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        return result.get("checks", [])

    async def _detect_pre_image_w_coverage(self, elder_response: str) -> list[str]:
        """
        判斷生圖前 Q1（_has_usable_detail 已經判定「不夠具體」時才會呼叫）
        要走情境1還是情境2：逐一核對 Where/When/How/Why 四個維度，在長者這
        句話裡找出對應的原文證據，跟 _has_usable_detail 判斷「地點/活動/
        時間」用同一套「逐項列證據」模式（見該函式說明的2026-08-14稽核
        紀錄）——yes/no整體判斷本地小模型會穩定漏判，逐項列證據更難含糊
        帶過。

        全部找不到證據（回傳空list）→ process_response 判定為情境1（完全
        答不出來），問域縮小範例；至少一個維度有證據 → 情境2（有內容但缺
        維度），回傳的清單直接當 get_scenario2_followup() 的起始 covered_w，
        不用另外再核對一次同一句話。

        2026-08-15：Where／How／Why 三項維持嚴格逐字比對，When（時間）
        這一項放寬成允許「合理推斷」——例如「在稻田裡工作」沒有明講
        「白天」，但這個線索能合理推斷出大概是白天，也算涵蓋，不用再
        追問一次「白天還是晚上」。放寬的只有「evidence可以是隱含時間的
        線索、不必是時間詞本身」，不是放寬「evidence可以亂猜」——
        evidence 仍然要求是原話裡逐字存在的片段，讓模型「找一個能推斷
        出時間的具體線索」而不是「宣稱有推斷出時間」，跟其他維度一樣
        靠 _missing_from_checks／_is_real_evidence 驗證這個線索是不是
        真的存在於原文，避免模型亂編一個原話沒有的線索當證據。
        """
        dimensions = _PRE_IMAGE_PRIORITY_ORDER
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            "請逐一檢查「Where（地點）」「When（時間）」「How（方式/過程/"
            "感受）」「Why（原因/意義）」這四個維度，在這段話裡找出對應的"
            "具體片段當作證據。Where／How／Why 這三項，evidence都必須是"
            "逐字從長者原話裡複製出來的片段，一個字都不能改寫或替換成"
            "其他說法，即使意思一樣也不行。When（時間）這一項可以放寬："
            "如果原話沒有直接講時間詞，但話裡有其他具體線索能合理推斷出"
            "大概的時間（例如具體的動作、場景描述隱含白天/晚上/季節，像是"
            "「在稻田裡工作」隱含白天），也算涵蓋——這種情況evidence欄位"
            "要填「讓你推斷出時間的那個具體線索」，這個線索本身仍然必須"
            "是逐字從原話複製出來的片段，不能瞎猜一個原話沒有的線索，也"
            "不能只寫「推斷」兩個字交差。無論哪個維度，都不能直接複製這句"
            "指示本身的文字當證據。如果這段話裡真的沒有這個維度、也找不到"
            "任何能推斷的線索，evidence欄位就必須填「找不到」這三個字，"
            "不要為了湊答案硬找不相關的片段當證據。\n"
            "回傳一個JSON物件，格式：\n"
            "{\"checks\": ["
            "{\"dimension\": \"Where\", \"evidence\": \"對應的原文片段，或"
            "「找不到」\"}, "
            "{\"dimension\": \"When\", \"evidence\": \"...\"}, "
            "{\"dimension\": \"How\", \"evidence\": \"...\"}, "
            "{\"dimension\": \"Why\", \"evidence\": \"...\"}"
            "]}\n"
            "checks陣列一定要包含Where、When、How、Why四項，不能省略。只回"
            "JSON，不要任何說明文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        checks = result.get("checks", [])
        print(f"  → [DEBUG] _detect_pre_image_w_coverage 原始 checks: {checks}")
        # 跟 _has_usable_detail 同一套救援機制（見該函式 2026-08-15 說明）：
        # 只把疑似整句照抄的維度單獨挑出來重問短語格式，不要求「全部維度
        # 都整句抄」才重試，答對短語的維度不受影響。
        retry_items = _whole_sentence_copy_items(checks, "dimension", elder_response)
        if retry_items:
            print(f"  → {retry_items} 證據疑似整句照抄（偷懶交差），改用短語格式單獨重問")
            retried = await self._retry_short_phrase_evidence(
                elder_response, retry_items, "dimension",
            )
            print(f"  → 短語格式重問結果: {retried}")
            checks = _merge_retried_checks(checks, retried, "dimension")
        missing = _missing_from_checks(dimensions, checks, "dimension", elder_response)
        covered = [d for d in dimensions if d not in missing]
        print(f"  → Q1回答的W維度核對，涵蓋: {covered}，缺: {missing}")
        return covered

    async def _classify_pre_image_sub_item(
        self, theme: str, elder_detail: str
    ) -> str | None:
        """
        情境2成立、且該主題是 sub_item granularity（見 _FIVE_W1H_BANK）時
        才分類，判斷長者這句話最接近主題底下的哪個子項目（例如「童年經歷」
        底下的「威權教育」vs「物質生活佳」——兩者該排除的欄位不一樣，見
        excluded_fields）。

        只在真的用得到時才付這次LLM呼叫成本：多數Q1回答會落在「已經夠
        具體直接生圖」或情境1（完全答不出來），這兩種都用不到子項目，跟
        _has_usable_detail「不問不一定用得到的問題」同一個設計哲學。

        分類失敗（LLM輸出不在該主題的子項目清單裡）回傳 None，呼叫端
        （get_scenario2_followup）會直接視為問不到、跳過情境2的追問，不
        勉強瞎猜錯的子項目、問錯排除規則的維度。
        """
        theme_data = _FIVE_W1H_BANK.get(theme)
        if not theme_data or theme_data["granularity"] != "sub_item":
            return None
        sub_items = list(theme_data.get("sub_items", {}).keys())
        options = "、".join(sub_items)
        prompt = (
            f"長者剛才說：「{elder_detail}」\n\n"
            f"這段話最接近「{theme}」主題底下的哪一個子類別？\n{options}\n\n"
            f"只回答一個子類別名稱，完全比照上面的寫法，不要加任何說明或標點。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        sub_item = raw.strip()
        result = sub_item if sub_item in sub_items else None
        print(f"  → 子項目分類: {result!r}（候選: {sub_items}）")
        return result

    async def _classify_topic_category(self, today_topic: str) -> str | None:
        """
        判斷 today_topic（治療師輸入的自由文字，例如「中秋節」「工廠上班的
        日子」）最接近懷舊治療16大主題分類（_TOPIC_CATEGORIES）裡的哪一類，
        決定要問 _PRE_IMAGE_Q2_SCENARIO1_TEMPLATES／_FIVE_W1H_BANK 裡對應
        的哪一句 Q2，也決定 _build_pre_image_question 的 Q1 要用哪一句
        _PRE_IMAGE_Q1_INVITATIONS。

        2026-08：呼叫時機從「只在 Q1 答得不夠具體時才懶惰執行」改成
        「start_round 一開始就跑」（見該函式docstring、檔案開頭流程說明）——
        因為 Q1 本身也要依分類挑對應的邀請語，不能再等長者答完才分類。分類
        結果存進 state["topic_category"]，process_response 的 pre_image_q1
        分支若真的需要問 Q2，直接複用這個結果，不會再呼叫這支函式第二次；
        這裡本身仍是每次 start_round 都會付出一次的 LLM 呼叫成本，不是
        「省下不一定用得到的呼叫」。

        回傳值必須完全比對 _TOPIC_CATEGORIES 裡的16個名稱之一才算分類成功；
        LLM 輸出稍微跑題、多加說明文字等情況一律視為分類失敗回傳 None，
        呼叫端會直接退回用 Q1 的回答生圖，不勉強瞎猜分類、問錯方向的 Q2。
        """
        options = "、".join(_TOPIC_CATEGORIES)
        prompt = (
            f"今日主題：「{today_topic}」\n\n"
            f"這個主題最接近以下16個懷舊治療主題分類裡的哪一個？\n{options}\n\n"
            f"只回答一個分類名稱，完全比照上面的寫法，不要加任何說明或標點。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        category = raw.strip()
        return category if category in _TOPIC_CATEGORIES else None

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
        raw = await self.llm.ask(prompt, temperature=0)
        return raw.strip().upper().startswith("Y")

    def _is_quick_end(self, elder_response: str) -> bool:
        """短回答或放棄關鍵字 → 直接標記話題結束，不呼叫 LLM。"""
        # _NO_RESPONSE_MARKER 字數超過5字、也不含放棄關鍵字，要獨立判斷，
        # 不然接不到問題設計規則.pdf「沉默超過10秒→轉話題」這條。
        if elder_response.strip() == _NO_RESPONSE_MARKER:
            return True
        if len(elder_response.strip()) < 5:
            return True
        return any(kw in elder_response for kw in _GIVE_UP_KEYWORDS)

    def _is_true_refusal(self, elder_response: str) -> bool:
        """
        判斷長者是「真的不想／不能答」，只認沉默逾時（_NO_RESPONSE_MARKER）
        或明確放棄關鍵字（「不知道」「不記得」等）這兩種，跟 _is_quick_end
        不一樣的地方是**不把單純的短回答算進來**。

        2026-08：process_response 的 pre_image_q1 分支原本直接拿 _is_quick_end
        的結果決定要不要略過Q2追問、直接退回RAG記憶——但 _is_quick_end 的
        「字數<5就算quick_end」這條，把「有喔」「會啊」這種長者真的有回答、
        只是講得很短的內容，跟「長者完全不想講」一視同仁地跳過Q2。短回答
        依然是長者本人真實給的內容，應該讓他有機會在Q2多說一點，不該直接
        放棄改用RAG舊記憶——只有長者真的沉默逾時或明確表示不知道/不記得
        時，才没有必要再多問一題。_is_quick_end 在其他地方的判斷（是否要
        存進RAG記憶、STEP2/3話題是否結束）維持原樣不受影響，只有生圖前
        Q1→Q2 這一步改用這支較窄的判斷。
        """
        stripped = elder_response.strip()
        if stripped == _NO_RESPONSE_MARKER:
            return True
        return any(kw in elder_response for kw in _GIVE_UP_KEYWORDS)

    async def _detect_covered_w(
        self, elder_response: str, already_covered: list[str]
    ) -> list[str]:
        """
        偵測長者回應中自然涵蓋了哪些尚未記錄的 W 維度（STEP2 背景追蹤／生圖前
        訪談／情境2續問共用，見三個呼叫點）。

        2026-08-16稽核：原本一次性問LLM「這段話有沒有涵蓋以下W維度」直接吐
        結論，是跟 _has_usable_detail 稽核前同一種不可靠模式——本地8B量化
        基底模型對這種整體判斷會穩定漏判，短答案（例如「晚上」「晚上7點」）
        尤其容易被漏掉，導致 covered_w 卡住不動、長者已經答過的W維度被重複
        追問（實測案例：情境2連續兩輪都問「時段」，即使長者已經回答「晚上」
        「晚上7點」）。改成逐一核對每個未涵蓋維度、要求標出原文證據，跟
        _check_pre_image_basic_dims／_detect_pre_image_w_coverage 同一套
        「逐項列證據」模式，共用 _missing_from_checks／_is_real_evidence／
        _whole_sentence_copy_items 這幾個已驗證過的輔助函式。
        """
        unchecked = [w for w in _W_ORDER if w not in already_covered]
        if not unchecked:
            return []
        dim_list = "、".join(f"「{w}」" for w in unchecked)
        desc_list = "\n".join(f"- {w}：{_W_DESC[w]}" for w in unchecked)
        checks_format = ", ".join(
            f'{{"dimension": "{w}", "evidence": "對應的原文片段，或「找不到」"}}'
            for w in unchecked
        )
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            f"請逐一檢查{dim_list}這{len(unchecked)}個維度，在這段話裡找出"
            f"對應的具體片段當作證據，每個維度的定義：\n{desc_list}\n\n"
            "evidence都必須是逐字從長者原話裡複製出來的片段，一個字都不能"
            "改寫或替換成其他說法，即使意思一樣也不行，也不能直接複製這句"
            "指示本身的文字當證據。如果這段話裡真的沒有這個維度，evidence"
            "欄位就必須填「找不到」這三個字，不要為了湊答案硬找不相關的"
            "片段當證據。同一段文字只能當一個維度的證據——如果它同時符合"
            "多個維度的定義，只能選語意最直接對應的那一個維度填入，其他"
            "維度不能重複使用這段文字，一律填「找不到」。\n"
            "回傳一個JSON物件，格式：\n"
            f"{{\"checks\": [{checks_format}]}}\n"
            f"checks陣列一定要包含{dim_list}這{len(unchecked)}項，不能省略。"
            "只回JSON，不要任何說明文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        checks = result.get("checks", [])
        print(f"  → [DEBUG] _detect_covered_w 原始 checks: {checks}")
        retry_items = _whole_sentence_copy_items(checks, "dimension", elder_response)
        if retry_items:
            print(f"  → {retry_items} 證據疑似整句照抄（偷懶交差），改用短語格式單獨重問")
            retried = await self._retry_short_phrase_evidence(
                elder_response, retry_items, "dimension",
            )
            print(f"  → 短語格式重問結果: {retried}")
            checks = _merge_retried_checks(checks, retried, "dimension")
        checks = _resolve_when_duplicate_evidence(checks)
        checks = _backstop_when_evidence(checks, elder_response)
        missing = _missing_from_checks(unchecked, checks, "dimension", elder_response)
        newly_covered = [w for w in unchecked if w not in missing]
        print(f"  → _detect_covered_w 核對，新涵蓋: {newly_covered}，仍缺: {missing}")
        return newly_covered

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
        raw = await self.llm.ask(prompt, temperature=0)
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

        2026-07-31 曾試過拆成「收縮期→結果期」兩次獨立呼叫（對應 Chao, Chen,
        Liu, & Clark, 2008 的四階段模型），但實測本地模型（DPO 訓練資料 Track D
        一律是「收尾語+問題」成對出現）看到只要求單一欄位的新任務形狀時會退化成
        逐字複誦長者的話，收尾語品質反而比單次呼叫差，因此改回單次生成。

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
            f"問題：（≤25字）"
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

    async def _generate_emotional_response(
        self,
        user: dict,
        elder_response: str,
        retry_feedback: str = "",
    ) -> dict:
        """
        長者觸發情緒訊號（見 _detect_emotional_trigger）時的專屬回應：先給情緒
        支持，再輕柔引導回療程。

        system_content／user_content 逐字比照 dpo/collect_data.py 的
        build_emotional_inference_prompt（Track B），避免又製造一次 Track A/C/D
        都曾發生過的 train/serve prompt 漂移——那支函式本身就沒有情緒欄位（跟
        Track A/C/D 不同），這裡故意不加 emotion 參數，維持跟訓練資料一致。
        """
        system_content = (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，當他出現負面情緒時，你要先給予情緒支持，再輕柔地引導回療程。"
        )
        taboo_str = "、".join(user["taboos"]) if user["taboos"] else "無"
        user_content = (
            f"長者剛才說了：\n{elder_response}\n\n"
            f"【禁忌話題（絕對不可主動提及或追問細節）】\n{taboo_str}\n\n"
            f"請先給予溫暖的情緒回應，再加上一句輕柔的後續引導。若長者說的內容本身就觸及\n"
            f"【禁忌話題】，承接要溫和但不深入追問細節，儘快輕柔地轉向安全的方向；\n"
            f"後續引導也不能引導向【禁忌話題】。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"情緒回應：（溫暖承接情緒，30-50字）\n"
            f"後續引導：（一句輕柔的問題或肯定，引導回療程）"
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_emotional_response(raw)

    def _parse_emotional_response(self, raw: str) -> dict:
        """解析 Track B（情緒回應 + 後續引導）的輸出。

        故意不用 scene_text 當 key（見 process_response 呼叫 guarded_generate
        的說明）：scene_text_addresses_elder 檢查會把任何出現「你」的 scene_text
        判定成違規，但情緒回應語意上必須用「你」直接安慰長者，用專屬 key 名稱
        天然繞開這個誤判，比照 closing_text 的既有模式。
        """
        result: dict = {"emotional_text": "", "question": ""}
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("情緒回應："):
                result["emotional_text"] = line[len("情緒回應："):].strip()
                current_field = "emotional_text"
            elif line.startswith("後續引導："):
                result["question"] = line[len("後續引導："):].strip()
                current_field = "question"
            elif current_field == "emotional_text":
                result["emotional_text"] = f"{result['emotional_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("emotional_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            result["question"] = _FALLBACK_QUESTION
        if not result["emotional_text"]:
            print(f"[Orchestrator] ⚠ 情緒回應欄位是空的，退回保底安撫語。"
                  f"原始輸出: {raw[:200]!r}")
            result["emotional_text"] = "謝謝你願意說這些，我在這裡陪著你。"
        return result

    # ══════════════════════════════════════════════════════════════
    # 私有：問題生成
    # ══════════════════════════════════════════════════════════════

    async def _decide_scene_anchor(self, user: dict) -> str:
        """
        決定這次 _plan_image 該用哪個錨點，回傳 'hometown'／'occupation'／'interest'／'topic'。
        獨立判斷、不跟生圖 prompt 混在一起，理由見 _plan_image 的說明。

        2026-08：原本「興趣欄位空白就跳過LLM、直接鎖定occupation」的短路邏輯拿掉了
        ——節慶類主題（例如中秋節）其實更貼近「家鄉／過年過節習俗」這個錨點，但
        很多長者資料本來就沒填興趣欄位，短路邏輯會讓這類主題永遠選不到「家鄉」，
        一律套用職業場景硬湊主題。沒有興趣資料時，改成只列A/B兩個選項讓LLM依
        主題判斷，而不是不問就先認定是職業。

        2026-08：又加了D「都不相關」選項——原本強制在A/B/C裡選一個，遇到主題本身
        跟長者的家鄉/職業/興趣都沒有直接對應時，硬選一個容易逼出勉強拼湊的畫面
        （例如把主題硬套進職業場景）。允許LLM回答「都不是」，改用主題本身在那個
        年代真實會有的情境挑元素，不強求要跟長者的家鄉/職業/興趣掛勾。
        """
        choice_map = {"A": "hometown", "B": "occupation"}
        options = [
            "A：家鄉／成長地／故鄉生活",
            f"B：職業背景（{user['main_occupation']}）",
        ]
        if user.get("preferences"):
            choice_map["C"] = "interest"
            options.append(f"C：興趣嗜好（{user['preferences']}）")
        choice_map["D"] = "topic"
        options.append("D：都不相關，這個主題本身有自己的生活情境，不需要硬套家鄉/職業/興趣")
        prompt = (
            f"今日主題：「{user['today_topic']}」\n\n"
            f"這個主題最貼近長者的哪一種真實生活情境？\n"
            + "\n".join(options) +
            f"\n\n只回答{'、'.join(choice_map)}其中一個字母，不要任何說明或標點。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        choice = raw.strip().upper()[:1]
        return choice_map.get(choice, "occupation")

    def _build_plan_image_prompt(
        self, user: dict, anchor: str, memories: list[dict] | None = None,
        hide_field: str | None = None, memory_checklist: list[str] | None = None,
    ) -> str:
        """
        組 _plan_image 的 prompt。【長者資料】固定完整顯示，anchor 只決定
        【任務】那句指令這次要用哪個情境。hide_field 只在重試時才會給值
        （見 _plan_image），把對應欄位從長者資料整個拿掉。

        memory_checklist：_extract_checklist_from_memories 從回憶內容獨立
        抽出的細節清單，只在有記憶時給值。純文字叮嚀「不要漏掉回憶裡任何
        一個有畫面感的細節」（下面task_instruction）跟翻譯重構最初的做法
        一樣不可靠，這裡加一份結構化checklist當硬性要求，生成後另外由
        _plan_image 呼叫 _find_missing_memory_items 核對，見該函式說明。
        """
        memory_section = ""
        if memories:
            memory_section = "\n【長者相關回憶（優先從這裡挑場景元素）】\n"
            for m in memories:
                memory_section += f"- {m.get('summary', m.get('text', ''))}\n"
            if memory_checklist:
                memory_section += (
                    "\n【回憶細節清單（elements和image_prompt必須逐一涵蓋，"
                    "一個都不能少，這是硬性要求）】\n"
                    f"{'、'.join(memory_checklist)}\n"
                )

        profile_lines = [
            f"姓名：{user['name']}",
            f"年齡：{2026 - user['birth_year']} 歲",
            f"出生地：{user['birth_place']}",
        ]
        if hide_field != "main_occupation":
            profile_lines.append(f"職業背景：{user['main_occupation']}")
        if hide_field != "preferences":
            profile_lines.append(f"興趣：{user.get('preferences') or '無'}")
        profile_lines.append(f"今日主題：{user['today_topic']}")
        profile_block = "\n".join(profile_lines)

        # 有記憶／沒記憶是完全分開的兩條指令文字，不要用一句條件句（若有...若無...）
        # 硬湊在一起——實測發現硬湊在一起時，即使真的有記憶，模型也會被句子裡
        # 沒用到的另一半分支文字干擾，變成兩邊都不用、跑去生成完全不相關的內容。
        # 2026-08-15起 memories 可能是 RAG 撈回來的舊記憶，也可能是長者這回合
        # 親口給的內容（兩種情況統一都走這裡，見 _plan_image docstring）。
        if memories:
            task_instruction = (
                "這次的每一個畫面元素都只從【長者相關回憶】的內容延伸挑選，完全"
                "不使用興趣、職業背景或出生地。回憶裡提到幾個不同的具體物件/"
                "地點/活動，就要對應選出幾個元素、每一個都要出現在elements和"
                "image_prompt裡——不要漏掉回憶裡任何一個有畫面感的細節（例如"
                "回憶同時提到烤肉、賞月、提燈籠三件事，這三件事都要畫進去，"
                "不能只挑一兩個就當作涵蓋了整段回憶）。下面【回憶細節清單】"
                "（如果有列出）就是逐一涵蓋的硬性依據。同時也不要新增回憶沒"
                "提到的細節，只能延伸、不能創造——回憶如果只提到一件具體的事"
                "（例如只說「跟家人一起吃月餅」），elements就只能有這一個"
                "對應的元素（例如「月餅」），不要為了湊數量、讓畫面看起來"
                "更豐富，自己加場景或道具（例如自己多加「院子」「烤肉架」）。"
                "「不能創造」也包含敘事框架、情緒描述，不是只針對實體物件——"
                "不要自己加一個「有人正在回憶／懷念」的敘事角度（回憶只提到"
                "吃月餅這件事本身，image_prompt不要寫成「一位長者在回憶童年」"
                "這種框架；這種框架等於自己加了一個代表長者本人的人物，違反"
                "下面【嚴格規定】第3點）。image_prompt要直接描述回憶裡的畫面"
                "本身，不要加一層「正在回憶/想起」的敘事外殼。"
                "下面【嚴格規定】裡「元素數量通常2-4個」是給沒有回憶內容、"
                "要自己發想情境的其他情況參考用，這裡不適用，回憶內容能撐出"
                "幾個元素就是幾個，哪怕只有1個。"
            )
        elif anchor == "hometown":
            task_instruction = (
                "這次主場景以【出生地】的真實生活情境挑選元素（想像長者在自己的"
                "家鄉生活的真實一刻），不要用到興趣，元素彼此都要屬於同一個情境。"
                "元素最優先要能呼應【今日主題】——先想這個主題在長者的家鄉會是"
                "什麼樣子。只有【職業背景】跟【今日主題】直接相關時，才把職業"
                "背景的元素帶進來；兩者不相關時，不要帶入職業背景，單純用家鄉"
                "本身會出現的元素就好，不要為了硬湊職業背景犧牲跟主題的貼合度"
                "（【今日主題】本身如果是像節慶這種有清楚視覺意象的詞，可以直接"
                "當一個元素使用）。想得出幾個真正符合上面規定的元素就用幾個——"
                "不要把【出生地】欄位的文字本身直接當成一個元素，那是地名，不是"
                "看得到的實體物件，要轉換成那個地方實際會出現的具體東西才能當"
                "元素。"
            )
        elif anchor == "interest":
            task_instruction = (
                "這次主場景以【興趣】的真實生活情境挑選元素（想像長者從事這項興趣"
                "時的真實一刻），不要用到職業背景，元素彼此都要屬於同一個情境。"
                "元素還要能呼應【今日主題】——不是隨便選這個興趣情境裡常見的東西，"
                "是要選這個情境裡跟今日主題最有關的那些東西；【今日主題】本身如果"
                "是像節慶這種有清楚視覺意象的詞，可以直接當一個元素使用。想得出"
                "幾個真正符合上面規定的元素就用幾個——不要把【興趣】欄位的文字本身"
                "直接當成一個元素，那是興趣的名稱，不是看得到的實體物件，要轉換成"
                "從事這項興趣時實際會出現的具體東西才能當元素。"
            )
        elif anchor == "topic":
            task_instruction = (
                "這次【今日主題】本身跟長者的出生地、職業背景、興趣都沒有直接對應，"
                "不要硬套其中一個——改直接以【今日主題】本身在長者生活的那個年代"
                "會出現的真實情境挑選元素（想像這個場合、這個節日普遍會有的畫面），"
                "元素彼此都要屬於同一個情境；可以自然帶入長者的職業背景增添真實感，"
                "但不用刻意硬拗在一起。【今日主題】本身如果是像節慶這種有清楚視覺"
                "意象的詞，可以直接當一個元素使用。想得出幾個真正符合上面規定的"
                "元素就用幾個。"
            )
        else:
            task_instruction = (
                "這次主場景以【職業背景】的真實生活情境挑選元素（想像長者在職業"
                "現場的真實一刻），不要用到興趣，元素彼此都要屬於同一個情境。"
                "元素還要能呼應【今日主題】——不是隨便選這個職業情境裡常見的東西，"
                "是要選這個情境裡跟今日主題最有關的那些東西；【今日主題】本身如果"
                "是像節慶這種有清楚視覺意象的詞，可以直接當一個元素使用。想得出"
                "幾個真正符合上面規定的元素就用幾個——不要把【職業背景】欄位的文字"
                "本身直接當成一個元素，那是職業的名稱，不是看得到的實體物件，要"
                "轉換成職業現場實際會出現的具體東西才能當元素。"
            )

        return f"""你是水彩畫家，正在用文字描述一幅已經畫好的懷舊主題水彩畫——不是在規劃
一個「正在發生什麼事」的場景、也不是在敘述長者的回憶故事，是用「畫面裡看
得到什麼」的角度描述這幅畫本身（物件、光線、色彩、構圖），不要用「誰在做
什麼」這種敘事角度。這幅畫裡完全沒有人物、沒有人形。

【長者資料】
{profile_block}
{memory_section}
【任務】
描述一幅水彩風格的懷舊場景畫，符合主題，畫面內容要能引發長者的回憶。

{task_instruction}

不要只憑【今日主題】天馬行空聯想，也不要選跟長者實際生活背景無關的通俗畫面。

【嚴格規定】
1. 每個元素必須是「不需要湊近看細節、一眼就能辨認形狀」的大範圍實體物件或情境
   （例如：建築物、交通工具、農具、地景、天色），問題會直接錨定在第一個元素上。
2. 絕對不要用「需要讀出文字」的元素（黑板文字、招牌字樣、書頁內容、標語等）——
   AI 生圖無法穩定畫出清楚可讀的文字，長者也答不出畫面上寫了什麼。
3. 絕對不要生成任何人物元素——不管是長者本人、家人、同事、客人或路人，一個人
   或一群人都不行，只描述場景、物件、情境本身，不要為了讓畫面「有人」自己
   加角色進去，也不要把「大家」「一群人」這種籠統集合稱呼當元素。（這條規則
   2026-08-15前只禁止「代表長者本人」的人物、其他人物可以當元素，後來需求
   改成畫面一律不出現人物，範圍擴大成全面禁止；image_prompt 最後送去生圖 API
   前還會另外跑 _strip_people_clauses／_add_no_people_directive 當事後最後
   防線，見 start_round 呼叫處說明。）
4. 每個元素必須是「同年代、同職業背景的人普遍會有印象」的常見物件，不要選個人
   化程度太高、地域限定太窄或太罕見的物件（例如特定花卉品種、特定小眾嗜好用
   品）——長者答不出自己沒印象的東西，元素越通俗普遍，長者才越可能真的有共鳴。
5. image_prompt 撰寫時，每個元素都要展開成「地點＋看得到的具體視覺內容」，
   不能只是把元素名詞堆在一起當場景清單（例如不要只寫"barbecue grill and
   lanterns lit up"，要寫"at the backyard grill, skewers of meat sizzling
   over glowing charcoal with rising smoke"）——實測發現只堆物件名詞，AI
   生圖模型會把該物件畫成靜態背景道具，長者真正想回憶的「活動氛圍」反而
   不見了。這裡要寫的是活動留下的畫面痕跡／狀態本身，不能用「誰在做這件
   事」的敘事角度描述（見規則3，畫面不出現人物）——例如不要寫「一群人圍著
   烤肉」，要寫「炭火發亮、肉串滋滋作響、輕煙裊裊」；不要寫「小孩提著燈籠
   跑」，要寫「幾盞燈籠高掛，光暈搖曳」：
   - 地點＋視覺痕跡：先點出具體地點，再接上那個地點裡看得到的具體視覺內容
     （物件形狀/材質、光線/煙霧/水氣等看得到的狀態），不能只寫抽象動詞帶
     過，也不要點名或暗示是誰在做。
   - 同時有多個元素時，每一組描述的視覺細節份量要盡量對等，並用地位平行的
     獨立子句呈現（例如用"and"連接），不要用主副句結構——份量或句構不對等
     時，模型會只把資源分給描述較豐富的那一組，另一組會在畫面裡消失或被弱化
     成背景裝飾。
6. image_prompt 要指定暖色調、高對比配色，且明確避免藍、綠、紫三色互相鄰接
   （例如寫 "warm high-contrast palette, avoid adjacent blue-green-purple
   tones"）——年長者對藍/綠/紫及其鄰近色的辨識能力較弱，色差不夠大會導致
   長者根本看不清楚畫面裡的錨點物件。
7. image_prompt 開頭必須固定寫"watercolor painting style, 1980s Taiwan..."
   （年代固定用1980年代，不管回憶內容是長者哪個人生階段都一樣，不要自己
   依內容或年齡推算成其他年代——固定寫死是為了讓每次生成的年代一致，不
   要有變動）；結尾必須固定接"nostalgic warm tones, no text"。這兩段是
   送去生圖 API 的技術參數（畫風、色調、文字排除），不算在上面幾點要求的
   場景內容裡，兩段缺一個都不行——不能只靠下面的JSON範例暗示格式，這是
   硬性規定。
8. 元素之間、以及元素與長者的職業背景/今日主題之間，必須符合現實邏輯，不能互相
   矛盾（例如：導遊、業務跑外勤這類白天在外活動的職業，畫面不要無故選夜景；適合
   用夜景的情境是活動本身就發生在晚上，如夜市、廟會、夜校、值夜班等）。第6點要求
   的暖色高對比，白天陽光、黃昏落日一樣能達成，不是只有夜晚才符合。
9. 回傳一個 JSON 物件，**只回 JSON，不要任何說明文字或 markdown 標記**。元素數量
   不固定，通常2-4個，想得出幾個真正符合上面規定的元素就填幾個，不用刻意湊滿。
   elements 本身維持短物件名詞（後面問題生成會直接錨定在第一個元素上），把
   「地點+視覺痕跡」的具體描述留給 image_prompt 展開。
格式：
{{
  "elements": ["元素1", "元素2", "..."],
  "image_prompt": "英文 prompt 給生圖 API，包含水彩風格、年代、場景、每個元素展開後的地點+視覺痕跡描述，不含任何人物"
}}

範例（主題=清明掃墓）：
{{
  "elements": ["墓園", "香燭", "供品"],
  "image_prompt": "watercolor painting style, 1970s Taiwan hillside graveyard on tomb-sweeping day, at the family gravesite incense sticks burning with smoke curling upward before the tombstone, fruit and food offerings laid out on a cloth nearby, overcast spring light, nostalgic warm tones, no text"
}}
"""

    async def _plan_image_from_detail(self, user: dict, elder_detail: str) -> dict:
        """
        長者這回合親口給的生圖前訪談內容（已去識別化）當生圖來源時的規劃
        路徑，跟 _plan_image 分開——手動測試多輪發現，_plan_image 那套
        「先問LLM抽成elements清單、再讓LLM看著elements清單自由重新想像
        image_prompt」的流程，這層elements抽象化會弄丟長者原話裡的空間/
        相對位置關係（例如誰站在誰旁邊、東西擺在什麼前面），這是elements
        抽象化天生的限制，不是提示詞能修好的問題。這裡改成 image_prompt
        由 _translate_detail_to_image_prompt 直接從長者原話翻譯重構，不
        經過elements這層中間表示；elements另外用 _extract_elements_
        from_detail 獨立抽一次，只給後續5W1H問題生成當錨點用，不影響
        image_prompt內容——兩者都只從同一段 elder_detail 延伸，但彼此不
        互相依賴。

        2026-08-15：曾經一度把這條路徑整個併回 _plan_image（理由是「只要
        生出一個場景，不用逐一涵蓋細節」，見那次改動的說明），後來發現
        併回去之後畫面明顯變差、空間關係常常錯亂或消失，才又拆回這條
        獨立路徑——但吸收了那次簡化的教訓：不再對動作/人物做checklist+
        重試核對（見舊版 _translate_detail_to_image_prompt 的稽核紀錄），
        單次生成即可，不強求逐一涵蓋每個動作/人物，只要求忠實翻譯、保留
        空間關係；畫面裡不出現任何人物的保證，交給 _start_scene_after_
        detail 統一呼叫的 _strip_people_clauses／_add_no_people_directive
        確定性處理，不需要在這裡另外驗證。
        """
        image_prompt = await self._translate_detail_to_image_prompt(user, elder_detail)
        elements = await self._extract_elements_from_detail(elder_detail)
        return {"elements": elements, "image_prompt": image_prompt}

    async def _translate_detail_to_image_prompt(self, user: dict, elder_detail: str) -> str:
        """
        組給AI生圖模型用的prompt，見 _plan_image_from_detail 說明。

        2026-08-15（中文直接送出版）：原本這裡是把長者原話（中文）整段
        翻譯成英文場景描述，一直踩到「翻譯失真」的問題——「車庫」被翻成
        backyard、carport這類語意相關但構造不同的英文詞，加了地點/活動
        主動抽取+明講、事後核對+重試都只能治標。實測改成把中文原話直接
        送給生圖模型（gpt-image-2 本身建立在多模態語言模型上，中文理解
        力足夠好）之後，鐵捲門這種構造細節第一次就正確畫出來，不需要
        「先壓縮成一個精準英文詞」這個有損步驟——問題根源本來就是翻譯
        這一步，跳過它就跳過了整個問題，不是靠更多提示詞技巧修得好的。

        改成確定性組合，不再是一次LLM生成：固定的風格/技術指令（畫風、
        年代、色調、物件擺放要合理、光線要跟時間符合現實邏輯、不要文字）
        + 今日主題（明講帶入，不靠核對+重試補救）+ 長者原話本身（完整
        保留，動作/地點/相對位置/時間都在同一段話裡，不用再拆開個別
        抽取核對）。不需要LLM生成，也就不會有生成不穩定、漏翻、格式跑掉
        這些問題。

        2026-08-15（全中文版）：風格指令原本用英文，實測發現改成中文
        效果一樣好、甚至更貼近節慶氛圍（見這次改動前的手動測試：中秋節
        主題配中文prompt，正確畫出滿月+鐵捲門車庫）。但同一次測試也
        發現一個問題：燈光規則原本用「例如提燈籠、賞月配月光」當範例
        說明「光線要跟活動時間符合邏輯」這個抽象規則，結果模型把範例
        本身當成真的要畫的內容，畫面裡憑空多出長者根本沒提過的燈籠——
        這違反懷舊治療最基本的原則（畫面只能反映長者真正的記憶，不能
        自己聯想加東西）。改成兩處修正：(1) 燈光規則不再用具體活動當
        範例，只講抽象邏輯本身；(2) 額外加一句防幻覺指示。

        2026-08-15（第二次）：第一版防幻覺指示寫成「只能畫畫面內容裡
        實際提到的東西」，矯枉過正了——長者原話通常很短（例如只提到
        車庫、家人、烤肉），逼模型只畫這幾樣，畫面變得像空舞台，缺少
        一般生活場景該有的環境雜物，反而不像「長者記憶中的真實場景」。
        問題出在沒有區分兩種「添加」：(a) 有敘事意義、代表長者記得某個
        具體細節的物件（例如燈籠——沒提到卻畫出來，等於暗示長者記得
        有燈籠，這才是真的失真）；(b) 純粹讓畫面有生活感的環境陳設
        （家具、植栽、雜物、光線氛圍——不代表任何「長者記得的具體
        事情」，只是場景本身合理該有的背景）。改成只禁止(a)，明講(b)
        可以自然補充，不用畫得過度乾淨/空曠。

        「不要人物」這句沒有寫死在這裡——呼叫端（_start_scene_after_
        detail）統一會套用 _strip_people_clauses／_add_no_people_
        directive，這裡如果自己也寫一次會被 _strip_people_clauses 的
        關鍵字咬到自己這句話，變成殘缺片段又被後面補一次完整版，兩段
        撞在一起。只在下游統一加一次，這裡不重複。
        """
        return (
            "水彩畫風格，1980年代台灣，懷舊溫暖色調，"
            "物件擺放要自然合理，"
            "光線必須跟「畫面內容」裡實際描述的時間點符合現實邏輯，不能"
            "互相矛盾，"
            "不要虛構長者沒提過的具體敘事內容或象徵物件（例如節慶裝飾、"
            "特定道具、看起來像是「長者記得的細節」的東西）——「畫面"
            "內容」段落裡沒提到的關鍵物件、地點、動作不要自己加；但"
            "可以自然補充一般生活場景常見的環境陳設（例如家具、植栽、"
            "雜物、光線氛圍），讓畫面有生活感，不用畫得過度乾淨、空曠，"
            "畫面中不要有任何文字。"
            f"今日主題：{user['today_topic']}。"
            f"畫面內容：{elder_detail}"
        )

    async def _extract_elements_from_detail(self, elder_detail: str) -> list[str]:
        """
        從長者原話（已去識別化）獨立抽取畫面元素清單，只給後續5W1H問題
        生成當錨點用（跟 image_prompt 內容脫鉤，見 _plan_image_from_detail
        說明）。只是給問題生成當錨點參考，不要求逐一涵蓋不遺漏，所以不像
        image_prompt 翻譯那樣需要checklist+重試機制，單次生成即可。
        """
        prompt = (
            f"長者剛才說：「{elder_detail}」\n\n"
            "列出這段話裡提到的具體畫面元素（大範圍實體物件、地點、明確"
            "指名的人物，例如「廟口」「腳踏車」「阿嬤」），只從這段話"
            "延伸，不要新增原文沒提到的元素。如果這段話沒有具體元素，"
            "就回傳空陣列。\n"
            "回傳一個JSON物件，格式：{\"elements\": [\"元素1\", \"元素2\"]}，"
            "只回JSON，不要任何說明文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        elements = [e for e in result.get("elements", []) if e and isinstance(e, str)]
        return filter_scene_elements(elements)

    async def _plan_image(self, user: dict, memories: list[dict] | None = None) -> dict:
        """
        請 LLM 規劃圖片元素與生圖 prompt，可傳入 memories 讓 LLM 從回憶挑元素。
        只有「沒有長者直接素材」的情況才會走到這裡（RAG舊記憶、或完全沒有
        記憶要用anchor自由發想）——長者這回合親口給的內容改走
        _plan_image_from_detail，見 _start_scene_after_detail 呼叫處說明。

        沒有記憶時，先用 _decide_scene_anchor 獨立判斷這次該用出生地+職業、
        純職業、還是純興趣當主場景。長者資料一律完整顯示所有欄位，只在
        【任務】那句指令裡明確告訴模型這次要用哪個情境。

        某些職業（例如導遊，本質就是「帶人看東西、介紹文化」）即使給了明確
        指令，仍可能把職業跟興趣兩個不相干的情境湊進同一張圖——這裡加一層
        事後偵測：elements/image_prompt 同時出現職業跟興趣的字面就重新生成
        一次，且只有這次重試才把沒被選中的那個欄位從長者資料整個拿掉
        （hide_field），physically 保證不會再犯。只重試一次，延遲上限可控。

        2026-08-14：有記憶時單次生成、只靠文字叮嚀「不要漏掉」不可靠，這裡
        加了對應的checklist+事後核對機制：先用 _extract_checklist_from_memories 從
        回憶內容獨立抽出細節清單，塞進 _build_plan_image_prompt 當硬性
        checklist；生成後用 _find_missing_memory_items 核對有沒有涵蓋，
        漏了就重新生成一次（只重試一次）。這個重試分支只在有記憶時觸發，
        跟上面「沒記憶時」的職業/興趣重試分支條件互斥，不會疊加。

        2026-08-14 實測發現：核對基準一開始用 elements+image_prompt 合併
        文字，出現過假陽性——elements只是短物件名詞清單，從來不會送去
        Stability AI（只有image_prompt會），曾經出現elements裡列了「剝
        柚子」「削蘋果」但image_prompt完全沒展開這兩件事的情況，卻因為
        字詞出現在elements裡就被判定「有涵蓋」，實際生出來的圖根本不會
        畫這兩個動作。改成只核對 image_prompt 本身，才是真正決定生圖
        內容的依據。
        """
        anchor = "hometown"  # 有記憶時錨點不影響結果，memory_section 優先權更高，這裡給預設值即可
        if not memories:
            anchor = await self._decide_scene_anchor(user)

        memory_checklist = (
            await self._extract_checklist_from_memories(memories) if memories else []
        )

        prompt = self._build_plan_image_prompt(
            user, anchor, memories, memory_checklist=memory_checklist,
        )
        # 有記憶時是「忠實延伸、不能創造」的任務，跟翻譯重構同理該用
        # temperature=0（見 _translate_detail_to_image_prompt 2026-08-14
        # 說明；實測 0.3 時 retry 曾憑空掰出回憶完全沒提到的「阿公泡茶」）。
        # 沒記憶時是給 anchor 自由發想情境，維持預設溫度不強制收斂。
        raw = await self.llm.ask(prompt, temperature=0 if memories else None)
        plan = self._extract_json(raw)
        plan["elements"] = filter_scene_elements(plan.get("elements", []))

        occupation = user.get("main_occupation", "")
        interest = user.get("preferences", "")
        if not memories and occupation and interest:
            combined = " ".join(plan.get("elements", [])) + " " + plan.get("image_prompt", "")
            if occupation in combined and interest in combined:
                # anchor 選了哪個，就拿掉沒被選中的那個候選欄位重試一次
                hide_field = "preferences" if anchor in ("hometown", "occupation") else "main_occupation"
                print(f"  → ⚠ 圖片元素同時混進職業（{occupation}）跟興趣（{interest}），"
                      f"重新生成一次（拿掉{hide_field}）: {plan.get('elements')}")
                prompt_retry = self._build_plan_image_prompt(
                    user, anchor, memories, hide_field=hide_field,
                )
                raw_retry = await self.llm.ask(prompt_retry)
                plan = self._extract_json(raw_retry)
                plan["elements"] = filter_scene_elements(plan.get("elements", []))

        if memory_checklist:
            missing = await self._find_missing_memory_items(
                memory_checklist, plan.get("image_prompt", "")
            )
            if missing:
                print(f"  → ⚠ 圖片規劃漏掉回憶細節 {missing}，重新生成一次")
                prompt_retry = self._build_plan_image_prompt(
                    user, anchor, memories, memory_checklist=memory_checklist,
                ) + (
                    f"\n\n【上一次生成遺漏了以下回憶細節，這次務必補上，並"
                    f"保持其他元素原有的完整性】：{'、'.join(missing)}\n"
                )
                raw_retry = await self.llm.ask(prompt_retry, temperature=0)
                plan = self._extract_json(raw_retry)
                plan["elements"] = filter_scene_elements(plan.get("elements", []))

        return plan

    async def _extract_checklist_from_memories(self, memories: list[dict]) -> list[str]:
        """
        從【長者相關回憶】內容獨立抽出「有畫面感的具體細節」清單，只在
        _plan_image 有記憶分支當checklist生成/驗證用。

        2026-08-14 實測發現：清單第一版切得太細——把同一組人物+動作拆成
        好幾個獨立單詞（例如「阿公在門口泡茶」拆成「門口」「泡茶」兩項，
        「小孩子提著燈籠到處跑」拆成「小孩子」「提著燈籠」「到處跑」三
        項），項目數暴增到單場記憶10幾個人共20項，遠超過一張圖能畫的
        份量，重試一次也補不齊。改成要求每項都合併成一個完整的「人物+
        動作」或「地點+活動」短語（不能再拆），項目數才會貼近實際能塞進
        一張圖的量。

        2026-08-14 討論後拿掉「排除主題名稱本身」這條規則——原本是照抄
        _extract_elements_from_detail 第2點，但那條規則的理由是「主題
        名稱不適合當5W1H追問錨點」，只對該函式的下游用途（生成追問問題）
        成立，對這裡的下游用途（image_prompt覆蓋率checklist）不適用。
        _build_plan_image_prompt 的 anchor 分支本來就明講「【今日主題】
        本身如果是像節慶這種有清楚視覺意象的詞，可以直接當一個元素
        使用」，這裡排除反而不一致；而且長者回憶原文常常就直接提到主題
        本身（例如回憶原文寫「以前中秋節,全家人...」），排除掉等於漏記
        了長者原話真的提到的內容。
        """
        memory_text = "\n".join(
            m.get("summary", m.get("text", "")) for m in memories
        )
        prompt = (
            "格式示範（跟下面的長者回憶內容完全無關，只是示範怎麼把細節"
            "合併成一個短語，絕對不要把示範裡的人物或動作本身當成真實"
            "內容抄進輸出）：如果回憶提到「阿伯在海邊撿貝殼」「小朋友在"
            "沙灘上堆城堡」，就各自算一項，不要拆成「海邊」「撿貝殼」"
            "或「小朋友」「沙灘」「堆城堡」。\n\n"
            f"長者相關回憶（下面才是這次真正要處理的內容）：\n{memory_text}"
            "\n\n"
            "列出上面這段回憶裡實際提到的具體細節，每一項都要合併成一個"
            "完整的「人物+動作」或「地點+活動」短語，不要再往下拆成單一"
            "物件或單一動詞。只能使用回憶原文裡真的出現過的人物、地點、"
            "動作，絕對不要把上面格式示範的內容當成回憶內容輸出。不要"
            "新增回憶沒提到的東西，也不要遺漏任何一組人物/動作，但同一"
            "個人的連續動作只算一項。如果回憶原文有直接提到節慶/場合"
            "名稱本身，這個名稱也要列成一項。每一項都必須用繁體中文"
            "輸出，不要用英文，即使回憶原文有中英夾雜也一律轉成中文。\n"
            "回傳一個JSON物件，格式：{\"items\": [\"細節1\", \"細節2\"]}，"
            "只回JSON，不要任何說明文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        return [i for i in result.get("items", []) if i and isinstance(i, str)]

    async def _find_missing_memory_items(
        self, items: list[str], image_prompt: str
    ) -> list[str]:
        """
        核對 items（回憶細節清單）裡每一項是否都能在 image_prompt（真正
        送去 Stability AI 生圖的英文文字，elements只是短物件名詞清單，
        不會送去生圖，不能拿來當核對基準，見 _plan_image 2026-08-14
        說明）裡找到對應的具體描述，回傳沒涵蓋到的項目（原始中文詞）。
        做法採「逐項要求列出對應證據」模式：找不到就必須明講「找不到」，
        不直接問yes/no整體判斷；漏掉的判定交給共用的 _missing_from_checks
        （見該函式說明）——實測10項清單一次核對時，這裡出現過兩種問題：
        (1) 模型把畫風樣板字"no text"硬套成「狗在旁邊追來追去」這種
        完全沒畫進去的項目的證據，(2) 「哥哥剝柚子」「姊姊削蘋果」被
        套用「小孩提燈籠」「打麻將」的證據句，甚至有一項在checks陣列
        裡直接消失、連檢查都沒做。
        """
        items_str = "、".join(items)
        prompt = (
            f"回憶提到的細節清單：{items_str}\n\n"
            f"以下是根據這份回憶生成的英文生圖prompt：\n{image_prompt}\n\n"
            "請逐一檢查清單裡的每一項，在上面這段文字裡找出對應的具體"
            "文字片段當作證據（不需要逐字對應，語意上有對應到、且有"
            "具體畫面細節即可）。如果真的找不到任何對應片段，evidence"
            "欄位就必須填「找不到」這三個字，不要為了湊答案硬找不相關的"
            "片段當證據。\n"
            "回傳一個JSON物件，格式：\n"
            "{\"checks\": ["
            "{\"item\": \"細節原文\", "
            "\"evidence\": \"對應的具體文字片段，或「找不到」\"}"
            "]}\n"
            "checks陣列順序要跟原文清單一致，每一項都要有一筆，"
            "不能省略，每筆都一定要有evidence欄位。只回JSON，不要任何"
            "說明文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        checks = result.get("checks", [])
        return _missing_from_checks(items, checks, "item", image_prompt)

    async def _generate_question(
        self,
        step: str,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        scene_composition: str = "",
        elder_response: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
    ) -> dict:
        """
        生成 STEP1 開場問題（不含場景文字，畫面一律由別處負責出示）。
        prompt 格式對齊 dpo/collect_data.py build_inference_prompt（Track A）。
        Returns: {"question": str, "covered_w": list[str]}

        retry_feedback: guarded_generate 偵測到上一次輸出違規時傳入的具體說明，
            見 _retry_feedback_section。

        2026-08：這裡原本有 memory_section（直接把記憶文字塞進問題生成
        prompt），實測是null result甚至有違規訊號，且這個欄位從沒進過DPO
        訓練資料，train/serve本來就不對齊。個人化已經由 _plan_image 的
        記憶→畫面元素這條路徑達成（有驗證過、且元素本來就走模型訓練過的
        elements_str 格式），這裡不需要再重複做一次，故拿掉。
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "問題必須念起來自然、溫和、不超過25個字，且開頭要包含畫面中看得到的具體物件。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        topic_str    = user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"

        elder_section = f"\n【長者剛才說的話】\n{elder_response}\n" if elder_response else ""

        user_content = (
            f"【長者資料】\n"
            f"姓名：{user['name']}\n"
            f"職業背景：{user['main_occupation']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"興趣：{user.get('preferences') or '無'}\n"
            f"懷舊治療主題類別：{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"{elder_section}"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n{_STEP_TASKS[step]}\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"思考：（主題判斷：一句話判斷今日主題最貼近哪個核心主題；"
            f"切入角度：一到兩句話決定這題要用什麼當錨點、往哪個方向問——"
            f"兩段都要寫，不會念給長者聽）\n"
            f"問題：（≤25字，開放式，開頭要有畫面中的具體物件）\n"
            f"問題類型：{_STEP_TYPE_LABEL[step]}\n"
            f"本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個W維度名稱本身，"
            f"不要自創其他詞彙、不要加括號說明）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        # 不能沿用 _parse_question_response——那支函式現在專門服務STEP3補問，
        # 會找「承接語：」這個標籤、在scene_text是空字串時印警告、塞一句保底
        # 承接語；STEP1開場本來就不產出承接語/場景文字，不是解析失敗，用專屬
        # parser 避免誤判成錯誤。
        return self._parse_step1_response(raw, scene_elements=scene_elements)

    def _parse_step1_response(
        self, raw: str, scene_elements: list[str] | None = None,
    ) -> dict:
        """
        解析 _generate_question 的輸出（只有問題＋涵蓋的W，沒有場景文字——
        STEP1開場的畫面一律由別處負責出示，見 question_5w1h.txt）。不能沿用
        _parse_question_response——那支函式在 scene_text 是空字串時會印警告、
        塞一句保底場景文字，這裡場景文字本來就故意沒請求，不是解析失敗。
        """
        result: dict = {"question": "", "covered_w": []}
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif line.startswith("本回合已涵蓋的W："):
                w_raw = _strip_leaked_brackets(line[len("本回合已涵蓋的W："):].strip())
                result["covered_w"] = [
                    w.strip()
                    for w in w_raw.replace("，", "、").split("、")
                    if w.strip()
                ]
                current_field = None
            elif line.startswith("問題類型："):
                # 這欄不儲存，但要停止把後面的行接到問題——STEP1格式裡「問題類型：」
                # 緊接在「問題：」後面，沒有這行會被上面「問題：」設下的 current_field
                # 一路吃下去，變成「問題類型：STEP1開場」整句被接到問題文字後面，
                # 汙染送給長者的內容（同 _parse_question_response 既有的防線，這裡原本
                # 漏掉了，2026-08稽核補上）。
                current_field = None
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        result["question"] = _strip_leaked_brackets(result["question"])
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        return result

    async def _generate_image_reveal_reaction(
        self,
        user: dict,
        scene_elements: list[str],
        elder_response: str,
        scene_composition: str = "",
        covered_w: list[str] | None = None,
        pre_image_detail: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
        already_deferred: bool = False,
    ) -> dict:
        """
        長者看完剛生成的圖、說出第一反應（回答 _IMAGE_REVEAL_QUESTION）後：
        依反應類型生成承接語，再接上 STEP1 開場問題——這一步取代原本
        _start_scene_after_detail 生圖後立刻問 STEP1 的做法，中間插入「出示
        圖片＋留白＋依反應承接」這一輪（見本檔頂部流程說明、
        _start_scene_after_detail）。

        承接語分4類，只有前3類＋「情緒明顯（感動）」會走到這裡——「情緒明顯
        （不安）」這類已經被 process_response 最前面的 _detect_emotional_
        trigger／Track B 攔截走了（那條規則本來就是為了先於一切分支處理
        需要優先安撫的情緒訊號），不會進到這支函式：
          1. 圖跟長者記得的一致 → 肯定
          2. 有差異，但長者還沒具體講出哪裡不一樣 → 好奇追問哪裡不一樣，
             不評價對錯、不承諾修圖
          3. 有差異，且長者已經具體講出哪裡不一樣了 → 不要重複問「哪裡不
             一樣」，改成誠實承認AI示意圖本來就有畫不出來的限制，不說
             「沒關係」「不重要」這種輕描淡寫的話（2026-08稽核：分類2/3
             原本用「語氣平靜/在意」區分，但這是主觀語氣判斷，本地模型不
             穩定；長者是否已經主動講出具體差異是客觀可判斷的信號，也
             同時解掉了「長者已經回答過『哪裡不一樣』、承接語卻還是重問
             一次」這個曾經發生過的問題）
          4. 長者明顯被觸動、感動 → 直接承接這份情緒，不急著轉開話題

        承接語之後由這裡生成的 question 帶進 STEP1 開場問題（不是 LLM生成，
        呼叫端組裝）。分類2（好奇追問哪裡不一樣）的承接語本身就是要長者
        回答的問題，呼叫端（process_response 的 image_reveal 分支）會攔
        下來，這一輪只問承接語本身、不接過渡句也不接這裡生成的 question，
        見該分支 2026-08-16 稽核說明與 image_reveal_deferred 欄位。

        _IMAGE_REVEAL_TRANSITION（「謝謝你跟我說這麼多...」，見該常數
        說明）只有在長者這句話是回答分類2追問的「哪裡不一樣」時
        （process_response 的 was_deferred）才會接在承接語後面——這句話
        內容上是在謝長者「說了這麼多」，只有長者真的在回答分類2追問時才
        對得上。分類1/3/4若是第一輪就直接命中（沒經過分類2追問），長者
        講的通常很簡短，不接這句過渡句，直接接 STEP1 問題（見該分支
        2026-08-16 第二次稽核說明）。

        故意不用 scene_text 當 key（沿用 closing_text/emotional_text 的既有
        模式）：承接語本來就要直接對長者說話、會用到「你」，用專屬 key 名稱
        天然繞開只檢查 "scene_text" 這個 key 的「不能用你」誤判。

        covered_w: 生圖前訪談（Q1/Q2）已經自然涵蓋的W維度（見 _start_scene_
            after_detail 的 _detect_covered_w 呼叫）。長者剛才在訪談裡可能
            已經講過某個W方向，這裡生成的STEP1問題要避開，不要問長者剛講過
            的內容，跟 _generate_question 的「已涵蓋的W維度」用途相同。

        pre_image_detail: 長者生圖前Q1/Q2訪談的原話。這裡的「承接語」欄位
            職責仍然是回應長者「對圖片」的反應，不重複去呼應這段訪談內容
            （那是 _generate_quick_end_recap 專門處理的情境，長者已經在
            這裡的反應裡表達過意見了，不用疊兩次呼應）；但緊接著要生成的
            STEP1問題，選錨點/切入角度時可以參考這段訪談內容，讓問題更貼近
            長者剛才實際講過的人事物，不是只能從畫面元素或圖片反應本身延伸。

        retry_feedback: 見 _generate_question 的同名參數說明。

        emotion: Kinect 即時偵測的情緒（見 process_response 的同名參數）。
            2026-08稽核發現：這支函式先前完全沒有這個參數，長者剛看完AI畫出來
            的回憶圖說出第一反應，正是全流程裡最需要情緒敏感度的時刻之一（見
            上面第4類「明顯被圖片觸動、感動」），但語氣指引完全沒用上Kinect
            訊號，跟STEP1/STEP2/STEP3/收尾語都會注入 _emotion_guidance() 不
            一致，這裡補上，讓4類反應分類的措辭也能參考長者當下的即時情緒。

        2026-08稽核：這裡原本用完全獨立的手寫system_content，沒有載入
        question_5w1h.txt——代表這裡生成的STEP1開場問題完全沒套用到檔案裡
        「先選角度、後選錨點」的五步驟流程、五個切入角度優先順序、避免
        過程步驟陷阱的邏輯。改成跟 _generate_supplement_question（STEP3）
        一樣載入整份檔案當system_content，任務本身的細節（4類反應分類）
        移到user_content的【任務】欄位——這個組合方式STEP3已經驗證過
        可行：檔案本身描述的是STEP1/STEP2/STEP3各自的格式，跟這裡實際要
        產出的格式不完全一樣沒關係，user_content自己的【輸出格式】欄位
        會蓋過去，跟STEP3同一個模式。

        2026-08-16稽核：4類分類判斷不準，改成先列「判斷依據」再輸出「分類」
        數字、最後才寫「承接語」（跟本檔其他地方用「證據式核對」取代小模型
        直接下整體判斷的模式一致，見 _has_usable_detail、_decide_topic_
        continuation 稽核筆記——8B量化基底模型對整體判斷穩定漏判/誤判，
        改成逐項列證據後才穩定下來）。
        原本分類完全隱含在承接語的措辭裡，沒有獨立欄位，出錯時無從得知
        模型是分類錯還是措辭沒依照分類寫；現在拆出可解析、可記錄的欄位，
        且強迫模型先講出線索再下結論，同一次呼叫內完成，不額外增加一次
        LLM 呼叫延遲。「判斷依據」「分類」目前只用來記錄／除錯，不影響
        後續流程（承接語才是真正念給長者聽的內容），parser 見
        _parse_image_reveal_response。

        2026-08-16稽核（第二次，實測後補）：本機基底模型（未經DPO）實測
        6例，只有1例真的輸出「判斷依據／分類」這兩個新欄位，其餘5例都
        跳過、改用模型自己習慣的「思考：」自由格式開頭——代表它有先推理
        的傾向，但沒對齊我們要的欄位名稱；還有一例把分類4的例句整句原封
        不動照抄，違反下面的「不可照抄」規則。补上一組完整的【範例】區塊
        （用跟本次任務無關的情境示範判斷依據/分類/承接語/問題四欄位一起
        長什麼樣子），具體示範通常比純文字規則更能提高本地小模型的格式
        遵循度；範例情境刻意跟任何真實場景不同，降低被照抄整句的風險。

        2026-08-16稽核（第三次，實測後補）：長者說「還好」這種簡短/籠統的
        反應時，模型會生成「喔，『還好』是嗎，你記得的畫面跟這張有點不同，
        可以多說說看嗎。」這種把長者原話用引號複述、後面接反問語尾的句子
        ——中文語境下這樣講聽起來像是在調侃/質疑長者，不是溫暖承接。追查
        後發現源頭是【範例】1原本示範的承接語就是「喔，是嗎，...」這個
        開頭，模型照樣套用、把長者的原話填進引號裡。改掉範例1的措辭，並
        在【範例】區塊後面明文加一條規則禁止這種「引號複述＋反問語尾」的
        寫法。

        2026-08-16稽核（第四次，實測後補）：長者說「我覺得滿像當時的場景
        的」（明確的正面相似訊號）被誤判成分類2，模型自己寫的「判斷依據」
        卻是「沒有提到具體差異或情緒線索」——推理本身是對的，結論卻選錯。
        追查發現分類1原本的線索範例只列了「對/沒錯/就是這樣」，完全沒有
        「像/很像/差不多」這類同樣是肯定訊號的詞，而分類2的線索範例裡有
        「不像」，「像」這個字元跟「不像」表面相近，容易被誤歸進分類2。
        補上「像/很像/差不多」到分類1的線索範例跟例句，並明文加一句話
        區分「像」（肯定，分類1）跟「不像」（差異，分類2）意思完全相反，
        不要只靠字面出現「像」這個字就聯想到分類2。

        2026-08-16稽核（第五次）：分類1補上的例句原本開頭是「對，就是這種
        感覺」，這句話等於把長者剛才自己講的肯定詞（「對」「就是這樣」）
        原句複述回去，聽起來像鸚鵡學舌，不是真的在往下接話——長者都已經
        自己確認過了，不需要AI再附和一次同樣的話。改成不開頭複述肯定詞，
        直接用自己的話表達溫暖呼應。

        2026-08-17稽核（實測後補）：長者已經被分類2追問過一次「哪裡不一樣」，
        這次回答如果模型還是判成分類2，process_response 那邊依「只擋一次」
        設計會強制往下走進STEP1，不會再追問——但這支函式當時完全不知道
        「這是追問過一次之後的回答」，寫出的承接語還是「哪裡不一樣呢，可以
        多說一點嗎」這種要長者回答的問句，接到後面的過渡句＋新STEP1問題，
        變成「問完馬上道謝、又問下一題」，長者根本沒機會回答，語意不通
        （這是使用者實測抓到的bug）。新增 already_deferred 參數，讓提示詞
        知道這個情境、引導模型直接寫分類3語氣的承接語；但呼叫端
        process_response 不完全相信這裡的輸出——如果分類仍回傳2，會用固定
        句子覆蓋承接語，不賭這次LLM有沒有照做（見該分支說明），這裡的提示詞
        只是讓分類本身更準確、盡量讓LLM自己就選對分類3，不是唯一防線。

        2026-08-17稽核（第六次，實測後補）：長者說「我覺得蠻像我印象中的
        樣子」（明確肯定訊號）連續發生兩種錯誤：(1) 第一次呼叫被 ResponseGuard
        攔下，「判斷依據」欄位引用了長者沒說過的話「這個好像跟我印象不太
        一樣」——這句話跟【範例】1原本示範的長者反應文字一字不差，本地
        模型很可能是被範例裡也出現「印象」兩個字錨定、直接把範例內容當這次
        長者說的話照抄，即使規則已經明講不可以照抄範例。把範例1的措辭改掉
        （拿掉「印象」，改用「咦，好像不太一樣耶」），降低跟真實輸入撞詞的
        機率。(2) 重生成後正確引用了原話，卻還是判成分類2，判斷依據寫「沒有
        講出是哪裡不同」——上面第四次稽核補的清單只列了「像/很像/差不多」，
        沒有「蠻像」「滿像」，模型沒把這兩個詞跟已知的肯定詞歸為同一類。
        補上「蠻像」「滿像」到清單跟分類1說明裡。

        但只補列舉的詞治標不治本——長者的話是STT轉出來的文字，不保證
        百分百正確，同音字誤植/漏字隨時可能讓真實輸入剛好沒對到清單上任何
        一個詞（例如這次「蠻像」剛好沒列到），照這種模式每次都得等實測抓到
        新詞才補一個，補不完。額外加一條通用規則：明講清單只是舉例、不是
        窮舉，要求模型看整句語意（肯定/相似 vs 差異/不同）判斷，不要求
        逐字比對到清單上的詞才算數，同時提醒STT可能有同音錯字，觀念上更
        貼近之後遇到清單沒列到的新詞或錯字時也不該誤判。

        2026-08-17稽核（第七次，實測後補）：長者說「很有當時烤肉的氛圍」，
        正確判成分類4（情緒觸動），承接語也寫得很好「聽你這樣說，感覺你跟
        家人一起烤肉的回憶真的很溫暖」——但緊接著的STEP1問題卻是「你們通常
        在烤什麼肉呢」，從溫暖的情感語氣突然掉回中性的事務性細節，聽起來
        兩句像在講不同的事，跟分類4「不急著轉開話題」的原則自相矛盾。追查
        發現問題出在範例本身：【範例】2（阿嬤想念情境）的問題原本就是「阿嬤
        常聽的收音機節目是什麼」，也是同一種「情緒承接→突然問中性細節」的
        落差，模型等於是照著範例的模式走。補上明文規則（分類4的問題不能跳去
        中性事務性細節，要順著同一份情緒/同一個人/同一件事繼續問），並把
        範例2的問題改成延續情緒的版本，源頭示範對了，才不會一直讓模型有
        「反正範例都這樣寫」的藉口。

        2026-08-17稽核（第八次，實測後補）：長者說「我覺得有點不像，因為我家
        不是用那種桌子跟椅子，但那個氛圍有出來」——已經明確點名「桌子跟椅子」
        這個具體差異，該判成分類3，模型卻還是判成分類2、判斷依據寫「還沒
        具體講出是哪裡不一樣」，又重複問了一次「哪裡不一樣」，長者等於白說
        了一次。追查發現分類3的說明跟範例都只圍繞「位置」（例句是「烤肉的
        地方是在前面」），完全沒有「桌椅家具」這類物品/擺設款式的例子，跟
        之前分類1漏列「蠻像」同一種模式——模型過度依賴範例裡出現過的具體
        詞類，沒有真的類推到「擺設」這個抽象類別涵蓋的其他物品。補上「不限
        地點」跟「桌椅家具、物品款式」到分類3的線索與例句裡，明講只要點名
        了具體人事物就算，不是只有地點才算。
        """
        already_deferred_note = (
            "\n【重要】長者已經被追問過一次「哪裡不一樣」了，這是他這次的"
            "回答——不管這次的差異講得夠不夠具體，都不能再判成分類2、不能"
            "再寫一句要長者回答「哪裡不一樣」的問句，一律比照分類3的原則："
            "誠實承認AI示意圖本來就有畫不出來的限制，肯定長者記得的畫面比"
            "圖裡的還要豐富（除非長者這次的反應明顯是分類4的情緒觸動，才"
            "判成4）。\n"
            if already_deferred else ""
        )
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "問題必須念起來自然、溫和、不超過25個字，開頭要有具體錨點（畫面中看"
            "得到的物件、長者提到的具體人事物皆可）。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        taboo_str = "、".join(user["taboos"]) if user["taboos"] else "無"
        covered_str = "、".join(covered_w) if covered_w else "無"
        pre_image_str = pre_image_detail or "無"

        user_content = (
            f"【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【長者看完圖後的第一反應】\n{elder_response}\n"
            f"\n【長者生圖前分享的內容】\n{pre_image_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"{already_deferred_note}"
            f"\n【任務】\n"
            f"長者剛看完AI依據他先前說的內容生成的一張示意圖，說出了他的第一"
            f"反應。請先具體列出這句反應裡有哪些線索（例如：出現「對/沒錯/"
            f"就是這樣/像/很像/蠻像/滿像/差不多」這類肯定或相似詞、或「不"
            f"像/不一樣/有點不同」這類籠統差異詞、或位置/顏色/擺設等具體"
            f"細節詞、或明顯的情緒字眼），再依這些線索判斷屬於下面哪一種，"
            f"最後用對應的原則寫一句承接語：\n"
            f"（注意：「像」「很像」「蠻像」「滿像」「差不多」單獨出現、"
            f"前面沒有「不」字，是肯定圖跟記憶一致的正面訊號，要判成分類1，"
            f"不要因為字面上出現「像」這個字，就聯想到分類2的「不像」，"
            f"兩者意思完全相反；只有明確帶「不」字的「不像」，或整句語氣是"
            f"在指出落差，才算分類2的線索）\n"
            f"（注意：上面列的詞都只是常見例句，不是要逐字比對的完整清單。"
            f"【長者看完圖後的第一反應】是語音辨識（STT）轉出來的文字，"
            f"可能有同音字誤植、漏字或跟例句用詞不完全一樣（例如「蠻像」"
            f"被辨識成「慢像」「满相」之類的同音錯字），只要整體語意上是"
            f"在表達肯定/相似，就算分類1的線索，不要因為沒有出現清單上的"
            f"精確字詞就判定不算；同理，只要語意上是在表達差異/不同，就算"
            f"分類2或3的線索，一律以整句語意為準，不是比對字面。）\n"
            f"1. 覺得圖跟自己記得的一致 → 肯定的語氣呼應，不要把長者剛才的"
            f"肯定詞（例如「對」「就是這樣」）原句複述回去，那樣像鸚鵡學舌；"
            f"改用自己的話接，例如「聽你這樣說，我彷彿也看到了當時的畫面"
            f"。」（長者說「滿像的」「蠻像我印象中的樣子」「很像」「差不多"
            f"就是這樣」都屬於這一類）\n"
            f"2. 覺得圖跟自己記得的不一樣，但還沒具體講出是哪裡不一樣（只"
            f"籠統說「不像」「不一樣」「有點不同」，例如「跟我的印象不太"
            f"像」）→ 好奇追問哪裡不一樣，不評價對錯、不承諾要修改圖片，"
            f"例如「喔？哪裡不一樣呢，我很想知道你記憶中的樣子，可以多說"
            f"一點。」\n"
            f"3. 覺得圖跟自己記得的不一樣，而且已經具體講出是哪裡不一樣"
            f"（不限地點，只要點名了具體的人事物、擺設、細節都算，例如位置、"
            f"顏色、擺設、桌椅家具、物品款式等，例如「烤肉的地方是在前面」"
            f"「我家不是用那種桌子跟椅子」）→ 長者已經自己講出具體差異了，"
            f"不要重複問「哪裡不"
            f"一樣」，改成誠實承認AI示意圖本來就有畫不出來的限制，不要說"
            f"「沒關係」「不重要」這種輕描淡寫的話，肯定長者記得的畫面比"
            f"圖裡的還要豐富，例如「這張圖確實沒辦法把每個細節都畫得剛剛"
            f"好，聽你這樣說，你記得的畫面比圖裡的還要豐富。」\n"
            f"4. 明顯被圖片觸動、感動 → 直接承接這份情緒，不急著轉開話題，"
            f"例如「這段回憶對你來說真的很重要，謝謝你願意跟我分享。」；緊接著"
            f"的問題也不能突然跳去中性、事務性的細節（例如吃什麼、幾點、什麼"
            f"牌子），那樣會讓剛才的溫暖語氣顯得斷裂、像在應付——問題要順著"
            f"同一份情緒、同一個人或同一件事繼續往下問，讓長者能多說一點這份"
            f"感觸本身（例如長者提到想念阿嬤，就問阿嬤留給他印象最深的是什麼"
            f"樣子，不要突然問阿嬤平常聽什麼收音機節目）\n"
            f"\n【範例】（跟這次任務完全無關的另一組情境，只是示範輸出格式"
            f"長什麼樣子——判斷依據/分類/承接語/問題都要照這個順序、這個"
            f"欄位名稱輸出）\n"
            f"範例1（眼前畫面元素：稻田、扁擔、斗笠；長者反應：「咦，好像"
            f"不太一樣耶」）\n"
            f"判斷依據：長者只籠統說「不太一樣」，沒有講出是哪裡不同。\n"
            f"分類：2\n"
            f"承接語：你記得的畫面好像跟這張有點不一樣，我很想知道是哪裡"
            f"不同，可以多說一點嗎。\n"
            f"問題：扁擔挑的稻穀通常要挑去哪裡？\n"
            f"本回合已涵蓋的W：無\n"
            f"範例2（眼前畫面元素：大灶、柴火、老收音機；長者反應：「阿嬤"
            f"以前常在灶前聽收音機，看到這個我好想她」）\n"
            f"判斷依據：長者提到已故的阿嬤，語氣裡有明顯的想念與情緒。\n"
            f"分類：4\n"
            f"承接語：聽你這樣說，感覺阿嬤陪你的那些時光都還在心裡。\n"
            f"問題：阿嬤陪你的那些時光裡，最讓你想念的是哪個畫面呢？\n"
            f"本回合已涵蓋的W：無\n"
            f"（以上4句分類例句跟上面2個範例的所有內容——判斷依據、承接語、"
            f"問題——都只是示範語氣跟格式用，情境也跟這次任務無關，不是可以"
            f"直接照抄的答案。這次的判斷依據、承接語、問題必須根據長者這次"
            f"實際說的反應內容跟【眼前畫面元素】重新寫，禁止把上面任何一句"
            f"原封不動搬過來用。）\n"
            f"承接語絕對不要把長者剛才說的原話用「」引號整段複述出來、後面"
            f"接「是嗎」「呢」這種反問語尾（例如「你剛才說『還好』是嗎」）"
            f"——長者的反應如果本來就簡短、籠統（例如「還好」「沒有」），"
            f"這樣把他的話原句引用再反問，聽起來會像是在質疑或調侃長者，"
            f"不是溫暖的承接。要具體呼應時，改用自己的話轉述長者的意思"
            f"（例如「聽起來你覺得還好，不算特別不一樣」），不要用引號原句"
            f"複述。\n"
            f"承接語只要1-2句、30字以內。承接語之後，緊接著問長者一個新問題"
            f"——依【STEP2自由追問／STEP3補問：生成流程】的選角度、選錨點方式"
            f"生成（先依五個切入角度優先順序選方向，再從【眼前畫面元素】或"
            f"【長者生圖前分享的內容】裡找一個能撐起這個角度的具體人事物當"
            f"錨點，接不上就換角度重選，不要硬套）。若上面列出【已涵蓋的W"
            f"維度】，這些方向長者剛才在生圖前的訪談裡已經自然講過了，這題"
            f"不要重複問同一個方向。另外，如果【長者看完圖後的第一反應】裡"
            f"長者已經主動講出某個人事物的具體細節（例如位置、顏色、數量），"
            f"這題不要再問同一個細節（例如長者剛說「柚子樹在門口左邊」，"
            f"就不要再問「柚子樹在哪裡」），換一個角度、或換一個錨點問。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"判斷依據：（一句話列出你在長者這句反應裡看到的具體線索，不要空泛"
            f"帶過）\n"
            f"分類：（只能填1、2、3、4其中一個數字，對應上面4種分類）\n"
            f"承接語：（1-2句，30字以內，依上面判斷依據與分類撰寫）\n"
            f"問題：（≤25字，開放式，開頭要有具體錨點，畫面物件或長者生圖前分享的"
            f"內容皆可）\n"
            f"本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個"
            f"W維度名稱本身，不要自創其他詞彙、不要加括號說明）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_image_reveal_response(raw, scene_elements=scene_elements)

    async def _regenerate_image_reveal_question(
        self,
        user: dict,
        scene_elements: list[str],
        elder_response: str,
        scene_composition: str = "",
        covered_w: list[str] | None = None,
        pre_image_detail: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
        already_deferred: bool = False,
    ) -> dict:
        """
        guarded_generate 的 question_only_retry_fn（見該參數說明），專供
        _generate_image_reveal_reaction 使用：問題那半段違規、但承接語已經
        通過所有檢查時，只重新生成問題本身，不重新賭一次承接語的4類分類。

        問題的生成邏輯本來就跟承接語選中哪一類分類無關（原本那支函式的
        【任務】說明裡，承接語分類規則寫在前面，問題怎麼選角度/錨點是
        完全獨立的一段），這裡直接複用同一套「選角度、選錨點」邏輯，只是
        拿掉4類分類那部分的任務說明和「承接語：」輸出欄位。

        參數跟 _generate_image_reveal_reaction 對齊（guarded_generate 用
        同一份 call_kwargs 呼叫兩者，簽名必須相容，否則會 TypeError）——
        elder_response、already_deferred 這裡雖然不再用來分類，仍保留參數
        位置，不使用。
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "問題必須念起來自然、溫和、不超過25個字，開頭要有具體錨點（畫面中看"
            "得到的物件、長者提到的具體人事物皆可）。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        taboo_str = "、".join(user["taboos"]) if user["taboos"] else "無"
        covered_str = "、".join(covered_w) if covered_w else "無"
        pre_image_str = pre_image_detail or "無"

        user_content = (
            f"【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【長者生圖前分享的內容】\n{pre_image_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"請問長者一個新問題——依【STEP2自由追問／STEP3補問：生成流程】"
            f"的選角度、選錨點方式生成（先依五個切入角度優先順序選方向，再從"
            f"【眼前畫面元素】或【長者生圖前分享的內容】裡找一個能撐起這個"
            f"角度的具體人事物當錨點，接不上就換角度重選，不要硬套）。若上面"
            f"列出【已涵蓋的W維度】，這些方向長者剛才在生圖前的訪談裡已經"
            f"自然講過了，這題不要重複問同一個方向。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"問題：（≤25字，開放式，開頭要有具體錨點，畫面物件或長者生圖前分享的"
            f"內容皆可）\n"
            f"本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個"
            f"W維度名稱本身，不要自創其他詞彙、不要加括號說明）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_question_only_response(raw, scene_elements=scene_elements)

    def _parse_question_only_response(
        self, raw: str, scene_elements: list[str] | None = None,
    ) -> dict:
        """解析 _regenerate_image_reveal_question 的輸出（問題＋涵蓋的W，沒有承接語欄位）。"""
        result: dict = {"question": "", "covered_w": []}
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif line.startswith("本回合已涵蓋的W："):
                w_raw = _strip_leaked_brackets(line[len("本回合已涵蓋的W："):].strip())
                result["covered_w"] = [
                    w.strip()
                    for w in w_raw.replace("，", "、").split("、")
                    if w.strip()
                ]
                current_field = None
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        result["question"] = _strip_leaked_brackets(result["question"])
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        return result

    async def _generate_quick_end_recap(
        self,
        user: dict,
        scene_elements: list[str],
        pre_image_detail: str,
        scene_composition: str = "",
        covered_w: list[str] | None = None,
        emotion: str = "happy",
        retry_feedback: str = "",
    ) -> dict:
        """
        長者看完剛生成的圖，沒有給出真正可分類的反應（quick_end，例如「沒有」
        「還好」）——但生圖前的Q1/Q2訪談（pre_image_detail）通常有實質內容，
        不能因為長者對圖片本身沒反應，就讓STEP1問題前面接一句完全跟長者剛才
        說的話無關的固定過渡句（原本這裡固定用 _IMAGE_REVEAL_QUICK_END_ACK），
        那樣長者會覺得AI沒在聽他剛才說的話。

        跟 _generate_image_reveal_reaction 不同：那支函式回應的是長者「對
        圖片」的反應，要分類成4種類型；這裡沒有反應可以分類，單純簡短具體
        呼應 pre_image_detail 的內容，再接上STEP1開場問題——只在
        pre_image_detail 非空時才會呼叫這裡（見 process_response 的
        image_reveal quick_end 分支），pre_image_detail 是空的（Q1/Q2也沒
        講出什麼有用內容、生圖時已經退回RAG記憶）時沒有東西可以具體呼應，
        維持原本的固定過渡句就好。

        covered_w: 同 _generate_image_reveal_reaction 的用途，避免STEP1
        問題跟Q1/Q2已經自然涵蓋的W維度重複。

        emotion: 見 _generate_image_reveal_reaction 的同名參數說明——2026-08
        稽核發現這裡原本也完全沒有這個參數，一併補上，跟其他生成函式一致。

        輸出格式跟 _generate_image_reveal_reaction 相同（承接語／問題／
        本回合已涵蓋的W），沿用同一支 parser。

        2026-08稽核：理由同 _generate_image_reveal_reaction 那份稽核筆記——
        原本這裡也是完全獨立的手寫system_content，沒套用question_5w1h.txt
        裡「先選角度、後選錨點」的邏輯，改成一致的做法：載入整份檔案當
        system_content，任務細節移到user_content的【任務】欄位。
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "問題必須念起來自然、溫和、不超過25個字，開頭要有具體錨點（畫面中看"
            "得到的物件、長者提到的具體人事物皆可）。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        taboo_str = "、".join(user["taboos"]) if user["taboos"] else "無"
        covered_str = "、".join(covered_w) if covered_w else "無"

        user_content = (
            f"【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【長者生圖前分享的內容】\n{pre_image_detail}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"長者剛才在AI生成畫面前，已經簡單分享過一些內容，AI依此生成了"
            f"一張示意圖給長者看，但長者看完圖後沒有特別的反應或想法（例如"
            f"說「沒有」「還好」）。請針對【長者生圖前分享的內容】寫一句簡短"
            f"溫暖的呼應語，具體提到長者剛才說過的人事物，讓長者感覺到你有"
            f"在聽，不能只是空泛的稱讚（例如不寫「謝謝你告訴我這些」，而是"
            f"具體點出內容本身）。承接語只要1-2句、30字以內，不能寫成問句、"
            f"不能用「呢」「嗎」這類疑問語尾詞結尾——真正的提問留給下面的"
            f"「問題：」欄位。承接語之後，緊接著問長者一個新問題——依"
            f"【STEP2自由追問／STEP3補問：生成流程】的選角度、選錨點方式"
            f"生成（先依五個切入角度優先順序選方向，再從【眼前畫面元素】或"
            f"【長者生圖前分享的內容】裡找一個能撐起這個角度的具體人事物當"
            f"錨點，接不上就換角度重選，不要硬套）。若上面列出【已涵蓋的W"
            f"維度】，這些方向長者剛才已經自然講過了，這題不要重複問同一個"
            f"方向。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"承接語：（1-2句，30字以內，具體呼應長者生圖前分享的內容）\n"
            f"問題：（≤25字，開放式，開頭要有具體錨點，畫面物件或長者生圖前分享的"
            f"內容皆可）\n"
            f"本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個"
            f"W維度名稱本身，不要自創其他詞彙、不要加括號說明）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_image_reveal_response(raw, scene_elements=scene_elements)

    def _parse_image_reveal_response(
        self, raw: str, scene_elements: list[str] | None = None,
    ) -> dict:
        """
        解析 _generate_image_reveal_reaction 的輸出（判斷依據＋分類＋承接語＋
        問題＋涵蓋的W）。

        judgment_evidence/classification 是2026-08-16新增的除錯／記錄欄位
        （見 _generate_image_reveal_reaction 該次稽核說明），只有前者的輸出
        會有這兩個欄位；_generate_quick_end_recap 沒有分類任務、不會產生
        這兩個欄位，共用這支 parser 時保持空字串即可，不影響它原本的行為。
        classification 只取開頭的1個數字字元，LLM若多寫了說明文字一併丟棄，
        找不到數字就保留原始字串以便從log看出是哪裡解析失敗。
        """
        result: dict = {
            "judgment_evidence": "", "classification": "",
            "reaction_text": "", "question": "", "covered_w": [],
        }
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("判斷依據："):
                result["judgment_evidence"] = line[len("判斷依據："):].strip()
                current_field = "judgment_evidence"
            elif line.startswith("分類："):
                result["classification"] = line[len("分類："):].strip()
                current_field = "classification"
            elif line.startswith("承接語："):
                result["reaction_text"] = line[len("承接語："):].strip()
                current_field = "reaction_text"
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif line.startswith("本回合已涵蓋的W："):
                w_raw = _strip_leaked_brackets(line[len("本回合已涵蓋的W："):].strip())
                result["covered_w"] = [
                    w.strip()
                    for w in w_raw.replace("，", "、").split("、")
                    if w.strip()
                ]
                current_field = None
            elif current_field == "judgment_evidence":
                result["judgment_evidence"] = f"{result['judgment_evidence']} {line}".strip()
            elif current_field == "reaction_text":
                result["reaction_text"] = f"{result['reaction_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        digit_match = re.search(r"[1-4]", result["classification"])
        if digit_match:
            result["classification"] = digit_match.group()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("reaction_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if not result["reaction_text"]:
            print(f"[Orchestrator] ⚠ 出示圖片承接語欄位是空的，退回保底承接語。"
                  f"原始輸出: {raw[:200]!r}")
            # 這裡不能留空：這支解析函式同時給 _generate_image_reveal_reaction
            # 跟 _generate_quick_end_recap 共用，後者組 scene_text 時是直接用
            # result["reaction_text"]（不像前者那樣可能在後面接一次
            # _IMAGE_REVEAL_TRANSITION 墊底，見該常數說明），留空會讓
            # quick_end 這條路徑的 scene_text 整段空白。改用真正的畫面元素
            # 組保底句，跟 _element_fallback（見該函式說明）同一套做法。
            elements_str = _natural_join(scene_elements or [])
            result["reaction_text"] = (
                f"謝謝你看著眼前有{elements_str}的畫面，跟我說了這麼多。"
                if elements_str else "謝謝你陪我看這張圖，跟我說了這麼多。"
            )
        return result

    async def _generate_open_followup(
        self,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        skipped_w: list[str],
        elder_response: str,
        scene_composition: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
        pre_image_detail: str = "",
    ) -> dict:
        """
        STEP2 開放式追問（Track C）：承接長者情緒，自然延伸問題，順道帶出未涵蓋的W。
        prompt 格式對齊 dpo/collect_data.py build_track_c_inference_prompt。
        Returns: {"scene_text": str, "question": str}

        retry_feedback: 見 _generate_question 的同名參數說明。

        pre_image_detail: 長者生圖前Q1/Q2訪談的原話。2026-08新增：question_5w1h.txt
        【STEP2自由追問／STEP3補問：生成流程】步驟3的錨點優先順序已經把這個
        列為次於「長者這一輪剛提到的人事物」的第二選擇（比畫面元素優先）——
        長者自己說過的話，不管是這一輪還是稍早，都比AI選的畫面元素更貼近長者
        真正想聊的東西。這裡把它傳進去，才有東西可以套用那條規則。

        2026-08-16稽核：實測發現感官記憶（question_5w1h.txt 五個切入角度裡的
        第③種）幾乎從沒被問到過——問題出在這裡的【任務】只講「把問題帶到某個
        W維度」，完全沒提到感官記憶這個選項；question_5w1h.txt 雖然整份當
        system_content 載入、裡面確實有五感的詳細規則，但埋在一大份規則庫裡，
        跟每次呼叫都會直接看到、具體可照做的 user_content 任務指示相比分量
        差很多，本地基底模型幾乎不會自己回頭套用。補上一句明講可以用感官記憶
        切入、依主題挑貼近的感官，讓這個選項具體出現在任務指示裡。
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
        topic_str    = user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"
        pre_image_str = pre_image_detail or "無"

        uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
        uncovered_str = "、".join(uncovered) if uncovered else "無（已全部涵蓋）"

        user_content = (
            f"長者剛才說：\n「{elder_response}」\n"
            f"\n【今日主題】\n{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【長者生圖前分享的內容】\n{pre_image_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【尚未涵蓋的W維度】\n{uncovered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n請先承接長者的情緒（1-2句，符合他當下的心情，具體呼應他剛才說的內容），"
            f"再順著長者說的話問下一個問題（≤25字，開頭錨點依序優先用：長者這一輪"
            f"剛提到的具體人事物→【長者生圖前分享的內容】裡的具體人事物→畫面元素，"
            f"開放式，不必勉強拉回畫面）。\n"
            f"⚠️ 觸發條件檢查（每次回應前都要先看兩件事）：\n"
            f"1. 長者剛才的話裡有沒有把決定權丟回來的句子？分兩種，處理方式不同："
            f"(a) 像「換一個好不好」「聊點別的吧」這種已經做出決定、明確要求換話題的"
            f"句子——承接語要溫暖地肯定他這個選擇（例如「不想說的事就不用勉強」），"
            f"但不用承諾「你想聊什麼，我們就聊什麼」這種空話；「有溫度」跟「不做空頭"
            f"承諾」要同時做到，不能為了避免空話就把承接語縮成只剩「沒關係」兩三個字，"
            f"那樣反而顯得冷淡生硬；接著問一個新方向的具體問題就是在尊重他的要求；"
            f"(b) 像「你真的想知道嗎」「我要跟你說嗎」「你想聽嗎」這種還沒決定、把決定"
            f"權真的丟回來問你的反問句——這種才需要把主導權完全交還，問題絕對不能硬拉去"
            f"不相干的話題（那樣會讓「主導權在你」這句話顯得言行不一，是這條規則最容易"
            f"出錯的地方），而是問一個尊重他步調、讓他自己決定要不要繼續/現在說或晚點說"
            f"的問題。\n"
            f"2. 長者剛才的話裡有沒有在吐槽、指出眼前這張AI示意圖本身不合理、不對勁的"
            f"地方？（例如「以前的腳踏車哪有這種煞車」「這個字看不懂啦」「這個顏色怪"
            f"怪的」）——這種情況不糾正、不爭辯、不用制式道歉解釋AI畫面為什麼會這樣；"
            f"承接語要順著長者的話，帶點輕鬆語氣附和他說得對、肯定他眼力好、觀察力敏銳"
            f"（例如「真的耶，這個電腦畫得不太一樣，你眼睛真尖一下就發現了！」這種語氣，"
            f"不是每次都套同一句），問題不要死守著眼前這張圖裡出錯的那個細節，改順著"
            f"長者剛才話裡透露出的真實記憶（例如他提到「以前的煞車」，就問以前真正的"
            f"煞車是怎樣的）延伸問下去；問題本身仍要符合其他所有格式規則（不用「您」、"
            f"≤25字、不是是非題）。\n"
            f"先判斷長者是不是正說得起勁、自己滔滔不絕地敘述——如果是，「問題」改用"
            f"聊天中真的會脫口而出的簡短延續句（例如「後來呢？」「你們還做了什麼？」），"
            f"順著他的話往下接就好，不用刻意湊出結構完整、以W維度為目標的問題；只有"
            f"長者的敘述明顯停下來、需要換方向時，才自然地把問題帶到【尚未涵蓋的W維度】"
            f"其中一個上。承接語（同理、具體呼應長者剛才說的內容）不受這條影響，維持原本要求。\n"
            f"把問題帶到某個W維度上時，優先考慮用感官記憶切入（聞到的氣味、聽到的"
            f"聲音、摸起來的感覺、吃起來的味道、看到的樣貌），比直接問事實更容易"
            f"勾起長者的回憶與情緒，用哪一種感官依【今日主題】情境挑最貼近的（例如"
            f"節慶/圍爐類優先嗅覺、味覺；童玩/郊遊類優先視覺、聽覺、觸覺；軍旅/"
            f"工作類優先聽覺、觸覺；哀傷之事類則不用感官細節）。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"承接語：（1-2句，30字以內）\n"
            f"問題：（≤25字）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages)
        return self._parse_track_c_response(raw, scene_elements=scene_elements)

    async def _generate_supplement_question(
        self,
        user: dict,
        scene_elements: list[str],
        covered_w: list[str],
        target_w: str,
        scene_composition: str = "",
        emotion: str = "happy",
        retry_feedback: str = "",
        elder_response: str = "",
        pre_image_detail: str = "",
    ) -> dict:
        """
        W 補問：明確針對尚未涵蓋的 W 維度切入（STEP3 格式）。
        Returns: {"scene_text": str, "question": str}

        retry_feedback: 見 _generate_question 的同名參數說明。
        elder_response: 長者最近說的話——STEP3觸發時（can_continue=False，見
            _decide_topic_continuation／_is_quick_end）長者的回應通常很短或
            話題已經自然結束，這一步的工作是「收一下、換方向」，不是「順著
            聊下去」，跟STEP2的自由追問性質不同（2026-08稽核，問題設計規則.pdf
            原始設計STEP3是「結合圖片元素，從還沒涵蓋的W切入」，不是接話續聊）。
            2026-08改版把這裡的輸出欄位從「場景文字」改成「承接語」，因為它
            實際要做的事更接近Track C的承接語（呼應長者剛才的話、自然轉場），
            不是STEP1那種單純描述畫面的場景文字；若不知道長者剛才說了什麼，
            退回單純自然轉場，不用憑空硬描述畫面（2026-07 使用者回饋發現這裡
            漏了 elder_response，_next_step_or_end 手上明明有這個值卻沒往下傳，
            當時的修法還在用「場景文字」框架，這次改版一併調整措辭）。
            預設空字串是為了兼容沒有對應到單一長者回應、本來就沒有值可傳的呼叫端。
        pre_image_detail: 見 _generate_open_followup 的同名參數說明——理由相同。

        2026-08-16稽核：跟 _generate_open_followup 同一個問題——原本【任務】
        只丟 _W_HINT[target_w] 這種字面事實提示（哪裡/誰/什麼事），完全沒
        提到感官記憶這個切入角度，實測感官細節的問題幾乎從沒被問出來過。
        補上一句明講可以用感官記憶切入，說明見 _generate_open_followup 的
        同一則稽核筆記。
        """
        system_content = _load_prompt("question_5w1h.txt") or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "問題必須念起來自然、溫和、不超過25個字，開頭要有具體錨點（畫面中看"
            "得到的物件、長者提到的具體人事物、或「那個時候」回指情境皆可）。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        topic_str    = user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"
        elder_section = f"\n【長者剛才說的話】\n{elder_response}\n" if elder_response else ""
        pre_image_str = pre_image_detail or "無"

        user_content = (
            f"【長者資料】\n"
            f"姓名：{user['name']}\n"
            f"職業背景：{user['main_occupation']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"興趣：{user.get('preferences') or '無'}\n"
            f"懷舊治療主題類別：{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【長者生圖前分享的內容】\n{pre_image_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"{elder_section}"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"生成一個問題，順著長者剛才的話跟眼前畫面自然地深入問下去，不是在核對清單。"
            f"{_W_HINT[target_w]}\n"
            f"如果眼前畫面或情境合適，優先考慮用感官記憶切入（聞到什麼氣味、聽到"
            f"什麼聲音、摸起來什麼感覺、吃起來什麼味道、看到的樣貌），這種問法"
            f"常常同時就能自然帶出上面這個W維度，比直接問事實更容易勾起長者的"
            f"回憶與情緒——用哪一種感官，依【今日主題】情境挑最貼近的（例如"
            f"節慶/圍爐類優先嗅覺、味覺；童玩/郊遊類優先視覺、聽覺、觸覺；軍旅/"
            f"工作類優先聽覺、觸覺；哀傷之事類則不用感官細節）。\n"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"思考：（主題判斷：一句話判斷今日主題最貼近哪個核心主題；"
            f"切入角度：一到兩句話決定這題要用什麼當錨點、往哪個方向問——"
            f"兩段都要寫，不會念給長者聽）\n"
            f"承接語：（1-2句，30字以內，若【長者剛才說的話】有內容，具體呼應那句話，"
            f"不要空泛帶過；若長者剛才的話很短或沒有可延伸的內容，就溫和地收一下、"
            f"自然轉場，不要硬接一句跟長者的話無關的話）\n"
            f"問題：（≤25字，開放式，開頭要有具體錨點，畫面物件、長者提到的具體"
            f"人事物、或「那個時候」回指情境皆可）\n"
            f"問題類型：STEP3補問\n"
            f"本回合已涵蓋的W：（只能填 Where／Who／What／When／How／Why 這6個W維度名稱本身，"
            f"不要自創其他詞彙、不要加括號說明）"
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
        """解析 STEP3補問的結構化輸出（_generate_supplement_question 專用；STEP1
        有自己專屬的 _parse_step1_response，STEP2 有 _parse_track_c_response）。

        scene_elements: 若 LLM 沒吐出「承接語：」那一行，用這份畫面元素清單
        組一句「畫面裡有OOO」的保底鋪陳語，而不是完全籠統、跟畫面無關的通用句——
        承接語本來的功能就是幫長者建立畫面感、順著話接下去，保底時也不該把這個
        功能整個丟掉。
        """
        result: dict = {"scene_text": "", "question": "", "covered_w": []}
        thinking = ""
        # current_field 追蹤「目前正在填哪個欄位」，讓後續沒有標籤的行可以接到
        # 上一個標籤欄位——本地模型偶爾會把「問題：」單獨放一行、實際問題文字
        # 放在下一行，原本逐行比對「這行開頭是不是問題：」的寫法抓不到這種格式，
        # 會讓 result["question"] 停留空字串，觸發下面的「question 欄位是空的」
        # 保底邏輯，把整段原始輸出（思考+承接語+問題+W全部黏在一起）誤判成
        # 問題內容塞進去——2026-08 實測發現這是造成 too_long 重試的常見成因。
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("思考："):
                # CoT 草稿行：question_5w1h.txt 的【思考欄位】規則要求模型先在這裡
                # 判斷主題方向、選錨點，再輸出正式內容。這行故意不進 result、不會
                # 被念給長者聽，只印出來方便觀察模型的選題邏輯、調整 prompt。
                thinking = line[len("思考："):].strip()
                current_field = "thinking"
            elif line.startswith("承接語："):
                # 這支函式現在只服務STEP3補問（見下方docstring），STEP3的prompt
                # 只會要求輸出「承接語：」，不會有「場景文字：」這個標籤——
                # question_5w1h.txt裡已經不存在任何「場景文字：」的輸出格式
                # 範例，2026-08 把那個分支拿掉，避免留著一個永遠不會命中的
                # 標籤讓人誤以為這支parser還服務STEP1（STEP1有自己專屬的
                # _parse_step1_response，見該函式說明）。承接語結果仍存進
                # result["scene_text"]這個key，下游（TTS、保底邏輯）沿用舊
                # key名稱，不用跟著改。
                result["scene_text"] = line[len("承接語："):].strip()
                current_field = "scene_text"
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
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
                current_field = None
            elif line.startswith("問題類型："):
                current_field = None  # 這欄不儲存，但要停止把後面的行接到問題/承接語
            elif current_field == "scene_text":
                result["scene_text"] = f"{result['scene_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
            elif current_field == "thinking":
                thinking = f"{thinking} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("scene_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            print(f"[Orchestrator] ⚠ 問題欄位清洗後是空的（本地模型把格式範本原封不動echo回來），"
                  f"退回{'含畫面元素的' if first_element else ''}保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if not result["scene_text"]:
            fallback = (
                f"眼前的畫面裡有{elements_str}，我們接著聊聊這個吧。"
                if (elements_str := _natural_join(scene_elements or [])).strip()
                else _FALLBACK_TRANSITION_TEXT
            )
            print(f"[Orchestrator] ⚠ 承接語（引導語）欄位是空的，退回{'含畫面元素的' if scene_elements else ''}"
                  f"保底鋪陳語，避免長者只聽到問題、沒有任何引導。原始輸出: {raw[:200]!r}")
            result["scene_text"] = fallback
        if thinking:
            print(f"  → 思考: {thinking}")
        return result

    def _parse_track_c_response(self, raw: str, scene_elements: list[str] | None = None) -> dict:
        """解析 Track C（承接語 + 問題）的輸出。"""
        result: dict = {"scene_text": "", "question": ""}
        # 同 _parse_question_response：追蹤目前正在填哪個欄位，處理標籤跟內容
        # 分兩行的情況（見該函式的說明）。
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("承接語："):
                result["scene_text"] = line[len("承接語："):].strip()
                current_field = "scene_text"
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif current_field == "scene_text":
                result["scene_text"] = f"{result['scene_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("scene_text", "question"):
            result[key] = _strip_leaked_brackets(result[key])
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            print(f"[Orchestrator] ⚠ Track C 問題欄位清洗後是空的，退回"
                  f"{'含畫面元素的' if first_element else ''}保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if not result["scene_text"]:
            print(f"[Orchestrator] ⚠ Track C 承接語（引導語）欄位是空的，退回保底鋪陳語。"
                  f"原始輸出: {raw[:200]!r}")
            result["scene_text"] = _FALLBACK_TRANSITION_TEXT
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