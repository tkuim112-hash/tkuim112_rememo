"""
CosyVoice 2 FastAPI Server
- 主要 endpoint：POST /v1/audio/speech（相容 BreezyVoice / OpenAI TTS API 格式）
- 輔助 endpoint：POST /inference_zero_shot（直接傳 prompt_wav 做 zero-shot 克隆）
- 健康檢查：GET /health

啟動方式：
    python server.py --model_path FunAudioLLM/CosyVoice2-0.5B \
                     --model_dir  /workspace/models/CosyVoice2-0.5B \
                     --port 8080
"""

import argparse
import io
import logging
import os
import sys
import wave

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

# CosyVoice repo 放在 /workspace/CosyVoice，PYTHONPATH 已由 Dockerfile 設好
sys.path.insert(0, "/workspace/CosyVoice")
sys.path.insert(0, "/workspace/CosyVoice/third_party/Matcha-TTS")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 全域：模型實例 ────────────────────────────────────────────
cosyvoice_model = None
DEFAULT_SPEAKER = "中文女"  # CosyVoice2 SFT 內建音色

# ── 預設參考音檔（zero-shot 時使用） ────────────────────────
# 可透過 SPEAKER_PROMPT_AUDIO_PATH / SPEAKER_PROMPT_TEXT 環境變數覆寫
DEFAULT_PROMPT_AUDIO = os.environ.get("SPEAKER_PROMPT_AUDIO_PATH", "")
DEFAULT_PROMPT_TEXT  = os.environ.get("SPEAKER_PROMPT_TEXT_TRANSCRIPTION", "")


# ============================================================
# 模型載入
# ============================================================

def load_model(model_path: str, model_dir: str):
    """
    優先從 model_dir 載入本地快取；若沒有則從 HuggingFace 下載。
    model_path 格式：'FunAudioLLM/CosyVoice2-0.5B'
    """
    from cosyvoice.cli.cosyvoice import CosyVoice2
    from huggingface_hub import snapshot_download

    if not os.path.isdir(model_dir) or not os.listdir(model_dir):
        logger.info(f"本地模型不存在，從 HuggingFace 下載：{model_path} → {model_dir}")
        os.makedirs(model_dir, exist_ok=True)
        snapshot_download(model_path, local_dir=model_dir)
    else:
        logger.info(f"從本地載入模型：{model_dir}")

    logger.info("初始化 CosyVoice2 …")
    model = CosyVoice2(model_dir)
    logger.info("CosyVoice2 載入完成 ✓")
    return model


# ============================================================
# 音訊工具
# ============================================================

def numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int = 22050) -> bytes:
    """把 numpy float32 array 轉成 WAV bytes"""
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


