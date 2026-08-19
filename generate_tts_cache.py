"""
TTS 預生成腳本
把句庫清單裡所有固定句子一次生成成 WAV 音檔，存到 tts_cache/ 資料夾。
使用方式：python generate_tts_cache.py
"""
import subprocess
import os
import time

# 輸出資料夾（在容器內）
CONTAINER_OUTPUT = "/workspace/tts_cache"
# 本機輸出資料夾
LOCAL_OUTPUT = "tts_cache"

# 所有需要預生成的句子，格式：(檔名, 句子)
sentences = [
    # 1. 完全固定句
    ("fixed_pre_image_q1_intro", "很高興今天能坐下來陪你聊聊天。"),
    ("fixed_image_reveal_scene_text", "我把你剛剛說的故事畫成一張圖了，想給你看看。"),
    ("fixed_image_reveal_question", "你看看這張圖，想到什麼都可以跟我說。"),
    ("fixed_image_reveal_transition", "謝謝你跟我說這麼多，讓我更了解你記得的畫面是什麼樣子了。"),
    ("fixed_image_reveal_quick_end_ack", "沒關係，那我們來聊聊，"),
    ("fixed_fallback_question", "還有什麼想說的呢？"),
    ("fixed_fallback_transition_text", "我們接著聊聊這個吧。"),
    ("fixed_fallback_closing_text", "謝謝你今天的分享，辛苦了。"),
    ("fixed_emotional_response_fallback", "謝謝你願意說這些，我在這裡陪著你。"),
    ("fixed_image_reveal_reaction_fallback", "謝謝你告訴我這些。"),
    ("fixed_closing_fallback_question", "現在心裡在想些什麼呢？"),

    # 2. 16大主題邀請語
    ("q1_inv_childhood", "我一直很好奇，你小時候的生活是什麼樣子呢？"),
    ("q1_inv_school", "我很想知道，你以前上學的日子是什麼樣子呢？"),
    ("q1_inv_family", "我很想知道，你家裡的故事、跟家人相處的日子是什麼樣子呢？"),
    ("q1_inv_romance", "我很好奇，你當年是怎麼認識另一半的呢？"),
    ("q1_inv_work", "我很想知道，你以前工作的日子是什麼樣子呢？"),
    ("q1_inv_struggle", "這一生有沒有什麼特別不容易、但你撐過來的事呢？"),
    ("q1_inv_military", "如果你願意，我很想聽你說說看，當兵從軍那段日子是什麼樣子呢？不方便說的部分可以跳過。"),
    ("q1_inv_hobby", "我很好奇，這件事最讓你開心的是哪個部分呢？"),
    ("q1_inv_skill", "我很好奇，你是怎麼練出這個本事的呢？"),
    ("q1_inv_place", "這一生有沒有哪個地方，讓你特別難忘呢？"),
    ("q1_inv_leisure", "你以前從事這些休閒活動的時候，最喜歡哪個部分呢？"),
    ("q1_inv_festival", "我很好奇，你以前都是怎麼過節的呢？"),
    ("q1_inv_grief", "如果你願意，我很想聽你說說看，他平常的樣子，或者你們相處的時候，是什麼樣子呢？"),
    ("q1_inv_goal", "這一生有沒有什麼特別想完成的心願呢？"),
    ("q1_inv_achievement", "這一生最讓自己驕傲的一件事，是什麼呢？"),
    ("q1_inv_special_event", "有沒有什麼特別難忘、印象深刻的回憶呢？"),

    # 3. Q2 情境1 範例句
    ("q2_s1_childhood", "像是玩耍的時候，或者跟家人在一起的時候，你會想到什麼呢？"),
    ("q2_s1_school", "像是上課的時候，或者跟同學相處的時候，你會想到什麼呢？"),
    ("q2_s1_family", "像是跟孩子相處，或者一家人團聚的時候，你會想到什麼呢？"),
    ("q2_s1_romance", "像是剛認識的時候，或者籌備婚禮的時候，你會想到什麼呢？"),
    ("q2_s1_work", "像是剛開始工作，或者做得最上手的時候，你會想到什麼呢？"),
    ("q2_s1_struggle", "像是最難熬的時候，或者後來撐過去的時候，你會想到什麼呢？"),
    ("q2_s1_military", "像是受訓的時候，或者跟同袍相處的時候，你會想到什麼呢？不方便說也沒關係。"),
    ("q2_s1_hobby", "像是自己一個人的時候，或者跟人一起的時候，你會想到什麼呢？"),
    ("q2_s1_skill", "像是剛學會的時候，或者做得最順手的時候，你會想到什麼呢？"),
    ("q2_s1_place", "像是那裡的景色，或者在那裡發生的事，你會想到什麼呢？"),
    ("q2_s1_leisure", "像是跟朋友一起，或者自己一個人的時候，你會想到什麼呢？"),
    ("q2_s1_festival", "像是準備過節的時候，或者過節當天，你會想到什麼呢？"),
    ("q2_s1_grief", "像是他平常的樣子，或者你們相處的時候，你會想到什麼呢？"),
    ("q2_s1_goal", "像是達成之後的生活，或者身邊的人，你會想到什麼呢？"),
    ("q2_s1_achievement", "像是完成的那一刻，或者旁人的反應，你會想到什麼呢？"),
    ("q2_s1_special_event", "像是當時的場景，或者身邊的人，你會想到什麼呢？"),

    # 4. 5W1H 題庫
    # 童年 - 威權教育
    ("w_childhood_authority_where", "那是在家裡的哪裡呢？"),
    ("w_childhood_authority_when", "那是白天，還是吃過晚飯後的時間呢？"),
    ("w_childhood_authority_how1", "當時的規矩是什麼樣子呢？"),
    ("w_childhood_authority_how2", "那時候，通常是怎麼管教的呢？"),
    ("w_childhood_authority_why1", "這段記憶裡，你最想記住的是哪一部分？"),
    ("w_childhood_authority_why2", "說起這段，最讓你印象深的是什麼？"),
    # 童年 - 物質生活佳
    ("w_childhood_wealth_when", "那是過年才有的，還是平常也有呢？"),
    ("w_childhood_wealth_how", "那時候，家裡的生活是什麼樣子呢？"),
    ("w_childhood_wealth_why", "這段生活裡，你最想記住的是哪一部分？"),
    # 童年 - 升學
    ("w_childhood_study_where", "那是在哪裡呢？"),
    ("w_childhood_study_when", "那是開學的時候，還是考試前後呢？"),
    ("w_childhood_study_how", "那時候，家裡對你唸書的看法是什麼樣子呢？"),
    ("w_childhood_study_why", "這件事裡，你最想記住的是哪一部分？"),
    # 童年 - 童玩
    ("w_childhood_play_where", "那是在什麼地方玩呢？"),
    ("w_childhood_play_when", "那個時候，是放學後，還是假日的時候呢？"),
    ("w_childhood_play_how", "當時是怎麼玩起來的呢？"),
    ("w_childhood_play_why", "這段回憶裡，你最想記住的是哪一個畫面？"),
    # 讀書求學
    ("w_school_where1", "那是在哪個學校呢？"),
    ("w_school_where2", "那個時候，是在哪裡上學呢？"),
    ("w_school_when1", "那是白天上課，還是晚自習的時候呢？"),
    ("w_school_when2", "那是開學不久，還是快放假的時候呢？"),
    ("w_school_how1", "當時是怎麼上課或相處的呢？"),
    ("w_school_how2", "那時候，你們都是怎麼相處的呢？"),
    ("w_school_why1", "這段經驗裡，你最想記住的是哪一部分？"),
    ("w_school_why2", "說起這段求學的日子，最讓你難忘的是什麼？"),
    # 家庭 - 養兒育女
    ("w_family_children_when", "那是白天忙家務的時候，還是晚上哄孩子睡覺的時候呢？"),
    ("w_family_children_how", "那時候，你都是怎麼照顧孩子的呢？"),
    ("w_family_children_why1", "這段日子裡，你最想記住的是哪一部分？"),
    ("w_family_children_why2", "養孩子的過程，最讓你難忘的是什麼？"),
    # 家庭 - 晚輩孝順
    ("w_family_filial_when", "那是過節團聚的時候，還是平常的日子呢？"),
    ("w_family_filial_how", "那時候，是怎麼樣的情形呢？"),
    ("w_family_filial_why1", "這段回憶裡，你最想記住的是哪一部分？"),
    ("w_family_filial_why2", "說起這件事，最讓你感動的是什麼？"),
    # 家庭 - 家庭衝突
    ("w_family_conflict_why", "現在想起來，有沒有什麼和好的片刻，讓你印象深刻呢？"),
    # 家庭 - 家庭組成
    ("w_family_composition_where", "那是在什麼地方呢？"),
    ("w_family_composition_when", "那是白天，還是一家人晚上聚在一起的時候呢？"),
    ("w_family_composition_how", "那時候，是怎麼樣的情形呢？"),
    ("w_family_composition_why", "這段回憶裡，你最想記住的是哪一部分？"),
    # 感情
    ("w_romance_where1", "那是在哪裡呢？"),
    ("w_romance_where2", "那個時候，你們常去的地方是哪裡呢？"),
    ("w_romance_when1", "那是白天約會，還是晚上見面的時候呢？"),
    ("w_romance_when2", "那是什麼季節的事呢？"),
    ("w_romance_how1", "當時的心情是什麼樣子呢？"),
    ("w_romance_how2", "那時候，你們是怎麼相處的呢？"),
    ("w_romance_why1", "這段感情裡，你最想記住的是哪一個畫面？"),
    ("w_romance_why2", "說起這段，最讓你難忘的是什麼？"),
    # 工作
    ("w_work_where1", "那是在哪裡呢？"),
    ("w_work_where2", "那個時候，工作的地方是什麼樣子呢？"),
    ("w_work_when1", "那是白天上班的時候，還是加班到很晚呢？"),
    ("w_work_when2", "那是忙季，還是比較清閒的時候呢？"),
    ("w_work_how1", "當時是怎麼做到的呢？"),
    ("w_work_how2", "那時候，你都是怎麼處理的呢？"),
    ("w_work_why1", "這件事裡，你最想記住的是哪一部分？"),
    ("w_work_why2", "說起這段工作經驗，最讓你難忘的是什麼？"),
    # 奮鬥經歷
    ("w_struggle_where1", "那是在什麼地方呢？"),
    ("w_struggle_where2", "那個時候，是在哪裡發生的呢？"),
    ("w_struggle_when1", "那是什麼時候的事呢？"),
    ("w_struggle_when2", "那是白天，還是晚上發生的呢？"),
    ("w_struggle_how1", "後來是怎麼撐過去的呢？"),
    ("w_struggle_how2", "那時候，你是怎麼熬過來的呢？"),
    ("w_struggle_why1", "這段經歷裡，你最想記住的是哪一個片刻？"),
    ("w_struggle_why2", "這段日子，最讓你放不下的是什麼？"),
    # 軍旅 - 戰爭
    ("w_military_war_where", "那個時候，是在哪裡呢？"),
    ("w_military_war_when", "那是白天，還是晚上發生的呢？"),
    ("w_military_war_why", "如果你願意，這段記憶對你來說是什麼樣的感覺？"),
    # 軍旅 - 遷台
    ("w_military_migration_where", "那個時候，是在哪裡發生的呢？"),
    ("w_military_migration_when", "那是白天，還是晚上出發的呢？"),
    ("w_military_migration_why", "如果你願意，這段經歷對你來說是什麼樣的感覺？"),
    # 軍旅 - 當軍人
    ("w_military_soldier_where", "那個時候，是在哪裡呢？"),
    ("w_military_soldier_when", "那是白天出操，還是晚上站哨的時候呢？"),
    ("w_military_soldier_why", "這段當兵的日子，如果你想聊，特別在哪裡呢？"),
    # 軍旅 - 軍事教育
    ("w_military_training_where", "那個時候，是在哪裡受訓的呢？"),
    ("w_military_training_when", "那是白天訓練，還是晚上的時候呢？"),
    ("w_military_training_why", "這段訓練的日子，如果你想聊，特別在哪裡呢？"),
    # 興趣
    ("w_hobby_where1", "那通常是在哪裡呢？"),
    ("w_hobby_where2", "那個時候，都在哪裡進行呢？"),
    ("w_hobby_when1", "那通常是什麼時候呢？"),
    ("w_hobby_when2", "那個時候，通常是什麼時間做這件事呢？"),
    ("w_hobby_how1", "通常是怎麼進行的呢？"),
    ("w_hobby_how2", "那時候，你都是怎麼做的呢？"),
    ("w_hobby_why1", "這件事裡，最讓你放不下的是哪一部分？"),
    ("w_hobby_why2", "說起這個興趣，最讓你著迷的是什麼？"),
    # 專長
    ("w_skill_where1", "那是在哪裡的事呢？"),
    ("w_skill_where2", "那個時候，是在哪裡學的呢？"),
    ("w_skill_when1", "那讓你想到是什麼時候的事？"),
    ("w_skill_when2", "那通常是白天，還是晚上練習的呢？"),
    ("w_skill_how1", "是怎麼學會、怎麼做到的呢？"),
    ("w_skill_how2", "那時候，你是怎麼練成的呢？"),
    ("w_skill_why1", "這項本事裡，你最想記住的是哪一部分？"),
    ("w_skill_why2", "說起這項本領，最讓你驕傲的是什麼？"),
    # 印象最深刻的地方
    ("w_place_where1", "那確切是哪裡呢？"),
    ("w_place_where2", "那個時候，那個地方是什麼樣子呢？"),
    ("w_place_when1", "大概是什麼時候去的呢？"),
    ("w_place_when2", "那個時候，是什麼季節或時期呢？"),
    ("w_place_why1", "這個地方裡，你最想記住的是哪一個畫面？"),
    ("w_place_why2", "說起這個地方，最讓你難忘的是什麼？"),
    # 休閒
    ("w_leisure_where1", "那是在哪裡呢？"),
    ("w_leisure_where2", "那個時候，都去哪裡呢？"),
    ("w_leisure_when1", "那通常是什麼時候呢？"),
    ("w_leisure_when2", "那個時候，通常是什麼時間去呢？"),
    ("w_leisure_how1", "當時的氣氛或心情是怎麼樣的呢？"),
    ("w_leisure_how2", "那時候，通常是怎麼樣的情形呢？"),
    ("w_leisure_why1", "這件事裡，你最想記住的是哪一部分？"),
    ("w_leisure_why2", "說起這段休閒時光，最讓你懷念的是什麼？"),
    # 節慶
    ("w_festival_where1", "是在什麼地方呢？"),
    ("w_festival_where2", "那個時候，是在哪裡呢？"),
    ("w_festival_when1", "那通常會是在什麼時段呢？"),
    ("w_festival_when2", "大概什麼時候會這麼做呢？"),
    ("w_festival_how1", "你們家通常是怎麼過的呢？"),
    ("w_festival_how2", "那時候，都是怎麼準備的呢？"),
    ("w_festival_why1", "這個節日裡，你最想記住的是哪一個畫面？"),
    ("w_festival_why2", "說起這個節日，最讓你懷念的是什麼？"),
    # 哀傷
    ("w_grief_death_why1", "這段回憶對你來說，特別珍貴的地方是什麼？"),
    ("w_grief_illness_why", "這段日子裡，有沒有什麼讓你撐過來的片刻呢？"),
    ("w_grief_regret_why", "這件事對你來說，最放在心上的是什麼？"),
    ("w_grief_lonely_why", "現在的日子裡，有沒有什麼時刻，讓你覺得比較自在呢？"),
    # 人生目標
    ("w_goal_wish_where", "你想像中，那會是在哪裡呢？"),
    ("w_goal_wish_why", "這個心願對你來說，特別在哪裡？"),
    ("w_goal_money_why", "這件事對你來說，特別在哪裡？"),
    ("w_goal_motto_why", "這句話對你來說，特別在哪裡？"),
    ("w_goal_children_why", "這件事對你來說，特別在哪裡？"),
    # 自我成就感
    ("w_achievement_where1", "那是在哪裡達成的呢？"),
    ("w_achievement_where2", "那個時候，是在哪裡完成的呢？"),
    ("w_achievement_when1", "那是什麼時候達成的呢？"),
    ("w_achievement_when2", "那是白天，還是晚上發生的呢？"),
    ("w_achievement_how1", "當時是怎麼做到的呢？"),
    ("w_achievement_how2", "那時候，你是怎麼完成的呢？"),
    ("w_achievement_why1", "這件事裡，最讓你驕傲的是哪一部分？"),
    ("w_achievement_why2", "說起這件事，最讓你自豪的是什麼？"),
    # 生命中特殊事件
    ("w_special_where1", "那是在哪裡發生的呢？"),
    ("w_special_where2", "那個時候，是在什麼地方呢？"),
    ("w_special_when1", "那是什麼時候發生的呢？"),
    ("w_special_when2", "那是白天，還是晚上發生的呢？"),
    ("w_special_why1", "這件事裡，你最想記住的是哪一部分？"),
    ("w_special_why2", "說起這件事，最讓你難忘的是什麼？"),

    # 5. 五感保底問句
    ("sense_visual", "那時候，天氣或光線是什麼樣子呢？"),
    ("sense_auditory", "那個時候，有沒有什麼聲音，讓你印象特別深呢？"),
    ("sense_olfactory", "那個時候，空氣中有沒有什麼味道呢？"),
    ("sense_taste", "那時候，有沒有嚐到什麼味道呢？"),
    ("sense_touch", "那時候，有沒有摸到或感覺到什麼呢？"),

    # 6. W維度保底問句
    ("w_fallback_who", "那個時候，還有誰跟你在一起呢？"),
    ("w_fallback_when", "那大概是什麼時候的事呢？"),
    ("w_fallback_where", "那是在什麼地方呢？"),
    ("w_fallback_what", "那時候還有什麼讓你印象深刻的地方？"),
    ("w_fallback_how", "當時是怎麼樣的情形呢？"),
]

