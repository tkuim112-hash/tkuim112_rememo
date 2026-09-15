"""
預錄音檔對照表——固定字串模板（非LLM即時生成）對應的音檔 key。

見 docs/TTS預生成句庫清單.md（來源清單，2026-08-15整理）／docs/心得分享.md
（心得環節，2026-08-17整理）。

2026-08-18稽核：closing_templates.py 原本的註解說心得環節「前端會播放
預錄音檔」，一度誤以為 sharing_* 那批已經接好、不用再處理。實際查過 Unity
專案（GameController.cs／ShareController.cs）才發現前端目前完全沒有「依
key 播放本地音檔」的機制，全部語音都是即時向後端下載播放，心得環節甚至
完全沒有播音檔（ShareController 只有逐字動畫）——sharing_* 那句註解描述的
是規劃、不是已完成的現況。使用者確認：sharing_* 併入本檔案，跟其餘固定句
用同一套機制接，不分開做兩套。

架構決定（2026-08-18，使用者確認）：跟 sharing_* 那批同一套模式——這裡的
key 只是給前端（Unity）拿去對應內建預錄音檔播放用，後端不讀取、不回傳
tts_cache 裡的 wav 本身，也不再為這些固定字串呼叫 TTS 即時合成。

兩種查表方式並存，取決於文字有沒有跨主題重複：
  - lookup_audio_key(text)：純文字比對，給「完全固定句」「Q2情境1範例句」
    「心得環節」用——這幾組逐一核對過，同一組內文字彼此不重複。
  - five_w1h_key(topic, dimension, ...)：結構化查表，_FIVE_W1H_BANK 專用。
    這個題庫「同一句話在不同主題下重複出現」的情況很常見（例如「那是在
    哪裡呢？」童年經歷／家庭／奮鬥經歷都有，「這件事對你來說，特別在哪裡？」
    金錢掌控權／座右銘／兒女成就三個子項目共用），寫成扁平文字表會被
    Python dict 字面量的重複key覆蓋掉、悄悄少收錄好幾個key（第一版就是這樣
    出的錯，用腳本核對才抓到：131句字面量裡有14句重複，實際只收到117個
    key）。改用「主題/子項目/W維度/第幾個variant」當查表依據，跟
    orchestrator.py _FIVE_W1H_BANK 選字串variants[i]用同一個index，才能保證
    查到的key精準對應長者實際聽到的那一句，不會因為兩個不相干主題剛好用了
    同一句中文問法就查錯。這邊的取捨是backend需要在選定variant的地方額外
    回傳(topic, dimension, variant_index)，比純文字比對多一點wiring，但換來
    的是不會有「音檔跟文字對不上」的風險。

不用管的部分：五感通用保底問句（_SENSE_QUESTION）、W維度通用保底問句
  （_W_FALLBACK_QUESTION）、_generate_image_reveal_reaction／_generate_
  quick_end_recap 共用的出示圖片承接語保底句，這三處全部屬於 STEP1/STEP2/
  STEP3（回合2「自由追問」才會生成，見 orchestrator.py「STEP1問題延後到
  round 2 開場才生成」說明）。2026-08-18查證：app/routers/session.py 的
  TTS/audio_key 呼叫點本來就用 round_number not in (2, 3)／body.state.round
  != 2 把回合2、3整段擋掉（回合3固定只有一輪問答、答完必定進
  action=="end_session"，同樣被擋），這三處的文字連 _synthesize_or_key 都
  不會被呼叫到，不需要在這裡另外處理（曾經加過 is_silent_fallback_text
  這層判斷，後來確認是死碼，已移除）。fixed_image_reveal_reaction_
  fallback.wav（錄的是「謝謝你告訴我這些。」）因此也用不到，tts_cache 裡
  留著沒關係，Unity StreamingAssets 已經沒有這個檔案。
"""

# ── 16大主題分類・英文 key（對應 orchestrator.py _TOPIC_CATEGORIES）──────
TOPIC_KEYS = {
    "童年經歷": "childhood",
    "讀書求學": "school",
    "家庭": "family",
    "感情": "romance",
    "工作": "work",
    "奮鬥經歷": "struggle",
    "軍旅": "military",
    "興趣": "hobby",
    "專長": "skill",
    "印象最深刻的地方": "place",
    "休閒": "leisure",
    "節慶": "festival",
    "哀傷之事": "grief",
    "人生目標": "goal",
    "自我成就感": "achievement",
    "生命中特殊的事件": "special_event",
}

