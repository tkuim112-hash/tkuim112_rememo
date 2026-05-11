import requests
import json
import base64
import os

# --- 實體設定區 ---
STABILITY_KEY = "sk-TdxyAlg2W2Oo8zIK3A4fr01yaVd2MlnkjodOhR0dKyVjpsle"
STT_API = "http://localhost:8001/v1/audio/transcriptions"
OLLAMA_API = "http://localhost:11434/api/generate"
TTS_API = "http://localhost:9233/v1/audio/speech" 
AUDIO_FILE = "test_memory.wav" 
# ----------------

def run_final_integration():
    # 1. 實體 STT (Whisper GPU)
    print("--- 1. STT：正在將錄音轉為文字 ---")
    with open(AUDIO_FILE, "rb") as f:
        res = requests.post(STT_API, files={"file": f}, data={"model": "base"})
    transcript = res.json().get("text")
    print(f">> [辨識文字]: {transcript}")

    # 2. 實體 LLM (Llama-3 GPU)：強制繁體中文與字串格式
    print("--- 2. LLM：生成暖心中文與英文指令 ---")
    system_prompt = (
        "你是一位溫暖的記憶拼圖治療師。請輸出 JSON 格式，內容規範：\n"
        "1. scene_text: 必須使用『繁體中文』，約150字，加入感官描述給予情緒價值。\n"
        "2. open_question: 必須使用『繁體中文』，將 5W1H 融合進一段自然的問句（字串格式），禁止輸出清單或字典。\n"
        "3. image_prompt: 必須為英文(English)，描述 1960s 台灣復古風格、油畫質感。"
    )
    llm_payload = {
        "model": "llama3:8b-instruct-q4_K_M",
        "prompt": f"{system_prompt}\n長者回憶：{transcript}",
        "format": "json", "stream": False
    }
    llm_res = requests.post(OLLAMA_API, json=llm_payload).json()
    data = json.loads(llm_res['response'])
    
    # 印出結果確認
    print(f">> [中文場景]: {data['scene_text']}")
    print(f">> [5W1H 提問]: {data['open_question']}")

    # 3. 實體 Stability AI (Cloud)：生成圖片
    print("--- 3. Stability AI：生成復古圖片 ---")
    engine_id = "stable-diffusion-xl-1024-v1-0"
    stability_url = f"https://api.stability.ai/v1/generation/{engine_id}/text-to-image"
    headers = {"Authorization": f"Bearer {STABILITY_KEY}", "Content-Type": "application/json", "Accept": "application/json"}
    body = {"text_prompts": [{"text": data['image_prompt']}], "cfg_scale": 7, "height": 1024, "width": 1024, "samples": 1, "steps": 30}
    img_res = requests.post(stability_url, headers=headers, json=body)
    
    if img_res.status_code == 200:
        with open("real_output_image.png", "wb") as f:
            f.write(base64.b64decode(img_res.json()["artifacts"][0]["base64"]))
        print(">> ✅ 圖片已成功儲存：real_output_image.png")

    # 4. 實體 TTS (CosyVoice)：改用語音 ID 嘗試
    print("--- 4. TTS：正在下載語音檔 ---")
    full_text = f"{data['scene_text']} {data['open_question']}"
    
    tts_payload = {
        "model": "cosyvoice",
        "input": full_text,
        # 改用 ID 撥號，這是最精確的連線方式
        "voice": "d0f44e420c02" 
    }
    
    tts_res = requests.post(TTS_API, json=tts_payload)
    
    if tts_res.status_code == 200:
        with open("real_output_audio.wav", "wb") as f:
            f.write(tts_res.content)
        print(">> ✅ 音檔已成功儲存：real_output_audio.wav")
    else:
        print(f"❌ 語音合成失敗：{tts_res.status_code}")
        print(f"伺服器回報訊息：{tts_res.text}")

if __name__ == "__main__":
    run_final_integration()

