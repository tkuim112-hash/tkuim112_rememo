"""
療程編排器(Orchestrator)。

實作懷舊療法完整狀態機：三回合、5W1H追蹤、開放訪談與補問路徑。

2026-08-17起三回合性質不同，不是三次一樣的生圖流程：round 1 才是下面第1、2
點描述的完整生圖流程；round 2 是自由追問（STEP2+STEP3，不生圖、不合成語音）；
round 3 是 closing（回縮期，不生圖、不合成語音，單輪問答就結束整場療程）。
round 2/3 各自的開場邏輯與跟既有「三回合結束後心得」機制的關係，見
start_round、_start_round2_free_followup、_start_round3_closing 的說明，
不在下面這份流程說明的範圍內（下面談的是 round 1 生圖前後的狀態機）。

2026-08-18起 round 1／round 2 的分界改變：round 1 問完「出示圖片」那句
（_IMAGE_REVEAL_SCENE_TEXT + _IMAGE_REVEAL_QUESTION）、長者回答後就直接
結束，不在回合1內生成承接語反應、也不問 STEP1（5W1H）開場問題——長者看完
圖的反應分類、承接語、STEP1 開場問題全部移到 round 2 開場第一步驟才做
（_start_round2_free_followup 呼叫 _handle_image_reveal_answer，見這兩支
函式的說明），問完 STEP1 才接續原本 round 2 的 STEP2/STEP3 自由追問。下面
「出示圖片（image_reveal）」這段說明的仍是同一套反應分類＋STEP1生成邏輯，
只是觸發時機從 round 1 內部改成 round 2 開場。

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
     → 真的不想／不能答（_is_true_refusal：沉默（被治療師跳過）或明確講不知道/不記得，
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
   看圖後說出第一反應。state 的 last_question_type 是 "image_reveal"。這是
   round 1 問的最後一題——長者這句回答由 process_response 收到後直接結束
   round 1（action="end_round"，見下方 _end_action 呼叫），回答內容原封不動
   透過 carryover 交給 round 2 開場處理：
     → 依長者反應生成承接語（圖與記憶相符/有差異/情緒明顯-感動，見
       _generate_image_reveal_reaction；情緒明顯-不安已經被更前面的
       _detect_emotional_trigger 攔截走，不會走到這裡），再接 STEP1 開場
       問題，回傳 action="scene_ready"（分類只分「一致／有差異／情緒明顯」
       三類，見 _generate_image_reveal_reaction 說明）
     這整段實際執行位置是 _handle_image_reveal_answer，由 round 2 開場
     （_start_round2_free_followup）第一次呼叫，見該函式說明。

   其餘 action 說明：
     "open_followup"    → 話題豐富，繼續順著長者深入（有 scene_text + question）
     "ask_supplement_w" → 話題結束，切入未問的W維度（有 scene_text + question）
     "end_round"        → 本回合結束（round 1 是問完出示圖片那題就結束；
                          round 2 是W全部覆蓋或跳過），前端用 next_round
                          呼叫 start_round
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
import asyncio
import json
import random
import re
import unicodedata
from pathlib import Path
from services.llm import LLMService
from services.image import OpenAIImageService
from services.rag_client import RealRAGClient
from services.user_profile_db import DBUserProfileClient
from services.audio_bank import q1_invitation_key, five_w1h_key
from privacy.deidentifier import Deidentifier
from safety.response_guard import guarded_generate, normalize_anchor
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
# 定義）跟 _void_pure_time_evidence_from_other_dims（跑在 _missing_from_checks
# 之前的去重修正，見該函式），兩邊共用同一份字面詞才不會各自漂移（同一種
# 「兩邊各自 hardcode 忘改一邊」的坑，之前 DPO 用詞審查也踩過）。
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

_SENSE_DESC = {
    "視覺": "視覺（看到的顏色、光線、天氣、樣貌）",
    "聽覺": "聽覺（聽到的聲音）",
    "嗅覺": "嗅覺（聞到的氣味）",
    "味覺": "味覺（嚐到的味道）",
    "觸覺": "觸覺（摸到、感覺到的觸感、溫度）",
}

# 話題延伸方向（_decide_topic_continuation 併入 _detect_covered_w_and_senses
# 前原本各自的4個判斷方向，見該函式2026-09-12稽核說明），跟 _W_ORDER／
# _SENSE_DESC 的 key 放在同一個模組層級常數，才能安全併進同一次「逐項列
# 證據」的LLM呼叫、事後再依 key 落在哪個清單拆回三組分開處理。
_CONTINUATION_DIRECTIONS = ["情感／意義", "陪伴的人的互動", "感官細節", "接下來發生的事"]

_W_HINT = {
    "Where": "用 Where 角度問（哪裡、哪個地方、場所）",
    "Who":   "用 Who 角度問（誰、哪個人、關係）",
    "What":  "用 What 角度問（什麼事、什麼東西、發生什麼事）",
    "When":  "用 When 角度問（什麼時候、季節、人生階段）",
    "How":   "用 How 角度問（怎麼做、如何、過程、感受）",
    "Why":   "用 Why 角度問（為什麼、原因、動機）——僅在長者狀態良好時使用",
}

# STEP1 開場問題比照 STEP2/STEP3，開放式選角度、選錨點，不由呼叫端指定
# 明確的 target_w／target_sense 方向。
_STEP1_OPEN_DIRECTION_HINT = (
    "依【STEP2自由追問／STEP3補問：生成流程】的選角度、選錨點方式"
    "生成（先依五個切入角度優先順序選方向，再從【眼前畫面元素】或"
    "【長者生圖前分享的內容】裡找一個能撐起這個角度的具體人事物當"
    "錨點，接不上就換角度重選，不要硬套）。錨點請優先用【長者生圖"
    "前分享的內容】裡的具體人事物，接不上時才改用【眼前畫面元素】。\n"
)

# 只剩 STEP1 用得到——STEP2（_generate_open_followup）、STEP3
# （_generate_supplement_question）都是自己inline寫死【任務】/問題類型文字，
# 不透過這兩個dict。不寫「優先問 Where 或 What」：跟 question_5w1h.txt
# STEP1流程步驟2的切入角度優先序（情感／意義→陪伴的人→感官記憶→敘事推進
# 或今昔對比→過程步驟）互相矛盾，混進【任務】欄位會把模型導向字面地點/
# 事物問法，跟系統prompt想要的「情感意義優先」精神打架。
_STEP_TASKS = {
    "STEP1": "生成第一個【開場問題】，引導長者進入回憶",
}

_STEP_TYPE_LABEL = {
    "STEP1": "STEP1開場",
}

# STEP2/STEP3 共用：要求模型額外交代「問題」引用了哪個來源，讓 response_guard.py
# 的 question_anchor_unsupported 能用引號逐字核對，攔截「開頭錨點合規、內容卻是
# 憑空編造」的話題跳躍（例如長者在講三杯雞，問題卻問起沒人提過的滷豬腳）。
_ANCHOR_FIELD_SPEC = (
    "錨點：（用引號「」逐字標出「問題」引用的來源片段，出處只能是【長者剛才說的話】"
    "／【長者生圖前分享的內容】／【眼前畫面元素】其中之一；若只是用「那個時候」這類"
    "純時間回指、沒有具體引用任何一項，就寫「無」，不要硬套一組引號）"
)

# _generate_supplement_question 改用結構化輸出（JSON Schema，grammar-
# constrained decoding）取代標籤文字格式——本地小模型常漏寫其中一段，
# schema 強制每個 key 都要生成。沒有 covered_w：唯一的呼叫端 _ask_
# supplement 改用 _classify_question_dimension() 另外判斷W維度，模型
# 自報的這份從沒被讀過，拿掉減輕格式負擔。
#
# 2026-09-14稽核：STEP2/3不再生成承接語（scene_text）——
# 拿掉的理由跟過程見 _ask_open_followup／_ask_supplement 上方說明，這裡
# 只是schema跟著拿掉這個key，不再要求模型生成、也不再驗證它。
_SUPPLEMENT_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "thinking": {
            "type": "string",
            "description": "先摘要【長者剛才說的話】重點，再判斷今日主題最貼近哪個核心主題，"
                            "最後決定這題要用什麼當錨點、往哪個方向問——不會念給長者聽。",
        },
        "anchor": {
            "type": "string",
            "description": "逐字引用「question」引用的來源片段（只能來自長者剛才說的話／"
                            "生圖前分享的內容／眼前畫面元素之一），沒有具體引用就填「無」。",
        },
        "question": {
            "type": "string",
            "description": "開放式問題，25字左右、不超過30字，開頭要有具體錨點。",
        },
    },
    "required": ["thinking", "anchor", "question"],
}

# _generate_closing 同樣改用結構化輸出，理由同上。
_CLOSING_SCHEMA = {
    "type": "object",
    "properties": {
        "closing_text": {
            "type": "string",
            "description": "收尾語，1句話（用逗號銜接、只用一個句號收尾），30字以內，"
                            "把長者帶回當下。",
        },
        "question": {
            "type": "string",
            "description": "不超過25字的開放式問題。",
        },
    },
    "required": ["closing_text", "question"],
}

# _generate_open_followup 同樣改用結構化輸出，理由同上。
#
# 2026-09-14稽核：STEP2/3不再生成承接語（scene_text），理由見
# _ask_open_continuation 上方說明，這裡schema跟著拿掉這個key。
_OPEN_FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {
        "thinking": {
            "type": "string",
            "description": "主題判斷＋切入角度，不會念給長者聽。",
        },
        "anchor": {
            "type": "string",
            "description": "逐字引用「question」引用的來源片段，沒有具體引用就填「無」。",
        },
        "question": {
            "type": "string",
            "description": "開放式問題（或簡短延續句），25字左右、不超過30字。",
        },
    },
    "required": ["thinking", "anchor", "question"],
}

# _generate_image_reveal_reaction／_generate_quick_end_recap（出示圖片承接）
# 共用同一支 parser，但輸出的 key 不完全一樣（前者沒有 question，後者沒有
# judgment_evidence／classification），分成兩份 schema。
_IMAGE_REVEAL_REACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "judgment_evidence": {
            "type": "string",
            "description": "一句話列出在長者這句反應裡看到的具體線索，只能引用長者"
                            "這次反應內容裡實際出現的字詞，看不出明確線索就寫「反應"
                            "內容簡短，看不出明確線索」。",
        },
        "classification": {
            "type": "string",
            "enum": ["1", "2", "3"],
            "description": "1=圖跟長者記得的一致，2=圖跟長者記得的有差異，"
                            "3=長者明顯被圖片觸動、感動。",
        },
    },
    "required": ["judgment_evidence", "classification"],
}

# 2026-09-10（減少出示圖片承接語的等待時間）：承接語本來是
# LLM 依 judgment_evidence／classification 現寫的 1-2 句話，改成直接依
# classification 對應固定句——3句都取自這支函式舊版 prompt 裡教模型寫法
# 用的示範句（見下方 _generate_image_reveal_reaction docstring），已經是
# 通過長期稽核、語氣穩定的版本。分類本身仍由 LLM 判斷（見該函式），只是
# 不用再多等一次「依判斷依據生成承接語」的文字生成——分類 3 選 1 遠比
# 現寫一段話快。
#
# 代價：不再具體呼應長者這次反應裡的細節內容（例如分類2長者若講出「院子
# 比較大」，模板不會提到院子），只保留「類別」層級的溫暖回應。是使用者
# 確認過、可接受的取捨，不是遺漏。
_IMAGE_REVEAL_REACTION_TEMPLATES = {
    "1": "聽你這樣說，我彷彿也看到了當時的畫面。",
    "2": "這張圖確實沒辦法把每個細節都畫得剛剛好，聽你這樣說，你記得的畫面比圖裡的還要豐富。",
    "3": "這段回憶對你來說真的很重要，謝謝你願意跟我分享。",
}

# covered_w 同 _SUPPLEMENT_QUESTION_SCHEMA 的稽核說明，唯一呼叫端
# （_handle_image_reveal_answer 的 quick_end 分支）明確標示「自報W…不
# 採信」、從不讀這個值，拿掉。
_QUICK_END_RECAP_SCHEMA = {
    "type": "object",
    "properties": {
        "reaction_text": {
            "type": "string",
            "description": "承接語，1-2句、30字以內，具體呼應長者生圖前分享的內容，不能是問句。",
        },
        "question": {
            "type": "string",
            "description": "開放式問題，不超過25字，開頭要有具體錨點。",
        },
    },
    "required": ["reaction_text", "question"],
}

# Kinect 即時偵測情緒（app/routers/sensor.py 的 emotion_raw）→ 給 LLM 的語氣指引
_EMOTION_GUIDANCE = {
    "sad":     "長者目前情緒低落，請優先給予溫暖同理與正向肯定，語氣放柔，暫緩深入提問，可引導至輕鬆或正向的話題。"
               "但仍必須維持【任務】要求的提問形式、錨定眼前畫面元素或長者說過的內容，"
               "不要脫離任務去建議深呼吸、賞花等與任務無關的活動或安慰語。",
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
# round 2（生圖後自由追問，見 _start_round2_free_followup）沒有生圖前後那些
# 額外步驟撐場面，全部題數都算在題數上限內（不像round 1有生圖前Q1/Q2、出示
# 圖片反應這三步不計數，見這三處各自的 question_count 說明）——要求
# round 2 題數上限跟round 1不同，維持較短的5題，用一份對照表按round查表，
# 避免兩個常數散在各處、之後想再調整某一回合的上限時漏改。
_MAX_QUESTIONS_PER_ROUND_BY_ROUND = {2: 5}
_MAX_SUPPLEMENT_PER_ROUND = 2

# round 2 最少問滿3題才能自然收尾——早收尾捷徑（5W1H+感官全涵蓋、情緒happy
# 且已補問過一次、補問名額用完）只顧著避免「硬湊題數」，沒有下限會出現補完
# 1個W就因情緒好直接收尾、全程只問2題的情況。這幾條早收尾判斷都額外檢查
# 「這回合題數是否已到最低要求」，沒到就再生一題開放式延續問題撐住（見
# _ask_open_continuation）。只設 round 2 的下限，其他回合預設1（沒有下限）
# ——round 1 有生圖前Q1/Q2跟STEP1撐場面，round 3 是單輪closing，都不適用。
_MIN_QUESTIONS_PER_ROUND_BY_ROUND = {2: 3}


def _max_questions_for_round(round_number: int) -> int:
    return _MAX_QUESTIONS_PER_ROUND_BY_ROUND.get(round_number, _MAX_QUESTIONS_PER_ROUND)


def _min_questions_for_round(round_number: int) -> int:
    return _MIN_QUESTIONS_PER_ROUND_BY_ROUND.get(round_number, 1)


# 生圖前情境2追問（見 _FIVE_W1H_BANK）最多問幾輪，避免長者還沒看到圖之前，
# 就被連環追問拖住。到上限或 get_scenario2_followup() 提早回傳 None（該問的
# 維度都已涵蓋或被排除）兩者任一成立就結束追問、進生圖。
#
# 依 Q1（或情境1轉情境2那句）當下實際還缺幾個W維度，取「缺幾項」跟這裡的
# 上限兩者較小值（見 _pre_image_q2_round_cap）——只缺一項的話問完那一項就
# 直接生圖，不會為了湊滿輪數多問；缺到超過上限時，這個常數就是最後的煞車，
# 剩下沒問到的維度交給生圖後的STEP1/2/3自然補問。
_MAX_PRE_IMAGE_Q2_ROUNDS_CAP = 2


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


# 正式環境用的是本地 Ollama 模型（cwchang/llama-3-taiwan-8b-instruct:q4_k_m，
# 見 app/config.py；DPO 微調版 rememo-llama3 已停用），不是頂尖級
# 模型，指令遵循度較弱，偶爾會把 prompt 裡「問題：（格式說明）」這種待填格式
# 範本原封不動照抄回來，當成自己的答案（尤其 prompt 越長、規則越密，這種「範本
# 回聲」越容易發生）。與其每次針對某一種洩漏樣式加一條 regex 打地鼠，這裡改成
# 通用策略：任何括號內容（全形/半形）一律清掉——真人講給長者聽的問題/場景文字/
# 承接語本來就不該有括號註解，清掉不會誤傷正常輸出。
_LEAK_BRACKET_RE = re.compile(r"[（(][^）)]*[）)]")

# 上面 _LEAK_BRACKET_RE 要求開閉括號成對出現才會清掉，但本地模型偶爾會把
# 輸出截斷在開括號之後、沒生成對應的閉括號（例如「...都會準備哪些食材？
# （」），逃過配對正則、殘留在送給長者的問題裡。這裡補一條：清掉字串結尾
# 找不到對應閉括號的開括號（含開括號後面到結尾的殘餘文字）。
_UNMATCHED_LEAK_BRACKET_RE = re.compile(r"[（(][^）)]*$")

# 2026-09-08：本地模型偶爾會把「判斷依據：」「承接語：」這類欄位名自己
# 多包一層全形方括號輸出成「【承接語】：」，即使prompt範例明明是不帶括號
# 的裸欄位名——猜測是被同一份prompt裡到處都是的【眼前畫面元素】【任務】
# 【輸出格式】這類段落標題格式帶偏，模仿了外層的方括號寫法。
# _parse_image_reveal_response 逐行比對 line.startswith("承接語：") 這類
# 裸欄位名，比對不到就整段視為解析失敗、退回保底句（見該函式呼叫處），
# 明明LLM這次回得完全正確，卻因為多包一層括號被整句丟棄。這裡在逐行比對
# 前先把「【欄位名】：」正規化成「欄位名：」，需求任兩者其中一種寫法
# 都能命中，不需要每次多一種格式就再加一組 startswith 分支。
_LEAKED_FIELD_LABEL_RE = re.compile(r"^【([^】]{1,20})】(?=[：:])")

# 本地模型偶爾會把多步驟輸出寫成編號清單（「1. 判斷依據：」）或加項目符號
# （「- 承接語：」），逐行比對 line.startswith("承接語：") 就完全比對不到、
# 整段解析失敗退回保底句。跟上面 _LEAKED_FIELD_LABEL_RE 同一種處理方式：
# 逐行比對前先把行首的編號／項目符號去掉。
_LEAKED_LIST_MARKER_RE = re.compile(r"^(?:\d{1,2}[.\)、]|[-•*])\s*")

# 本地弱模型偶爾會忘記在「問題：」那一行結尾換行，直接接著寫「本回合已
# 涵蓋的W：...」，導致逐行解析（raw.splitlines()）時兩個欄位擠在同一行、
# 沒有換行可以切開，後面那段整個被當成問題文字吞進去（例如變成「烤肉的
# 時候，大家都喜歡吃哪一種肉？本回合已涵蓋的W：What」）。「本回合已涵蓋
# 的W：」在所有共用這份輸出格式的prompt裡都是最後一個欄位，不管洩漏進
# 哪個欄位、在同一行什麼位置，把它跟後面內容整段砍掉都是安全的。
_TRAILING_COVERED_W_LEAK_RE = re.compile(r"本回合已涵蓋的W：.*", re.DOTALL)


def _strip_trailing_covered_w_leak(text: str) -> str:
    return _TRAILING_COVERED_W_LEAK_RE.sub("", text).strip()


# 同上一條的成因（本地模型忘記換行），但洩漏的是緊接在「問題：」後面的
# 「錨點：」欄位（STEP2/STEP3 共用 _ANCHOR_FIELD_SPEC，見該常數），例如
# 「你最喜歡的是哪一種滷法？錨點：「我最拿手的就是滷豬腳」」——長者會
# 聽到問題後面多一句突兀的「錨點：...」。「錨點：」在 STEP2/STEP3 的
# 輸出格式裡永遠緊接在「問題：」之後、早於「問題類型：」「本回合已涵蓋
# 的W：」，不管洩漏進哪個欄位，把它跟後面內容整段砍掉都是安全的——跟
# _strip_trailing_covered_w_leak 同一種洩漏、同一種修法。
_TRAILING_ANCHOR_LEAK_RE = re.compile(r"錨點：.*", re.DOTALL)


def _strip_trailing_anchor_leak(text: str) -> str:
    return _TRAILING_ANCHOR_LEAK_RE.sub("", text).strip()


# 「問題」欄偶爾會把「錨點」欄該有的引號習慣（見 _ANCHOR_FIELD_SPEC）滲透
# 帶進來，只拔掉引號符號本身、保留裡面的內容。
_LEAK_QUOTE_RE = re.compile(r"[「」『』]")


def _strip_leaked_quotes(text: str) -> str:
    return _LEAK_QUOTE_RE.sub("", text).strip()


# 清洗後若整段變空（代表 LLM 那一行輸出「整句」都是洩漏出來的格式說明，不是真的
# 在回答），退回這句通用、任何情境都安全的開放式問題，而不是把空字串送給長者。
# 開放式、非是非題，適用任何上下文，符合 question_5w1h.txt 的規則。
_FALLBACK_QUESTION = "還有什麼想說的呢？"

# STEP2/STEP3 承接語（見 _generate_open_followup／_generate_supplement_
# question）欄位是空的時候的保底鋪陳語，跟 _FALLBACK_QUESTION 同一套邏輯。
# 注意：不能用「我們接著聊聊這個吧」這類措辭——會命中 response_guard.py
# 的 _GENERIC_ACK_PATTERNS，讓保底句自己被判定違規，白白多燒一次重試。
_FALLBACK_TRANSITION_TEXT = "那我們換個方向聊聊吧。"

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
    "興趣":               "我很好奇，這件事最讓你開心的是哪個部分呢？",
    "專長":               "我很好奇，你是怎麼練出這個本事的呢？",
    "印象最深刻的地方":   "這一生有沒有哪個地方，讓你特別難忘呢？",
    "休閒":               "你以前從事這些休閒活動的時候，最喜歡哪個部分呢？",
    "節慶":               "我很好奇，你以前都是怎麼過節的呢？",
    "哀傷之事":           "如果你願意，我很想聽你說說看，他平常的樣子，或者你們相處的時候，是什麼樣子呢？",
    "人生目標":           "這一生有沒有什麼特別想完成的心願呢？",
    "自我成就感":         "這一生最讓自己驕傲的一件事，是什麼呢？",
    "生命中特殊的事件":   "有沒有什麼特別難忘、印象深刻的回憶呢？",
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
        # Why排除：Q1邀請語「這件事最讓你開心的是哪個部分呢？」已經在問Why
        # 這件事本身的內容，Why欄位再問一次「最讓你著迷的是什麼」等於重複，
        # 長者常常在Q1就已經順口答過了（見get_scenario2_followup說明）。
        "excluded_fields": ["Why"],
        "fields": {
            "Where": {"variants": ["那通常是在哪裡呢？", "那個時候，都在哪裡進行呢？"]},
            "When": {"variants": ["那通常是什麼時候呢？", "那個時候，通常是什麼時間做這件事呢？"]},
            "How": {"variants": ["通常是怎麼進行的呢？", "那時候，你都是怎麼做的呢？"]},
        },
    },

    "專長": {
        "granularity": "theme",
        # How排除：Q1邀請語「你是怎麼練出這個本事的呢？」本身就是How問句，
        # How欄位再問「怎麼學會、怎麼做到的」等於重複問同一件事。
        "excluded_fields": ["How"],
        "fields": {
            "Where": {"variants": ["那是在哪裡的事呢？", "那個時候，是在哪裡學的呢？"]},
            "When": {"variants": ["那讓你想到是什麼時候的事？", "那通常是白天，還是晚上練習的呢？"]},
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
        # Why排除：Q1邀請語「最喜歡哪個部分呢？」已經在問Why，跟Why欄位
        # 「最想記住的是哪一部分／最懷念的是什麼」是同一個問題。
        "excluded_fields": ["Why"],
        "fields": {
            "Where": {"variants": ["那是在哪裡呢？", "那個時候，都去哪裡呢？"]},
            "When": {"variants": ["那通常是什麼時候呢？", "那個時候，通常是什麼時間去呢？"]},
            "How": {"variants": ["當時的氣氛或心情是怎麼樣的呢？", "那時候，通常是怎麼樣的情形呢？"]},
        },
    },

    "節慶": {
        "granularity": "theme",
        # How排除：Q1邀請語「你以前都是怎麼過節的呢？」本身就是How問句，
        # How欄位再問「你們家通常是怎麼過的」等於重複。
        "excluded_fields": ["How"],
        "fields": {
            "Where": {"variants": ["是在什麼地方呢？", "那個時候，是在哪裡呢？"]},
            "When": {"variants": ["那通常會是在什麼時段呢？", "大概什麼時候會這麼做呢？"]},
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
        # Why排除：Q1邀請語「這一生最讓自己驕傲的一件事，是什麼呢？」已經
        # 用了「驕傲」這個詞問過Why，Why欄位「最讓你驕傲的是哪一部分」
        # 等於重複問同一件事。
        "excluded_fields": ["Why"],
        "fields": {
            "Where": {"variants": ["那是在哪裡達成的呢？", "那個時候，是在哪裡完成的呢？"]},
            "When": {"variants": ["那是什麼時候達成的呢？", "那是白天，還是晚上發生的呢？"]},
            "How": {"variants": ["當時是怎麼做到的呢？", "那時候，你是怎麼完成的呢？"]},
        },
    },

    "生命中特殊的事件": {
        "granularity": "theme",
        # Why排除：Q1邀請語「有沒有什麼特別難忘、印象深刻的回憶呢？」已經
        # 用了「難忘」這個詞問過Why，Why欄位「最讓你難忘的是什麼」等於
        # 重複問同一件事。
        "excluded_fields": ["How", "Why"],
        "fields": {
            "Where": {"variants": ["那是在哪裡發生的呢？", "那個時候，是在什麼地方呢？"]},
            "When": {"variants": ["那是什麼時候發生的呢？", "那是白天，還是晚上發生的呢？"]},
        },
        # 涉及228等政治敏感內容時，orchestrator 可視情況在Where/When問題前
        # 加註「不方便說也沒關係」（Why已經固定排除，不需要再另外動態處理）
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
    skipped_w: list[str] | None = None,
) -> tuple[str, str, str | None] | None:
    """
    生圖前 Q2・情境2專用：依 _PRE_IMAGE_PRIORITY_ORDER 找下一個該問的W維度，
    直接複用 _FIVE_W1H_BANK 裡已經核對過、排除過不適用欄位的那份題庫——
    每個主題／子項目該排除什麼，題庫裡已經寫好了（excluded_fields），不需要
    另外維護一組地點專用句庫，也不會再出現「這個主題問地點問不通」的狀況。

    covered_w：目前已經涵蓋的W維度（來自 _detect_pre_image_w_coverage 對
    Q1的核對，或後續每一輪 _detect_covered_w 對長者回答的更新），已涵蓋的
    欄位跳過不問。
    skipped_w：已經問過、但長者這輪沒答到的W維度（見 process_response 的
    pre_image_q2 迴圈說明）——跟 covered_w 一起排除不問，差別是 covered_w
    代表「已經答對」，skipped_w 代表「問過但沒答到，不再重問」，避免同一個
    問題被 _PRE_IMAGE_PRIORITY_ORDER 連續選中、卡住答不出來的長者。

    回傳值改成 (question, w, audio_key)：多回傳這次挑中的是哪個維度，讓
    呼叫端知道「這題在問哪個W」，下一輪核對長者答了沒有時才能對得上（否則
    呼叫端只有問題文字，沒辦法回推是哪個維度，就沒辦法在沒答到時把它加進
    skipped_w）。audio_key（2026-08-18新增）是 question 對應的前端內建
    預錄音檔 key（見 services/audio_bank.py five_w1h_key），查無對應（例如
    variants[i] 剛好是「哀傷之事→親人死亡」那個帶 {person} 稱謂詞的變體，
    audio_bank 故意沒收錄，見該函式說明）就是 None，呼叫端要退回即時TTS。
    回傳 None 代表該問的都問完了（扣掉 excluded_fields、covered_w、skipped_w
    後，_PRE_IMAGE_PRIORITY_ORDER 已經沒有欄位可問）——呼叫端據此結束情境2的
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
    skipped = set(skipped_w or [])
    fields = field_data.get("fields", {})
    for w in _PRE_IMAGE_PRIORITY_ORDER:
        if w in excluded or w in covered_w or w in skipped:
            continue
        field = fields.get(w)
        variants = field.get("variants") if field else None
        if not variants:
            continue
        variant_index = random.randrange(len(variants))
        question = variants[variant_index]
        # audio_key 一定要在 .format(person=...) 代換之前查——代換後的文字
        # 含實際稱謂詞，跟 audio_bank 收錄的原始 variants[i] 不會相等；
        # five_w1h_key 對這個{person}變體本來就沒收錄，代換前後查都是
        # None，這裡固定寫代換前是為了不管未來題庫怎麼改都成立。
        audio_key = five_w1h_key(theme, w, variant_index, sub_item)
        if field.get("requires") == "named_person":
            question = question.format(person=named_person or "他")
        return question, w, audio_key
    return None