# ── 完全固定句（對應 orchestrator.py 模組層常數／guarded_generate fallback）
# text 已逐一核對過 orchestrator.py 目前的實際字串（不是照抄清單），見檔案
# 開頭「已知不對應」說明——只有這 10 句是目前程式碼真的還在用的。
FIXED_TEXT_KEYS = {
    "很高興今天能坐下來陪你聊聊天。": "fixed_pre_image_q1_intro",
    "我把你剛剛說的故事畫成一張圖了，想給你看看。": "fixed_image_reveal_scene_text",
    "你看看這張圖，想到什麼都可以跟我說。": "fixed_image_reveal_question",
    "謝謝你跟我說這麼多，讓我更了解你記得的畫面是什麼樣子了。": "fixed_image_reveal_transition",
    "沒關係，那我們來聊聊，": "fixed_image_reveal_quick_end_ack",
    "還有什麼想說的呢？": "fixed_fallback_question",
    "我們接著聊聊這個吧。": "fixed_fallback_transition_text",
    "謝謝你今天的分享，辛苦了。": "fixed_fallback_closing_text",
    "謝謝你願意說這些，我在這裡陪著你。": "fixed_emotional_response_fallback",
    "現在心裡在想些什麼呢？": "fixed_closing_fallback_question",
}

# ── 16大主題分類・生圖前 Q2・情境1範例句（對應 _PRE_IMAGE_Q2_SCENARIO1_TEMPLATES）
# 完全固定、無變數，可直接文字比對；16句彼此互不重複（已核對）。
Q2_SCENARIO1_TEXT_KEYS = {
    "像是玩耍的時候，或者跟家人在一起的時候，你會想到什麼呢？": "q2_s1_childhood",
    "像是上課的時候，或者跟同學相處的時候，你會想到什麼呢？": "q2_s1_school",
    "像是跟孩子相處，或者一家人團聚的時候，你會想到什麼呢？": "q2_s1_family",
    "像是剛認識的時候，或者籌備婚禮的時候，你會想到什麼呢？": "q2_s1_romance",
    "像是剛開始工作，或者做得最上手的時候，你會想到什麼呢？": "q2_s1_work",
    "像是最難熬的時候，或者後來撐過去的時候，你會想到什麼呢？": "q2_s1_struggle",
    "像是受訓的時候，或者跟同袍相處的時候，你會想到什麼呢？不方便說也沒關係。": "q2_s1_military",
    "像是自己一個人的時候，或者跟人一起的時候，你會想到什麼呢？": "q2_s1_hobby",
    "像是剛學會的時候，或者做得最順手的時候，你會想到什麼呢？": "q2_s1_skill",
    "像是那裡的景色，或者在那裡發生的事，你會想到什麼呢？": "q2_s1_place",
    "像是跟朋友一起，或者自己一個人的時候，你會想到什麼呢？": "q2_s1_leisure",
    "像是準備過節的時候，或者過節當天，你會想到什麼呢？": "q2_s1_festival",
    "像是他平常的樣子，或者你們相處的時候，你會想到什麼呢？": "q2_s1_grief",
    "像是達成之後的生活，或者身邊的人，你會想到什麼呢？": "q2_s1_goal",
    "像是完成的那一刻，或者旁人的反應，你會想到什麼呢？": "q2_s1_achievement",
    "像是當時的場景，或者身邊的人，你會想到什麼呢？": "q2_s1_special_event",
}

# 五感通用保底問句（_SENSE_QUESTION）／W維度通用保底問句（_W_FALLBACK_
# QUESTION）：2026-08-18使用者確認這兩組（跟 _generate_image_reveal_
# reaction 的保底句一樣）不需要預錄音檔，維持即時TTS，故意不收錄進
# TEXT_TO_KEY——tts_cache/ 裡對應的 sense_*.wav／w_fallback_*.wav 10個檔案
# 不會被用到，Unity StreamingAssets 那邊也沒放這幾個檔案。

