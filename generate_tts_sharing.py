"""
心得分享 TTS 生成腳本
使用方式：python generate_tts_sharing.py
"""
import subprocess
import os
import time

CONTAINER = "tku-care-tts-1"
TTS_URL = "http://localhost:8080/inference_clone"
CONTAINER_OUTPUT = "/workspace/tts_cache"
LOCAL_OUTPUT = "tts_cache"

sentences = [
    # 1. 開場邀請（替換版本）
    ("sharing_opening", "今天聊了這麼多，回想整場聊下來，你有什麼想跟我分享的呢？"),

    # 2. 承接語
    ("sharing_ack_positive_1", "聽你這樣說，我也覺得很溫暖。"),
    ("sharing_ack_positive_2", "能感覺到你很珍惜這些回憶呢。"),
    ("sharing_ack_short", "沒關係，能陪你聊今天這些，我也很開心。"),
    ("sharing_ack_emotional", "這些回憶對你來說真的很重要，謝謝你願意跟我分享。"),

    # 3. 系統整合肯定（辛苦類）
    ("sharing_affirm_hard_1", "你經歷了這麼多事，也都一一走過來、撐過來了，這是很不容易、很值得驕傲的一件事。"),
    ("sharing_affirm_hard_2", "這一路走來不容易，但你都撐過來了，這份堅強真的很讓人佩服。"),
    ("sharing_affirm_hard_3", "不管過程多辛苦，你都一步一步走過來了，這些都是你這一生的勳章。"),
    ("sharing_affirm_hard_4", "這些不容易的日子，你都好好地撐過來了，這份韌性很值得為自己驕傲。"),

    # 4. 系統整合肯定（溫暖類）
    ("sharing_affirm_warm_1", "你這一生有這麼多美好的時光可以回味，這些都是屬於你自己的、獨一無二的故事，真的很珍貴。"),
    ("sharing_affirm_warm_2", "這些美好的回憶，都是只屬於你的故事，很珍貴、很值得好好收藏。"),
    ("sharing_affirm_warm_3", "能擁有這麼多值得回味的時光，真的是很幸福的一件事。"),
    ("sharing_affirm_warm_4", "這一生留下這麼多溫暖的回憶，都是屬於你自己獨一無二的寶藏。"),

    # 5. 結尾感謝語
    ("sharing_closing_1", "謝謝你今天願意跟我分享這麼多，希望這些美好的時光，能常常陪著你、讓你覺得溫暖。"),
    ("sharing_closing_2", "謝謝你今天陪我聊了這麼多，希望這份溫暖能一直留在你心裡。"),
    ("sharing_closing_3", "很謝謝你把這些故事說給我聽，希望你隨時想起來，都能感覺到溫暖。"),

    # 6. 長者對系統不滿時的承接語
    ("sharing_complaint_impatient", "不好意思，讓你覺得不耐煩了。"),
    ("sharing_complaint_distrust", "抱歉，我沒辦法像真人一樣理解你，這是我的限制。"),
    ("sharing_complaint_want_human", "不好意思，沒能讓你覺得像在跟真人聊天。"),
    ("sharing_complaint_useless", "抱歉讓你覺得這個沒有幫助，這個方式不一定適合每個人。"),
]

def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

run(f'docker exec {CONTAINER} mkdir -p {CONTAINER_OUTPUT}')
os.makedirs(LOCAL_OUTPUT, exist_ok=True)

total = len(sentences)
success = 0
failed = []

print(f"開始生成，共 {total} 個句子...\n")

for i, (name, text) in enumerate(sentences, 1):
    local_path = f"{LOCAL_OUTPUT}/{name}.wav"
    out_path = f"{CONTAINER_OUTPUT}/{name}.wav"

    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
        print(f"[{i}/{total}] 跳過（已存在）: {name}")
        success += 1
        continue

    print(f"[{i}/{total}] 生成: {name}")
    print(f"         句子: {text[:40]}{'...' if len(text) > 40 else ''}")

    result = run(
        f'docker exec {CONTAINER} curl -s -X POST {TTS_URL} '
        f'-F "tts_text={text}" '
        f'-o {out_path}'
    )

    if result.returncode != 0:
        print(f"         ❌ 失敗: {result.stderr}")
        failed.append(name)
        continue

    cp_result = run(f'docker cp {CONTAINER}:{out_path} {local_path}')
    if cp_result.returncode != 0:
        print(f"         ❌ 複製失敗")
        failed.append(name)
        continue

    size = os.path.getsize(local_path)
    if size < 1000:
        print(f"         ❌ 音檔太小（{size} bytes）")
        failed.append(name)
        os.remove(local_path)
        continue

    print(f"         ✅ 完成 ({size // 1024} KB)")
    success += 1
    time.sleep(0.5)

print(f"\n{'='*50}")
print(f"完成！成功：{success}/{total}")
if failed:
    print(f"失敗的句子：")
    for f in failed:
        print(f"  - {f}")
else:
    print("所有句子都生成成功！")
print(f"音檔存放在：{LOCAL_OUTPUT}/")