def _build_pre_image_question(today_topic: str, category: str | None) -> tuple[str, str, str | None]:
    """
    組生圖前破冰問題 Q1（見上方 _PRE_IMAGE_Q1_INTRO 一帶的說明）：「說到
    {today_topic}，」接分類對應的邀請語，本身已經是一句完整的問題。是否
    追問 Q2 由 process_response 依 _has_usable_detail 的判斷另外決定，不在
    這裡處理。分類失敗（category是None，或分類結果不在 _PRE_IMAGE_Q1_
    INVITATIONS 裡）時退回完整的 _PRE_IMAGE_Q1_FALLBACK_QUESTION，不勉強
    拼湊。

    Returns: (question, tts_text, audio_key)。
      question：完整問句，給畫面顯示/log用，跟改動前一樣。
      tts_text：呼叫端真正該送去即時TTS合成的文字——「說到{today_topic}，」
        這段治療師自由輸入、無法窮舉，一定要即時TTS；分類成功時只送這段
        前綴（後半段邀請語改用 audio_key 對應的前端內建預錄音檔），分類
        失敗時 tts_text 等於 question 整句（沒有可用的預錄音檔，整句都要
        即時TTS）。
      audio_key：分類成功時是 _PRE_IMAGE_Q1_INVITATIONS[category] 那句的
        預錄音檔 key（見 services/audio_bank.py q1_invitation_key），前端
        要接在 tts_text 那段語音播完之後接著播放；分類失敗時是 None。
    """
    if category and category in _PRE_IMAGE_Q1_INVITATIONS:
        prefix = _PRE_IMAGE_Q1_QUESTION_TEMPLATE.format(today_topic=today_topic)
        question = f"{prefix}{_PRE_IMAGE_Q1_INVITATIONS[category]}"
        return question, prefix, q1_invitation_key(category)
    question = _PRE_IMAGE_Q1_FALLBACK_QUESTION.format(today_topic=today_topic)
    return question, question, None


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

# 長者看完圖沒有特別反應（quick_end）時的固定過渡句，取代承接語，直接接上
# STEP1 問題，見 process_response 的 image_reveal 分支。
_IMAGE_REVEAL_QUICK_END_ACK = "沒關係，那我們來聊聊，"

# 治療師網頁按「跳過」（/session/{id}/control action=="skip_scene"）時，
# Unity 端送出的合成 marker（GameController.cs AutoSubmitNoResponse），
# 不是長者真的說的話，不該拿去問 LLM 有沒有情緒訊號。2026-08-20 commit
# a6161e9「移除逾時跳題」之後，Unity 已經不會自動倒數逾時送出這個
# marker了——現在唯一的觸發來源就是治療師手動按跳過，長者自己沒回應
# 時畫面會一直停在原地等他，不會被系統自動跳過（這則註解原本寫「問題
# 播放完30秒沒按麥克風」，已經是那次改動之前的舊行為，2026-09-07更新）。
_NO_RESPONSE_MARKER = "（長者未回應）"

# 長者明確表示放棄／答不出來的關鍵字，_is_quick_end 跟 _is_true_refusal
# 共用同一份清單，避免兩處各自維護一份、改一邊漏改另一邊。
#
# 2026-09-07稽核（實測發現，隨後撤回）：曾經在這裡加過「不想講/不想
# 說/不想提/不想聊」這類明確拒答關鍵字，理由是拿掉STEP2「長者要求換話題」
# 那條prompt規則後，模型有機會不理會這句拒答、繼續追問同一個話題（實測
# 案例：「換一個好不好，這個我不想講。」→「那你們最後是怎麼處理的？」）。
# 但決定「長者要求換話題」這件事完全交給治療師端既有的「跳過」功能
# 處理就好，不需要LLM這層再自動偵測、繞路——跟「不知道/不記得」這種純粹
# 答不出來（沒有選擇餘地）性質不同，「不想講」是長者主動的話題選擇，交給
# 人（治療師）判斷比較合適，這裡改回原本只認「答不出來」的關鍵字清單。
_GIVE_UP_KEYWORDS = ["不記得", "不知道", "忘了", "忘記了", "不清楚", "沒印象"]

def _strip_leaked_brackets(text: str) -> str:
    text = _LEAK_BRACKET_RE.sub("", text).strip()
    return _UNMATCHED_LEAK_BRACKET_RE.sub("", text).strip()


# 2026-09-14稽核：closing.txt 已經要求承接語只寫一句話、用逗號銜接需要
# 對比的內容、最後只用一個句號收尾（見該檔【收尾語規則】），這裡機械式
# 保證這件事，不完全依賴模型自己遵守——找不到句號（模型用
# 「！」「？」結尾、或忘記加標點）就整句保留，不動它，避免誤刪掉唯一
# 一句話僅有的內容。
def _truncate_to_first_period(text: str) -> str:
    if not text:
        return text
    idx = text.find("。")
    if idx == -1:
        return text
    return text[:idx + 1]


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
# 「五感當切入角度」那條挑選原則同一套邏輯：
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

# STEP2/3 沒有像 covered_w 那樣追蹤「這回合已經問過哪個感官」，同一個感官
# 可能被連問好幾次，其他相關感官卻一次都沒問到。
#
# topic_senses 用 _classify_topic_senses 直接對 today_topic 本身跑一次LLM
# 分類（跟 _classify_topic_category 同一套「開場分類一次、存進 state、之後
# 直接複用」模式），不靠16大主題分類這層轉手查表——16大類是粗分桶，同一類
# 底下內容差異可能很大（例如「興趣」同時涵蓋唱歌→聽覺、種花→嗅覺/觸覺、
# 看報紙→視覺），查表只能對到整桶共用的答案，對不到 today_topic 實際內容。
#
# 哀傷之事／人生目標／生命中特殊的事件刻意不給感官：由呼叫端（start_round）
# 用 topic_category 硬性覆寫成空list，不交給 LLM 自由判斷——理由跟
# _TOPIC_SENSE_FALLBACK 的說明一致（哀傷之事怕把注意力拉回場景細節、人生
# 目標是尚未發生的想像場景、特殊事件性質差異太大無法一概而論），是刻意的
# 安全考量。
_SENSE_EXCLUDED_TOPIC_CATEGORIES = {"哀傷之事", "人生目標", "生命中特殊的事件"}


def _relevant_uncovered_senses(
    topic_senses: list[str] | None,
    covered_senses: list[str] | None,
    skipped_senses: list[str] | None = None,
) -> list[str]:
    """跟【今日主題】相關、但這回合還沒自然涵蓋過的感官，供 _generate_open_
    followup／_generate_supplement_question／_generate_image_reveal_
    reaction 具體點名當切入選項，取代「依主題挑貼近的感官」這種要模型自己
    臨時判斷的籠統講法。topic_senses 是呼叫端已用 _classify_topic_senses
    對 today_topic 分類好、存進 state 的結果；空list（例如哀傷之事、人生
    目標，或分類失敗）時回傳空list，呼叫端據此不提感官選項。

    skipped_senses 跟 skipped_w 同等規格：covered_senses 只記錄「長者的
    回答有沒有自然涵蓋某個感官」，長者這題答得很短或答非所問時
    _detect_covered_senses 抓不到證據、感官不會進 covered_senses，若沒有
    skipped_senses 記錄「問過但沒答到」，下次還是可能被同一個感官選中
    重問一次，跟 covered_senses 一起排除才能真正避開。"""
    relevant = topic_senses or []
    excluded = set(covered_senses or []) | set(skipped_senses or [])
    return [s for s in relevant if s not in excluded]


def _topic_relevant_covered_senses(
    topic_senses: list[str] | None, covered_senses: list[str] | None,
) -> list[str]:
    """跟【今日主題】相關、且這回合已經自然涵蓋過的感官——供 _sense_entry_
    hint 明講「這幾個已經問過，不要再問」。只列主題相關的，跟主題不相關、
    剛好在別的脈絡下被偵測到的感官不用特別提醒模型避開（本來就不會被當
    選項端出來）。"""
    relevant = topic_senses or []
    return [s for s in relevant if s in (covered_senses or [])]


def _sense_entry_hint(
    remaining_senses: list[str] | None, covered_senses: list[str] | None = None,
) -> str:
    """
    _generate_open_followup（STEP2）／_generate_supplement_question（STEP3）
    共用的感官切入提示文字，抽成共用函式避免兩處分開寫、之後改一邊忘改
    另一邊。

    感官切入是完全獨立的第三種問法，不對應任何W維度——曾經把它框成「達成
    某個W維度目標的一種手段」，結果模型會把感官問題硬套進W維度的慣用句型
    （例如選了「海浪聲」當錨點卻問「你都怎麼聽呢」，把感官動作本身當成有
    「方法」可問的How句型）。明講感官切入不需要對應W維度，也不用管輸出
    格式「本回合已涵蓋的W」欄位怎麼填，直接比照 _SENSE_QUESTION 的自然
    句型，禁止「你/你們+怎麼+感官動詞」句型。

    covered_senses 必須用負面表列明講「這幾個已經問過、不要再問」，只把
    已涵蓋感官從候選清單拿掉還不夠——本地基底模型不會靠「這個選項沒被
    列出來」推論「不能問這個」，需要跟 _W_HINT「已涵蓋的W維度」一樣明確
    列出來才擋得住。
    """
    sense_examples = "、".join(f"「{q}」" for q in _SENSE_QUESTION.values())
    covered_note = (
        f"這回合已經問過、長者也答過的感官（絕對不能再問，即使用不同的"
        f"scene元素當錨點也不行）：{'、'.join(covered_senses)}。\n"
        if covered_senses else ""
    )
    if remaining_senses:
        return (
            f"{covered_note}"
            "感官記憶切入是完全獨立的第三種問法，不需要對應任何W維度、也不用"
            "管輸出格式「本回合已涵蓋的W」欄位要填什麼——比直接問事實更容易"
            f"勾起長者的回憶與情緒，這回合還可以嘗試的感官：{'、'.join(remaining_senses)}"
            f"（挑其中一種即可，不用全部問過）。自然問法比照：{sense_examples}"
            "（都是「有沒有什麼X，讓你印象特別深呢」這種問印象/記憶本身的"
            "句型，套用同一個句型即可，不要問「你/你們+怎麼+看／聽／聞／摸／"
            "嚐」這種把感官動作本身當成有「方法」可問的不自然問法，例如不要"
            "問「海浪聲你都怎麼聽呢」）。這只是個加分選項，優先度低於「承接語"
            "必須具體回應長者剛才說的話」——只在長者剛才的話裡本來就有可以"
            "自然接上這個感官的內容時才用，不能為了湊感官，捨棄長者剛才實際"
            "說的內容、硬拉回主題畫面編一個不相關的情境。\n"
        )
    return (
        f"{covered_note}"
        "如果情境合適，也可以考慮用感官記憶切入（是完全獨立的第三種問法，"
        "不需要對應任何W維度）比直接問事實更容易勾起長者的回憶與情緒；這個"
        "主題適合的感官這回合已經問過了，或這個主題本來就不適合用感官切入，"
        "不用勉強套用。\n"
    )


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

    2026-09-14稽核：STEP2/3不再生成承接語，"scene_text" 這個key改固定
    回傳空字串——維持這個key存在只是不用去動下游一整串
    result.get("scene_text", "") 的呼叫點，不是還有地方在讀這個值。
    """
    first = scene_elements[0] if scene_elements else None
    fallback = {
        "scene_text": "",
        "question": (
            _W_FALLBACK_QUESTION.get(target_w)
            or _TOPIC_SENSE_FALLBACK.get(topic_category)
            or (f"{first}，讓你想到什麼？" if first else _FALLBACK_QUESTION)
        ),
    }
    if with_covered_w:
        fallback["covered_w"] = []
    return fallback


def _image_reveal_fallback_question(scene_elements: list[str], pre_image_detail: str) -> str:
    """
    STEP1（出示圖片反應）專用保底問句：guarded_generate 重試多次仍違規時
    的最終保底值，優先延伸 pre_image_detail（長者生圖前 Q1/Q2 實際分享的
    內容），不是隨手抓畫面元素當保底——跟其他生成函式的錨點優先序（長者剛
    提到的具體人事物→pre_image_detail→畫面元素）一致，避免長者剛分享的
    具體故事被晾在一邊、保底句卻只問到泛泛的畫面元素。

    不直接引用 pre_image_detail 原文（那是自由文字，沒經過格式規則核對，
    直接塞進保底句有踩到taboo/格式違規的風險——保底句的價值就在於保證
    合規，不能為了貼合內容犧牲這個保證），改用通用但語意上承接同一段
    內容的問法。
    """
    if pre_image_detail:
        return "剛剛你分享的這些，還有什麼想再多說一點的呢？"
    if scene_elements:
        return f"{scene_elements[0]}，讓你想到什麼？"
    return _FALLBACK_QUESTION


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


def _is_real_evidence(
    evidence: str | None, source_text: str, dimension: str | None = None
) -> bool:
    """
    判斷「逐項列證據」模式（見 _find_missing_memory_items）拿到的 evidence
    是不是「真的對應到具體內容」的證據，不是空字串、「找不到」、或隨手抓
    的樣板字。四道防線，各自對應下面一段檢查邏輯：

    1. evidence 不能是畫風/色調樣板字本身（例如"no text"，見
       _STYLE_ONLY_FRAGMENTS_RE）——這類字每張圖都會出現，不可能是任何
       特定項目的專屬視覺內容。
    2. substring 比對前用 _normalize_punct_width 正規化兩邊的標點寬度——
       模型偶爾把長者原話的全形標點抄成半形，逐字比對會被這一個字元差異
       誤判成「沒有真的出現在原文」。
    3. dimension 是「Why」時，evidence 長度佔 source_text 比例太高（>0.8，
       且 source_text 至少8字），視為「整句交差」不算真證據——本機模型對
       「Why」這種抽象維度找不到真正的原因/動機字句時，常整句照抄長者
       原話充數，雖然真的是原文子字串，卻不是要求的「具體片段」。門檻
       定在 8 字而非更高，是因為長者常見的短回答（例如10字左右的句子）
       會卡在較高門檻外，讓這道防呆完全不觸發。
       只對 Why 套用這道比例防線——2026-09-09 實測發現 Where/Who/What/
       When/How 這幾個具體維度，長者常常一句十幾字的完整短句就是那個
       維度唯一該有的內容（例如問「通常會怎麼樣子」，長者答「用木頭去
       燒火」，這句話本身就是完整的How，不是模型偷懶整句照抄），對這些
       維度硬套同一道比例防線，反而會把長者已經答過的內容誤判成沒答，
       導致同一輪追問到語意重複的問題（見session 9ff9bde9 round2 的
       實例：How的合法證據被這道防線誤刪，逼系統多問了一輪其實已經
       問過的內容）。dimension 傳 None（呼叫端不在乎維度、或維度未知）
       時維持舊行為套用比例防線，只有明確傳入非Why的維度名稱才豁免。
    4. 整段比對失敗、但 evidence 裡有頓號/逗號等分隔符時，拆開來看——長者
       一句話裡常有兩件真事，模型會用自己加的連接詞把兩個都塞進同一個
       evidence，這個連接詞本身不在原文裡；只要求分隔符兩側的實質內容
       各自是原文子字串就算數，不強求連接詞也要對得上。
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
    if dimension in (None, "Why") and _is_whole_sentence_copy(evidence, source_text):
        return False
    return True


def _is_whole_sentence_copy(evidence: str, source_text: str) -> bool:
    """
    _is_real_evidence 用的「整句交差」判斷本體。呼叫端必須先確認 evidence
    不是空字串/「找不到」/純樣板字/真的存在於原文，這裡只管長度比例本身。
    即使 prompt 已要求盡量精簡的短語格式，模型偶爾還是可能不理會、繼續
    整句照抄，這裡是驗證最終 checks 時的最後一道防線，不是重試觸發的
    判斷依據。
    """
    return len(source_text) >= 8 and len(evidence) / len(source_text) > 0.8


# 時間詞前後常見的泛泛填詞——「晚上的時候」「晚餐時間」這類片語裡，除了
# _WHEN_KEYWORDS 本身的時間詞，唯一剩下的就是這些不帶任何實質內容的填詞，
# 見 _void_pure_time_evidence_from_other_dims 說明。「時間」「時候」等單獨
# 出現時通常緊接在某個時間詞後面組成「X時間／X時候」（例如「晚餐時間」拿掉
# 「晚餐」後剩「時間」），不是獨立的實質內容，一併算填詞。
_TEMPORAL_FILLER_RE = re.compile(r"的時候|的時間|的時段|時候|時段|時分|時間|之時")


def _void_pure_time_evidence_from_other_dims(checks: list) -> list:
    """
    _detect_covered_w 專用，跑在 _missing_from_checks 之前。見 _WHEN_KEYWORDS
    定義處的說明：本地模型常把時間詞誤標到別的維度（通常是 What）的證據。

    2026-08-17第一版（_resolve_when_duplicate_evidence）只處理「其他維度的
    證據跟 When 的證據逐字相同」這個案例（例如兩邊都是「晚上」），但實測
    發現「晚上的時候」這種除了時間詞、只剩「的時候」這類泛泛填詞、沒有任何
    其他實質內容的片語，也會被標成 What 的證據——這段文字跟 When 自己的
    證據（可能是 _backstop_when_evidence 補上的短版「晚上」）字面對不起來，
    逐字比對抓不到，讓 What 白白多算一項本來不存在的內容。

    改成更通用的判準：不比對是否跟 When 的證據字面相同，直接檢查每個非
    When 維度的證據本身，把已知時間詞跟常見時間填詞都拿掉後，如果什麼都
    不剩，代表這段證據整體其實只是在講時間，本來就不該算是那個維度的
    證據，一律清成「找不到」——這個判準本身就涵蓋了原本「逐字相同」的
    情況（拿掉時間詞後兩邊都會剩空字串），不用再分別處理兩種案例。
    """
    result = []
    for c in checks:
        if not isinstance(c, dict):
            result.append(c)
            continue
        evidence = (c.get("evidence") or "").strip()
        if c.get("dimension") != "When" and evidence and evidence != "找不到":
            stripped = evidence
            for kw in _WHEN_KEYWORDS:
                stripped = stripped.replace(kw, "")
            stripped = _TEMPORAL_FILLER_RE.sub("", stripped)
            if not stripped.strip():
                c = {**c, "evidence": "找不到"}
        result.append(c)
    return result


def _backstop_when_evidence(checks: list, elder_response: str) -> list:
    """
    _detect_covered_w 專用，跟 _void_pure_time_evidence_from_other_dims 處理
    的是另一種失效模式：2026-08-17 實測發現本地模型對「晚餐時間」這類時間詞，
    即使 _W_DESC["When"] 已經明列（見 _WHEN_KEYWORDS），有時不是重複標到
    別的維度，而是直接對 When 回「找不到」，完全沒偵測到——即使長者原話
    裡的時間詞比 _void_pure_time_evidence_from_other_dims 能處理的案例（同一
    段文字同時被標到兩個維度）更明確，模型還是漏判。這種「該有的證據完全沒找到」
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


# _classify_question_dimension 專用，見 _backstop_what_copula 說明。
# 「A是什麼」這個判斷句結構本身只可能是在問A的名稱/內容（What），不可能
# 是在問方式(How)或原因(Why)，跟「為什麼」不會撞在一起（"為什麼"裡沒有
# 連續的"是什麼"三字）。
_WHAT_COPULA_RE = re.compile(r"是什麼")


def _backstop_what_copula(dimension: str | None, question_text: str) -> str | None:
    """
    _classify_question_dimension 專用，跟 _backstop_when_evidence 同一套
    「已知失效模式，靠關鍵詞規則直接覆蓋，不再賭模型這次判斷準不準」的
    做法：2026-09-09 實測發現本地模型會把「那道菜是什麼？」這種明顯在問
    「什麼事/什麼東西」的問題誤判成 How（見 session 9ff9bde9 round2
    的實例：target_w=How 生成出這句問題，_classify_question_dimension
    也判成 How，沒被攔下，導致長者已經答過的How繼續卡在「未涵蓋」，
    下一輪又問出語意重複的題目）。

    「X是什麼」是判斷句結構，文法上只可能是在問X的名稱/內容，不可能是
    在問方式(How)或原因(Why)——只在模型分類成這兩個維度時才覆蓋成
    What，分類成 Where/Who/When 時維持模型原本判斷（這幾個維度的疑問詞
    跟「是什麼」語意不重疊，還沒觀察到被這個結構誤導的案例，不需要
    覆蓋）。

    只能攔到明確帶有「是什麼」這個結構的案例（例如「那一頓你們都吃了
    哪些菜？」这种「哪些X」結構不會被攔到）——這是已知的覆蓋範圍限制，
    不是這條規則想解決的問題，那類案例目前還是得靠模型自己判斷準不準。
    """
    if dimension not in ("How", "Why"):
        return dimension
    if _WHAT_COPULA_RE.search(question_text):
        return "What"
    return dimension


# 純粹主觀偏好/情緒詞（例如「喜歡」）不描述任何具體氣味或味道，卻可能被
# 模型同時標成「嗅覺」跟「味覺」的證據——跟 _void_pure_time_evidence_
# from_other_dims 同一套「拿掉已知的非實質內容詞彙後，如果什麼都不剩，
# 代表證據本身沒有實質內容」判準，這裡拿掉常見的主觀偏好/情緒詞彙，不
# 限定哪個維度（任何感官維度都可能被這類詞彙誤標）。
_NON_SENSORY_OPINION_RE = re.compile(
    r"喜歡|討厭|愛吃|不愛|覺得|認為|印象深刻|印象|記得|不錯|還好|很棒|"
    r"開心|難過|高興|滿意"
)


def _void_non_sensory_opinion_evidence(checks: list) -> list:
    """
    _detect_covered_senses 專用，跑在 _missing_from_checks 之前。把純粹
    主觀偏好/情緒詞（不描述任何具體感官內容本身）當成的感官證據清成
    「找不到」，理由見上方說明。
    """
    result = []
    for c in checks:
        if not isinstance(c, dict):
            result.append(c)
            continue
        evidence = (c.get("evidence") or "").strip()
        if evidence and evidence != "找不到":
            stripped = _NON_SENSORY_OPINION_RE.sub("", evidence)
            if not stripped.strip():
                c = {**c, "evidence": "找不到"}
        result.append(c)
    return result


# _missing_from_checks 的 allow_cross_dimension_gap 用：縫隙如果只是這些
# 沒有實質內容的連接詞/標點（例如「五點『，就』等他們下班」中間的「，就」），
# 不需要在其他維度的證據裡找到對應，直接視為可放行的縫隙——這種詞不可能是
# 任何維度「專屬」的內容，跟需要「縫隙=其他維度已驗證證據」那條規則要防的
# 「憑空編造內容」是不同的風險等級。
_GAP_FILLER_RE = re.compile(r"^[，,、。！？\s]*(?:就|便|即|才|都|也|還|再)?[，,、。！？\s]*$")


def _mark_task_exception_retrieved(task: asyncio.Task) -> None:
    """給 asyncio.create_task 背景任務掛 add_done_callback 用（見
    _start_scene_after_detail 的 pre_image_covered_w_task）：如果呼叫端
    在真正 await 到這個 task 之前，因為別的例外提早離開函式，這個 task
    就永遠不會被 await——若它本身也失敗，asyncio 會印出「Task exception
    was never retrieved」的警告噪音。這裡只是呼叫 task.exception() 把
    例外標記為「已讀取」，不做任何處理；如果呼叫端後續真的有機會
    await 到這個 task，結果/例外的行為完全不受影響。"""
    if not task.cancelled():
        task.exception()


def _find_evidence_gap(evidence: str, source_text: str) -> str | None:
    """
    見 _missing_from_checks 的 allow_cross_dimension_gap 說明。若 evidence
    可以拆成「前段＋原文裡的
    縫隙＋後段」，且前段、後段各自都是原文裡真實存在的片段（前段在縫隙
    前、後段在縫隙後），回傳縫隙文字；找不到這種拆法回傳 None。只嘗試
    「一個缺口」的情況（例如「一起烤肉」拆成「一起」＋缺口＋「烤肉」），
    不處理更複雜的多段缺口——真的需要放寬到這麼複雜的情況，代表證據
    本身已經編得太離譜，不該無條件放行。
    """
    for i in range(1, len(evidence)):
        prefix, suffix = evidence[:i], evidence[i:]
        start = source_text.find(prefix)
        if start == -1:
            continue
        prefix_end = start + len(prefix)
        suffix_start = source_text.find(suffix, prefix_end)
        if suffix_start == -1:
            continue
        return source_text[prefix_end:suffix_start]
    return None


def _missing_from_checks(
    items: list[str], checks: list, key: str, source_text: str,
    allow_cross_dimension_gap: bool = False,
) -> list[str]:
    """
    比對 items 清單跟 LLM 逐項核對回傳的 checks，判斷哪些項目算漏掉，給
    _find_missing_memory_items 用。除了 _is_real_evidence 逐項驗證外，還抓
    兩種漏網：(1) item 在 checks 裡完全沒有對應 entry——模型漏檢查、不是
    給了無效證據；(2) evidence 文字整段跟前面某一項重複——模型偷懶複製
    別項證據湊數，雖然通過 substring 檢查，但同一段證據不太可能同時是
    兩個不同細節的專屬視覺內容，重複一律當漏掉。

    allow_cross_dimension_gap：evidence 前後兩段都是原文真實片段、但中間
    有缺口（例如「一起烤肉」漏掉中間的「在院子」）時，只有這個缺口本身
    能在同一批次裡「其他維度」已驗證的證據裡找到，才放行——不是無條件
    放寬「跳字」，避免這條規則被拿來掩護真正編造的內容。只有
    _check_pre_image_basic_dims／_detect_pre_image_w_coverage／
    _detect_covered_w／_detect_covered_senses 這幾個「同一批次核對多個
    維度」的呼叫端適合開啟；核對「延伸方向」或「生圖prompt有沒有涵蓋畫面
    元素」（錯誤代價是生出長者沒說過的內容）的呼叫端維持預設 False。
    """
    checked: dict[str, str] = {}
    for c in checks:
        if isinstance(c, dict) and c.get(key):
            checked[c[key]] = (c.get("evidence") or "").strip()

    accepted_evidence_texts: list[str] = []
    if allow_cross_dimension_gap:
        accepted_evidence_texts = [
            ev for dim, ev in checked.items()
            if ev and ev != "找不到" and _is_real_evidence(ev, source_text, dim)
        ]

    missing = []
    seen_evidence: set[str] = set()
    for item in items:
        evidence = checked.get(item)
        if evidence is None:
            missing.append(item)
            continue
        evidence_norm = evidence.lower()
        is_valid = _is_real_evidence(evidence, source_text, item)
        if not is_valid and allow_cross_dimension_gap and evidence != "找不到":
            gap = _find_evidence_gap(evidence, source_text)
            is_valid = bool(gap) and (
                _GAP_FILLER_RE.match(gap) is not None
                or any(gap in accepted for accepted in accepted_evidence_texts)
            )
        if evidence_norm in seen_evidence or not is_valid:
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
    問一次LLM——兩者是用不同措辭框架各自判斷同一句長者回答的獨立LLM呼叫，
    判斷本身非決定性，直接丟掉前者已查到的證據重問可能得出矛盾答案，把
    長者答過的內容誤判成沒答。

    只回傳「有真的查到證據」的維度（Why 沒有對應的基本維度，一律不出現）；
    回傳非空list就代表可信證據足以直接判定情境2；三項都沒查到（空list）
    時，才需要呼叫 _detect_pre_image_w_coverage 做完整的四維度判斷
    （含Why）。
    """
    covered = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        mapped = _PRE_IMAGE_BASIC_DIM_MAP.get(c.get("dimension"))
        if mapped and _is_real_evidence(c.get("evidence"), source_text, mapped):
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
    片語的子句，整段丟掉。保證送去生圖 API 的內容不會出現人物，不依賴
    模型的判斷或聽話程度。

    刪掉人物詞本身會留下三種破碎殘留，各自對應下面的清理規則：所有格
    孤兒（"woman's birthday"→"'s birthday"，多吃掉緊接的's）、介系詞+
    動名詞沒有主詞（"surrounded by family members eating"→"surrounded by
    eating"，整段子句丟掉）、句尾孤兒介系詞（"backyard barbecue with
    family"→"...with"，只刪介系詞本身，保留前面場景內容）。
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
    後面用：光是 prompt 文字裡沒有人物詞不夠，生圖模型（gpt-image-2）看到
    「grilling on a barbecue」這類本身就隱含「有人在做」的動作描述，還是
    會自己腦補畫出人物——這是模型自己的推論層級，不是拿掉人物詞就能擋住
    的。額外加一句明確排除指示（不是「沒提到人」，是「明講不要有人」），
    插在固定的畫風/色調片語之前，讓它算進場景指示、不被
    _strip_style_descriptors 濾掉；找不到標記時（理論上不會發生）就直接
    接在句尾。

    chinese: image_prompt 的語言。is_direct_detail（長者原話直接翻譯）
    路徑是中文，固定色調片語「懷舊溫暖色調」在開頭；RAG／anchor路徑
    （_build_plan_image_prompt）是英文，固定色調片語"nostalgic warm
    tones"在結尾。兩種語言各自對應自己的指示文字跟插入點 marker，不要
    混用。
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