# ── 心得環節（對應 app/services/closing_templates.py）───────────────────
# 逐一核對 closing_templates.py 目前的實際內容（不是照抄 docs/心得分享.md）。
# 2026-09-08：心得環節拿掉了「承接語」那段（見 closing_templates.py
# build_closing_invitation 說明），原本對應 CLOSING_RECEIVING_PHRASES／
# SYSTEM_COMPLAINT_RECEIVING_PHRASES／HARDSHIP_CORE_VARIANTS／
# WARM_CORE_VARIANTS 的 sharing_ack_*／sharing_complaint_*／sharing_affirm_*
# 這批 key 已經沒有程式碼在查表，一併移除；對應的 .wav 還留在 Unity
# StreamingAssets/Audio，之後確定不會再用到可以整批刪掉。
SHARING_TEXT_KEYS = {
    # build_closing_invitation 固定問句
    "回想整場聊下來，你有什麼想跟我分享的呢？": "sharing_opening",
    # CLOSING_TAIL_VARIANTS
    "謝謝你今天願意跟我分享這麼多，希望這些美好的時光，能常常陪著你、讓你覺得溫暖。": "sharing_closing_1",
    "謝謝你今天陪我聊了這麼多，希望這份溫暖能一直留在你心裡。": "sharing_closing_2",
    "很謝謝你把這些故事說給我聽，希望你隨時想起來，都能感覺到溫暖。": "sharing_closing_3",
}

# 供 lookup_audio_key() 用的合併表。只收「組內文字互不重複」的三組——
# _FIVE_W1H_BANK 不在這裡，見上方模組說明／five_w1h_key()；五感/W維度保底
# 故意不收錄，見上方說明。
TEXT_TO_KEY: dict[str, str] = {
    **FIXED_TEXT_KEYS,
    **Q2_SCENARIO1_TEXT_KEYS,
    **SHARING_TEXT_KEYS,
}


def lookup_audio_key(text: str) -> str | None:
    """
    純文字比對——text 若跟已知固定句其中一句完全相同（去頭尾空白後），回傳
    對應的預錄音檔 key；查無則回傳 None，呼叫端應照舊走即時 TTS。只涵蓋
    TEXT_TO_KEY 那四組，_FIVE_W1H_BANK 的題庫請用 five_w1h_key()。

    不處理 Q1 邀請語（見 q1_invitation_key）——那句永遠帶著治療師自由輸入的
    「說到{today_topic}，」動態前綴，整句沒辦法完全比對。
    """
    return TEXT_TO_KEY.get((text or "").strip())


def q1_invitation_key(topic_category: str | None) -> str | None:
    """
    生圖前 Q1 邀請語（_PRE_IMAGE_Q1_INVITATIONS）的音檔 key，直接用分類結果
    （topic_category，orchestrator.py 已經分類好、存在 state 裡）查，不用
    文字比對——完整句是「說到{today_topic}，」+ 這句，動態前綴那段仍需要
    即時TTS，音檔只接後半段（跟 docs/TTS預生成句庫清單.md 第2節建議做法
    一致）。分類失敗（topic_category 是 None）沒有對應邀請語可用，回傳
    None，呼叫端走 _PRE_IMAGE_Q1_FALLBACK_QUESTION 整句即時TTS。
    """
    key = TOPIC_KEYS.get(topic_category or "")
    return f"q1_inv_{key}" if key else None