def load_prompt_wav(prompt_wav_bytes: bytes) -> np.ndarray:
    """把上傳的 WAV bytes 讀成 numpy float32"""
    buf = io.BytesIO(prompt_wav_bytes)
    audio, sr = sf.read(buf, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # stereo → mono
    # CosyVoice2 期望 16kHz
    if sr != 16000:
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    return audio


def collect_generator(generator) -> np.ndarray:
    """把 CosyVoice inference generator 的所有 chunk 合併"""
    chunks = []
    for result in generator:
        # result 是 dict，key 通常是 'tts_speech'
        speech = result.get("tts_speech")
        if speech is not None:
            # speech 可能是 torch.Tensor 或 np.ndarray
            if hasattr(speech, "numpy"):
                speech = speech.squeeze().numpy()
            chunks.append(speech)
    if not chunks:
        raise RuntimeError("CosyVoice 沒有產生任何音訊")
    return np.concatenate(chunks, axis=0)


# ============================================================
# FastAPI app
# ============================================================

app = FastAPI(title="CosyVoice2 TTS Service", version="1.0.0")


@app.on_event("startup")
async def startup_event():
    logger.info("FastAPI startup — 模型應已在 main 載入")


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": cosyvoice_model is not None}


# ── /v1/audio/speech ─────────────────────────────────────────
# 相容 BreezyVoice（和 OpenAI TTS API）的格式
# BreezyVoice 的 client 呼叫：
#   POST /v1/audio/speech
#   JSON body: {"model": "...", "input": "要說的文字", "voice": "..."}
#   回傳：audio/wav

class SpeechRequest(BaseModel):
    model: str = "cosyvoice2"
    input: str                # 要合成的文字
    voice: str = ""           # 空字串 → 使用預設音色或環境變數設定的 prompt


@app.post("/v1/audio/speech")
def create_speech(req: SpeechRequest):
    if cosyvoice_model is None:
        raise HTTPException(503, "模型尚未載入")
    if not req.input.strip():
        raise HTTPException(400, "input 不可為空")

    try:
        audio = _synthesize_text(req.input, voice=req.voice)
    except Exception as e:
        logger.exception("合成失敗")
        raise HTTPException(500, f"合成失敗：{e}")

    wav_bytes = numpy_to_wav_bytes(audio, sample_rate=cosyvoice_model.sample_rate)
    return Response(content=wav_bytes, media_type="audio/wav")


# ── /inference_zero_shot ─────────────────────────────────────
# 允許直接上傳參考音檔做 zero-shot 克隆，方便測試

@app.post("/inference_zero_shot")
async def inference_zero_shot(
    tts_text: str = Form(...),
    prompt_text: str = Form(""),
    prompt_wav: UploadFile = File(None),
):
    if cosyvoice_model is None:
        raise HTTPException(503, "模型尚未載入")

    prompt_audio = None
    prompt_txt   = prompt_text

    if prompt_wav is not None:
        raw = await prompt_wav.read()
        prompt_audio = load_prompt_wav(raw)
        if not prompt_txt:
            prompt_txt = DEFAULT_PROMPT_TEXT
    elif DEFAULT_PROMPT_AUDIO and os.path.exists(DEFAULT_PROMPT_AUDIO):
        audio_arr, sr = sf.read(DEFAULT_PROMPT_AUDIO, dtype="float32")
        if audio_arr.ndim > 1:
            audio_arr = audio_arr.mean(axis=1)
        prompt_audio = audio_arr
        prompt_txt   = DEFAULT_PROMPT_TEXT

    try:
        if prompt_audio is not None:
            import torch
            prompt_tensor = torch.from_numpy(prompt_audio).unsqueeze(0)
            gen = cosyvoice_model.inference_zero_shot(
                tts_text, prompt_txt, prompt_tensor, stream=False
            )
        else:
            # 沒有參考音，fallback 到 SFT 預設音色
            gen = cosyvoice_model.inference_sft(
                tts_text, DEFAULT_SPEAKER, stream=False
            )
        audio = collect_generator(gen)
    except Exception as e:
        logger.exception("zero-shot 合成失敗")
        raise HTTPException(500, str(e))

    wav_bytes = numpy_to_wav_bytes(audio, sample_rate=cosyvoice_model.sample_rate)
    return Response(content=wav_bytes, media_type="audio/wav")


# ── 內部：統一合成入口 ────────────────────────────────────────

def _synthesize_text(text: str, voice: str = "") -> np.ndarray:
    """
    優先順序：
    1. 環境變數 SPEAKER_PROMPT_AUDIO_PATH 有設定 → zero-shot
    2. voice 參數對應到 SFT 內建音色 → inference_sft
    3. fallback → DEFAULT_SPEAKER
    """
    import torch

    # 方案 A：用環境變數設定的參考音檔做 zero-shot
    if DEFAULT_PROMPT_AUDIO and os.path.exists(DEFAULT_PROMPT_AUDIO):
        audio_arr, sr = sf.read(DEFAULT_PROMPT_AUDIO, dtype="float32")
        if audio_arr.ndim > 1:
            audio_arr = audio_arr.mean(axis=1)
        prompt_tensor = torch.from_numpy(audio_arr).unsqueeze(0)
        gen = cosyvoice_model.inference_zero_shot(
            text, DEFAULT_PROMPT_TEXT, prompt_tensor, stream=False
        )
        return collect_generator(gen)

    # 方案 B：SFT 內建音色
    speaker = voice if voice else DEFAULT_SPEAKER
    gen = cosyvoice_model.inference_sft(text, speaker, stream=False)
    return collect_generator(gen)


# ============================================================
# 主程式
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CosyVoice2 TTS Server")
    parser.add_argument("--model_path", default="FunAudioLLM/CosyVoice2-0.5B",
                        help="HuggingFace model id（若本地有快取則不下載）")
    parser.add_argument("--model_dir",  default="/workspace/models/CosyVoice2-0.5B",
                        help="本地模型快取目錄")
    parser.add_argument("--port",       type=int, default=8080)
    parser.add_argument("--host",       default="0.0.0.0")
    args = parser.parse_args()

    # 啟動前先載入模型（避免第一個請求超時）
    cosyvoice_model = load_model(args.model_path, args.model_dir)

    uvicorn.run(app, host=args.host, port=args.port)