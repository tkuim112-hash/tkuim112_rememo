import requests
import json
import base64
import os

# --- 實體設定區 ---
STABILITY_KEY = "sk-TdxyAlg2W2Oo8zIK3A4fr01yaVd2MlnkjodOhR0dKyVjpsle"
STT_API = "http://localhost:8001/v1/audio/transcriptions"
OLLAMA_API = "http://localhost:11434/api/generate"
TTS_API = "http://localhost:9233/v1/audio/speech" 
AUDIO_FILE = "bad_memory.wav" 
VOICE_ID = "d0f44e420c02" # 太乙真人 ID
# ----------------

def run_final_integration():
    # 0. 執行前清理
    for f in ["real_output_image.png", "real_output_audio.wav"]:
        if os.path.exists(f): 
            try: os.remove(f)
            except: pass

    # 1. 實體 STT
    print(f"--- 1. STT：正在處理負面回憶 ---")
    with open(AUDIO_FILE, "rb") as f:
        res = requests.post(STT_API, files={"file": f}, data={"model": "base"})
    transcript = res.json().get("text")
    print(f">> [辨識文字]: {transcript}")

    # 2. 實體 LLM：加入 JSON 範例，強力約束輸出格式
    print("--- 2. LLM：深度共情與格式強效約束 ---")
    system_prompt = (
        "你是一位溫柔的心理治療師。請根據長者的回憶內容，嚴格輸出 JSON 格式。\n"
        "範例格式如下：\n"
        "{\n"
        '  "scene_text": "繁體中文安撫語...",\n'
        '  "open_question": "繁體中文 5W1H 問句...",\n'
        '  "image_prompt": "English detailed image prompt..." \n'
        "}\n"
        "規則：禁止輸出任何 JSON 以外的文字。針對負面情緒，請先給予溫暖的肯定與安慰。"
    )
    
    llm_payload = {"model": "llama3:8b-instruct-q4_K_M", "prompt": f"{system_prompt}\n長者回憶：{transcript}", "format": "json", "stream": False}
    llm_res = requests.post(OLLAMA_API, json=llm_payload).json()
    
    # 增加強健性解析
    try:
        data = json.loads(llm_res['response'])
        scene_text = data.get('scene_text', "聽起來當時真的很辛苦，辛苦你了。")
        open_question = data.get('open_question', "那時候有誰陪在你身邊嗎？")
        img_prompt = data.get('image_prompt', "A realistic and peaceful old factory, cinematic lighting, 8k.")
    except:
        print("⚠️ LLM 輸出格式異常，啟動保險絲預設值。")
        scene_text = "聽你分享這段往事，我能感受到當時的壓力真的很大，辛苦你了。"
        open_question = "在那個忙碌的工廠裡，有沒有什麼小角落是能讓你喘口氣的呢？"
        img_prompt = "Hyper-realistic 1960s factory interior, soft sunlight through windows, cinematic."

    print(f">> [治療師安慰]: {scene_text}")
    print(f">> [引導問題]: {open_question}")

    # 3. 實體 Stability AI：遵從 SDXL 1024x1024 規範
    print("--- 3. Stability AI：生成寫實大圖 (1024x1024) ---")
    engine_id = "stable-diffusion-xl-1024-v1-0"
    stability_url = f"https://api.stability.ai/v1/generation/{engine_id}/text-to-image"
    headers = {"Authorization": f"Bearer {STABILITY_KEY}", "Content-Type": "application/json", "Accept": "application/json"}
    
    body = {
        "text_prompts": [{"text": f"{img_prompt}, realistic photography, highly detailed, 8k"}],
        "cfg_scale": 7, "height": 1024, "width": 1024, "samples": 1, "steps": 30
    }
    img_res = requests.post(stability_url, headers=headers, json=body)
    
    if img_res.status_code == 200:
        with open("real_output_image.png", "wb") as f:
            f.write(base64.b64decode(img_res.json()["artifacts"][0]["base64"]))
        print(">> ✅ 寫實圖片已儲存！")
    else:
        print(f"❌ 生圖失敗：{img_res.text}")

    # 4. 實體 TTS (太乙真人 ID 撥號)
    print("--- 4. TTS：正在下載語音檔 ---")
    full_text = f"{scene_text} {open_question}"
    tts_payload = {"model": "cosyvoice", "input": full_text, "voice": VOICE_ID}
    tts_res = requests.post(TTS_API, json=tts_payload)
    if tts_res.status_code == 200:
        with open("real_output_audio.wav", "wb") as f:
            f.write(tts_res.content)
        print(">> ✅ 語音檔已成功儲存！")

if __name__ == "__main__":
    run_final_integration()