# ── _FIVE_W1H_BANK 題庫音檔對照（結構化查表，見檔案開頭說明）─────────────
# 結構跟 orchestrator.py _FIVE_W1H_BANK 對齊（granularity/sub_items/fields
# 同一組key名），value統一是 variants 陣列，順序對應 _FIVE_W1H_BANK 該欄位
# "variants" 清單的順序——orchestrator.py 用 variants[i] 選字串，這裡用同一
# 個 i 選key，才能保證文字跟音檔是同一句。已逐一核對 orchestrator.py
# 439-724行的實際內容（不是照抄清單）。
#
# 「哀傷之事→親人死亡→Why」只收第一個variant（w_grief_death_why1）：第二個
# variant含{person}稱謂詞佔位符，w_grief_death_person_why.wav是用保底詞錄的
# 單一版本，不確定跟長者實際抓到的稱謂詞（25種候選之一）是否一致，交給
# orchestrator.py照舊即時TTS，不冒音檔跟文字對不上的風險（見
# orchestrator.py _extract_named_person）。
FIVE_W1H_AUDIO_KEYS = {
    "童年經歷": {
        "granularity": "sub_item",
        "sub_items": {
            "威權教育": {
                "Where": ["w_childhood_authority_where"],
                "When": ["w_childhood_authority_when"],
                "How": ["w_childhood_authority_how1", "w_childhood_authority_how2"],
                "Why": ["w_childhood_authority_why1", "w_childhood_authority_why2"],
            },
            "物質生活佳": {
                "When": ["w_childhood_wealth_when"],
                "How": ["w_childhood_wealth_how"],
                "Why": ["w_childhood_wealth_why"],
            },
            "升學": {
                "Where": ["w_childhood_study_where"],
                "When": ["w_childhood_study_when"],
                "How": ["w_childhood_study_how"],
                "Why": ["w_childhood_study_why"],
            },
            "童玩經驗": {
                "Where": ["w_childhood_play_where"],
                "When": ["w_childhood_play_when"],
                "How": ["w_childhood_play_how"],
                "Why": ["w_childhood_play_why"],
            },
        },
    },
    "讀書求學": {
        "granularity": "theme",
        "fields": {
            "Where": ["w_school_where1", "w_school_where2"],
            "When": ["w_school_when1", "w_school_when2"],
            "How": ["w_school_how1", "w_school_how2"],
            "Why": ["w_school_why1", "w_school_why2"],
        },
    },
    "家庭": {
        "granularity": "sub_item",
        "sub_items": {
            "養兒育女": {
                "When": ["w_family_children_when"],
                "How": ["w_family_children_how"],
                "Why": ["w_family_children_why1", "w_family_children_why2"],
            },
            "晚輩孝順": {
                "When": ["w_family_filial_when"],
                "How": ["w_family_filial_how"],
                "Why": ["w_family_filial_why1", "w_family_filial_why2"],
            },
            "家庭衝突": {
                "Why": ["w_family_conflict_why"],
            },
            "家庭組成": {
                "Where": ["w_family_composition_where"],
                "When": ["w_family_composition_when"],
                "How": ["w_family_composition_how"],
                "Why": ["w_family_composition_why"],
            },
        },
    },
    "感情": {
        "granularity": "theme",
        "fields": {
            "Where": ["w_romance_where1", "w_romance_where2"],
            "When": ["w_romance_when1", "w_romance_when2"],
            "How": ["w_romance_how1", "w_romance_how2"],
            "Why": ["w_romance_why1", "w_romance_why2"],
        },
    },
    "工作": {
        "granularity": "theme",
        "fields": {
            "Where": ["w_work_where1", "w_work_where2"],
            "When": ["w_work_when1", "w_work_when2"],
            "How": ["w_work_how1", "w_work_how2"],
            "Why": ["w_work_why1", "w_work_why2"],
        },
    },
    "奮鬥經歷": {
        "granularity": "theme",
        "fields": {
            "Where": ["w_struggle_where1", "w_struggle_where2"],
            "When": ["w_struggle_when1", "w_struggle_when2"],
            "How": ["w_struggle_how1", "w_struggle_how2"],
            "Why": ["w_struggle_why1", "w_struggle_why2"],
        },
    },
    "軍旅": {
        "granularity": "sub_item",
        "sub_items": {
            "戰爭經驗": {
                "Where": ["w_military_war_where"],
                "When": ["w_military_war_when"],
                "Why": ["w_military_war_why"],
            },
            "隨政府遷台": {
                "Where": ["w_military_migration_where"],
                "When": ["w_military_migration_when"],
                "Why": ["w_military_migration_why"],
            },
            "當軍人過程": {
                "Where": ["w_military_soldier_where"],
                "When": ["w_military_soldier_when"],
                "Why": ["w_military_soldier_why"],
            },
            "軍事教育": {
                "Where": ["w_military_training_where"],
                "When": ["w_military_training_when"],
                "Why": ["w_military_training_why"],
            },
        },
    },
    "興趣": {
        "granularity": "theme",
        # orchestrator.py _FIVE_W1H_BANK 的「興趣」已經把整個 Why 欄位
        # 刪掉（跟Q1邀請語重複，見該檔案說明），這裡同步拿掉 Why，不留
        # 永遠查不到的音檔 key。
        "fields": {
            "Where": ["w_hobby_where1", "w_hobby_where2"],
            "When": ["w_hobby_when1", "w_hobby_when2"],
            "How": ["w_hobby_how1", "w_hobby_how2"],
        },
    },
    "專長": {
        "granularity": "theme",
        # orchestrator.py 已把「專長」的 How 欄位整個刪掉（跟Q1邀請語
        # 重複），這裡同步拿掉。
        "fields": {
            "Where": ["w_skill_where1", "w_skill_where2"],
            "When": ["w_skill_when1", "w_skill_when2"],
            "Why": ["w_skill_why1", "w_skill_why2"],
        },
    },
    "印象最深刻的地方": {
        "granularity": "theme",
        "fields": {
            "Where": ["w_place_where1", "w_place_where2"],
            "When": ["w_place_when1", "w_place_when2"],
            "Why": ["w_place_why1", "w_place_why2"],
        },
    },
    "休閒": {
        "granularity": "theme",
        # orchestrator.py 已把「休閒」的 Why 欄位整個刪掉（跟Q1邀請語
        # 重複），這裡同步拿掉。
        "fields": {
            "Where": ["w_leisure_where1", "w_leisure_where2"],
            "When": ["w_leisure_when1", "w_leisure_when2"],
            "How": ["w_leisure_how1", "w_leisure_how2"],
        },
    },
    "節慶": {
        "granularity": "theme",
        # orchestrator.py 已把「節慶」的 How 欄位整個刪掉（跟Q1邀請語
        # 重複），這裡同步拿掉。
        "fields": {
            "Where": ["w_festival_where1", "w_festival_where2"],
            "When": ["w_festival_when1", "w_festival_when2"],
            "Why": ["w_festival_why1", "w_festival_why2"],
        },
    },
    "哀傷之事": {
        "granularity": "sub_item",
        "sub_items": {
            "親人死亡": {"Why": ["w_grief_death_why1"]},
            "擾人疾病": {"Why": ["w_grief_illness_why"]},
            "人生缺憾": {"Why": ["w_grief_regret_why"]},
            "寂寞沒人陪": {"Why": ["w_grief_lonely_why"]},
        },
    },
    "人生目標": {
        "granularity": "sub_item",
        "sub_items": {
            "心中願望": {
                "Where": ["w_goal_wish_where"],
                "Why": ["w_goal_wish_why"],
            },
            "金錢掌控權": {"Why": ["w_goal_money_why"]},
            "座右銘": {"Why": ["w_goal_motto_why"]},
            "兒女成就": {"Why": ["w_goal_children_why"]},
        },
    },
    "自我成就感": {
        "granularity": "theme",
        # orchestrator.py 已把「自我成就感」的 Why 欄位整個刪掉（跟Q1
        # 邀請語重複），這裡同步拿掉。
        "fields": {
            "Where": ["w_achievement_where1", "w_achievement_where2"],
            "When": ["w_achievement_when1", "w_achievement_when2"],
            "How": ["w_achievement_how1", "w_achievement_how2"],
        },
    },
    "生命中特殊的事件": {
        "granularity": "theme",
        # orchestrator.py 已把「生命中特殊的事件」的 Why 欄位也整個刪掉
        # （跟Q1邀請語重複，How原本就已排除），這裡同步拿掉 Why，只留
        # Where/When。
        "fields": {
            "Where": ["w_special_where1", "w_special_where2"],
            "When": ["w_special_when1", "w_special_when2"],
        },
    },
}