def _load_prompt_modules(*filenames: str) -> str:
    """
    從 app/prompts/modules/ 依序讀取多個 prompt 片段檔案、串接成一份
    system_content。每個片段都是從 question_5w1h.txt 原封不動切出來的，
    不是重寫，也不新增內容。

    挑選片段給哪支函式用時要守兩條規則：
    1. 只有真的會輸出「思考：」欄位的函式才帶 format_and_examples.txt——
       這個模組裡的範例反覆示範「思考：／問題：／問題類型：」這組STEP1/2/3
       專用格式，帶給不用這個格式的函式會把輸出帶偏。
    2. 【提問規則】【禁止事項】【思考欄位／輸出格式／範例】要嘛整段一起載入，
       要嘛都不載入，不要單獨抽走【禁止事項】跟別的片段接在一起——原始
       檔案裡它刻意緊貼在「思考：」欄位之前，抽離會削弱約束力。

    找不到的檔案直接跳過（回傳空字串），不中斷其餘片段的組合。片段之間
    用空行隔開，避免上一個檔案的最後一行跟下一個檔案的第一行黏在一起。
    """
    parts = [_load_prompt(f"modules/{name}") for name in filenames]
    return "\n\n".join(p.rstrip("\n") for p in parts if p)


# 2026-09-13稽核：_detect_emotional_
# trigger 的YES/NO判斷完全交給一次LLM呼叫，實測發現連「想到過世的先生，
# 鼻子有點酸」這種課本等級的死別案例，都可能被判成NO（本地弱模型對這類
# 單次整體判斷不穩定，是這個檔案裡到處都在處理的同一種問題）——一旦漏判，
# 這句話會被當成一般話題送進STEP2/3的續問流程，而不是真正該接手的情緒
# 支持流程，長者剛表達完喪親的情緒，卻可能收到一句跟畫面元素有關、答非
# 所問的承接語，比單純漏放格式瑕疵嚴重得多。跟 _is_quick_end／
# _backstop_when_evidence 同一種取捨：死別這類關鍵字明確到幾乎不會誤判
# （「過世」「去世」等詞在長者口語裡幾乎不會用在死別以外的語境），不值得
# 只靠一次不穩定的LLM判斷，用關鍵字規則兜底、命中就直接判定YES，不用
# 等LLM確認；LLM判斷仍保留，處理關鍵字覆蓋不到的模糊情況（自責沮喪、
# 話說到一半停住等沒有固定字面的訊號）。
_BEREAVEMENT_KEYWORDS_RE = re.compile(r"過世|去世|走了|往生|不在了|離開了|過身")


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
          round 1：問長者一句生圖前的破冰問題（這時還沒有圖片，細節見下方
                   原本的說明），生圖後出示圖片、留白讓長者說第一反應，長者
                   答完這句就結束——不在回合1內問STEP1（5W1H開場問題）。
          round 2：先承接回合1「出示圖片」的長者反應、問 STEP1 開場問題，
                   再接續自由追問（STEP2+STEP3），不生圖、不合成語音（STT
                   仍照常）。延續 round 1 最後定案的畫面元素／話題與長者看完
                   圖後的反應，第一步直接呼叫 _handle_image_reveal_answer
                   處理，見 _start_round2_free_followup。
          round 3：closing（回縮期：從過去回到現實，將情緒引導回正向），
                   不生圖、不合成語音（STT仍照常）。內容沿用 closing.txt／
                   _generate_closing，只是觸發時機提前到「開始第三回合」
                   （而不是像現行三回合結束後心得那樣，等三回合真的跑完才問）。
                   長者回答這題後，process_response 直接視為本回合（也是整場
                   療程）結束，交給既有 _end_action 的 current_round>=3 分支
                   （原本用來產生「心得」問題那段，這次改動刻意保留不動，
                   見該處說明），因此長者實際上會連續被問兩次「回縮期」風格
                   的問題——目前刻意接受，「心得」回合之後會再另外重新設計。

        round 2/3 開場都需要上一回合結束時的內容（round 2 需要 round 1 最後的
        畫面元素/話題與長者最後一句話；round 3 需要 round 2 最後一句話）才能
        承接得上，但前端 round 邊界會捨棄 state（見 Unity GameController.
        StartRound：呼叫 /session/round 不帶 state），所以由呼叫端
        （app/routers/session.py）在每回合最後一次 respond 時把需要的內容存進
        Redis，下一回合開始時讀出來、包成 carryover 傳進來。carryover 為 None
        或缺欄位時（例如 Redis 過期、或不是正常三回合序列）都有保底處理，
        不會拋例外。

        round 1 先問長者一句破冰問題 Q1（_build_pre_image_question：「說到
        {today_topic}，」接依16大主題分類挑的邀請語，見該函式與檔案開頭流程
        說明），讓生圖內容能跟長者這次真正想聊的細節有關，不是只憑RAG記憶
        跟長者資料憑空規劃。分類用 _classify_topic_category，一開始就跑，
        結果存進 state["topic_category"]，長者答得不夠具體、需要問 Q2 縮小
        範圍時直接複用，不用重複分類。長者回答後（process_response 收到
        last_question_type=="pre_image_q1" 時）才判斷細節夠不夠具體，最後
        呼叫 _start_scene_after_detail 規劃並生成圖片——長者親口說的細節
        優先當生圖元素來源，RAG 記憶只在長者沒給出可用細節時才當 fallback，
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
                   "pre_image_q1"，長者回答這題後才會真的生圖、出示圖片
                   （或先問 Q2 縮小範圍，見本檔頂部流程說明）；STEP1 問題
                   延後到 round 2 開場才生成，round 1 不會問
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

        category = await self._classify_topic_category(user)
        # topic_senses：見 _classify_topic_senses docstring 與
        # _SENSE_EXCLUDED_TOPIC_CATEGORIES 說明——哀傷之事／人生目標／生命
        # 中特殊的事件這三類刻意不給感官，硬性覆寫成空list，不交給分類器
        # 自行判斷（安全考量，不是「這個主題剛好沒有感官」）。
        topic_senses = (
            [] if category in _SENSE_EXCLUDED_TOPIC_CATEGORIES
            else await self._classify_topic_senses(user["today_topic"])
        )
        scene_text = _PRE_IMAGE_Q1_INTRO
        question, question_tts_text, question_audio_key = _build_pre_image_question(
            user["today_topic"], category,
        )
        print(f"  → 主題分類: {category!r}，適合感官: {topic_senses}，"
              f"開場語: {scene_text}，生圖前破冰問題: {question}")

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
            "topic_senses": topic_senses,  # 開場已分類過，後續感官追蹤需要時直接複用，不重複分類
            "cached_rag_memories": cached_rag_memories,  # 開場已撈過，fallback需要時直接讀
        }

        return {
            "user_name": user["name"],
            "today_topic": user["today_topic"],
            "scene_text": scene_text,
            "scene_elements": [],
            "image_path": "",
            "question": question,
            # question_tts_text／question_audio_key：見 _build_pre_image_
            # question 說明——Q1邀請語一定帶著治療師自由輸入的今日主題，
            # 沒辦法整句預錄，呼叫端（app/routers/session.py）改送
            # question_tts_text（動態前綴，或分類失敗時的整句保底問句）去
            # 即時TTS，播完後接著播 question_audio_key 對應的前端內建
            # 預錄音檔（分類失敗時是 None，不用接）。
            "question_tts_text": question_tts_text,
            "question_audio_key": question_audio_key,
            "memories_used": [],
            "state": state,
        }

    async def _start_round2_free_followup(
        self, user: dict, user_id: str, session_id: str, carryover: dict | None,
    ) -> dict:
        """
        第二回合開場：承接回合1「出示圖片」的長者反應，先問 STEP1 開場問題，
        再接續自由追問（STEP2+STEP3），不生圖、不合成語音。

        2026-08-18起：回合1只問「出示圖片」那句（_IMAGE_REVEAL_SCENE_TEXT／
        _IMAGE_REVEAL_QUESTION）就直接結束（見 process_response 對
        last_question_type=="image_reveal" 的短路處理），5W1H（STEP1）留到
        這裡才開始問。原本 process_response 裡「長者看完圖後依反應分類、
        生成承接語、接上STEP1」那段邏輯抽成 _handle_image_reveal_answer，
        這裡是它唯一的呼叫點：用 round 1 carryover 的長者回答
        （carryover["last_elder_response"]，也就是長者對「你看看這張圖，
        想到什麼都可以跟我說」的回答）當 elder_response 觸發，同步算出
        承接語＋STEP1開場問題，回傳 action="scene_ready"、state 轉成
        last_question_type=="step1"。長者答完STEP1後會自然落入
        process_response 的一般分支（開放追問／補問W／回合結束判斷不特別
        檢查 last_question_type 是不是 "step1"），STEP2/STEP3 延續跟改動前
        round 1 STEP1 之後的行為完全相同，不需要另外處理。

        covered_w／covered_senses 直接沿用 round 1 結束時（其實是生圖前
        Q1/Q2 訪談自然涵蓋的內容，這時候 STEP1 還沒問過）的版本繼續累積，
        不歸零重算——這整段本質上是同一輪連續的5W1H訪談，只是行政上跨了
        round 1／round 2 這個邊界（供題數上限、carryover傳輸使用），不是
        round 1 結束後重開一輪獨立的5W1H追蹤，所以不需要像改動前那樣另開
        known_facts_w 當「軟性排除」用的影子欄位——直接讓 covered_w 帶著
        走即可。
        """
        carryover = carryover or {}
        scene_elements = carryover.get("scene_elements") or []
        scene_composition = carryover.get("scene_composition", "")
        pre_image_detail = carryover.get("pre_image_detail", "")
        topic_category = carryover.get("topic_category")
        topic_senses = carryover.get("topic_senses") or []
        last_elder_response = carryover.get("last_elder_response", "")
        emotion = carryover.get("emotion") or "happy"
        round1_covered_w = carryover.get("round1_covered_w") or []
        round1_covered_senses = carryover.get("round1_covered_senses") or []
        print(f"  → round 2開場 carryover 核對: round1_covered_w={round1_covered_w}，"
              f"round1_covered_senses={round1_covered_senses}，"
              f"last_elder_response={last_elder_response!r}")

        # 組一份「回合1剛結束時」的狀態，交給 _handle_image_reveal_answer
        # 當它原本在 process_response 裡處理 last_question_type=="image_
        # reveal" 時會讀到的 state——欄位需要跟 SessionState（app/routers/
        # session.py）對齊，之後才能透過 API 正常往返存活，不會被 FastAPI
        # 驗證悄悄丟棄（這個檔案已經因為漏宣告欄位踩過好幾次這個坑，見
        # SessionState 各欄位上方註解）。
        synthetic_state = {
            "user_id": user_id,
            "session_id": session_id,
            "round": 2,
            "scene_elements": scene_elements,
            "scene_composition": scene_composition,
            "covered_w": round1_covered_w,
            "skipped_w": [],
            "known_facts_w": [],
            "covered_senses": round1_covered_senses,
            "skipped_senses": [],
            "last_question_text": carryover.get("round1_last_question", ""),
            "last_question_type": "image_reveal",
            "last_w_asked": "",
            "last_sense_asked": "",
            "question_count": 0,
            "supplement_count": 0,
            "topic_category": topic_category,
            "topic_senses": topic_senses,
            "cached_rag_memories": [],
            "pre_image_q1_answer": "",
            "pre_image_detail": pre_image_detail,
        }
        result = await self._handle_image_reveal_answer(
            user, synthetic_state, last_elder_response, emotion,
            quick_end=self._is_quick_end(last_elder_response),
        )
        return {
            "user_name": user["name"],
            "today_topic": user["today_topic"],
            "scene_text": result["scene_text"],
            "scene_elements": scene_elements,
            "image_path": "",
            "question": result["question"],
            "memories_used": [],
            "state": result["state"],
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
        prior_rounds_transcript = carryover.get("prior_rounds_transcript", "")
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
            # 見 guarded_generate 的 retry_temperature 參數說明：收尾語違規
            # 重試時，預設temperature下常常幾乎每次輸出同一句話，拉高重試
            # 溫度能有效跳脫。
            retry_temperature=0.9,
            user=user, elder_response=last_elder_response, emotion=emotion,
            prior_rounds_transcript=prior_rounds_transcript,
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
        on_generating_image=None,
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
        避免同一句話因為LLM判斷非決定性、再問一次可能得出矛盾答案（外層判定
        「夠具體」才呼叫這裡，這裡卻判定「不夠具體」）。留 None（預設）給還
        沒判斷過的呼叫端（例如 pre_image_q2 合併Q1+Q2的情況），維持原本行為。
        """
        session_id = state["session_id"]
        round_number = state["round"]

        if on_generating_image:
            # 確定會生圖（image_plan／self.image.generate 都在下面才會呼叫），
            # 在這些慢動作之前先通知，前端才能在真正等待生圖時顯示「生圖中」，
            # 不會在追問Q2那種不生圖的分支誤觸發。
            await on_generating_image()

        if detail_is_usable is None:
            detail_is_usable = bool(elder_detail) and await self._has_usable_detail(elder_detail)

        is_direct_detail = bool(elder_detail) and detail_is_usable
        if is_direct_detail:
            memories = [{"summary": elder_detail}]
            # 長者這句生圖前的訪談回答通常已經自然帶出幾個W維度（例如提到
            # 誰、在哪裡、發生什麼事），不該生完圖就把這些內容丟掉、讓後面
            # 的5W1H流程從零開始重問一次——用跟 STEP2 背景追蹤同一套判斷
            # （_detect_covered_w），把這裡偵測到的W維度接續進 state，STEP1/
            # STEP3 就會自動避開已經聊過的方向。這個結果一直到下面組
            # new_state 才會用到，中間還要做脫敏、規劃/生成圖片這一長串
            # 工作，彼此互不依賴——用 create_task 先背景送出去，跟後面的
            # 脫敏/生圖同時進行，真正需要值的時候再 await，省下這次LLM
            # 往返原本要排隊等待的時間。
            pre_image_covered_w_task = asyncio.create_task(
                self._detect_covered_w(elder_detail, [])
            )
            # 這個 task 一直到下面才會 await，中間 _plan_image_from_detail／
            # _plan_image（沒包 try/except）如果先丟例外，函式會直接往外
            # 傳播、永遠不會走到下面 await 這個 task 的那一行——如果 task
            # 本身也剛好失敗，asyncio 會印出「Task exception was never
            # retrieved」的警告。掛一個 done_callback 把例外標記為已讀取，
            # task 本身還是照常在背景跑完，不影響任何邏輯。
            pre_image_covered_w_task.add_done_callback(_mark_task_exception_retrieved)
        else:
            # 退回 RAG 記憶時，elder_detail 不是這次生圖真正的內容來源
            # （太空洞或長者沒回答），不能拿它來判斷涵蓋了哪些W，也沒有
            # 實質內容可以在下面存進 state 給 quick_end 之後呼應。
            # RAG 已經在 start_round 一開始就撈過、存進 state["cached_rag_
            # memories"]，這裡直接讀，不用再發一次查詢讓長者多等一輪。
            memories = state.get("cached_rag_memories", [])
            pre_image_covered_w_task = None
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
            # 「1980年代」會被_remove_years誤判成確切年份換成「從前」（這條
            # 跟姓名判斷邏輯無關，不管_remove_chinese_names用regex還是
            # 2026-08-17改用的CKIP NER都一樣會誤傷，因為_remove_years本身
            # 就是單純比對「19xx年」這種年份格式，不分辨是不是使用者輸入）。
            # 固定指令不是使用者輸入，不需要也不能再套用這套針對中文個資
            # 設計的脫敏流程，這裡只保留防禦性的taboo原字串比對（純substring
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
        print(
            f"  → [DEBUG] safe_prompt（去識別化後，實際送給"
            f"{type(self.image).__name__}）: {safe_prompt}"
        )
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

        pre_image_covered_w = (
            await pre_image_covered_w_task if pre_image_covered_w_task else []
        )
        print(f"  → 生圖前訪談已自然涵蓋 W: {pre_image_covered_w}")

        # 圖生成後不直接問 STEP1，先出示圖片、留白讓長者自己反應——長者這句
        # 反應由 process_response 收到 last_question_type=="image_reveal"
        # 時直接結束 round 1（2026-08-18起，見該分支說明），分類反應、生成
        # 承接語、接上真正的 STEP1 開場問題延後到 round 2 開場才做（見
        # _handle_image_reveal_answer／_start_round2_free_followup）。這裡
        # 先把 scene_elements/scene_composition 存進 state，因為這兩個值要
        # 撐到 round 2 開場（透過 carryover）才會用到。
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

    async def _handle_image_reveal_answer(
        self, user: dict, state: dict, elder_response: str, emotion: str, quick_end: bool,
    ) -> dict:
        """
        出示圖片問題答完後：依長者反應分類、生成承接語，再接上 STEP1 開場問題。

        由回合2開場（_start_round2_free_followup）第一步觸發——回合1問完
        「出示圖片」那句（_IMAGE_REVEAL_SCENE_TEXT／_IMAGE_REVEAL_QUESTION）
        就直接結束（見 process_response 對 last_question_type=="image_
        reveal" 且 state["round"]==1 的短路處理），5W1H（STEP1）留到回合2
        才開始問，用 round 1 carryover 的長者回答（carryover["last_elder_
        response"]）當 elder_response 觸發。

        「情緒明顯（不安）」已被更前面的 _detect_emotional_trigger 攔截走，
        不會走到這裡；這裡處理的是其餘2類（相符/有差異）＋「情緒明顯
        （感動）」，見 _generate_image_reveal_reaction。
        """
        covered_w = list(state["covered_w"])
        skipped_w = list(state["skipped_w"])
        scene_els = state["scene_elements"]
        scene_comp = state.get("scene_composition", "")

        if quick_end:
            # 長者沒有特別想法／沒回應（例如「沒有」「還好」），沒有真正
            # 的反應可以分類承接，硬套反應分類容易答非所問（例如把
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
                        "question": _image_reveal_fallback_question(scene_els, pre_image_detail),
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
                    # 沒設這個欄位的話，round 1結束時state["last_question_
                    # text"]讀不到這一題，帶進round 2 carryover的
                    # round1_last_question（見_start_round2_free_followup）
                    # 會是空字串，round 2 開場完全不知道round 1最後一題
                    # 實際問了什麼。
                    "last_question_text": result["question"],
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
                    "question": _image_reveal_fallback_question(scene_els, pre_image_detail),
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
                "last_question_text": q["question"],
            }
            return {
                "action": "scene_ready",
                "scene_text": _IMAGE_REVEAL_QUICK_END_ACK,
                "question": q["question"],
                "state": new_state,
            }
        pre_image_detail = state.get("pre_image_detail", "")
        result = await guarded_generate(
            self._generate_image_reveal_reaction,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            max_retry=3,  # 理由同其他生成呼叫：多幾次嘗試換更高機率避開保底句
            text_keys=("reaction_text",),
            # _element_fallback 回傳的 dict 是 scene_text/question 這組 key，
            # 這裡 text_keys 換成 reaction_text，不能直接沿用，否則 fallback
            # 真的觸發時 result['reaction_text'] 會 KeyError。
            fallback={"reaction_text": ""},
            # 2026-09-10起 reaction_text 已改成依 classification 查
            # _IMAGE_REVEAL_REACTION_TEMPLATES 固定句（見該常數說明），不是
            # LLM 現寫，語氣已經過長期稽核核可。不加這個參數的話，guarded_
            # generate 仍會拿這句寫死的模板去跑 ai_claims_personal_memory_llm
            # ——模板1「聽你這樣說，我彷彿也看到了當時的畫面。」被穩定判成
            # 「疑似冒用經歷」，只要分類結果不變，重試幾次都是同一句話、同一個
            # 判定，白白燒光3次重試額度、每次都失敗，最後連累到要動用
            # pre_image_detail 那層保底（見下方 result["reaction_text"] 判斷）。
            skip_ack_memory_check=True,
            user=user, scene_elements=scene_els, elder_response=elder_response,
            scene_composition=scene_comp, covered_w=covered_w,
            pre_image_detail=pre_image_detail, emotion=emotion,
        )
        print(f"  → 出示圖片反應分類: {result.get('classification', '')!r}"
              f"（依據: {result.get('judgment_evidence', '')!r}）")
        # 判斷依據是空的，代表模型沒有真的針對長者這句話推理就直接下了
        # 分類——prompt本身有明文要求「看不出線索也要老實寫『反應內容簡短，
        # 看不出明確線索』，不能空著」（見上面【輸出格式】說明），空字串
        # 必然是違反格式。實測發現這種情況常伴隨照抄prompt裡少樣本範例的
        # 分類內容交差（2026-09-08 稽核：長者說「很像當時的氛圍」這種單純
        # 肯定、沒有轉折的反應，卻被判成分類2；當時承接語仍由LLM現寫，也
        # 幾乎原文照抄範例1的措辭，這個失效模式後來促成改用固定模板，見
        # _IMAGE_REVEAL_REACTION_TEMPLATES）。
        # 跟 STEP2/STEP3 補問角度對不上時「帶retry_feedback重打一次」同一
        # 套修法：判斷依據是唯一可靠、跟語意無關的格式訊號，不用另外猜
        # 長者這句話「應該」是哪個分類（那樣容易對到 prompt 自己也還在
        # 處理的模糊地帶，例如「很像...但...」這種真的該判分類2的句子）。
        if result.get("classification") and not result.get("judgment_evidence"):
            print("  → 出示圖片反應分類判斷依據是空的（疑似照抄範例），"
                  "帶 retry_feedback 重打一次")
            retry_feedback = (
                "上一次的輸出「判斷依據」欄位是空的，沒有先針對長者這句"
                f"「{elder_response}」具體列出裡面的線索就直接下了分類，"
                "也不能直接照抄範例的分類內容交差。請重新逐字檢查這句話裡"
                "有沒有肯定/相似詞、差異詞、比較級詞或明顯情緒字眼，寫出"
                "具體的判斷依據（看不出明確線索就老實寫「反應內容簡短，"
                "看不出明確線索」），再根據這個依據決定分類。"
            )
            result = await guarded_generate(
                self._generate_image_reveal_reaction,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=3,
                text_keys=("reaction_text",),
                fallback={"reaction_text": ""},
                skip_ack_memory_check=True,  # 理由同上一次呼叫：reaction_text是固定模板，不需要重查冒用經歷
                user=user, scene_elements=scene_els, elder_response=elder_response,
                scene_composition=scene_comp, covered_w=covered_w,
                pre_image_detail=pre_image_detail, emotion=emotion,
                retry_feedback=retry_feedback,
            )
            print(f"  → 重打後：出示圖片反應分類: {result.get('classification', '')!r}"
                  f"（依據: {result.get('judgment_evidence', '')!r}）")
        # 另一種失效模式：判斷依據不是空的，模型是真的針對這句話寫了理由，
        # 但理由本身誤用了prompt自己教的規則——prompt明講「單純正面、沒有
        # 提到任何具體差異，不能因為長者沒有明確說『像』或『一致』就判成
        # 分類2」，這裡剛好相反：長者明確用了「像/很像/差不多」這類肯定詞，
        # 判斷依據卻寫「只籠統說像，沒有具體講出哪裡相似」，把「沒講細節」
        # 當成分類2的理由（2026-09-08稽核第二次：同一句「很像當時的氛圍」，
        # 這次判斷依據不是空的，上面那條「空白才重打」的檢查攔不住，只能
        # 另外用關鍵字複查：長者這句話裡如果有肯定/相似詞、且沒有否定詞
        # 或比較級差異詞，分類卻是2，大機率是這種誤用規則的情況）。
        _positive_match = re.search(r"(?<!不)(?:很像|蠻像|滿像|差不多|一致|沒錯|就是這樣|像)", elder_response)
        _comparative_diff = re.search(r"更(?:大|小|多|少|高|矮|快|慢)|比較(?:大|小|高|矮|多|少)|沒有那麼", elder_response)
        if result.get("classification") == "2" and _positive_match and not _comparative_diff:
            print(f"  → 出示圖片反應分類疑似誤判(長者反應含肯定/相似詞、"
                  f"無比較級差異詞，卻判成分類2)，帶 retry_feedback 重打一次: "
                  f"{elder_response!r}")
            retry_feedback = (
                f"長者這句反應「{elder_response}」裡有明確的肯定/相似詞"
                "（像/很像/蠻像/滿像/差不多），且沒有出現「不像/不一樣」這類"
                "否定詞、也沒有「更大/更小/比較高/比較矮/沒有那麼X」這類比較"
                "級差異詞，這種情況依規則屬於分類1（覺得圖跟自己記得的一致），"
                "不能因為長者沒有具體講出「哪裡」相似，就當成分類2的理由——"
                "分類2成立的前提是反應裡真的有差異/落差的訊號，單純的肯定詞"
                "本身不構成差異訊號。請改判分類1。"
            )
            result = await guarded_generate(
                self._generate_image_reveal_reaction,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=3,
                text_keys=("reaction_text",),
                fallback={"reaction_text": ""},
                skip_ack_memory_check=True,  # 理由同上一次呼叫：reaction_text是固定模板，不需要重查冒用經歷
                user=user, scene_elements=scene_els, elder_response=elder_response,
                scene_composition=scene_comp, covered_w=covered_w,
                pre_image_detail=pre_image_detail, emotion=emotion,
                retry_feedback=retry_feedback,
            )
            print(f"  → 重打後：出示圖片反應分類: {result.get('classification', '')!r}"
                  f"（依據: {result.get('judgment_evidence', '')!r}）")
        # 反方向的同一種誤判（2026-09稽核，實測+temperature=0後發現是穩定
        # 重現、不是取樣運氣）：長者這句話開頭是「很像」這類肯定/相似詞，
        # 後段卻接了明確的比較級差異（「不過我家廚房好像沒有那麼大」這種），
        # 模型會被開頭的肯定詞定錨，整句直接判成分類1，完全忽略後段的
        # 差異內容——即使 prompt 裡就有幾乎一模一樣的範例句（「很像當時的
        # 場景，但以前我們家不會有那麼多人一起烤肉」）明文教了正確答案是
        # 分類2，模型仍然會判錯，多次測試100%重現，retry_feedback這條路
        # 已經測過走不通（模型連自己prompt裡的範例都學不會），改成直接用
        # 規則覆寫，不再讓模型重打。
        #
        # 這裡不管有沒有同時出現肯定詞，只要偵測到 _comparative_diff 這類
        # 明確的比較級差異訊號、當下分類卻是1，就直接覆寫成2——prompt自己
        # 的規則2本來就寫明「不管有沒有肯定詞，只要有差異訊號就算分類2」，
        # 覆寫的判準跟模型該遵守的規則是同一條，不是另外發明一套。
        if result.get("classification") == "1" and _comparative_diff:
            print(f"  → 出示圖片反應分類疑似誤判(長者反應含比較級差異詞"
                  f"「{_comparative_diff.group(0)}」，卻判成分類1)，"
                  f"依規則直接覆寫為分類2: {elder_response!r}")
            result["classification"] = "2"
            result["judgment_evidence"] = (
                f"{result.get('judgment_evidence', '')}"
                f"（規則覆寫：偵測到比較級差異詞「{_comparative_diff.group(0)}」，"
                f"依規則2改判分類2）"
            ).strip()
        # 長者看完圖的反應是在評論AI示意圖畫得準不準（像不像、哪裡不一樣），
        # 不是主動在敘述回憶本身，跟STEP2自由對話裡長者真的在講故事時性質
        # 不同——不呼叫 _detect_covered_w，這種評論內容硬套進5W1H覆蓋度會
        # 讓W追蹤失真。
        #
        # 若這一層連續違規（例如照抄範例、編造判斷依據）退回完全通用的固定
        # 保底句，跟長者剛才說的話完全無關；生圖前的Q1/Q2（pre_image_
        # detail）若有實質內容，改用 _generate_quick_end_recap 當第二層
        # 保底——那支函式任務更單純（只呼應pre_image_detail+問STEP1，不用
        # 再賭一次反應分類），成功機率比重試整個分類任務高，退回的內容也
        # 還貼著長者剛才講過的東西。reaction_text 判斷式空字串只有
        # guarded_generate 真的退回上面那組 fallback 才會發生，可以用來
        # 判斷第一層是否真的落到保底。
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
                    "question": _image_reveal_fallback_question(scene_els, pre_image_detail),
                    "covered_w": [],
                },
                user=user, scene_elements=scene_els, pre_image_detail=pre_image_detail,
                scene_composition=scene_comp, covered_w=covered_w, emotion=emotion,
            )
            # _generate_quick_end_recap 這條保底路徑本來就是自己生成
            # 完整的「呼應pre_image_detail＋STEP1問題」，不需要、也不該
            # 再疊一次下面的獨立問題生成呼叫。
        else:
            # STEP1問題獨立呼叫 _regenerate_image_reveal_question 生成
            # （該函式 user_content 故意不放【長者看完圖後的第一反應】，
            # 見上面 _generate_image_reveal_reaction docstring）。
            q_result = await guarded_generate(
                self._regenerate_image_reveal_question,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=3,
                text_keys=("question",),
                fallback={
                    "question": _image_reveal_fallback_question(scene_els, pre_image_detail),
                    "covered_w": [],
                },
                user=user, scene_elements=scene_els, elder_response=elder_response,
                scene_composition=scene_comp, covered_w=covered_w,
                pre_image_detail=pre_image_detail, emotion=emotion,
            )
            result["question"] = q_result["question"]

        print(f"  → 出示圖片承接: {result['reaction_text']}｜STEP1問題: {result['question']}"
              f"（自報W: {result.get('covered_w', [])}，不採信，見上方covered_w說明）")
        # covered_w 這裡帶的是上面 _detect_covered_w 更新過的版本（長者
        # 這句反應裡自然涵蓋的W），不是LLM自報的那份——LLM在
        # result["covered_w"]裡自己標記的是「它剛寫的STEP1問題涵蓋哪些
        # W」，那份仍然不採信，理由同上面兩條STEP1路徑。
        new_state = {
            **state,
            "covered_w": covered_w,
            "skipped_w": skipped_w,
            "last_question_type": "step1",
            "last_w_asked": "",
            "question_count": 1,  # STEP1 開場問題算本回合第 1 題（出示圖片那題不算）
            "supplement_count": 0,
            # STEP1開場問題先前沒有設這個欄位，round 1結束時state[
            # "last_question_text"]因此讀不到這一題，帶進round 2
            # carryover的round1_last_question（見_start_round2_free_
            # followup）也會是空字串，round 2 開場完全不知道round 1
            # 最後一題實際問了什麼。補上這個欄位，讓STEP1這題也能正常被
            # round 2看見。
            "last_question_text": result["question"],
            "last_sense_asked": "",
        }
        return {
            "action": "scene_ready",
            "scene_text": result["reaction_text"],
            "question": result["question"],
            "state": new_state,
        }

    async def process_response(
        self,
        elder_response: str,
        state: dict,
        emotion: str = "",
        on_generating_image=None,
    ) -> dict:
        """
        狀態機核心：根據長者回應決定下一步。

        Args:
            elder_response: 長者說的話（STT 轉譯結果）
            state: 上一輪回傳的 state dict
            emotion: Kinect 即時偵測的情緒（happy/excited/angry/sad，見 app/routers/sensor.py）
            on_generating_image: 真正開始生圖（_start_scene_after_detail）前呼叫的
                async callback，供呼叫端（session.py）推播 WS 通知給前端顯示「生圖中」，
                跟 _has_usable_detail 等判斷無關——只在確定要生圖時才觸發，見
                _start_scene_after_detail 內的呼叫點。

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
        # 純空字串（長者真的沒開口，不是 _NO_RESPONSE_MARKER 這個固定
        # marker）也要排除，不能塞進 _detect_emotional_trigger 的 prompt
        # 問LLM「這句話有沒有情緒訊號」——對空白內容問這種問題是未定義行為，
        # 量化基底模型不保證每次都答NO；一旦誤判YES，這題會直接不受
        # _MAX_QUESTIONS_PER_ROUND／_MAX_SUPPLEMENT_PER_ROUND限制（見下面
        # is_emotional_trigger分支），可能導致長者全程沉默的回合遠超題數
        # 上限才收尾。
        is_emotional_trigger = (
            False if (not elder_response.strip()) or elder_response.strip() == _NO_RESPONSE_MARKER
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
                "last_question_text": result["question"],
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
            # 沒機會在Q2多說一點。只有真的沉默（被治療師跳過）或明確講不知道/不記得，
            # 才不用再多問一題。短回答會落到下面 _has_usable_detail 判斷，
            # 通常判NO、自然會進到問Q2那條路，不需要在這裡特別處理。
            elder_detail = "" if self._is_true_refusal(elder_response) else elder_response
            if not elder_detail:
                # 長者真的不想／不能答，不用再多問Q2，直接退回RAG記憶生圖。
                return await self._start_scene_after_detail(
                    user, state, "", on_generating_image=on_generating_image,
                )
            basic_checks = await self._check_pre_image_basic_dims(elder_detail)
            has_usable_detail = await self._has_usable_detail(elder_detail, checks=basic_checks)
            if has_usable_detail:
                # 已經夠具體，不逼長者多答一題。把剛剛判斷過的結果直接傳下去，
                # 不讓 _start_scene_after_detail 對同一句話重新問一次LLM——
                # 那是非決定性判斷，兩次呼叫可能得出不同答案，見該函式
                # detail_is_usable 參數的說明。
                return await self._start_scene_after_detail(
                    user, state, elder_detail, detail_is_usable=True,
                    on_generating_image=on_generating_image,
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
                    on_generating_image=on_generating_image,
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
                        on_generating_image=on_generating_image,
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
            picked = get_scenario2_followup(category, sub_item, covered_w, named_person)
            if not picked:
                # 該問的維度都已經在Q1涵蓋或被排除，不用再多問一題。
                return await self._start_scene_after_detail(
                    user, state, elder_detail, detail_is_usable=has_usable_detail,
                    on_generating_image=on_generating_image,
                )
            q2_question, q2_w, q2_audio_key = picked
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
                "pre_image_q2_last_w": q2_w,
            }
            return {
                "action": "pre_image_followup",
                "scene_text": "",
                "question": q2_question,
                "question_audio_key": q2_audio_key,
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
                    picked = get_scenario2_followup(
                        category, sub_item, covered_w, named_person,
                    )
                    if picked:
                        next_question, next_w, next_audio_key = picked
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
                            "pre_image_q2_last_w": next_w,
                        }
                        return {
                            "action": "pre_image_followup",
                            "scene_text": "",
                            "question": next_question,
                            "question_audio_key": next_audio_key,
                            "state": new_state,
                        }
                    print(f"  → 情境1追問Q2已有實質內容，bank判定不需再追問，"
                          f"已涵蓋: {covered_w}")
                    return await self._start_scene_after_detail(
                        user, state, combined, detail_is_usable=True,
                        on_generating_image=on_generating_image,
                    )
                return await self._start_scene_after_detail(
                    user, state, combined, on_generating_image=on_generating_image,
                )
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
                    on_generating_image=on_generating_image,
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
            # 上一輪問的維度（pre_image_q2_last_w）如果這輪還是沒被涵蓋，
            # 代表長者沒答到、或答非所問——記進 skipped_w，下面傳給
            # get_scenario2_followup 排除，不要再被 _PRE_IMAGE_PRIORITY_
            # ORDER 選中、連續好幾輪問同一個問題。
            skipped_w = list(state.get("skipped_w", []))
            last_w = state.get("pre_image_q2_last_w")
            # 這一題是 get_scenario2_followup 針對 last_w 直接追問的，長者
            # 只要不是拒答（q2_answer 已經在上面用 _is_true_refusal 篩過），
            # 不管回答內容有沒有被 _detect_covered_w 抓到對應證據，都直接
            # 算已經回答到這個維度——證據抽取本來就可能有雜訊，與其一直
            # 修補抽取規則，不如直接信任「問A答A」這個更可靠的事實來源，
            # 不讓通用的證據比對反過來否定長者剛剛針對性回答過的內容，導致
            # 同一個維度反覆被判定沒答到、卡住一直重問（跳針）。
            if last_w and last_w not in covered_w:
                covered_w.append(last_w)
            round_count = state.get("pre_image_q2_round", 1)
            max_rounds = state.get("pre_image_q2_max_rounds", 1)
            picked = None
            if round_count < max_rounds:
                category = state.get("topic_category")
                sub_item = state.get("pre_image_sub_item")
                named_person = _extract_named_person(combined)
                picked = get_scenario2_followup(
                    category, sub_item, covered_w, named_person, skipped_w,
                )
            if not picked:
                print(f"  → 情境2追問結束，已涵蓋: {covered_w}（共{round_count}輪）")
                new_state = {**state, "covered_w": covered_w, "skipped_w": skipped_w}
                return await self._start_scene_after_detail(
                    user, new_state, combined, detail_is_usable=True,
                    on_generating_image=on_generating_image,
                )
            next_question, next_w, next_audio_key = picked
            print(f"  → 情境2第{round_count + 1}輪追問: {next_question}，已涵蓋: {covered_w}")
            new_state = {
                **state,
                "last_question_type": "pre_image_q2",
                "pre_image_q1_answer": combined,
                "pre_image_q2_scenario": 2,
                "covered_w": covered_w,
                "skipped_w": skipped_w,
                "pre_image_q2_round": round_count + 1,
                "pre_image_q2_last_w": next_w,
            }
            return {
                "action": "pre_image_followup",
                "scene_text": "",
                "question": next_question,
                "question_audio_key": next_audio_key,
                "state": new_state,
            }

        # ── 出示圖片階段：長者剛看完圖說出第一反應 ──────────────────
        # 2026-08-18起：回合1問完「出示圖片」這句就直接結束，不在回合1內
        # 生成承接語反應或問STEP1——真正的反應分類＋STEP1生成移到回合2
        # 開場第一步驟執行（_start_round2_free_followup 呼叫
        # _handle_image_reveal_answer）。last_question_type=="image_reveal"
        # 只會在round 1發生（只有 _start_scene_after_detail 會設這個值，
        # 該函式只在round 1的生圖前訪談流程裡被呼叫），這裡不需要再檢查
        # round是不是1。
        if last_type == "image_reveal":
            return await self._end_action(state, user, elder_response, emotion)

        # ── 感官追蹤：跟 covered_w 同等規格，記錄這回合長者的回答裡已經
        # 自然帶到哪些感官描述，避免STEP2/3選感官
        # 當切入角度時，一直挑同一種、或挑主題本來就不相關的感官。只在這裡
        # （STEP1之後的延續流程）追蹤。同樣直接mutate state，讓下游函式透過
        # {**state, ...} 自然帶到最新版本。
        covered_senses = list(state.get("covered_senses", []))
        skipped_senses = list(state.get("skipped_senses", []))
        last_sense_asked = state.get("last_sense_asked", "")

        # ── 補問路徑：先確認上一個 W 是否被回答 ─────────────────
        # 跟下面 covered_senses 那段同一套邏輯：只要不是 quick_end 就算已回答。
        if last_type == "supplement_w" and last_w:
            if quick_end:
                skipped_w.append(last_w)
                return await self._next_step_or_end(
                    user, scene_els, covered_w, skipped_w, elder_response, state, emotion,
                    question_count, supplement_count, scene_composition=scene_comp,
                )
            if last_w not in covered_w:
                covered_w.append(last_w)
            print(f"  → W({last_w}) 視為已回答（追問+非quick_end），covered_w={covered_w}")

        # ── STEP2：自由對話中背景追蹤 W 覆蓋 ────────────────────
        # can_continue_from_detection：見 _detect_covered_w_and_senses
        # 2026-09-12稽核——已經把原本獨立的 _decide_topic_continuation
        # 判斷併進同一次LLM呼叫一起問完，下面「話題能否繼續？」不用再
        # 另外呼叫一次，直接複用這裡算出的結果（quick_end時該函式不會被
        # 呼叫，維持None，下面比照原邏輯視為不可繼續）。
        can_continue_from_detection: bool | None = None
        if not quick_end:
            # Ollama只有單一模型實例，兩次獨立LLM呼叫用asyncio.gather平行
            # 送出也還是在server端排隊處理，省不到時間——改用
            # _detect_covered_w_and_senses 把兩份維度清單、以及原本獨立的
            # 話題延伸判斷合併成一次呼叫問完，才是真的減少呼叫次數。
            newly_covered, newly_covered_senses, can_continue_from_detection = (
                await self._detect_covered_w_and_senses(
                    elder_response, covered_w, covered_senses, state.get("topic_senses"),
                )
            )
            for w in newly_covered:
                if w not in covered_w:
                    covered_w.append(w)
            if newly_covered:
                print(f"  → 自然涵蓋 W: {newly_covered}，covered={covered_w}")

            for s in newly_covered_senses:
                if s not in covered_senses:
                    covered_senses.append(s)
            if newly_covered_senses:
                print(f"  → 自然涵蓋感官: {newly_covered_senses}，covered_senses={covered_senses}")
        state["covered_senses"] = covered_senses

        # 跟上面 supplement_w／covered_w 對稱——上一題如果是感官提問
        # （_next_step_or_end 的感官補問分支選了 target_sense），這裡核對
        # 長者這句有沒有真的答到那個感官：答到了會出現在上面剛更新的
        # covered_senses裡；答不到就記進 skipped_senses，避免下次又選中
        # 同一個感官重問，跟skipped_w同等規格。
        if last_sense_asked and last_sense_asked not in covered_senses:
            # 跟 pre_image_q2 那邊 covered_w／last_w 同一套邏輯——上一題就
            # 是針對 last_sense_asked 直接追問的，長者只要不是quick_end
            # （真的沒答/拒答，見 _is_quick_end），不管 _detect_covered_
            # senses 的證據抽取有沒有剛好抓到對應片段，都直接算已經回答到
            # 這個感官，不讓證據抽取本來就有的雜訊反過來否定長者剛剛針對性
            # 回答過的內容，導致同一個感官反覆被判定沒答到、卡住一直重問。
            # 只有quick_end（真的沒有實質回答）才記進 skipped_senses。
            if quick_end:
                if last_sense_asked not in skipped_senses:
                    skipped_senses.append(last_sense_asked)
                    print(f"  → 感官({last_sense_asked})這輪沒答到，加進 "
                          f"skipped_senses，不再重問")
            else:
                covered_senses.append(last_sense_asked)
                print(f"  → 感官({last_sense_asked})視為已回答（追問+非"
                      f"quick_end），covered_senses={covered_senses}")
        state["skipped_senses"] = skipped_senses

        # ── 5W1H 全部自然涵蓋 → 結束回合（順其自然的好結局，不是硬湊出來的）──
        # 這裡是比 _next_step_or_end 更早、更常先被打到的收尾出口，判準要
        # 跟它一致：也要主題相關感官都問過（或這個主題本來就沒有相關感官）
        # 才真的結束，不能只看W有沒有全部涵蓋——否則5W1H一全部涵蓋就直接
        # 在這裡結束回合，根本輪不到後面的感官判斷。
        remaining_senses = _relevant_uncovered_senses(
            state.get("topic_senses"), covered_senses, skipped_senses,
        )
        if (
            not self._next_uncovered_w(covered_w, skipped_w) and not remaining_senses
            and question_count >= _min_questions_for_round(state["round"])
        ):
            print("  → 5W1H 全部涵蓋，結束回合")
            return await self._end_action(state, user, elder_response, emotion)

        # ── 單回合題數已達上限 → 結束回合（避免對話冗長）───────────
        max_questions = _max_questions_for_round(state["round"])
        if question_count >= max_questions:
            print(f"  → 已達單回合題數上限（{max_questions}），結束回合")
            return await self._end_action(state, user, elder_response, emotion)

        # ── 話題能否繼續？ ────────────────────────────────────────
        # 2026-09-12稽核：不再另外呼叫一次LLM——can_continue_from_detection
        # 已經在上面 _detect_covered_w_and_senses 那次合併呼叫裡問完，這裡
        # 直接複用（quick_end時該次呼叫被跳過，維持None，視為不可繼續，
        # 跟原本 can_continue=False 的行為一致）。
        can_continue = bool(can_continue_from_detection)
        print(f"  → 話題能否繼續: {can_continue}")

        if can_continue:
            # STEP2：承接情緒 + 開放追問（Track C），包上禁忌話題防護
            return await self._ask_open_continuation(
                user, scene_els, covered_w, skipped_w, elder_response, state, emotion,
                question_count, supplement_count, scene_composition=scene_comp,
            )
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

    async def _ask_open_continuation(
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
        STEP2 開放追問（Track C）的生成本體，抽成共用函式，有兩個呼叫點：
        (1) process_response 判斷 can_continue=True 的原本路徑——長者還在
        講、順著自然延伸；(2) _next_step_or_end 為了滿足 _MIN_QUESTIONS_
        PER_ROUND_BY_ROUND（round 2 最少3題，見該常數2026-08-20說明）多問
        一題——這時W維度、感官都已經沒有結構化目標可補，直接生一句開放式
        延續問題，不強加任何target，讓模型順著長者剛才的話自然接下去，
        不是硬湊一個問題出來（跟強制指定target_sense那條分支性質不同，
        那條至少還有明確的感官可以點名）。
        """
        result = await guarded_generate(
            self._generate_open_followup,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            # 2026-09-12稽核（「回合內話題延續判斷」耗時波動太大，
            # 實測後補）：這裡跟同一段回合內續問流程另外幾處 guarded_generate
            # 呼叫（_ask_supplement／_next_step_or_end 感官補問分支）原本都是
            # max_retry=3，最壞情況要跑4次完整LLM往返——2026-09-12新增的
            # scene_text_echoes_elder_response／scene_text_ignores_elder_
            # for_scene_elements 這兩條規則上線後，實測發現內容很薄的長者
            # 回應常常在超字／複誦／離題這幾條規則之間來回打轉，燒光3次重試
            # 退回保底句，耗時衝到20秒以上。降到2（最多3次完整往返）直接
            # 壓低這條路徑的最壞情況耗時，代價是這類難答的輸入會更早退回
            # 保底句——這條路徑本來就有 fallback 兜底，不影響正確性，只影響
            # 品質/速度的取捨（使用者已確認接受）。
            max_retry=2,
            # STEP2不再生成承接語（scene_text），只剩 question 這個欄位
            # 需要檢查。
            text_keys=("question",),
            # _generate_open_followup 回傳沒有 covered_w 這個 key，跟 STEP1/STEP3
            # 用的 _generate_question/_generate_supplement_question 不一樣。
            fallback=_element_fallback(
                scene_els, topic_category=state.get("topic_category"), with_covered_w=False,
            ),
            # 見 guarded_generate 的 retry_temperature 參數說明，跟
            # _start_round3_closing 同一種用法：低溫度重試常常輸出幾乎一字
            # 不差的過長問題，拉高重試溫度能有效跳脫（2026-09-11稽核，
            # 批次品質測試腳本抓到）。
            retry_temperature=0.9,
            user=user, scene_elements=scene_els, covered_w=covered_w,
            skipped_w=skipped_w, elder_response=elder_response, emotion=emotion,
            scene_composition=scene_composition, pre_image_detail=state.get("pre_image_detail", ""),
            covered_senses=state.get("covered_senses"),
            skipped_senses=state.get("skipped_senses"),
            topic_senses=state.get("topic_senses"),
        )
        new_state = {
            **state,
            "covered_w": covered_w,
            "skipped_w": skipped_w,
            "last_question_type": "open",
            "last_w_asked": "",
            "last_question_text": result["question"],
            "question_count": question_count + 1,
            "supplement_count": supplement_count,
        }
        return {
            "action": "open_followup",
            "scene_text": result["scene_text"],
            "question": result["question"],
            "state": new_state,
        }

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
            max_retry=2,  # 理由同 _ask_open_continuation 呼叫處2026-09-12稽核說明
            text_keys=("question",),  # STEP3不再生成承接語
            fallback=_element_fallback(
                scene_els, target_w=target_w, topic_category=state.get("topic_category"),
            ),
            # 理由同 _ask_open_followup 呼叫處：低溫度重試常常輸出幾乎一字
            # 不差的過長問題，拉高重試溫度能有效跳脫（2026-09-11稽核，
            # 批次品質測試腳本抓到）。
            retry_temperature=0.9,
            user=user, scene_elements=scene_els, covered_w=covered_w, target_w=target_w,
            emotion=emotion, elder_response=elder_response, scene_composition=scene_composition,
            pre_image_detail=state.get("pre_image_detail", ""),
            covered_senses=state.get("covered_senses", []),
            skipped_senses=state.get("skipped_senses", []),
            topic_senses=state.get("topic_senses"),
        )
        # 模型可能沒照 target_w 的角度問（順著長者剛才的話自然延伸，見
        # _classify_question_dimension 說明），核對這題實際問的是哪個維度，
        # 分類不出來才退回原本指定的 target_w。
        actual_w = await self._classify_question_dimension(result["question"]) or target_w
        # Why 是六個W裡唯一要先通過 _check_elder_state_good 才輪得到問的
        # 維度（見 _next_step_or_end），得來不易，若模型問偏到別的維度卻
        # 沒被攔下，這輪的Why名額就白白浪費掉。「actual_w in covered_w 才
        # 重打」只顧到「有沒有白問已涵蓋的維度」，顧不到「target_w明明是
        # Why、結果問成別的（例如問成What）」這種情況，因為那個維度可能
        # 還沒被涵蓋過，不會觸發重打。Why 額外加一條：只要target_w是Why、
        # 實際分類不是Why，不論actual_w有沒有被涵蓋過都重打，其他W維度
        # 不受影響、仍維持原本「只有問到已涵蓋維度才重打」的設計（放行
        # 自然延伸）。
        why_drifted = target_w == "Why" and actual_w != "Why"
        if actual_w != target_w and (actual_w in covered_w or why_drifted):
            # 不只是「問偏了」，是問偏到一個長者這回合已經答過的維度——
            # 等於白問，浪費本回合有限的補問名額（見
            # _MAX_SUPPLEMENT_PER_ROUND），該補的 target_w 反而從沒被真的
            # 問過。這種情況才值得多花一次LLM呼叫重打；單純問偏到「還沒
            # 問過的其他維度」不算浪費，不需要重打（那是刻意允許的自然
            # 延伸，見 _classify_question_dimension docstring）——Why見
            # 上方說明，是唯一的例外。
            reason = (
                "這個維度長者已經回答過了，這次問等於白問"
                if actual_w in covered_w
                else "但Why是需要長者狀態良好才輪得到問的維度，問偏掉等於這次的"
                     "Why機會白白浪費"
            )
            print(
                f"  → 補問 W({target_w}) 但問到{'已涵蓋的' if actual_w in covered_w else ''}"
                f"{actual_w}（白問），帶 retry_feedback 重打一次: {result['question']!r}"
            )
            retry_feedback = (
                f"上一次生成的問題「{result['question']}」問的其實是"
                f"「{actual_w}」，{reason}。"
                f"這次請改成明確從「{target_w}」的角度切入——{_W_HINT[target_w]}"
            )
            result = await guarded_generate(
                self._generate_supplement_question,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=2,  # 理由同 _ask_open_continuation 呼叫處2026-09-12稽核說明
                text_keys=("question",),  # 理由同上一次呼叫：STEP3不再生成承接語
                fallback=_element_fallback(
                    scene_els, target_w=target_w, topic_category=state.get("topic_category"),
                ),
                # 理由同前面 _ask_supplement／_ask_open_followup 呼叫處。
                retry_temperature=0.9,
                user=user, scene_elements=scene_els, covered_w=covered_w, target_w=target_w,
                emotion=emotion, elder_response=elder_response, scene_composition=scene_composition,
                pre_image_detail=state.get("pre_image_detail", ""),
                covered_senses=state.get("covered_senses", []),
                skipped_senses=state.get("skipped_senses", []),
                topic_senses=state.get("topic_senses"),
                retry_feedback=retry_feedback,
            )
            # 重打後不再花一次LLM呼叫重新核對角度——重打已經帶著明確的
            # retry_feedback要求模型改問target_w，而且這裡本來就不論結果
            # 都接受、不會觸發第二次重打（避免疊加太多層LLM呼叫拖累品質），
            # 再核對一次的結果只會拿去存state["last_w_asked"]，不影響這題
            # 真正念給長者聽的內容，直接視為重打成功即可（2026-09-08稽核：
            # 這次核對呼叫是判斷鏈裡的純粹開銷，沒有實際擋下任何東西）。
            actual_w = target_w
            print(f"  → 重打後：補問 W({target_w}): {result['question']!r}")
        elif actual_w != target_w:
            print(f"  → 補問 W({target_w}) 但問題實際角度是 {actual_w}: {result['question']}")
        else:
            print(f"  → 補問 W({target_w}): {result['question']}")
        new_state = {
            **state,
            "covered_w": covered_w,
            "skipped_w": skipped_w,
            "last_question_type": "supplement_w",
            "last_w_asked": actual_w,
            "last_question_text": result["question"],
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

        known_facts_w（見 _start_round2_free_followup 說明）：round 1 已經
        自然涵蓋、round 2 開場時就知道的維度，一併從候選裡排除，避免補問
        選到長者上一回合已經明確答過的事實（例如地點）。只影響這裡選哪個W
        補問，不影響 covered_w／skipped_w 本身，round 2 自然對話該怎麼判斷
        結束仍照原本節奏走。
        """
        known_facts_w = state.get("known_facts_w") or []
        uncovered = [
            w for w in _W_ORDER
            if w not in covered_w and w not in skipped_w and w not in known_facts_w
        ]
        # 結束回合判準要同時看uncovered跟感官——round 1 通常會把六個W維度
        # 全部自然涵蓋掉，round 2 開場known_facts_w一次就吃光所有候選，
        # uncovered永遠是空的，若只檢查uncovered，這個函式會在round 2第一
        # 次呼叫就直接收尾，即使這個主題明明還有相關感官一次都沒問過，
        # 感官相關的supplement機制也根本沒機會被觸發到。
        remaining_senses = _relevant_uncovered_senses(
            state.get("topic_senses"), state.get("covered_senses"),
            state.get("skipped_senses"),
        )
        min_questions = _min_questions_for_round(state["round"])
        if not uncovered and not remaining_senses and question_count >= min_questions:
            return await self._end_action(state, user, elder_response, emotion)

        # 懷舊治療重點是長者的成就感／愉悅感，不是把5W1H打勾湊滿——情緒已經
        # 正向、且本回合已經補問過至少一次，代表這一輪已經達到效果，優先
        # 自然收尾，不用為了湊剩下的W硬多問一題。這條捷徑跟下面的補問上限
        # 一樣，要先問滿 _MIN_QUESTIONS_PER_ROUND_BY_ROUND 才能生效（見該
        # 常數2026-08-20說明），不然round 2實測出現過補完1個W就因為長者
        # 開心直接收尾、全程只問2題的案例。
        if (
            supplement_count >= 1 and emotion in ("happy", "excited")
            and question_count >= min_questions
        ):
            print(f"  → 長者情緒{emotion}且已補問過，優先收尾（成就感優先於題數）")
            return await self._end_action(state, user, elder_response, emotion)

        if (
            supplement_count >= _MAX_SUPPLEMENT_PER_ROUND
            or question_count >= _max_questions_for_round(state["round"])
        ) and question_count >= min_questions:
            print(f"  → 補問已達上限（{supplement_count}/{_MAX_SUPPLEMENT_PER_ROUND}），話題自然結束")
            return await self._end_action(state, user, elder_response, emotion)

        # 決定下一個要補問的W維度：非Why優先隨機挑，Why最低優先序且需要
        # 長者狀態良好才問。next_w 選不出來時（不管是uncovered本來就空、
        # 還是唯一剩下Why但因狀態不佳被排除）都統一落到下面「還有沒問過
        # 的感官就補問感官，否則才真的收尾」這條路徑，兩種情境共用同一套
        # 判斷——不能讓「唯一剩下Why且被排除」直接收尾，跳過感官檢查。
        next_w = None
        non_why = [w for w in uncovered if w != "Why"]
        if non_why:
            next_w = random.choice(non_why)
        elif "Why" in uncovered:
            elder_state_good = await self._check_elder_state_good(elder_response, emotion)
            print(f"  → Why 長者狀態良好: {elder_state_good}")
            if elder_state_good:
                next_w = "Why"
            else:
                skipped_w.append("Why")

        if next_w is not None:
            return await self._ask_supplement(
                user, scene_els, covered_w, skipped_w, next_w, state, emotion,
                question_count, supplement_count,
                elder_response=elder_response, scene_composition=scene_composition,
            )

        if not remaining_senses:
            if question_count < min_questions:
                # W、感官都沒有結構化目標可補了，但還沒問滿最低題數（見
                # _MIN_QUESTIONS_PER_ROUND_BY_ROUND 說明）——不勉強套用W或
                # 感官的框架，改生一句開放式延續問題撐住，讓模型順著長者
                # 剛才的話自然接下去。
                print(f"  → 已問{question_count}題，未達最低{min_questions}題，"
                      f"沒有W／感官可補，改問開放式延續問題")
                return await self._ask_open_continuation(
                    user, scene_els, covered_w, skipped_w, elder_response, state, emotion,
                    question_count, supplement_count, scene_composition=scene_composition,
                )
            return await self._end_action(state, user, elder_response, emotion)

        # 沒有W可以當補問目標（uncovered清空，或唯一剩下的Why因長者狀態
        # 不佳被排除），但還有主題相關的感官沒問過——改用
        # _generate_open_followup問最後一題，不硬套_ask_supplement那套
        # 「一定要鎖定某個W維度」的機制（那支函式的_W_HINT[target_w]查表、
        # actual_w核對邏輯都假設target_w一定是個真的W維度，沒有維度可傳
        # 時硬塞會語意不通）。
        target_sense = remaining_senses[0]
        print(f"  → 沒有W可補問，但還有相關感官未問過（{remaining_senses}），"
              f"補問一題嘗試帶出感官")
        result = await guarded_generate(
            self._generate_open_followup,
            taboo_words=user["taboos"],
            llm_service=self.llm,
            max_retry=2,  # 理由同 _ask_open_continuation 呼叫處2026-09-12稽核說明
            text_keys=("question",),  # 理由同 _ask_open_continuation 呼叫處：STEP2不再生成承接語
            fallback=_element_fallback(
                scene_els, topic_category=state.get("topic_category"), with_covered_w=False,
            ),
            retry_temperature=0.9,  # 理由同前面幾處 _generate_open_followup／_generate_supplement_question 呼叫處
            user=user, scene_elements=scene_els, covered_w=covered_w,
            skipped_w=skipped_w, elder_response=elder_response, emotion=emotion,
            scene_composition=scene_composition, pre_image_detail=state.get("pre_image_detail", ""),
            covered_senses=state.get("covered_senses"),
            skipped_senses=state.get("skipped_senses"),
            topic_senses=state.get("topic_senses"),
        )
        # _sense_entry_hint 只是prompt裡的軟提示，模型可能整段無視。比照
        # _ask_supplement對target_w的做法（_classify_question_dimension
        # 核對），這裡用_classify_question_sense核對一次「這題實際問的是
        # 哪個感官」，不在還缺的感官清單裡（含完全沒問到感官的NONE）就帶
        # retry_feedback重打一次——只重打一次、不論結果都接受，不套用疊加
        # 太多層LLM呼叫、拖累品質的迴圈式核對。
        actual_sense = await self._classify_question_sense(result["question"])
        if actual_sense not in remaining_senses:
            print(f"  → 補問感官(目標{target_sense})但問題實際感官是"
                  f"{actual_sense!r}（不在還缺的感官{remaining_senses}裡），"
                  f"帶retry_feedback重打一次")
            retry_feedback = (
                f"上一次生成的問題「{result['question']}」沒有真的問到感官"
                f"記憶（實際判斷角度：{actual_sense or '看不出明確感官'}）。"
                f"這次請務必明確用「{target_sense}」這個感官記憶切入，比照"
                "【尚未涵蓋的感官】給的自然句型，不要問跟聊天話題／延續句"
                "有關的內容。"
            )
            result = await guarded_generate(
                self._generate_open_followup,
                taboo_words=user["taboos"],
                llm_service=self.llm,
                max_retry=2,  # 理由同 _ask_open_continuation 呼叫處2026-09-12稽核說明
                text_keys=("question",),  # 理由同上一次呼叫：STEP2不再生成承接語
                fallback=_element_fallback(
                    scene_els, topic_category=state.get("topic_category"), with_covered_w=False,
                ),
                retry_temperature=0.9,  # 理由同前面幾處 _generate_open_followup／_generate_supplement_question 呼叫處
                user=user, scene_elements=scene_els, covered_w=covered_w,
                skipped_w=skipped_w, elder_response=elder_response, emotion=emotion,
                scene_composition=scene_composition, pre_image_detail=state.get("pre_image_detail", ""),
                covered_senses=state.get("covered_senses"),
                skipped_senses=state.get("skipped_senses"),
                topic_senses=state.get("topic_senses"),
                retry_feedback=retry_feedback,
            )
            # 同 _ask_supplement 對 target_w 的做法：重打後不再花一次LLM
            # 呼叫重新核對感官，這裡本來就「只重打一次、不論結果都接受」，
            # 再核對一次只會拿去存state["last_sense_asked"]，不影響這題
            # 真正念給長者聽的內容，直接視為重打成功即可。
            actual_sense = target_sense
            print(f"  → 重打後：補問感官(目標{target_sense}): {result['question']!r}")
        else:
            print(f"  → 補問感官(目標{target_sense})，實際感官"
                  f"{actual_sense!r}: {result['question']!r}")
        new_state = {
            **state,
            "covered_w": covered_w,
            "skipped_w": skipped_w,
            "last_question_type": "open",
            "last_w_asked": "",
            # 記下這題實際問到的感官（分類不出來就退回目標感官），下一輪
            # process_response 才能核對長者有沒有真的答到，答不到就記進
            # skipped_senses，不再重問同一個（見 _relevant_uncovered_
            # senses 的 skipped_senses 說明）——這個分支之前沒設這個
            # 欄位，等於白白鎖定了目標卻沒人追蹤有沒有打中。
            "last_sense_asked": actual_sense or target_sense,
            "last_question_text": result["question"],
            "question_count": question_count + 1,
            "supplement_count": supplement_count + 1,
        }
        return {
            "action": "open_followup",
            "scene_text": result["scene_text"],
            "question": result["question"],
            "state": new_state,
        }

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

        2026-09-13稽核：死別類關鍵字（見 _BEREAVEMENT_KEYWORDS_RE 上方
        說明）先用規則兜底判YES，不等LLM判斷——這類判斷本地弱模型不穩定，
        實測連課本等級的死別案例都可能被判成NO，漏判的代價（長者剛表達
        喪親情緒，卻被送進一般續問流程收到答非所問的承接語）比其他規則
        漏放嚴重很多，值得為這個明確、幾乎不會誤判的子集直接用規則攔截。
        """
        if _BEREAVEMENT_KEYWORDS_RE.search(elder_response):
            print("  → 情緒觸發偵測：命中死別關鍵字規則兜底，直接判定YES")
            return True
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

    async def _detect_dimension_evidence(
        self,
        elder_response: str,
        unchecked: list[str],
        key: str = "dimension",
        desc_map: dict[str, str] | None = None,
        extra_note: str = "",
    ) -> list[dict]:
        """
        共用的「逐項列證據」偵測本體——一律用短語格式直接問，
        _check_pre_image_basic_dims／_detect_pre_image_w_coverage／
        _detect_covered_w／_detect_covered_senses 四支呼叫端共用，取代
        各自維護的「先問完整版格式、疑似整句照抄才用短語格式重問」兩階段
        流程——短語格式本來就是為了逼模型不能整句照抄設計的，一開始就用
        短語格式問，省下「先答壞、偵測、重問」這一整趟往返。

        字數限制是「盡量精簡、但必須是連續存在於原文的完整片段，不能跳字
        拼湊」，不死板卡「2-4個字」：卡死字數上限會逼模型為了塞進字數把
        「孫女非常喜歡」硬壓縮成「孫女喜歡」（跳過中間的「非常」），結果
        這段文字在原文裡不是連續片段，substring比對真的比不到，反而把
        長者已經答過的證據誤判成「找不到」；「不能整句照抄」改用具體反例
        說明防呆，不靠字數上限硬擋。
        """
        if not unchecked:
            return []
        dim_list = "、".join(f"「{d}」" for d in unchecked)
        desc_section = (
            "，每個維度的定義：\n" + "\n".join(f"- {d}：{desc_map[d]}" for d in unchecked) + "\n\n"
            if desc_map else "。\n\n"
        )
        checks_format = ", ".join(
            f'{{"{key}": "{d}", "evidence": "簡短的原文連續片段，或「找不到」"}}'
            for d in unchecked
        )
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            f"請逐一檢查{dim_list}這{len(unchecked)}個維度，在這段話裡找出"
            f"對應的關鍵詞當作證據{desc_section}"
            f"{extra_note}"
            "每個維度的evidence都必須是原文裡連續存在的一小段文字，逐字"
            "複製、不能改寫或替換成其他說法即使意思一樣也不行。盡量精簡"
            "（通常幾個字到十個字左右），但精簡的前提是「必須逐字連續"
            "存在於原文」——不能從原文不同位置各挑一小段字詞拼接在一起，"
            "就算兩段字詞本身都真的出現在原文裡，只要中間隔著其他字、不是"
            "緊鄰相連，拼起來的組合就不算數（例如原文是「一起在院子烤肉」，"
            "「烤肉」或「在院子烤肉」都是連續存在、可以當證據，但不能寫成"
            "「一起烤肉」——原文裡「一起」跟「烤肉」中間隔著「在院子」，"
            "不是緊鄰的文字，硬拼在一起在原文裡根本找不到這四個字連續"
            "出現；同理，也不能為了縮短跳過中間的字，例如原文是「孫女"
            "非常喜歡」，不能縮成「孫女喜歡」，要嘛完整保留「孫女非常"
            "喜歡」，要嘛換一段真的連續存在的更短片段，不能跳字硬湊）。"
            "也不能把長者整句話原封不動照抄當證據，也不能直接"
            "複製這句指示本身的文字當證據。如果這段話裡真的沒有這個"
            "維度，evidence欄位就必須填「找不到」這三個字，不要為了湊"
            "答案硬找不相關的片段當證據。同一段文字只能當一個維度的"
            "證據——如果它同時符合多個維度的定義，只能選語意最直接對應"
            "的那一個維度填入，其他維度不能重複使用這段文字，一律填"
            "「找不到」。\n"
            "回傳一個JSON物件，格式：\n"
            f"{{\"checks\": [{checks_format}]}}\n"
            f"checks陣列一定要包含{dim_list}這{len(unchecked)}項，不能"
            "省略。只回JSON，不要任何說明文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        return result.get("checks", [])

    async def _check_pre_image_basic_dims(self, elder_response: str) -> list[dict]:
        """
        「地點／活動／時間」逐項核對本體，從 _has_usable_detail 抽出來，讓
        process_response 的 pre_image_q1 分支可以在判斷情境1/2時直接複用
        這份已查證的 checks（見 _map_basic_checks_to_w），不用再對
        _detect_pre_image_w_coverage 的 Where/When/How/Why 四維度框架重新
        問一次 LLM。

        逐一核對「地點」「活動」「時間」三個維度，每項都要求標出對應的
        原文具體片段當證據，找不到就必須明講「找不到」，再用共用的
        _missing_from_checks／_is_real_evidence 判斷哪些維度真的有覆蓋
        （擋掉樣板字硬套、證據重複挪用、整項憑空消失這幾種已知漏洞）——
        比一次性問LLM「有沒有任兩項同時出現」直接吐YES/NO更難含糊帶過，
        本地量化基底模型對整體判斷容易穩定漏判。改用共用的
        _detect_dimension_evidence，一律用短語格式（盡量精簡但必須逐字
        連續存在於原文）直接問，見該函式說明。
        """
        checks = await self._detect_dimension_evidence(
            elder_response, ["地點", "活動", "時間"], "dimension",
            extra_note=(
                "時間不用精確到年份/日期，只要是籠統的時間點或時段"
                "（例如「小時候」「晚上」「過年的時候」）就算數。\n"
            ),
        )
        print(f"  → [DEBUG] _has_usable_detail 原始 checks: {checks}")
        return checks

    async def _has_usable_detail(
        self, elder_response: str, checks: list[dict] | None = None,
    ) -> bool:
        """
        判斷長者回答生圖前引導問題時，內容裡有沒有同時包含「地點＋活動＋
        時間」三項，足夠當這次生圖的記憶來源（見 _start_scene_after_
        detail）。三項缺一都不算夠具體，跟 _PRE_IMAGE_PRIORITY_ORDER／
        _FIVE_W1H_BANK 用的 Where/When/How/Why 對齊，缺任何一項就退回讓
        process_response 走情境1/2那套精準補維度的流程，用 _FIVE_W1H_BANK
        問缺的那個維度，不是直接退回RAG記憶——只有 _is_true_refusal 判定
        的真拒答才會退回RAG記憶fallback，見 process_response 的
        pre_image_q1 分支。

        跟 _is_quick_end 不一樣：_is_quick_end 只看字數/放棄關鍵字，抓不到
        「有講話、字數也夠，但內容不足以撐出一個場景」這種回答。

        checks: process_response 若已經呼叫過 _check_pre_image_basic_dims
        （例如要接著判斷情境1/2、需要重用同一份 checks 給
        _map_basic_checks_to_w），直接把結果傳進來，不用對同一句話重新
        問一次 LLM——同一句話兩次獨立呼叫是非決定性判斷，可能給出不同
        答案。留 None（預設）給還沒查過的呼叫端，維持原本行為。
        """
        dimensions = ["地點", "活動", "時間"]
        if checks is None:
            checks = await self._check_pre_image_basic_dims(elder_response)
        missing = _missing_from_checks(
            dimensions, checks, "dimension", elder_response,
            allow_cross_dimension_gap=True,
        )
        covered = [d for d in dimensions if d not in missing]
        print(f"  → 生圖前訪談內容維度核對，涵蓋: {covered}，缺: {missing}")
        # 地點＋活動＋時間三項都要有才算數，缺一就不算夠具體。
        return "地點" in covered and "活動" in covered and "時間" in covered

    async def _detect_pre_image_w_coverage(self, elder_response: str) -> list[str]:
        """
        判斷生圖前 Q1（_has_usable_detail 已經判定「不夠具體」時才會呼叫）
        要走情境1還是情境2：逐一核對 Where/When/How/Why 四個維度，在長者
        這句話裡找出對應的原文證據，跟 _has_usable_detail 判斷「地點/活動/
        時間」用同一套「逐項列證據」模式，改用共用的 _detect_dimension_
        evidence，一律用短語格式（盡量精簡但必須逐字連續存在於原文）直接
        問——yes/no整體判斷本地小模型會穩定漏判，逐項列證據更難含糊帶過。

        全部找不到證據（回傳空list）→ process_response 判定為情境1（完全
        答不出來），問域縮小範例；至少一個維度有證據 → 情境2（有內容但缺
        維度），回傳的清單直接當 get_scenario2_followup() 的起始 covered_w，
        不用另外再核對一次同一句話。

        Where／How／Why 三項維持嚴格逐字比對，When（時間）這一項放寬成
        允許「合理推斷」（透過 extra_note 帶入）——例如「在稻田裡工作」
        沒有明講「白天」，但這個線索能合理推斷出大概是白天，也算涵蓋，
        不用再追問一次「白天還是晚上」。放寬的只有「evidence可以是隱含
        時間的線索、不必是時間詞本身」，不是放寬「evidence可以亂猜」——
        evidence 仍然要求是原話裡逐字存在的片段，一樣靠
        _missing_from_checks／_is_real_evidence 驗證這個線索是不是真的
        存在於原文，避免模型亂編一個原話沒有的線索當證據。
        """
        dimensions = _PRE_IMAGE_PRIORITY_ORDER
        checks = await self._detect_dimension_evidence(
            elder_response, dimensions, "dimension", desc_map=_W_DESC,
            extra_note=(
                "「When（時間）」這一項可以放寬：如果原話沒有直接講時間"
                "詞，但話裡有其他具體線索能合理推斷出大概的時間（例如"
                "具體的動作、場景描述隱含白天/晚上/季節，像是「在稻田裡"
                "工作」隱含白天），也算涵蓋——這種情況evidence欄位要填"
                "「讓你推斷出時間的那個具體線索」（一樣要盡量精簡），"
                "這個線索本身仍然必須是逐字從原話複製出來的片段，不能"
                "瞎猜一個原話沒有的線索，也不能只寫「推斷」兩個字交差。\n"
            ),
        )
        print(f"  → [DEBUG] _detect_pre_image_w_coverage 原始 checks: {checks}")
        missing = _missing_from_checks(
            dimensions, checks, "dimension", elder_response,
            allow_cross_dimension_gap=True,
        )
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

    async def _classify_topic_category(self, user: dict) -> str | None:
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

        2026-09-08：today_topic 跟【興趣】欄位裡某個項目字面完全對得上時
        （例如 preferences「做料理」、today_topic 直接沿用同一個字），不再
        交給LLM猜——跟 _decide_scene_anchor 8/21 那次修正同一個道理，
        temperature=0 只保證同樣輸入得到一致答案，不保證答案正確，實測
        「做料理」這種字面上偏家庭情境的興趣項目，LLM 穩定猜成「家庭」，
        生出跟長者實際興趣無關的破冰問句。這種字面精準命中的情況用程式
        判斷直接鎖定「興趣」，不需要LLM介入判斷。
        """
        today_topic = user["today_topic"]
        if user.get("preferences"):
            pref_items = [p.strip() for p in re.split(r"[、,，/\s]+", user["preferences"]) if p.strip()]
            if any(item in today_topic or today_topic in item for item in pref_items):
                return "興趣"

        options = "、".join(_TOPIC_CATEGORIES)
        prompt = (
            f"今日主題：「{today_topic}」\n\n"
            f"這個主題最接近以下16個懷舊治療主題分類裡的哪一個？\n{options}\n\n"
            f"只回答一個分類名稱，完全比照上面的寫法，不要加任何說明或標點。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        category = raw.strip()
        return category if category in _TOPIC_CATEGORIES else None

    async def _classify_topic_senses(self, today_topic: str) -> list[str]:
        """
        判斷 today_topic（治療師輸入的自由文字，例如「聽音樂」「中秋節」）
        本身適合用哪些感官記憶切入，直接對 today_topic 本身分類，不靠16大
        主題分類這層粗分桶轉手查表（見 _SENSE_EXCLUDED_TOPIC_CATEGORIES
        上方說明）。跟 _classify_topic_category 同一套「start_round 一開始
        就跑一次、存進 state、之後直接複用」模式，不是每次要用感官清單時
        才臨時分類。

        回傳值是 _SENSE_DESC 裡 0 到多個感官名稱組成的 list（可以是空
        list——這個主題內容本身就不適合用感官記憶切入時，不用勉強選一個）；
        LLM 輸出裡不在 _SENSE_DESC 裡的字詞一律過濾掉，不勉強猜測比對。
        呼叫端（start_round）另外會用 topic_category 硬性覆寫哀傷之事／
        人生目標／生命中特殊的事件這三類成空list，不受這裡分類結果影響，
        理由見 _SENSE_EXCLUDED_TOPIC_CATEGORIES 說明。
        """
        desc_list = "\n".join(f"- {s}：{d}" for s, d in _SENSE_DESC.items())
        prompt = (
            f"今日主題：「{today_topic}」\n\n"
            f"懷舊治療時，這個主題適合引導長者從哪些感官記憶切入回憶？\n{desc_list}\n\n"
            "只回答適合的感官名稱本身，用「、」分隔，完全比照上面的寫法；"
            "如果都不適合，回NONE。不要加任何說明或標點。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        senses = [s.strip() for s in raw.split("、")]
        return [s for s in senses if s in _SENSE_DESC]

    async def _classify_question_dimension(self, question_text: str) -> str | None:
        """
        判斷一個問題主要是在問哪個W維度，_ask_supplement 生成完STEP3補問的
        問題後用來核對「這題實際上問的是哪個維度」，不能盲目相信生成前指定
        的 target_w。

        _generate_supplement_question 的 prompt 雖然帶了 target_w 的角度
        提示，但同時也刻意要求「順著長者剛才的話自然地深入問下去，不是在
        核對清單」（允許自然延伸話題，不強制鎖死角度），兩個指示衝突時，
        模型常常選擇順著長者的話延伸、放棄target_w的角度（例如target_w=
        Where，卻改問「你們都怎麼搭配食材呢」這種How角度的問題）。若照樣
        把這題記成「問了Where」，會讓Where被錯誤記進skipped_w、永久跳過
        （其實從沒被真的問過），下一輪還可能選到跟這題角度雷同的其他維度，
        問出重複的問題。改成問題生成後另外核對一次「這題實際上是哪個
        角度」，用這個核對過的結果取代target_w記進state，讓後續的追蹤都
        根據長者實際被問到的角度，不是原本打算問但模型沒照做的角度。

        回傳 _W_ORDER 其中一個維度名稱，或 None（問題內容判斷不出明確對應
        哪個維度，呼叫端此時應該退回原本指定的 target_w，不強求一定要分類
        出新答案）。
        """
        dim_list = "、".join(f"「{w}」" for w in _W_ORDER)
        desc_list = "\n".join(f"- {w}：{_W_DESC[w]}" for w in _W_ORDER)
        prompt = (
            f"這是治療師問長者的一句話：「{question_text}」\n\n"
            f"這句話開頭如果是「那個時候」「那時候」這類字眼，那只是"
            f"question_5w1h.txt 教的「回指整段已經聊開的情境」最後手段錨點"
            f"（找不到更具體的錨點時的萬用開場語），不代表這句話真的在問"
            f"時間點，不要因為看到這幾個字就判成「When」——要看這句話後面"
            f"實際要求長者回答的具體內容是什麼（例如「那時候，有沒有嚐到"
            f"什麼特別的味道呢？」，開頭雖然是「那時候」，但實際要長者回答"
            f"的是味覺/味道，不是時間，應該判「How」或 NONE，不是「When」）。\n\n"
            f"這句話主要是想引導長者回答下面哪一個維度？\n{desc_list}\n\n"
            f"只回{dim_list}其中一個維度名稱本身，不要其他文字或說明；"
            "如果都不像，回NONE。"
        )
        raw = (await self.llm.ask(prompt, temperature=0)).strip()
        classified = raw if raw in _W_ORDER else None
        return _backstop_what_copula(classified, question_text)

    async def _classify_question_sense(self, question_text: str) -> str | None:
        """
        判斷一個問題主要是在問哪個感官（視覺/聽覺/嗅覺/味覺/觸覺），跟
        _classify_question_dimension 同一套模式，供 _next_step_or_end
        「還有相關感官未問過，補問一題」這條分支核對——不能盲目相信生成前
        指定的 target_sense 真的被問到了，_sense_entry_hint 只是prompt裡
        的軟提示，模型可能整段無視。刻意做成跟_ask_supplement對target_w
        一樣的「核對一次、不對就重打一次、重打後不論結果都接受」輕量版，
        不套用疊加太多層LLM呼叫、反而拖累品質的迴圈式核對。

        回傳感官名稱，或 None（問題內容判斷不出明確對應哪個感官，呼叫端
        此時應該退回原本指定的 target_sense，不強求一定要分類出新答案）。
        """
        dim_list = "、".join(f"「{s}」" for s in _SENSE_DESC)
        desc_list = "\n".join(f"- {s}：{d}" for s, d in _SENSE_DESC.items())
        prompt = (
            f"這是治療師問長者的一句話：「{question_text}」\n\n"
            f"這句話主要是想引導長者回答下面哪一種感官記憶？\n{desc_list}\n\n"
            f"只回{dim_list}其中一個名稱本身，不要其他文字或說明；"
            "如果都不像（例如問的是事實、社交互動，不是感官記憶），回NONE。"
        )
        raw = (await self.llm.ask(prompt, temperature=0)).strip()
        return raw if raw in _SENSE_DESC else None

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
        判斷長者是「真的不想／不能答」，只認沉默（_NO_RESPONSE_MARKER，見該
        常數說明——目前唯一觸發來源是治療師按跳過，長者自己不會被系統自動
        判定沉默）或明確放棄關鍵字（「不知道」「不記得」等）這兩種，跟
        _is_quick_end 不一樣的地方是**不把單純的短回答算進來**。

        2026-08：process_response 的 pre_image_q1 分支原本直接拿 _is_quick_end
        的結果決定要不要略過Q2追問、直接退回RAG記憶——但 _is_quick_end 的
        「字數<5就算quick_end」這條，把「有喔」「會啊」這種長者真的有回答、
        只是講得很短的內容，跟「長者完全不想講」一視同仁地跳過Q2。短回答
        依然是長者本人真實給的內容，應該讓他有機會在Q2多說一點，不該直接
        放棄改用RAG舊記憶——只有長者真的沉默（被治療師跳過）或明確表示
        不知道/不記得時，才没有必要再多問一題。_is_quick_end 在其他地方的
        判斷（是否要存進RAG記憶、STEP2/3話題是否結束）維持原樣不受影響，
        只有生圖前 Q1→Q2 這一步改用這支較窄的判斷。
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

        逐一核對每個未涵蓋維度、要求標出原文證據，跟 _check_pre_image_
        basic_dims／_detect_pre_image_w_coverage 同一套「逐項列證據」模式，
        共用 _missing_from_checks／_is_real_evidence 這幾個輔助函式，改用
        _detect_dimension_evidence 一律用短語格式直接問——比一次性問LLM
        「這段話有沒有涵蓋以下W維度」直接吐結論可靠，本地基底模型對整體
        判斷容易穩定漏判，短答案（例如「晚上」）尤其容易被漏掉，導致
        covered_w 卡住不動、長者已經答過的維度被重複追問。
        """
        unchecked = [w for w in _W_ORDER if w not in already_covered]
        checks = await self._detect_dimension_evidence(
            elder_response, unchecked, "dimension", desc_map=_W_DESC,
        )
        print(f"  → [DEBUG] _detect_covered_w 原始 checks: {checks}")
        checks = _void_pure_time_evidence_from_other_dims(checks)
        checks = _backstop_when_evidence(checks, elder_response)
        missing = _missing_from_checks(
            unchecked, checks, "dimension", elder_response,
            allow_cross_dimension_gap=True,
        )
        newly_covered = [w for w in unchecked if w not in missing]
        print(f"  → _detect_covered_w 核對，新涵蓋: {newly_covered}，仍缺: {missing}")
        return newly_covered

    async def _detect_covered_senses(
        self,
        elder_response: str,
        already_covered: list[str],
        topic_senses: list[str] | None = None,
    ) -> list[str]:
        """
        偵測長者回應中自然涵蓋了哪些尚未記錄的感官（視覺/聽覺/嗅覺/味覺/
        觸覺）——跟 _detect_covered_w 同一套「逐項列證據」模式，理由相同
        （本地量化基底模型對整體判斷不穩定，逐項要求原文證據才穩定）。

        不套用 _void_pure_time_evidence_from_other_dims／_backstop_when_
        evidence 這兩個後處理——那兩個是 _detect_covered_w 專門修「時間詞
        被誤標到其他W維度」的問題，跟感官偵測無關，感官之間沒有類似的
        已知混淆模式，硬套反而可能誤刪正常證據。

        topic_senses：只核對主題相關的感官，不是每次都把五種感官全部拿去
        核對——跟主題無關的感官從一開始就不會被 _relevant_uncovered_senses
        當成選項端出來，核對再多也用不到，純粹是多花LLM呼叫、多一次誤判
        機會的白工。topic_senses 是空list時（哀傷之事／人生目標等，見
        _SENSE_EXCLUDED_TOPIC_CATEGORIES 說明，或 _classify_topic_senses
        判斷這個主題內容不適合任何感官）relevant 也是空list，直接跳過
        整次核對。

        改用共用的 _detect_dimension_evidence，一律用短語格式（盡量精簡但
        必須逐字連續存在於原文）直接問，見該函式說明；另外用
        _void_non_sensory_opinion_evidence 後處理排除「喜歡」這種純主觀
        偏好詞被誤標成嗅覺／味覺證據的情況，見該函式說明。
        """
        relevant = topic_senses or []
        unchecked = [s for s in relevant if s not in already_covered]
        checks = await self._detect_dimension_evidence(
            elder_response, unchecked, "dimension", desc_map=_SENSE_DESC,
        )
        print(f"  → [DEBUG] _detect_covered_senses 原始 checks: {checks}")
        checks = _void_non_sensory_opinion_evidence(checks)
        missing = _missing_from_checks(
            unchecked, checks, "dimension", elder_response,
            allow_cross_dimension_gap=True,
        )
        newly_covered = [s for s in unchecked if s not in missing]
        print(f"  → _detect_covered_senses 核對，新涵蓋: {newly_covered}，仍缺: {missing}")
        return newly_covered

    async def _detect_covered_w_and_senses(
        self,
        elder_response: str,
        covered_w: list[str],
        covered_senses: list[str],
        topic_senses: list[str] | None = None,
    ) -> tuple[list[str], list[str], bool]:
        """
        process_response STEP2背景追蹤專用：把 _detect_covered_w／_detect_
        covered_senses 原本各自呼叫一次 _detect_dimension_evidence 合併成
        一次——兩者底層共用同一個prompt產生器，只是核對的維度清單不同，
        合併成一份清單一次問完不影響判斷本質。

        合併後用 _W_ORDER／_SENSE_DESC 的 key 把同一批 checks 拆回兩組，
        再各自套用原本專屬的後處理（_void_pure_time_evidence_from_other_dims／
        _backstop_when_evidence 只套用在W組，_void_non_sensory_opinion_evidence
        只套用在感官組），跟兩次分開呼叫時各自處理的範圍完全一致。

        2026-09-12稽核（「回合內話題延續判斷」這一步耗時波動到
        近40秒太慢）：process_response 在這裡問完W/感官涵蓋度後，緊接著
        （只要沒有在中間的「5W1H全涵蓋」「題數已達上限」提早收尾）就會呼叫
        原本獨立的 _decide_topic_continuation 判斷這句話值不值得繼續深入
        追問——兩次呼叫背靠背發生是常態，本機Ollama單次往返2-5秒，拆兩次
        問等於讓長者多等一輪完整LLM往返。這裡把 _decide_topic_continuation
        原本的判斷（見下方【第二組】沿用它原本一字不動的instruction文字）
        併進同一次呼叫、同一份JSON checks陣列，用 _CONTINUATION_DIRECTIONS
        當key、跟W/感官用不同的字面詞彙，不會互相碰撞，事後一樣拆回三組
        分開後處理。故意不透過 _detect_dimension_evidence 共用的通用「找
        關鍵字證據」措辭統一改寫兩組指示——W/感官組問的是「有沒有證據＝已
        涵蓋」，話題延伸組問的是「有沒有證據＝還能往下延伸」，兩種判斷意圖
        不同，硬套同一種簡化措辭有走味風險，這裡兩組各自完整保留原本已經
        實測調過的instruction文字，只合併成一次LLM呼叫本身。

        代價：即使這次elder_response之後會走「5W1H全涵蓋」或「題數已達
        上限」提早收尾、根本用不到can_continue，這裡還是會一併問了話題
        延伸這組——但這只是讓同一次呼叫的prompt/回應多幾個JSON欄位，不是
        多一次LLM往返，換到的是「正常會用到can_continue」（更常見）的情況
        下省下一整次來回，權衡上划算。
        """
        unchecked_w = [w for w in _W_ORDER if w not in covered_w]
        relevant_senses = topic_senses or []
        unchecked_senses = [s for s in relevant_senses if s not in covered_senses]
        unchecked = unchecked_w + unchecked_senses
        directions = _CONTINUATION_DIRECTIONS

        # 只在整個prompt最後放「唯一一份」合併後的flat範例陣列（涵蓋兩組
        # 全部項目），故意不在每組說明文字裡各自附一份局部範例陣列——
        # 2026-09-12稽核（實測後補）：曾經每組各自附一份`[{...}]`範例，
        # 模型會把兩份範例原封不動當成兩個子陣列塞進checks（變成
        # {"checks": [[...第一組...], [...第二組...]]}），沒有真的攤平成
        # 一個陣列，導致下面 isinstance(c, dict) 篩選全部落空、每一項都被
        # 誤判成「找不到」（含can_continue，連帶讓話題延伸判斷永遠回傳
        # False）。只保留一份、放在prompt最後的合併範例，大幅降低模型
        # 誤解成「兩個獨立陣列」的機會；下面的解析邏輯仍加一層防禦性攤平
        # （見下方flat_checks），雙重保險。
        all_items = unchecked + directions
        combined_format = ", ".join(
            f'{{"dimension": "{d}", "evidence": "..."}}' for d in all_items
        )

        sections = []
        if unchecked:
            dim_list = "、".join(f"「{d}」" for d in unchecked)
            desc_map = {**_W_DESC, **_SENSE_DESC}
            desc_section = "，每個維度的定義：\n" + "\n".join(
                f"- {d}：{desc_map[d]}" for d in unchecked
            )
            sections.append(
                f"【第一組：涵蓋度核對】請逐一檢查{dim_list}這{len(unchecked)}個維度，"
                f"在這段話裡找出對應的關鍵詞當作證據{desc_section}\n"
                "每個維度的evidence都必須是原文裡連續存在的一小段文字，逐字複製、"
                "不能改寫或替換成其他說法即使意思一樣也不行。盡量精簡（通常幾個字"
                "到十個字左右），但精簡的前提是「必須逐字連續存在於原文」——不能"
                "從原文不同位置各挑一小段字詞拼接在一起，就算兩段字詞本身都真的"
                "出現在原文裡，只要中間隔著其他字、不是緊鄰相連，拼起來的組合就"
                "不算數。也不能把長者整句話原封不動照抄當證據，也不能直接複製"
                "這句指示本身的文字當證據。如果這段話裡真的沒有這個維度，evidence"
                "欄位就必須填「找不到」這三個字，不要為了湊答案硬找不相關的片段"
                "當證據。同一段文字只能當一個維度的證據——如果它同時符合多個維度"
                "的定義，只能選語意最直接對應的那一個維度填入，其他維度不能重複"
                f"使用這段文字，一律填「找不到」。這組要包含{dim_list}這"
                f"{len(unchecked)}項，不能省略。"
            )
        sections.append(
            "【第二組：話題延伸判斷】請逐一檢查這段話，能不能自然延伸出更多值得"
            "追問的內容，分成「情感／意義」「陪伴的人的互動」「感官細節」"
            "「接下來發生的事」四個延伸方向，各自找出這段話裡有沒有可以往下"
            "追問的具體線索當作證據。evidence必須是逐字從長者原話裡複製出來的"
            "片段，一個字都不能改寫，也不能複製這句指示本身的文字當證據。如果"
            "這段話已經把某個方向講完整了（例如已經直接給出結論、帶有笑聲等"
            "收尾語氣，沒有懸而未答的細節），或這段話裡根本沒有這個方向的線索，"
            "evidence欄位就必須填「找不到」這三個字，不要為了湊答案硬找不相關"
            "的片段當證據。這組要包含「情感／意義」「陪伴的人的互動」「感官"
            "細節」「接下來發生的事」這四項，不能省略。"
        )
        prompt = (
            f"長者剛才說：「{elder_response}」\n\n"
            + "\n\n".join(sections)
            + "\n\n把上面兩組全部項目合併放進「同一個」checks陣列（不要分成兩個"
            f"子陣列，全部{len(all_items)}項攤平在一層裡），回傳一個JSON物件，格式：\n"
            f'{{"checks": [{combined_format}]}}\n'
            "checks陣列裡每一項都要出現、不能省略，只回JSON，不要任何說明"
            "文字或markdown標記。"
        )
        raw = await self.llm.ask(prompt, temperature=0)
        result = self._extract_json(raw)
        checks = result.get("checks", [])
        # 防禦性攤平一層：即使模型還是把兩組各自包成子陣列塞回來
        # （{"checks": [[...], [...]]}），這裡也能正確攤平取出每一項，
        # 不會因為 isinstance(c, dict) 全部落空、把每一項都誤判成「找不到」
        # （見上方2026-09-12稽核說明）。
        flat_checks = []
        for c in checks:
            if isinstance(c, list):
                flat_checks.extend(c)
            else:
                flat_checks.append(c)
        checks = flat_checks
        print(f"  → [DEBUG] _detect_covered_w_and_senses 原始 checks: {checks}")
        w_checks = [c for c in checks if isinstance(c, dict) and c.get("dimension") in _W_ORDER]
        sense_checks = [
            c for c in checks if isinstance(c, dict) and c.get("dimension") in _SENSE_DESC
        ]
        direction_checks = [
            c for c in checks if isinstance(c, dict) and c.get("dimension") in directions
        ]

        w_checks = _void_pure_time_evidence_from_other_dims(w_checks)
        w_checks = _backstop_when_evidence(w_checks, elder_response)
        missing_w = _missing_from_checks(
            unchecked_w, w_checks, "dimension", elder_response, allow_cross_dimension_gap=True,
        )
        newly_covered_w = [w for w in unchecked_w if w not in missing_w]
        print(f"  → _detect_covered_w_and_senses(W) 核對，新涵蓋: {newly_covered_w}，仍缺: {missing_w}")

        sense_checks = _void_non_sensory_opinion_evidence(sense_checks)
        missing_senses = _missing_from_checks(
            unchecked_senses, sense_checks, "dimension", elder_response, allow_cross_dimension_gap=True,
        )
        newly_covered_senses = [s for s in unchecked_senses if s not in missing_senses]
        print(f"  → _detect_covered_w_and_senses(感官) 核對，新涵蓋: {newly_covered_senses}，仍缺: {missing_senses}")

        # 話題延伸判斷不套用 allow_cross_dimension_gap（理由同原本
        # _decide_topic_continuation：這裡錯判的代價是「多深入問了一題其實
        # 已經講完的內容」，不像W/感官核對錯放行只是漏記已涵蓋維度，維持
        # 原本較嚴格的預設 False）。
        missing_directions = _missing_from_checks(directions, direction_checks, "dimension", elder_response)
        covered_directions = [d for d in directions if d not in missing_directions]
        can_continue = bool(covered_directions)
        print(f"  → 話題延伸方向核對，可延伸: {covered_directions}，找不到: {missing_directions}")

        return newly_covered_w, newly_covered_senses, can_continue

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
        prior_rounds_transcript: str = "",
        temperature: float | None = None,
    ) -> dict:
        """
        收尾引導：三回合結束後帶領長者從回憶回到現實，詢問感受或正向回憶。

        temperature: 見 guarded_generate 的 retry_temperature 參數說明——
        _start_round3_closing 帶了 retry_temperature，重試時（attempt>0）
        會透過這個參數收到比第一次更高的temperature；不傳（None）就沿用
        llm.py 的預設值。

        2026-07-31 曾試過拆成兩次獨立呼叫（收尾語／問題分開生），實測本地
        模型品質反而變差，改回單次生成至今。

        prior_rounds_transcript: 這場療程目前為止所有回合的逐字稿（見
        app/routers/session.py _build_prior_rounds_transcript），只有
        _start_round3_closing（回合3開場）這個呼叫點會帶——這支函式原本只收
        elder_response（長者上一句話），2026-09-09 稽核發現回合3開場因此完全
        看不到回合1訪談（例如生圖前Q1/Q2）裡更豐富的細節，只憑長者一句孤立的
        話容易生出脫離脈絡的內容（例如把「做料理」腦補成「跟大家一起上料理
        課」）。有給這個參數時優先呼應逐字稿裡最有情感重量的內容，不侷限在
        elder_response 那一句；沒給（例如DB查詢失敗的保底情況）就退回原本
        只看 elder_response 的行為。

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

        transcript_section = (
            f"\n【今天聊天的完整記錄（依回合順序）】\n{prior_rounds_transcript}\n"
            if prior_rounds_transcript else ""
        )
        last_response_section = (
            f"\n【長者最後說的話】\n{elder_response}\n" if elder_response else ""
        )
        taboo_str = "、".join(user["taboos"]) if user["taboos"] else "無"
        # 2026-09稽核（實測發現）：這裡原本還有「姓名：{user['name']}」一行，
        # 但 closing.txt 規則明文規定收尾語一律用「你」、絕對不能用長者的
        # 本名稱呼——這個規則從頭到尾都用不到姓名這個值，卻還是把它當成
        # 【長者資料】的第一筆欄位餵給模型，等於主動提供一個規則要求絕對
        # 不能用的東西，也難怪實測會抓到模型把姓名直接當稱呼詞用（「張
        # 老師，你今天分享了很多...」）。拿掉這行，模型從一開始就看不到
        # 姓名，沒有東西可以誤用。
        user_content = (
            f"【長者資料】\n"
            f"今日主題：{user['today_topic']}\n"
            f"{transcript_section}"
            f"{last_response_section}"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"三回合療程剛剛結束，請設計收尾引導：先用收尾語溫暖肯定長者今天的分享並帶回現實，"
            f"再問一句關於現在感受或今天正向回憶的問題（詳細規則見系統提示）。"
            + (
                "呼應對象優先從【今天聊天的完整記錄】裡挑最具體、最有情感重量的片段，"
                "不限定要用【長者最後說的話】那一句——只要那句話沒什麼可呼應的內容"
                "（例如很簡短、答非所問），就回頭挑更早的回合裡真正有意義的內容。\n"
                if prior_rounds_transcript else "\n"
            )
            + f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"輸出一個JSON物件，包含2個key：\n"
            f"closing_text（收尾語）：1句話（用逗號銜接、只用一個句號收尾），30字以內。\n"
            f"question（問題）：不超過25字。"
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages, format=_CLOSING_SCHEMA, temperature=temperature)
        return self._parse_closing_response_json(raw)

    def _parse_closing_response(self, raw: str) -> dict:
        """解析收尾引導的輸出。"""
        result: dict = {"closing_text": "", "question": ""}
        # current_field 追蹤續行：本地模型若把「收尾語：」單獨放一行、內容
        # 接在下一行（其他parser都有處理過的已知格式變體，見
        # _parse_question_response docstring），沒有這個追蹤會直接漏接，
        # closing_text整個變空字串，長者聽到的會是通用保底語而非模型真正
        # 生成的內容。
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _LEAKED_LIST_MARKER_RE.sub("", line)
            if line.startswith("收尾語："):
                result["closing_text"] = line[len("收尾語："):].strip()
                current_field = "closing_text"
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif line.startswith("問題類型："):
                # 同 _parse_question_only_response 等parser的guard說明——這支
                # 函式的prompt也沒有要求輸出這個欄位，一樣要擋。
                current_field = None
            elif current_field == "closing_text":
                result["closing_text"] = f"{result['closing_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("closing_text", "question"):
            result[key] = _strip_leaked_brackets(_strip_trailing_covered_w_leak(result[key]))
        if not result["question"]:
            result["question"] = _FALLBACK_QUESTION
        if not result["closing_text"]:
            print(f"[Orchestrator] ⚠ 收尾語（引導語）欄位是空的，退回保底收尾語。"
                  f"原始輸出: {raw[:200]!r}")
            result["closing_text"] = _FALLBACK_CLOSING_TEXT
        return result

    def _parse_closing_response_json(self, raw: str) -> dict:
        """解析收尾引導改用結構化輸出（format=_CLOSING_SCHEMA）後的JSON回應，
        取代逐行掃描「收尾語：/問題：」標籤的 _parse_closing_response。"""
        result: dict = {"closing_text": "", "question": ""}
        data = self._salvage_json_object(raw, ("closing_text", "question"))
        if isinstance(data, dict):
            result["closing_text"] = str(data.get("closing_text") or "").strip()
            result["question"] = str(data.get("question") or "").strip()
        for key in ("closing_text", "question"):
            result[key] = _strip_leaked_brackets(_strip_trailing_covered_w_leak(result[key]))
        # 2026-09-14稽核：closing.txt 已經要求承接語只寫一句話、只用一個
        # 句號收尾，但這只是prompt上的要求，本地弱模型偶爾還是會多寫一句
        # ——機械式只保留到第一個句號為止當結構性保證，不依賴模型自己
        # 遵守。找不到句號（模型用「！」「？」結尾、或忘記加標點）就整句保留，
        # 不動它，避免誤刪掉唯一一句話的內容。
        result["closing_text"] = _truncate_to_first_period(result["closing_text"])
        if not result["question"]:
            result["question"] = _FALLBACK_QUESTION
        if not result["closing_text"]:
            print(f"[Orchestrator] ⚠ JSON收尾語欄位是空的（schema保證了key存在，"
                  f"但內容是空字串），退回保底收尾語。原始輸出: {raw[:200]!r}")
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
            line = _LEAKED_LIST_MARKER_RE.sub("", line)
            if line.startswith("情緒回應："):
                result["emotional_text"] = line[len("情緒回應："):].strip()
                current_field = "emotional_text"
            elif line.startswith("後續引導："):
                result["question"] = line[len("後續引導："):].strip()
                current_field = "question"
            elif line.startswith("問題類型："):
                # 同 _parse_question_only_response 等parser的guard說明——這支
                # 函式的prompt也沒有要求輸出這個欄位，一樣要擋。
                current_field = None
            elif current_field == "emotional_text":
                result["emotional_text"] = f"{result['emotional_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        for key in ("emotional_text", "question"):
            result[key] = _strip_leaked_brackets(_strip_trailing_covered_w_leak(result[key]))
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

        2026-08-21：今日主題跟【興趣】欄位裡某個項目字面完全對得上時（例如主題
        「音樂」對興趣「音樂、運動、咖啡」），不再交給LLM猜——temperature=0只
        保證同樣輸入會得到一致答案，不保證答案正確，實測遇到字面完全命中的情況
        還是穩定猜成hometown，生出跟主題毫無關聯的畫面。這種字面精準命中的情況
        用程式判斷直接鎖定interest，不需要LLM介入判斷。
        """
        if user.get("preferences"):
            pref_items = [p.strip() for p in re.split(r"[、,，/\s]+", user["preferences"]) if p.strip()]
            today_topic = user["today_topic"]
            if any(item in today_topic or today_topic in item for item in pref_items):
                return "interest"

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
   不能只是把元素名詞堆在一起當場景清單。下面「烤肉」「燈籠」只是示範怎麼把
   一個場景動作轉寫成視覺痕跡的句型，跟本次長者資料/今日主題完全無關，
   絕對不要把範例裡的烤肉、燈籠等具體內容當成本次答案抄進輸出——本次
   elements/image_prompt 只能根據上面【長者資料】跟【任務】指令的情境自己
   決定，不能是烤肉或燈籠（除非上面情境真的就是烤肉或燈籠）。
   句型示範（例如不要只寫"barbecue grill and lanterns lit up"，要寫"at the
   backyard grill, skewers of meat sizzling over glowing charcoal with
   rising smoke"）——實測發現只堆物件名詞，AI 生圖模型會把該物件畫成靜態
   背景道具，長者真正想回憶的「活動氛圍」反而不見了。這裡要寫的是活動留下
   的畫面痕跡／狀態本身，不能用「誰在做這件事」的敘事角度描述（見規則3，
   畫面不出現人物）——例如不要寫「一群人圍著烤肉」，要寫「炭火發亮、肉串
   滋滋作響、輕煙裊裊」；不要寫「小孩提著燈籠跑」，要寫「幾盞燈籠高掛，
   光暈搖曳」：
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

下面是格式示範（主題=清明掃墓），只是示範 JSON 該長什麼樣子，跟本次長者
資料／今日主題完全無關，絕對不要把示範裡的墓園、香燭、供品等具體內容當成
本次答案抄進輸出——本次 elements/image_prompt 必須根據上面【長者資料】跟
【任務】指令的情境重新產生，不能是清明掃墓相關內容（除非上面【今日主題】
真的就是清明掃墓）：
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

        這條路徑刻意保持獨立、不併回 _plan_image——併回去會弄丟空間關係，
        使畫面品質變差。也不對動作/人物做checklist+重試核對，單次生成
        即可，不強求逐一涵蓋每個動作/人物，只要求忠實翻譯、保留空間關係；
        畫面裡不出現任何人物的保證，交給 _start_scene_after_detail 統一
        呼叫的 _strip_people_clauses／_add_no_people_directive 確定性處理，
        不需要在這裡另外驗證。
        """
        image_prompt = await self._translate_detail_to_image_prompt(user, elder_detail)
        elements = await self._extract_elements_from_detail(elder_detail)
        return {"elements": elements, "image_prompt": image_prompt}

    async def _translate_detail_to_image_prompt(self, user: dict, elder_detail: str) -> str:
        """
        組給AI生圖模型用的prompt，見 _plan_image_from_detail 說明。用確定性
        字串組合、不再是LLM生成：固定風格/技術指令＋今日主題＋長者原話本身
        完整保留（動作/地點/相對位置/時間都在同一段話裡）。跳過LLM生成也就
        跳過了「先把中文原話翻成英文」這個有損步驟的失真問題（例如「車庫」
        曾被翻成backyard、carport這類語意相關但構造不同的詞）——生圖模型
        本身中文理解力足夠好，直接送中文原話即可正確畫出構造細節；風格
        指令也改用中文，效果一樣好、甚至更貼近節慶氛圍。

        防幻覺指示只禁止「有敘事意義、代表長者記得某個具體細節」的物件
        （例如長者沒提到燈籠卻畫出來，等於暗示長者記得有燈籠，這才是真的
        失真）；純粹讓畫面有生活感的環境陳設（家具、植栽、雜物、光線氛圍）
        不受限制，可以自然補充——完全禁止添加任何東西會讓畫面像空舞台，
        缺少一般生活場景該有的環境雜物。

        「不要人物」沒有寫死在這裡：呼叫端（_start_scene_after_detail）
        統一套用 _strip_people_clauses／_add_no_people_directive，這裡若
        重複寫一次會被 _strip_people_clauses 的關鍵字咬到自己這句話、變成
        殘缺片段又被補一次完整版，兩段撞在一起。只在下游統一加一次。
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

        核對基準只用 image_prompt 本身，不合併 elements 一起核對——elements
        只是短物件名詞清單，從來不會送去 Stability AI（只有image_prompt
        會），若elements裡列了某個動作但image_prompt完全沒展開，只因字詞
        出現在elements裡就判定「有涵蓋」會是假陽性，實際生出來的圖根本
        不會畫那個動作。
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

        每項都要求合併成一個完整的「人物+動作」或「地點+活動」短語，不能
        再拆成獨立單詞（例如「阿公在門口泡茶」不能拆成「門口」「泡茶」
        兩項）——拆太細會讓項目數暴增，遠超過一張圖能畫的份量，重試一次
        也補不齊。

        不排除主題名稱本身：_build_plan_image_prompt 的 anchor 分支明講
        「【今日主題】本身如果是像節慶這種有清楚視覺意象的詞，可以直接
        當一個元素使用」，若這裡排除反而不一致；長者回憶原文也常常直接
        提到主題本身（例如「以前中秋節，全家人...」），排除掉等於漏記
        長者原話真的提到的內容。
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

        system_content 只組合 STEP1 用得到的片段（不含 STEP2/3流程），
        見 _load_prompt_modules 說明。這支函式輸出「思考：」欄位，所以要
        帶上 format_and_examples.txt。
        """
        system_content = _load_prompt_modules(
            "role_and_topics.txt", "step1_flow.txt",
            "question_wording_rules.txt", "prohibitions.txt",
            "format_and_examples.txt",
        ) or (
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

        # 2026-09稽核：姓名欄位拿掉，理由同 _generate_closing 那次——規則
        # 一律要求用「你」稱呼，這裡從沒用到真實姓名，留著只會被模型當成
        # 稱呼詞誤用（見 response_guard.py 用長者本名稱呼那條檢查的稽核說明）。
        user_content = (
            f"【長者資料】\n"
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
            line = _LEAKED_LIST_MARKER_RE.sub("", line)
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
                # 汙染送給長者的內容（同 _parse_question_response 既有的防線）。
                current_field = None
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        result["question"] = _strip_leaked_quotes(_strip_leaked_brackets(
            _strip_trailing_anchor_leak(_strip_trailing_covered_w_leak(result["question"]))
        ))
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
    ) -> dict:
        """
        長者看完剛生成的圖、說出第一反應後，只依反應內容判斷屬於哪一類、
        不現寫承接語——承接語改用固定模板（_IMAGE_REVEAL_REACTION_
        TEMPLATES），由呼叫端 _handle_image_reveal_answer 依這裡回傳的
        classification 查表決定，見該常數上方 2026-09-10 說明（
        現寫一句承接語要多等一次文字生成，換成固定模板可以省下這段等待，
        代價是不再具體呼應長者這次反應裡的細節內容）。也不在這裡順便生成
        STEP1開場問題（那由 _regenerate_image_reveal_question 另外呼叫，
        其 prompt 故意不放【長者看完圖後的第一反應】）：實測發現同一次
        呼叫會讓問題被反應內容帶偏（例如長者說「院子更大」，問題就問「院子
        多大」），即使明文禁止也擋不住，讓模型從一開始看不到這段內容比事後
        靠規則要求更可靠。

        分3類，只有前兩類＋「情緒明顯（感動）」會走到這裡（「情緒明顯
        不安」已被 process_response 最前面的 _detect_emotional_trigger 攔截）：
          1. 圖跟長者記得的一致 → 肯定
          2. 有差異（不論長者講得籠統或具體）→ 誠實承認AI示意圖畫不出所有
             細節，不說「沒關係」「不重要」這種輕描淡寫的話
          3. 長者明顯被觸動、感動 → 直接承接這份情緒，不急著轉開話題

        分類判斷本地量化基底模型不穩定，改成先列「判斷依據」再輸出「分類」
        數字（跟 _has_usable_detail、_decide_topic_continuation 同一種
        「證據式核對」取代直接下整體判斷的模式）。「判斷依據」只用來記錄／
        除錯，不影響後續流程；「分類」則會直接決定套用哪句固定模板，
        必須判準——parser 見 _parse_image_reveal_response_json。

        分類1（肯定/相似）跟分類2（差異）的邊界：判斷要看整句語意，不是
        逐字比對固定詞表——長者的話是STT轉出來的文字，同音字/漏字隨時可能
        讓真實輸入沒對到清單裡任何一個詞。比較級講法（更大/更小/比較高/
        比較矮/沒有那麼X）跟「像/很像/差不多/蠻像/滿像」都算分類1的相似
        訊號，但同一句話後段若接了具體差異，以差異（分類2）為準。

        covered_w／pre_image_detail 只當分類判斷任務的背景，不影響 STEP1
        問題生成（已拆到 _regenerate_image_reveal_question）；長者對圖片的
        反應本身是在評論AI示意圖畫得準不準，不是回憶敘述，不拿去跑
        _detect_covered_w，避免讓5W1H追蹤失真。

        system_content 只組合 role_only.txt＋prohibitions.txt（角色守則＋
        禁止事項），不含STEP1/STEP2/STEP3流程、提問規則庫或思考欄位／
        範例，見 _load_prompt_modules 說明。這支函式輸出的是「判斷依據／
        分類」，不是「思考／問題」，帶上STEP1/2/3的思考欄位／範例反而會把
        輸出格式帶偏，任務細節放在 user_content 的【任務】欄位定義，那裡的
        【輸出格式】才是唯一的格式依據。

        emotion: Kinect 即時偵測的情緒（見 process_response 同名參數），
            會注入 _emotion_guidance() 當背景資訊，跟STEP1/STEP2/STEP3/
            收尾語一致的作法，但改用固定模板後不再影響輸出內容（模板本身
            不分情緒）。
        retry_feedback: 見 _generate_question 的同名參數說明。
        """
        system_content = _load_prompt_modules(
            "role_only.txt", "prohibitions.txt",
        ) or (
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
            f"\n【任務】\n"
            f"長者剛看完AI依據他先前說的內容生成的一張示意圖，說出了他的第一"
            f"反應。請先具體列出這句反應裡有哪些線索（例如：出現「對/沒錯/"
            f"就是這樣/像/很像/蠻像/滿像/差不多」這類肯定或相似詞、或「不"
            f"像/不一樣/有點不同」這類差異詞、或「更大/更小/更多/更少/比較"
            f"高/比較矮/沒有那麼X」這類比較級差異詞、或位置/顏色/擺設/桌椅"
            f"家具等具體細節詞、或明顯的情緒字眼），再依這些線索判斷屬於"
            f"下面哪一種：\n"
            f"（注意：「像」「很像」「蠻像」「滿像」「差不多」單獨出現、"
            f"前面沒有「不」字，是肯定圖跟記憶一致的正面訊號，要判成分類1，"
            f"不要因為字面上出現「像」這個字，就聯想到分類2的「不像」，"
            f"兩者意思完全相反；只有明確帶「不」字的「不像」，或整句語氣是"
            f"在指出落差，才算分類2的線索）\n"
            f"（注意：「更大/更小/更多/更少/比較高/比較矮/沒有那麼大」這類"
            f"比較級講法，即使沒有出現「不像/不一樣」的字面，本質上也是在"
            f"拿AI示意圖跟自己記得的樣子比較、指出落差，一樣算分類2的線索"
            f"——不要因為沒看到「不」這個字，就誤判成分類1或看不出線索。"
            f"例如長者說「我家的院子更大」，是在說AI畫的院子比他記得的小，"
            f"這是明確的差異訊號，不是單純的肯定或無關的補充資訊）\n"
            f"（注意：上面列的詞都只是常見例句，不是要逐字比對的完整清單。"
            f"【長者看完圖後的第一反應】是語音辨識（STT）轉出來的文字，"
            f"可能有同音字誤植、漏字或跟例句用詞不完全一樣（例如「蠻像」"
            f"被辨識成「慢像」「满相」之類的同音錯字），只要整體語意上是"
            f"在表達肯定/相似，就算分類1的線索，不要因為沒有出現清單上的"
            f"精確字詞就判定不算；同理，只要語意上是在表達差異/不同，就算"
            f"分類2的線索，一律以整句語意為準，不是比對字面。）\n"
            f"（注意：完整讀完整句反應再判斷，不要只看到開頭的「像」就直接"
            f"歸類1、沒往下看完就下結論。長者常常一句話裡先講一個肯定的"
            f"開頭，後面才接真正想講的具體差異，例如「很像當時的場景，但"
            f"以前我們家不會有那麼多人一起烤肉」——開頭「很像」只是順口的"
            f"起手式，後半段「人數不對」才是這句話真正的重點，這種情況要"
            f"判成分類2，不是分類1）\n"
            f"（注意：長者的反應如果只是單純的正面評價（例如「很漂亮」「很"
            f"好看」「不錯」），完全沒有提到「像/不像」這類跟記憶比對有關的"
            f"字眼、也沒有指出任何具體差異，不能因為長者沒有明確說「像」或"
            f"「一致」，就預設他在暗示哪裡不一樣、判成分類2——分類2成立的"
            f"前提是反應裡真的有差異/落差的訊號，沒有這個訊號就不成立。這種"
            f"單純正面、沒有比對訊號的反應，一律當分類1處理，用溫暖肯定的"
            f"語氣接住這份正面評價就好，不要反過來暗示長者有哪裡不一樣。）\n"
            f"1. 覺得圖跟自己記得的一致（肯定/相似詞，且沒有接著出現具體"
            f"差異）。\n"
            f"2. 覺得圖跟自己記得的不一樣（不管長者有沒有具體講出是哪裡不"
            f"一樣，只籠統說「不像」「不一樣」「有點不同」，或已經點名具體"
            f"的人事物、擺設、細節，例如位置、顏色、桌椅家具、物品款式等，"
            f"都算這一類）。\n"
            f"3. 明顯被圖片觸動、感動（例如出現「心裡酸酸的」「好多事情都"
            f"想起來了」這類明顯情緒字眼）。\n"
            f"\n【範例】（跟這次任務完全無關的另一組情境，只是示範輸出格式"
            f"長什麼樣子——判斷依據/分類都要照這個順序、這個欄位名稱輸出）\n"
            f"範例1（長者反應：「咦，好像不太一樣耶」）\n"
            f"判斷依據：長者只籠統說「不太一樣」，沒有講出是哪裡不同。\n"
            f"分類：2\n"
            f"範例2（長者反應：「看到這個，我心裡酸酸的，好多事情都想起"
            f"來了」）\n"
            f"判斷依據：長者用「心裡酸酸的」「好多事情都想起來」表達明顯"
            f"被觸動的情緒。\n"
            f"分類：3\n"
            f"（以上2個範例只是示範語氣跟格式用，情境也跟這次任務無關，不是"
            f"可以直接照抄的答案。這次的判斷依據必須根據長者這次實際說的"
            f"反應內容重新寫，禁止把上面任何一句原封不動搬過來用。）"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"輸出一個JSON物件，包含以下2個key：\n"
            f"judgment_evidence（判斷依據）：一句話列出你在長者這句反應裡看到的"
            f"具體線索，不要空泛帶過。只能引用長者這次【反應內容】裡實際出現的"
            f"字詞，禁止編造、改寫或想像長者沒說過的話——如果反應內容很簡短、"
            f"籠統，看不出明確線索，就老實寫「反應內容簡短，看不出明確線索」，"
            f"不要為了湊出一個依據硬掰內容。\n"
            f"classification（分類）：只能填 \"1\"、\"2\"、\"3\" 其中一個，對應上面3種分類。"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        # temperature=0：這支函式現在純粹是分類任務（judgment_evidence＋
        # classification），不是自由生成——分類結果直接決定套用哪句固定
        # 模板給長者聽（見上方docstring）。跟 _classify_topic_category／
        # _decide_topic_continuation 等其他分類函式一樣該用0，原本漏設、
        # 吃了llm.py預設的0.3，同一句長者反應可能因取樣隨機性被判成不同
        # 分類（見 app/safety/taboo_checker.py llm_topic_check 同一種問題
        # 的稽核說明）。
        raw = await self.llm.chat(
            messages, format=_IMAGE_REVEAL_REACTION_SCHEMA, temperature=0,
        )
        return self._parse_image_reveal_response_json(
            raw, scene_elements=scene_elements, expects_question=False,
        )

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
    ) -> dict:
        """
        STEP1 開場問題的生成函式，_handle_image_reveal_answer 拿到
        _generate_image_reveal_reaction 的承接語／分類後，另外呼叫這支
        函式生成接下來要問的問題。這支函式的 user_content 故意不放【長者
        看完圖後的第一反應】，模型從一開始就看不到這段內容，結構上保證
        問題不會被長者對圖片的評論帶偏（例如長者說「我家的院子更大」，
        問題卻問「你們家院子多大呢」）——比在同一次呼叫裡用文字規則要求
        模型自己不要參考來得可靠。

        elder_response 參數仍保留（呼叫端傳的是跟 _generate_image_reveal_
        reaction 相同的一組參數，方便維護），但這支函式的 user_content
        故意不使用它——這是刻意設計，不是疏漏。

        套用的是STEP2/3流程的「先選角度、後選錨點」邏輯，不是STEP1。
        system_content 組合：角色守則、STEP2/3流程（借用其選角度/錨點的
        推理過程）、提問規則庫（這支函式要輸出「問題：」）、禁止事項——
        不含16大主題（不輸出主題判斷），也不含 format_and_examples.txt
        （這支函式的【輸出格式】只有「問題／錨點／本回合已涵蓋的W」，
        沒有「思考：」，帶那個模組會把輸出格式帶偏）。
        """
        system_content = _load_prompt_modules(
            "role_only.txt", "step23_flow.txt",
            "question_wording_rules.txt", "prohibitions.txt",
        ) or (
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
        question_direction_str = _STEP1_OPEN_DIRECTION_HINT

        user_content = (
            f"【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"\n【長者生圖前分享的內容】\n{pre_image_str}\n"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"請問長者一個新問題。{question_direction_str}"
            f"{_retry_feedback_section(retry_feedback)}"
            f"\n【輸出格式】\n"
            f"問題：（≤25字，開放式，開頭要有具體錨點，畫面物件或長者生圖前分享的"
            f"內容皆可）\n"
            f"{_ANCHOR_FIELD_SPEC}\n"
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
        """解析 _regenerate_image_reveal_question 的輸出（問題＋錨點＋涵蓋的W，
        沒有承接語欄位）。

        2026-09-08：補上「錨點：」欄位解析——這支函式原本沒有要求LLM輸出
        _ANCHOR_FIELD_SPEC，讓 response_guard.py 的 question_anchor_
        unsupported 防編造檢查對這條路徑形同虛設（result.get("anchor","")
        永遠拿到空字串，guard直接放行），實測出現過長者只講過籠統的「東西」，
        問題卻無中生有具體化成「這道家常菜」也沒被攔下來。現在補上跟
        STEP2/STEP3同一套 _ANCHOR_FIELD_SPEC，接上既有防護機制。"""
        result: dict = {"question": "", "covered_w": [], "anchor": ""}
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _LEAKED_LIST_MARKER_RE.sub("", line)
            if line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif line.startswith("錨點："):
                result["anchor"] = line[len("錨點："):].strip()
                current_field = "anchor"
            elif line.startswith("本回合已涵蓋的W："):
                w_raw = _strip_leaked_brackets(line[len("本回合已涵蓋的W："):].strip())
                result["covered_w"] = [
                    w.strip()
                    for w in w_raw.replace("，", "、").split("、")
                    if w.strip()
                ]
                current_field = None
            elif line.startswith("問題類型："):
                # 這欄不儲存，但要停止把後面的行接到問題——這支函式的prompt
                # 沒有要求輸出這個欄位，但本地模型仍會沿用其他prompt格式
                # （STEP1/STEP3都有「問題類型：」欄位）的習慣自己加一行，
                # 沒有這個guard會被「問題：」設下的current_field一路吃下去，
                # 把「問題類型：」也送去給長者聽。
                current_field = None
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
            elif current_field == "anchor":
                result["anchor"] = f"{result['anchor']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        result["question"] = _strip_leaked_quotes(_strip_leaked_brackets(
            _strip_trailing_anchor_leak(_strip_trailing_covered_w_leak(result["question"]))
        ))
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

        emotion: 見 _generate_image_reveal_reaction 的同名參數說明，跟其他
        生成函式一致都會注入 _emotion_guidance()。

        輸出格式跟 _generate_image_reveal_reaction 相同（承接語／問題／
        本回合已涵蓋的W），沿用同一支 parser；【任務】套用的是【STEP2自由
        追問／STEP3補問：生成流程】的「先選角度、後選錨點」邏輯，不是
        STEP1流程。system_content 組合跟 _regenerate_image_reveal_question
        同一套邏輯：不含16大主題、不含 format_and_examples.txt（這支函式
        沒有「思考：」欄位，帶那個模組會把輸出格式帶偏）。
        """
        system_content = _load_prompt_modules(
            "role_only.txt", "step23_flow.txt",
            "question_wording_rules.txt", "prohibitions.txt",
        ) or (
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
            f"輸出一個JSON物件，包含以下2個key：\n"
            f"reaction_text（承接語）：1-2句，30字以內，具體呼應長者生圖前分享的內容。\n"
            f"question（問題）：不超過25字，開放式，開頭要有具體錨點，畫面物件或"
            f"長者生圖前分享的內容皆可。"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages, format=_QUICK_END_RECAP_SCHEMA)
        return self._parse_image_reveal_response_json(raw, scene_elements=scene_elements)

    def _parse_image_reveal_response(
        self, raw: str, scene_elements: list[str] | None = None,
        expects_question: bool = True,
    ) -> dict:
        """
        解析 _generate_image_reveal_reaction／_generate_quick_end_recap 的
        輸出（判斷依據＋分類＋承接語＋問題＋涵蓋的W）。

        judgment_evidence/classification 是除錯／記錄欄位，只有
        _generate_image_reveal_reaction 的輸出會有這兩個欄位；
        _generate_quick_end_recap 沒有分類任務、不會產生這兩個欄位，共用
        這支 parser 時保持空字串即可，不影響它原本的行為。
        classification 只取開頭的1個數字字元，LLM若多寫了說明文字一併丟棄，
        找不到數字就保留原始字串以便從log看出是哪裡解析失敗。

        expects_question: _generate_image_reveal_reaction 的輸出格式只剩
        判斷依據／分類／承接語，prompt沒有要求「問題：」欄位——但下面「找
        不到問題欄位就把整段raw文字塞進question」這條保底邏輯是給
        _generate_quick_end_recap（輸出格式真的有「問題：」）設計的，對
        _generate_image_reveal_reaction 來說這個保底條件每次都成立，會把
        「判斷依據＋分類＋承接語」三行全部當成一句問題，送進
        guarded_generate 幾乎必然觸發too_long，讓分類機制實際上從沒真正
        生效過。呼叫端已經不讀這裡的 question（STEP1問題另外呼叫
        _regenerate_image_reveal_question 生成），expects_question=False
        時直接讓 question 保持空字串，不觸發這條保底；
        _generate_quick_end_recap 呼叫時仍傳 True，維持原本行為。
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
            line = _LEAKED_FIELD_LABEL_RE.sub(r"\1", line)
            line = _LEAKED_LIST_MARKER_RE.sub("", line)
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
            elif line.startswith("問題類型："):
                # 這欄不儲存，同 _parse_question_only_response 的guard說明——
                # 這支函式的prompt也沒有要求輸出這個欄位，一樣要擋，不然會被
                # 上面「問題：」設下的current_field接到問題文字後面。
                current_field = None
            elif current_field == "judgment_evidence":
                result["judgment_evidence"] = f"{result['judgment_evidence']} {line}".strip()
            elif current_field == "reaction_text":
                result["reaction_text"] = f"{result['reaction_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
        digit_match = re.search(r"[1-3]", result["classification"])
        if digit_match:
            result["classification"] = digit_match.group()
        if expects_question and not result["question"]:
            result["question"] = raw.strip()
        for key in ("reaction_text", "question"):
            result[key] = _strip_leaked_quotes(_strip_leaked_brackets(
                _strip_trailing_covered_w_leak(result[key])
            ))
        if expects_question and not result["question"]:
            first_element = (scene_elements or [None])[0]
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if not result["reaction_text"]:
            print(f"[Orchestrator] ⚠ 出示圖片承接語欄位是空的，退回保底承接語。"
                  f"原始輸出: {raw[:200]!r}")
            # 這裡不能留空：這支解析函式同時給 _generate_image_reveal_reaction
            # 跟 _generate_quick_end_recap 共用，兩者的呼叫端都直接把
            # result["reaction_text"] 當 scene_text 使用，留空會讓那一輪
            # scene_text 整段空白。改用真正的畫面元素組保底句，跟
            # _element_fallback（見該函式說明）同一套做法。
            elements_str = _natural_join(scene_elements or [])
            result["reaction_text"] = (
                f"謝謝你看著眼前有{elements_str}的畫面，跟我說了這麼多。"
                if elements_str else "謝謝你陪我看這張圖，跟我說了這麼多。"
            )
        return result

    def _parse_image_reveal_response_json(
        self, raw: str, scene_elements: list[str] | None = None,
        expects_question: bool = True,
    ) -> dict:
        """解析 _generate_image_reveal_reaction／_generate_quick_end_recap 改用
        結構化輸出後的JSON回應，取代逐行掃描的 _parse_image_reveal_response。

        expects_question=False（_generate_image_reveal_reaction）時 raw 是
        _IMAGE_REVEAL_REACTION_SCHEMA 的形狀（沒有 question，也沒有
        reaction_text——這支函式改依 classification 查
        _IMAGE_REVEAL_REACTION_TEMPLATES 固定模板來填，不再讀LLM輸出的
        文字）；True（_generate_quick_end_recap）時是 _QUICK_END_RECAP_
        SCHEMA 的形狀（沒有 judgment_evidence／classification，reaction_
        text 仍是LLM現寫的）——用 .get() 讀取，對方沒有的 key 就自然保持
        初始值，不用另外分支。

        covered_w：兩支 schema 都已經拿掉這個key（2026-09稽核，見
        _QUICK_END_RECAP_SCHEMA 上方說明——唯一會用到它的呼叫端明確標示
        「自報W…不採信」），這裡固定回傳空list只是維持舊版parser的dict
        形狀，不代表還有在解析它。
        """
        result: dict = {
            "judgment_evidence": "", "classification": "",
            "reaction_text": "", "question": "", "covered_w": [],
        }
        data = self._salvage_json_object(
            raw, ("judgment_evidence", "classification", "reaction_text", "question"),
        )
        if isinstance(data, dict):
            result["judgment_evidence"] = str(data.get("judgment_evidence") or "").strip()
            result["classification"] = str(data.get("classification") or "").strip()
            if expects_question:
                # _QUICK_END_RECAP_SCHEMA 仍要求LLM現寫承接語，具體呼應
                # pre_image_detail，不是下面 _IMAGE_REVEAL_REACTION_
                # TEMPLATES 那組固定句能取代的。
                result["reaction_text"] = str(data.get("reaction_text") or "").strip()
            result["question"] = str(data.get("question") or "").strip()
        digit_match = re.search(r"[1-3]", result["classification"])
        if digit_match:
            result["classification"] = digit_match.group()
        if not expects_question:
            # _IMAGE_REVEAL_REACTION_SCHEMA 已經拿掉 reaction_text 欄位
            # （2026-09-10，見該常數與 _IMAGE_REVEAL_REACTION_TEMPLATES
            # 上方說明），承接語改成依 classification 查固定模板，不再
            # 現寫——分類無效（空字串／不在1-3之間）時 templates.get 回傳
            # None，讓下面的空字串保底邏輯接手。
            result["reaction_text"] = _IMAGE_REVEAL_REACTION_TEMPLATES.get(
                result["classification"], ""
            )
        for key in ("reaction_text", "question"):
            result[key] = _strip_leaked_quotes(_strip_leaked_brackets(
                _strip_trailing_covered_w_leak(result[key])
            ))
        if expects_question and not result["question"]:
            first_element = (scene_elements or [None])[0]
            print(f"[Orchestrator] ⚠ 出示圖片JSON問題欄位是空的，退回"
                  f"{'含畫面元素的' if first_element else ''}保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if not result["reaction_text"]:
            print(f"[Orchestrator] ⚠ 出示圖片JSON承接語欄位是空的，退回保底承接語。"
                  f"原始輸出: {raw[:200]!r}")
            elements_str = _natural_join(scene_elements or [])
            result["reaction_text"] = (
                f"謝謝你看著眼前有{elements_str}的畫面，跟我說了這麼多。"
                if elements_str else "謝謝你陪我看這張圖，跟我說了這麼多。"
            )
        print(f"  → 承接語: {result['reaction_text']!r}")
        return result

    # 2026-09-14稽核：_compose_ack_or_none（STEP2/3承接語的
    # 抽取→造句流程，_generate_open_followup／_generate_supplement_
    # question 曾經唯一的呼叫方式）整支移除——這裡持續抓到「承接語跟長者
    # 原話對不上」「答非所問」「AI冒用經歷」「我也覺得很隨便」這類語意
    # 不通的失效模式（見 ack_composer.py 整份模組的稽核記錄），
    # 提案後決定STEP2/3乾脆不生成承接語，直接問問題就好。ack_composer.py
    # 這份模組本身沒有跟著刪掉（保留給未來需要時參考／重新掛回去），只是
    # 這裡不再呼叫它。

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
        covered_senses: list[str] | None = None,
        skipped_senses: list[str] | None = None,
        topic_senses: list[str] | None = None,
        temperature: float | None = None,
    ) -> dict:
        """
        STEP2 開放式追問（Track C）：問題（question）的生成方式維持原本
        自由生成不變。

        2026-09-14稽核：不再生成承接語（scene_text）——
        拿掉之前這裡曾經歷「自由生成承接語→改用ack_composer抽取→造句
        算出來當既定事實塞給模型」這兩個階段，過程中持續抓到「承接語
        跟長者原話對不上」「答非所問」「AI冒用經歷」（見ack_composer.py
        整份模組的稽核記錄）等一連串失效模式，決定這一步乾脆整段拿掉，
        直接問問題就好，不再多一句承接語。連帶好處：
        省下 _compose_ack_or_none 那一串抽取＋造句的LLM往返，每輪切題
        少花好幾秒（見2026-09-14 docker log 撈取延遲來源那次稽核）。
        Returns: {"question": str}（"scene_text" 這個key仍保留在回傳
        dict裡、固定是空字串——不刪掉這個key是為了不用同步改動這支
        函式下游一整串 result.get("scene_text", "") 的呼叫點，那些
        地方拿到空字串本來就會正確跳過承接語，不會壞掉）。

        retry_feedback: 見 _generate_question 的同名參數說明。
        pre_image_detail: 長者生圖前Q1/Q2訪談的原話，錨點優先序次於「長者
            這一輪剛提到的人事物」、高於畫面元素——長者自己說過的話都比AI
            選的畫面元素更貼近他真正想聊的東西。
        covered_senses／topic_senses: 這回合已涵蓋 vs 主題相關但還沒問過的
            感官，具體點名給模型選，避免同一個感官被重複問、其他相關感官
            卻一次都沒問到。
        temperature: 見 guarded_generate 的 retry_temperature 參數說明，跟
            _generate_closing 同一種用法——_ask_open_followup 呼叫處帶了
            retry_temperature，重試時（attempt>0）會透過這個參數收到比第
            一次更高的temperature；不傳（None）就沿用 llm.py 的預設值。
            2026-09-11稽核（批次品質測試腳本抓到）：承接語補上30字長度
            事後防護後，實測發現低溫度重試常常輸出幾乎一字不差的過長句子
            （retry_feedback「請縮短」沒被真的採納），跟 _generate_closing
            當初補 retry_temperature 要解決的問題一樣，這裡補齊。
        """
        # 2026-09稽核：不載入 format_and_examples.txt——這支函式已經改用
        # 結構化輸出（JSON schema），但那份模組整份都在教模型輸出舊版的
        # 「思考：/問題：/問題類型：/本回合已涵蓋的W：」文字標籤格式，跟
        # user_content 要求的JSON格式互相矛盾，system prompt跟user prompt
        # 打架容易讓模型更混亂。核心的用詞規則已經有 question_wording_
        # rules.txt 覆蓋，不受影響。
        system_content = _load_prompt_modules(
            "role_and_topics.txt", "step23_flow.txt",
            "question_wording_rules.txt", "prohibitions.txt",
        ) or (
            "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
            "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
            "稱呼長者一律用「你」，語氣像老朋友聊天。"
            "每次聽完長者說話，先用1-2句溫暖的話具體承接他的情緒，再順著他"
            "說的話自然問下一個問題，不必勉強拉回畫面元素。"
            "絕對不在輸出中加任何括號說明或格式標記，也不用任何 markdown 語法。"
            "絕對不用是非題，也不問需要精確數字、年份、人名或地名的問題。"
        )

        elements_str = "、".join(scene_elements)
        topic_str    = user["today_topic"]
        covered_str  = "、".join(covered_w) if covered_w else "無"
        taboo_str    = "、".join(user["taboos"]) if user["taboos"] else "無"
        pre_image_str = pre_image_detail or "無"
        # 2026-09-12稽核（實測後補）：長者這一輪如果已經有實質
        # 內容（不是 _is_quick_end 判定的沉默/短回答/放棄關鍵字），完全不
        # 把【長者生圖前分享的內容】放進 prompt——實測發現只要這個區塊還在，
        # 就會跟【眼前畫面元素】重複強化同一個舊主題，即使長者這次講的是
        # 完全不同的新內容，模型也常常被拉回舊主題（例如長者說「都是自己
        # 踩裁縫車」，承接語卻還是回去講「庭院烤肉、賞月」）。pre_image_
        # detail 唯一的用途就是「長者這次沒話可說時的備用錨點」（這支函式
        # 跟 _generate_supplement_question／_generate_quick_end_recap／
        # _image_reveal_fallback_question 的說明都一致），長者這次已經給了
        # 實質內容時完全不需要，留著只會製造不必要的競爭訊號。也符合這套
        # 追問邏輯本身的設計精神——「順著長者說的話問下一個問題，不必勉強
        # 拉回畫面元素」，眼前的圖／生圖前訪談只是觸發長者回憶的引子，不是
        # 長者接下來整段話都要被拉回去的框架，真正該接住連貫性的是長者
        # 自己剛說的話，不是這張圖。用 _is_quick_end 判斷（沉默／少於5字／
        # 放棄關鍵字），跟其他地方判斷「長者這次算不算有講出東西」共用同一
        # 套標準，不重新發明門檻。
        _pre_image_needed = self._is_quick_end(elder_response)
        pre_image_section = (
            f"\n【長者生圖前分享的內容】\n{pre_image_str}\n" if _pre_image_needed else ""
        )
        remaining_senses = _relevant_uncovered_senses(
            topic_senses, covered_senses, skipped_senses,
        )
        covered_relevant_senses = _topic_relevant_covered_senses(topic_senses, covered_senses)

        uncovered = [w for w in _W_ORDER if w not in covered_w and w not in skipped_w]
        uncovered_str = "、".join(uncovered) if uncovered else "無（已全部涵蓋）"

        # elder_response 是空字串或_NO_RESPONSE_MARKER時代表長者沉默沒回應
        # （manual_test_full_round.py 按 Enter 沒輸入時傳的是_NO_RESPONSE_
        # MARKER這個字串，不是真的空字串，兩種都要當沉默處理）——若直接印
        # 「長者剛才說：「」」，模型看到沒東西可回應、但下面【長者生圖前
        # 分享的內容】還是照常列出，就會把那段稍早已經聊過的內容包裝成
        # 好像剛才才提到的新鮮反應，長者會覺得AI在複誦舊內容、沒真的在
        # 聽。明確告訴模型這是沉默、不能假裝在回應一句沒被說出口的話。
        _elder_said_something = (
            bool(elder_response.strip())
            and elder_response.strip() != _NO_RESPONSE_MARKER
        )
        elder_response_note = (
            f"長者剛才說：\n「{elder_response}」\n"
            if _elder_said_something else (
                "長者剛才沉默、沒有回應。問題不能假裝長者剛才說了什麼，也不能"
                "把下面【長者生圖前分享的內容】包裝成好像是他剛才才提到的新鮮"
                "反應——那是稍早已經聊過的內容，現在當成剛講完的話直接複述，"
                "會讓長者覺得根本沒被聽見。問題本身仍可以照常從畫面元素或"
                "生圖前分享的內容裡找錨點，但措辭不能製造「長者剛才有講到」"
                "的錯覺。\n"
            )
        )

        sense_hint_str = _sense_entry_hint(remaining_senses, covered_relevant_senses)

        # 2026-09-12稽核（實測後補）：原本這段 prompt 疊了十幾條
        # 規則＋三個各自獨立的「動筆前先自問」自我檢查段落，逐條規則都是實測
        # 抓到具體案例後補的，但整體堆起來像是在逼模型完成一張規則核對清單，
        # 讀起來反而不像自然接話——使用者反饋「不像是在聊天」，這裡精簡成
        # 一段任務說明＋一組不能踩的線，把同類規則合併、拿掉重複的自我檢查
        # 框架，實質限制（禁忌話題、不能腦補發明情節、決定主詞不能冒充長者
        # 經歷）全部保留，只是不再用「規則清單＋反覆自問」的形式堆疊。
        #
        # 2026-09-14稽核：拿掉承接語（scene_text）之後，這段
        # 任務說明也跟著整段改寫——原本「承接語錨點優先序」「承接語跟問題
        # 接同一件事」這類規則都是圍繞著承接語怎麼寫展開的，承接語不存在了
        # 這些規則本身也跟著沒有意義，不是直接刪掉留下空洞，而是把其中
        # 「問題要接著長者剛才這件事往下問，不是另起爐灶」這個精神保留、
        # 直接套用在問題本身身上。
        user_content = (
            f"{elder_response_note}"
            f"\n【今日主題】\n{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"{pre_image_section}"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"\n【尚未涵蓋的W維度】\n{uncovered_str}\n"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"直接問一個開放式問題（25字左右，不超過30字），順著長者剛才說的"
            f"內容自然往下問，不用先講一句話承接——question 要能看出是接著"
            f"長者剛才那句話問的，不是憑空冒出來的新話題。\n"
            + (
                f"問題錨點優先序：長者這一輪剛提到的具體人事物→【長者生圖前"
                f"分享的內容】→畫面元素。\n"
                if _pre_image_needed else (
                    f"問題錨點用長者這一輪剛提到的具體人事物，接不上才改用"
                    f"畫面元素。\n"
                )
            )
            + f"\n【幾條不能踩的線】\n"
            f"不要腦補、發明長者沒說過的具體情節或後續發展，緊貼他這輪實際"
            f"說的內容就好；稱呼長者一律用「你」。\n"
            f"問題裡的「我」只能用在表達AI自己當下的情緒反應，不能延伸出AI"
            f"自己的具體人生經歷、家人或童年往事（例如「我小時候」「我媽媽」），"
            f"也不能用「我們」把AI跟長者寫成一起經歷過同一件事——AI在這段"
            f"對話裡只是聆聽者，沒有自己的人生經歷，這些內容只屬於長者本人。\n"
            f"{sense_hint_str}"
            f"{_retry_feedback_section(retry_feedback)}"
            + f"\n【輸出格式】\n"
            f"輸出一個JSON物件，包含以下3個key：\n"
            f"thinking："
            + (
                (
                    "第一步先把「長者剛才說」那句話裡的關鍵字詞原文照抄一次——一字"
                    "不改，不要換成同義詞、相關詞，也不要用自己的話摘要或改寫，只要"
                    "抄出裡面實際出現的具體詞語本身；接著做主題判斷（一句話判斷"
                    "今日主題最貼近哪個核心主題）＋切入角度（一到兩句話決定這題要"
                    "用什麼當錨點、往哪個方向問，優先延續剛才原文照抄出來的那個"
                    "詞語）——不會念給長者聽。\n"
                ) if _elder_said_something else (
                    "主題判斷（一句話判斷今日主題最貼近哪個核心主題）＋切入角度"
                    "（一到兩句話決定這題要用什麼當錨點、往哪個方向問）——不會念"
                    "給長者聽。\n"
                )
            )
            + f"question（問題）：以25字為目標，不超過30字，開放式問題。\n"
            f"{_ANCHOR_FIELD_SPEC}"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages, format=_OPEN_FOLLOWUP_SCHEMA, temperature=temperature)
        return self._parse_track_c_response_json(raw, scene_elements=scene_elements)

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
        covered_senses: list[str] | None = None,
        skipped_senses: list[str] | None = None,
        topic_senses: list[str] | None = None,
        temperature: float | None = None,
    ) -> dict:
        """
        W 補問（STEP3）：問題（question）的生成方式、target_w 角度提示、
        事後核對、問偏了帶 retry_feedback 重打，整套機制不受影響。

        2026-09-14稽核：不再生成承接語（scene_text），理由
        跟過程見 _generate_open_followup 上方同一則說明。
        Returns: {"question": str}（"scene_text" key仍保留、固定空字串，
        理由同 _generate_open_followup）。

        retry_feedback: 見 _generate_question 的同名參數說明。
        elder_response: 長者最近說的話——STEP3觸發時（can_continue=False，見
            _decide_topic_continuation／_is_quick_end）長者的回應通常很短或
            話題已經自然結束，這一步要做的是「收一下、換方向」，不是「順著
            聊下去」，跟STEP2的自由追問性質不同。
        pre_image_detail／covered_senses／topic_senses: 見 _generate_open_
            followup 的同名參數說明，理由相同。
        temperature: 見 _generate_open_followup 的同名參數說明，理由相同——
            _ask_supplement 呼叫處帶了 retry_temperature。

        【任務】除了 _W_HINT[target_w] 的字面提示，也要提到可以用感官記憶
        切入（否則感官細節幾乎不會被問到），同時禁止問【已涵蓋的W維度】
        清單裡的方向。

        「思考：」欄位要求先摘要一次【長者剛才說的話】才能選錨點/切入角度，
        且問題稱呼一律用「你」、不能用「長者」這個第三人稱——避免模型
        思考階段用旁白語氣描述長者、問題又沿用同樣的人稱與語氣（事後
        防護見 response_guard.py 的 third_person_elder_wording）。

        這是 STEP3，system_content 只組合STEP2/3用得到的片段（含
        format_and_examples.txt，這支函式要輸出「思考：」欄位）。
        """
        # 2026-09稽核：不載入 format_and_examples.txt——這支函式已經改用
        # 結構化輸出（JSON schema），但那份模組整份都在教模型輸出舊版的
        # 「思考：/問題：/問題類型：/本回合已涵蓋的W：」文字標籤格式，跟
        # user_content 要求的JSON格式互相矛盾，system prompt跟user prompt
        # 打架容易讓模型更混亂。核心的用詞規則已經有 question_wording_
        # rules.txt 覆蓋，不受影響。
        system_content = _load_prompt_modules(
            "role_and_topics.txt", "step23_flow.txt",
            "question_wording_rules.txt", "prohibitions.txt",
        ) or (
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
        # 2026-09-07稽核（實測發現）：elder_response 是
        # _NO_RESPONSE_MARKER（長者沒回應，或治療師按跳過）時，這裡原本會
        # 原封不動把「（長者未回應）」這串固定字串當成「長者剛才說的話」
        # 塞給模型，模型可能誤當成真的講了這句話去解讀，同一個問題
        # _generate_open_followup（STEP2）用 _elder_said_something 早就
        # 排除掉了，這裡少了同一層防護，這裡補齊。
        _elder_said_something = (
            bool(elder_response.strip())
            and elder_response.strip() != _NO_RESPONSE_MARKER
        )
        elder_section = (
            f"\n【長者剛才說的話】\n{elder_response}\n" if _elder_said_something else ""
        )
        pre_image_str = pre_image_detail or "無"
        # 理由同 _generate_open_followup 的同名判斷：長者這輪已有實質內容
        # 時完全不放【長者生圖前分享的內容】進prompt，避免跟畫面元素重複
        # 強化同一個舊主題、把模型拉走。
        _pre_image_needed = self._is_quick_end(elder_response)
        pre_image_section = (
            f"\n【長者生圖前分享的內容】\n{pre_image_str}\n" if _pre_image_needed else ""
        )

        remaining_senses = _relevant_uncovered_senses(
            topic_senses, covered_senses, skipped_senses,
        )
        covered_relevant_senses = _topic_relevant_covered_senses(topic_senses, covered_senses)
        sense_hint_str = _sense_entry_hint(remaining_senses, covered_relevant_senses)

        # 2026-09稽核：姓名欄位拿掉，理由同 _generate_question／_generate_closing。
        # 2026-09-12稽核：跟 _generate_open_followup 同一次精簡，
        # 理由跟改法都相同（見該函式上方說明）——這裡原本疊了同一套「規則
        # 清單＋動筆前自問」堆疊寫法（重複的自我檢查框架、重貼一次長者原話、
        # 獨立的「承接語跟問題要接同一件事」自問區塊），一併精簡合併，這支
        # 函式獨有的規則（拒答時的處理、不能自己編原因當既定事實、稱呼不能用
        # 「長者」第三人稱）全部保留。
        user_content = (
            f"【長者資料】\n"
            f"職業背景：{user['main_occupation']}\n"
            f"今日主題：{user['today_topic']}\n"
            f"興趣：{user.get('preferences') or '無'}\n"
            f"懷舊治療主題類別：{topic_str}\n"
            f"\n【眼前畫面元素】\n{elements_str}\n"
            f"{_composition_section(scene_composition)}"
            f"{pre_image_section}"
            f"\n【已涵蓋的W維度】\n{covered_str}\n"
            f"{elder_section}"
            f"\n【長者目前情緒】\n{_emotion_guidance(emotion)}\n"
            f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
            f"\n【任務】\n"
            f"生成一個問題，順著長者剛才的話跟眼前畫面自然深入問下去，不是在核對"
            f"清單，也不用先講一句話承接。問題要具體錨定在【長者剛才說的話】裡的"
            f"實際內容（提到誰、提到什麼事——換成任何長者、任何回應都問得出口就是"
            f"太空泛，要換成只對得上這句話才問得出來的內容），只有那句話是空的或"
            f"沒有可延伸內容時，才能改用畫面元素或今日主題當備用錨點。"
            f"{_W_HINT[target_w]}不能問【已涵蓋的W維度】清單裡列出的方向，長者"
            f"已經回答過了，等於白問。\n"
            + f"\n【幾條不能踩的線】\n"
            f"不要腦補、發明長者沒說過的具體情節，緊貼他這輪實際說的內容就好；"
            f"稱呼對方一律用「你」，絕對不能用「長者」這種第三人稱指稱對方（要寫"
            f"「你剛才提到...」，不是「長者剛才提到...」）——你是在直接跟他說話，"
            f"不是在寫案例紀錄。\n"
            f"問題裡的「我」只能用在表達AI自己當下的情緒反應，不能延伸出AI自己"
            f"的具體人生經歷、家人或童年往事（例如「我小時候」「我媽媽」），也"
            f"不能用「我們」把AI跟長者寫成一起經歷過同一件事——AI在這段對話裡"
            f"只是聆聽者，這些內容只屬於長者本人。\n"
            f"{sense_hint_str}"
            f"{_retry_feedback_section(retry_feedback)}"
            + f"\n【輸出格式】\n"
            f"輸出一個JSON物件，包含以下3個key：\n"
            f"thinking：第一步先把【長者剛才說的話】裡的關鍵字詞原文照抄一次——"
            f"一字不改，不要換成同義詞、相關詞，也不要用自己的話摘要或改寫，只要"
            f"那個欄位裡有任何文字內容，就要抄出裡面實際出現的具體詞語本身（「沒有"
            f"回應」只能用在【長者剛才說的話】欄位本身是空的時候，不能因為那句話"
            f"沒有正面答到上一題、或答非所問，就當作沒有回應）；接著判斷今日主題"
            f"最貼近哪個核心主題；最後決定這題要用什麼當錨點、往哪個方向問，優先"
            f"延續剛才原文照抄出來的那個詞語，只有那一步是空的才改用畫面元素或"
            f"今日主題——不會念給長者聽。\n"
            f"{_ANCHOR_FIELD_SPEC}\n"
            f"question（問題）：以25字為目標，不超過30字，開放式，開頭要有具體錨點，"
            f"畫面物件、長者提到的具體人事物、或「那個時候」回指情境皆可。"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        raw = await self.llm.chat(messages, format=_SUPPLEMENT_QUESTION_SCHEMA, temperature=temperature)
        return self._parse_question_response_json(raw, scene_elements=scene_elements)

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
        result: dict = {"scene_text": "", "question": "", "covered_w": [], "anchor": ""}
        thinking = ""
        # current_field 追蹤「目前正在填哪個欄位」，讓後續沒有標籤的行可以接到
        # 上一個標籤欄位——本地模型偶爾會把「問題：」單獨放一行、實際問題文字
        # 放在下一行，原本逐行比對「這行開頭是不是問題：」的寫法抓不到這種格式，
        # 會讓 result["question"] 停留空字串，觸發下面的「question 欄位是空的」
        # 保底邏輯，把整段原始輸出誤判成問題內容塞進去——2026-08 實測發現這是
        # 造成 too_long 重試的常見成因。
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _LEAKED_LIST_MARKER_RE.sub("", line)
            if line.startswith("思考："):
                # CoT 草稿行：question_5w1h.txt 的【思考欄位】規則要求模型先在這裡
                # 判斷主題方向、選錨點，再輸出正式內容。這行故意不進 result、不會
                # 被念給長者聽，只印出來方便觀察模型的選題邏輯、調整 prompt。
                thinking = line[len("思考："):].strip()
                current_field = "thinking"
            elif line.startswith("錨點："):
                result["anchor"] = line[len("錨點："):].strip()
                current_field = "anchor"
            elif line.startswith("承接語："):
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
                current_field = None  # 這欄不儲存，但要停止把後面的行接到問題
            elif current_field == "scene_text":
                result["scene_text"] = f"{result['scene_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
            elif current_field == "thinking":
                thinking = f"{thinking} {line}".strip()
            elif current_field == "anchor":
                result["anchor"] = f"{result['anchor']} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        result["scene_text"] = _strip_leaked_quotes(
            _strip_leaked_brackets(_strip_trailing_covered_w_leak(result["scene_text"]))
        )
        result["question"] = _strip_leaked_quotes(_strip_leaked_brackets(
            _strip_trailing_anchor_leak(_strip_trailing_covered_w_leak(result["question"]))
        ))
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            print(f"[Orchestrator] ⚠ 問題欄位清洗後是空的（本地模型把格式範本原封不動echo回來），"
                  f"退回{'含畫面元素的' if first_element else ''}保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if thinking:
            print(f"  → 思考: {thinking}")
        if not result["scene_text"]:
            elements_str = _natural_join(scene_elements or [])
            print(f"[Orchestrator] ⚠ 承接語（引導語）欄位是空的，退回"
                  f"{'含畫面元素的' if elements_str else ''}保底鋪陳語。原始輸出: {raw[:200]!r}")
            result["scene_text"] = (
                f"眼前的畫面裡有{elements_str}，我們換個方向聊聊吧。"
                if elements_str else _FALLBACK_TRANSITION_TEXT
            )
        return result

    def _parse_question_response_json(
        self, raw: str, scene_elements: list[str] | None = None,
    ) -> dict:
        """解析 STEP3補問改用結構化輸出（format=_SUPPLEMENT_QUESTION_SCHEMA）後的
        JSON回應——取代原本逐行掃描「思考：/承接語：/問題：...」標籤的
        _parse_question_response。JSON schema 保證這幾個 key 都會出現在輸出裡，
        不會再發生本地小模型漏寫其中一段標籤、解析後欄位是空字串的問題，但
        schema 不保證「內容」不是空字串，所以底下的空值保底邏輯還是保留，
        當最後一道防線。

        2026-09-14稽核：STEP3不再生成承接語，schema已經拿掉
        scene_text 這個key，這裡固定回傳空字串維持跟舊版parser一樣的dict
        形狀（下游一整串 result.get("scene_text", "") 呼叫點才不用跟著改），
        不代表還有在解析或驗證它。
        """
        # covered_w 這個key已從schema拿掉（見 _SUPPLEMENT_QUESTION_SCHEMA
        # 上方2026-09稽核說明：唯一的呼叫端 _ask_supplement 從不讀這個值），
        # 這裡固定回傳空list只是維持跟舊版parser一樣的dict形狀，不代表還
        # 有在解析它。
        result: dict = {"scene_text": "", "question": "", "covered_w": [], "anchor": ""}
        thinking = ""
        data = self._salvage_json_object(raw, ("thinking", "anchor", "question"))
        if isinstance(data, dict):
            thinking = str(data.get("thinking") or "").strip()
            result["anchor"] = normalize_anchor(str(data.get("anchor") or "").strip())
            result["question"] = str(data.get("question") or "").strip()
        result["question"] = _strip_leaked_quotes(_strip_leaked_brackets(
            _strip_trailing_anchor_leak(_strip_trailing_covered_w_leak(result["question"]))
        ))
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            print(f"[Orchestrator] ⚠ JSON問題欄位是空的（schema保證了key存在，"
                  f"但內容是空字串），退回{'含畫面元素的' if first_element else ''}"
                  f"保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        if thinking:
            print(f"  → 思考: {thinking}")
        return result

    def _parse_track_c_response(self, raw: str, scene_elements: list[str] | None = None) -> dict:
        """解析 Track C（思考 + 承接語 + 問題 + 錨點）的輸出，承接語存進
        result["scene_text"]。"""
        result: dict = {"scene_text": "", "question": "", "anchor": ""}
        thinking = ""
        current_field: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _LEAKED_LIST_MARKER_RE.sub("", line)
            if line.startswith("思考："):
                # 同 _parse_question_response：CoT草稿行，不會念給長者聽，只印出來
                # 方便觀察模型的選題邏輯。
                thinking = line[len("思考："):].strip()
                current_field = "thinking"
            elif line.startswith("承接語："):
                result["scene_text"] = line[len("承接語："):].strip()
                current_field = "scene_text"
            elif line.startswith("問題："):
                result["question"] = line[len("問題："):].strip()
                current_field = "question"
            elif line.startswith("錨點："):
                result["anchor"] = line[len("錨點："):].strip()
                current_field = "anchor"
            elif line.startswith("問題類型："):
                # 這欄不儲存，同 _parse_question_only_response／_parse_image_
                # reveal_response 的guard說明——這支函式的prompt也沒有要求
                # 輸出這個欄位，一樣要擋，不然會被上面「問題：」設下的
                # current_field接到問題文字後面。
                current_field = None
            elif current_field == "scene_text":
                result["scene_text"] = f"{result['scene_text']} {line}".strip()
            elif current_field == "question":
                result["question"] = f"{result['question']} {line}".strip()
            elif current_field == "anchor":
                result["anchor"] = f"{result['anchor']} {line}".strip()
            elif current_field == "thinking":
                thinking = f"{thinking} {line}".strip()
        if not result["question"]:
            result["question"] = raw.strip()
        result["scene_text"] = _strip_leaked_quotes(
            _strip_leaked_brackets(_strip_trailing_covered_w_leak(result["scene_text"]))
        )
        result["question"] = _strip_leaked_quotes(_strip_leaked_brackets(
            _strip_trailing_anchor_leak(_strip_trailing_covered_w_leak(result["question"]))
        ))
        if thinking:
            print(f"  → 思考: {thinking}")
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

    def _parse_track_c_response_json(
        self, raw: str, scene_elements: list[str] | None = None,
    ) -> dict:
        """解析 Track C 改用結構化輸出（format=_OPEN_FOLLOWUP_SCHEMA）後的JSON
        回應，取代逐行掃描的 _parse_track_c_response。

        2026-09-14稽核：STEP2不再生成承接語，schema已經拿掉
        scene_text 這個key，這裡固定回傳空字串維持跟舊版parser一樣的dict
        形狀（下游一整串 result.get("scene_text", "") 呼叫點才不用跟著改），
        不代表還有在解析或驗證它。
        """
        result: dict = {"scene_text": "", "question": "", "anchor": ""}
        thinking = ""
        data = self._salvage_json_object(raw, ("thinking", "anchor", "question"))
        if isinstance(data, dict):
            thinking = str(data.get("thinking") or "").strip()
            result["anchor"] = normalize_anchor(str(data.get("anchor") or "").strip())
            result["question"] = str(data.get("question") or "").strip()
        result["question"] = _strip_leaked_quotes(_strip_leaked_brackets(
            _strip_trailing_anchor_leak(_strip_trailing_covered_w_leak(result["question"]))
        ))
        if thinking:
            print(f"  → 思考: {thinking}")
        if not result["question"]:
            first_element = (scene_elements or [None])[0]
            print(f"[Orchestrator] ⚠ Track C JSON問題欄位是空的，退回"
                  f"{'含畫面元素的' if first_element else ''}保底問題。原始輸出: {raw[:200]!r}")
            result["question"] = f"{first_element}，讓你想到什麼？" if first_element else _FALLBACK_QUESTION
        return result

    def _extract_json(self, text: str) -> dict:
        """
        從 LLM 回應中萃取 JSON。

        解析失敗時印警告、回傳空dict，不往上炸例外——本地量化基底模型
        偶爾會在輸出中途被截斷（例如陣列生到一半就結束），連「找最後一個
        }」的救援都救不回來，若讓例外一路往上炸穿 process_response，整個
        療程回合會當場中斷、長者連下一句話都收不到。全檔呼叫點
        （_detect_covered_w／_detect_covered_senses／_has_usable_detail／
        _plan_image 等）全部是「偵測/核對」性質的輔助判斷，不是長者會
        直接聽到的生成內容，且都用 .get(key, 預設值) 讀取，判斷不出來時
        退回「這次什麼都沒偵測到」遠比讓整個回合當機更好。
        """
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end   = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            print(f"[Orchestrator] ⚠ LLM 沒有回有效的 JSON（可能是輸出中途被"
                  f"截斷），這次判斷視為「沒有偵測到」。原始輸出: {text[:200]!r}")
            return {}

    def _salvage_json_object(self, raw: str, expected_keys: tuple[str, ...]) -> dict:
        """
        給 _parse_closing_response_json／_parse_image_reveal_response_json／
        _parse_question_response_json／_parse_track_c_response_json 這四支
        「結構化輸出（JSON schema）」解析函式共用的救援層，取代它們原本各自
        的「json.loads失敗就整包當空dict」。

        2026-09-13稽核（manual_test_real_transcripts.py 用真實
        長者對話紀錄實測後補）：這四支函式的schema都要求輸出是一份完整
        JSON——但JSON語法要求整份文件合法，只要其中一個欄位（尤其排在
        schema第一個、內容不定長的「thinking」）生成中途被截斷，後面幾個
        欄位可能根本還沒開始生成，json.loads直接整包判讀失敗，連已經完整
        生成的欄位（例如scene_text／question其實都已經寫完，只是後面接的
        欄位還沒生完）也一起被當成空的、退回完全通用的保底句——實測案例：
        長者原話是STT辨識出來、同一個字重複100次以上的破碎文字，本地弱
        模型在thinking欄位陷入複誦/重複輸出迴圈，一路生到被截斷，這幾支
        函式全部回傳保底值。

        救援分三層，前面救不回來才試下一層：
          1. 直接 json.loads(raw)——正常情況。
          2. 抓第一個「{」到最後一個「}」之間的子字串重新解析——處理前後
             夾雜多餘文字（例如```包住）但中間本體其實合法的情況，跟
             _extract_json 是同一招。
          3. 前兩層都失敗，代表文件真的中途被截斷、大括號/引號沒有閉合，
             改成逐一用 regex 找「"key": "已經完整加上收尾引號的字串值"」
             這個模式，把每個確實已經完整生成的欄位個別救出來——只救「有
             收尾引號」的欄位，不去猜測還沒寫完、缺收尾引號的殘缺字串，
             避免把截斷到一半的亂碼硬塞給呼叫端；救不到的key自然不會出現
             在回傳的dict裡，呼叫端本來就有 .get(key) 或空字串保底邏輯
             接手，不用另外處理。

        expected_keys 只用來決定要嘗試搶救哪幾個欄位，不影響前兩層（前兩層
        本來就是解析整份JSON，找到什麼欄位算什麼）。回傳的 dict 不保證含有
        expected_keys 裡的每一個key，呼叫端要維持用 .get() 讀取。
        """
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, TypeError):
                pass
        salvaged: dict = {}
        for key in expected_keys:
            m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
            if m:
                value = m.group(1).replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
                salvaged[key] = value
        if salvaged:
            print(
                f"[Orchestrator] ⚠ JSON整份解析失敗（可能是輸出中途被截斷），"
                f"改用逐欄位搶救，救回：{list(salvaged.keys())}。原始輸出: {raw[:200]!r}"
            )
        return salvaged