CONTAINER = "tku-care-tts-1"
TTS_URL = "http://localhost:8080/inference_clone"
CONTAINER_OUTPUT = "/workspace/tts_cache"
LOCAL_OUTPUT = "tts_cache"

def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

# 建立容器內輸出資料夾
run(f'docker exec {CONTAINER} mkdir -p {CONTAINER_OUTPUT}')

# 建立本機輸出資料夾
os.makedirs(LOCAL_OUTPUT, exist_ok=True)

total = len(sentences)
success = 0
failed = []

print(f"開始生成，共 {total} 個句子...\n")

for i, (name, text) in enumerate(sentences, 1):
    out_path = f"{CONTAINER_OUTPUT}/{name}.wav"
    local_path = f"{LOCAL_OUTPUT}/{name}.wav"

    # 如果已經存在就跳過
    if os.path.exists(local_path):
        print(f"[{i}/{total}] 跳過（已存在）: {name}")
        success += 1
        continue

    print(f"[{i}/{total}] 生成: {name}")
    print(f"         句子: {text[:30]}{'...' if len(text) > 30 else ''}")

    # 在容器內呼叫 TTS
    result = run(
        f'docker exec {CONTAINER} curl -s -X POST {TTS_URL} '
        f'-F "tts_text={text}" '
        f'-o {out_path}'
    )

    if result.returncode != 0:
        print(f"         ❌ 失敗: {result.stderr}")
        failed.append(name)
        continue

    # 把音檔複製到本機
    cp_result = run(f'docker cp {CONTAINER}:{out_path} {local_path}')

    if cp_result.returncode != 0:
        print(f"         ❌ 複製失敗: {cp_result.stderr}")
        failed.append(name)
        continue

    # 確認檔案大小
    size = os.path.getsize(local_path)
    if size < 1000:
        print(f"         ❌ 音檔太小（{size} bytes），可能生成失敗")
        failed.append(name)
        os.remove(local_path)
        continue

    print(f"         ✅ 完成 ({size // 1024} KB)")
    success += 1
    time.sleep(0.5)  # 稍微等一下，避免打爆 TTS

print(f"\n{'='*50}")
print(f"完成！成功：{success}/{total}")
if failed:
    print(f"失敗的句子：")
    for f in failed:
        print(f"  - {f}")
else:
    print("所有句子都生成成功！")
print(f"音檔存放在：{LOCAL_OUTPUT}/")