def five_w1h_key(topic: str, dimension: str, variant_index: int = 0, sub_item: str | None = None) -> str | None:
    """
    查 _FIVE_W1H_BANK 對應的音檔 key。granularity=="sub_item" 的主題一定要帶
    sub_item（跟 orchestrator.py 呼叫 _FIVE_W1H_BANK 時的邏輯一樣，先分類
    子項目才查得到欄位）。variant_index 對應 _FIVE_W1H_BANK 該欄位
    "variants" 清單的索引，呼叫端選了 variants[i] 當文字，這裡就要傳同一個
    i，才能保證查到的key是同一句。查無對應（欄位被排除、index 超出範圍、
    或「哀傷之事→親人死亡→Why」選到 index 1 那個含 {person} 的variant，見
    上方模組說明）一律回傳 None，呼叫端應 fallback 回即時 TTS。
    """
    entry = FIVE_W1H_AUDIO_KEYS.get(topic)
    if not entry:
        return None
    if entry["granularity"] == "sub_item":
        if sub_item is None:
            return None
        fields = entry["sub_items"].get(sub_item)
    else:
        fields = entry["fields"]
    if not fields:
        return None
    variants = fields.get(dimension)
    if not variants or variant_index >= len(variants):
        return None
    return variants[variant_index]
