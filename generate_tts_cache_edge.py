"""
用 edge-tts（微軟雲端 TTS）生成全部預錄音檔——demo 前的暫時方案。
句子清單直接沿用 generate_tts_cache.py / generate_tts_sharing.py 裡的
sentences 清單（用 AST 安全擷取，不會觸發那兩支腳本裡呼叫本地 TTS 容器的
副作用程式碼），輸出檔名跟 audio_bank.py 的 key 一一對應，可直接覆蓋
tts_cache/ 裡舊的本地 TTS 版本。

使用方式：python generate_tts_cache_edge.py
"""
import asyncio
import os
import subprocess

import edge_tts
import imageio_ffmpeg

VOICE = "zh-TW-HsiaoYuNeural"
RATE = "+0%"
LOCAL_OUTPUT = "tts_cache"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def load_sentences(path):
    src = open(path, encoding="utf-8").read()
    start = src.index("sentences = [")
    end = src.index("\n]", start) + 2
    ns = {}
    exec(src[start:end], ns)
    return ns["sentences"]


sentences = load_sentences("generate_tts_cache.py") + load_sentences("generate_tts_sharing.py")

os.makedirs(LOCAL_OUTPUT, exist_ok=True)


async def gen_one(name, text):
    mp3_path = f"{LOCAL_OUTPUT}/{name}.mp3.tmp"
    wav_path = f"{LOCAL_OUTPUT}/{name}.wav"
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE)
    await communicate.save(mp3_path)
    subprocess.run(
        [FFMPEG, "-y", "-i", mp3_path, "-ar", "24000", "-ac", "1", wav_path],
        check=True, capture_output=True,
    )
    os.remove(mp3_path)


async def main():
    total = len(sentences)
    failed = []
    for i, (name, text) in enumerate(sentences, 1):
        print(f"[{i}/{total}] {name}: {text[:30]}{'...' if len(text) > 30 else ''}")
        try:
            await gen_one(name, text)
        except Exception as e:
            print(f"         失敗: {e}")
            failed.append(name)

    print("\n" + "=" * 50)
    print(f"完成！成功：{total - len(failed)}/{total}")
    if failed:
        print("失敗的句子：")
        for f in failed:
            print(f"  - {f}")
    print(f"音檔存放在：{LOCAL_OUTPUT}/")


asyncio.run(main())
