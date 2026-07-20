"""
BlueMagpie-TTS FastAPI Server
- POST /v1/audio/speech  — 相容 BreezyVoice / OpenAI TTS 格式
- POST /inference_clone  — 直接上傳參考音檔做聲音複製
- GET  /health

改動：
- 啟動時預先計算 audrey.wav 的語者向量，存在記憶體
- 每次請求直接用向量，不重新讀音檔 → 速度快很多
- cfg_value 從 2.8 降到 2.0 → 語速較自然
"""

import argparse
import io
import logging
import os

import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 全域 ─────────────────────────────────────────────────────
bm_model       = None
bm_tokenizer   = None
default_spk_vec = None   # 預先計算好的語者向量（啟動時算一次）

DEFAULT_REF_WAV = os.environ.get("SPEAKER_PROMPT_AUDIO_PATH", "")
CFG_VALUE       = float(os.environ.get("CFG_VALUE", "2.0"))
INFER_STEPS     = int(os.environ.get("INFER_STEPS", "9"))


# ============================================================
# 模型載入
# ============================================================

def load_model(model_id: str, model_dir: str):
    from huggingface_hub import snapshot_download
    from transformers import PreTrainedTokenizerFast
    from bluemagpie import BlueMagpieModel

    if not os.path.isdir(model_dir) or not os.listdir(model_dir):
        logger.info(f"從 HuggingFace 下載模型：{model_id} → {model_dir}")
        os.makedirs(model_dir, exist_ok=True)
        snapshot_download(model_id, local_dir=model_dir)
    else:
        logger.info(f"從本地載入模型：{model_dir}")

    logger.info("初始化 BlueMagpieModel …")
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_file=os.path.join(model_dir, "tokenizer.json")
    )
    model = BlueMagpieModel.from_local(
        model_dir, tokenizer=tokenizer, training=False, device="cuda"
    )
    logger.info("BlueMagpieModel 載入完成 ✓")
    return model, tokenizer


def precompute_speaker_vector(model_dir: str):
    """BlueMagpie 不支援 encode_speaker，改用 reference_wav_path 做聲音複製"""
    if DEFAULT_REF_WAV and os.path.exists(DEFAULT_REF_WAV):
        logger.info(f"參考音檔確認存在：{DEFAULT_REF_WAV}，將用 reference_wav_path 合成")
        return True
    logger.warning(f"找不到參考音檔：{DEFAULT_REF_WAV}，fallback 到 hung_yi_lee")
    return False


# ============================================================
# 音訊工具
# ============================================================

def numpy_to_wav_bytes(audio, sample_rate: int) -> bytes:
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    buf = io.BytesIO()
    sf.write(buf, audio.squeeze(), sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(title="BlueMagpie-TTS Service", version="1.1.0")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": bm_model is not None,
        "speaker_vector": default_spk_vec is not None,
        "cfg_value": CFG_VALUE,
        "infer_steps": INFER_STEPS,
    }


# ── /v1/audio/speech ─────────────────────────────────────────

class SpeechRequest(BaseModel):
    model: str = "bluemagpie"
    input: str
    voice: str = ""


@app.post("/v1/audio/speech")
def create_speech(req: SpeechRequest):
    if bm_model is None:
        raise HTTPException(503, "模型尚未載入")
    if not req.input.strip():
        raise HTTPException(400, "input 不可為空")

    try:
        audio = _synthesize(req.input)
    except Exception as e:
        logger.exception("合成失敗")
        raise HTTPException(500, f"合成失敗：{e}")

    wav = numpy_to_wav_bytes(audio, bm_model.sample_rate)
    return Response(content=wav, media_type="audio/wav")


# ── /inference_clone ─────────────────────────────────────────

@app.post("/inference_clone")
async def inference_clone(
    tts_text: str = Form(...),
    ref_wav: UploadFile = File(None),
):
    if bm_model is None:
        raise HTTPException(503, "模型尚未載入")

    try:
        if ref_wav is not None:
            # 臨時音檔 → 即時提取向量
            tmp_path = f"/tmp/ref_{ref_wav.filename}"
            content = await ref_wav.read()
            with open(tmp_path, "wb") as f:
                f.write(content)
            audio = bm_model.generate(
                target_text=tts_text,
                reference_wav_path=tmp_path,
                cfg_value=CFG_VALUE,
                inference_timesteps=INFER_STEPS,
                max_len=2000,
                retry_badcase=True,
            )
        else:
            # 用預先計算好的向量，快很多
            audio = _synthesize(tts_text)
    except Exception as e:
        logger.exception("clone 合成失敗")
        raise HTTPException(500, str(e))

    wav = numpy_to_wav_bytes(audio, bm_model.sample_rate)
    return Response(content=wav, media_type="audio/wav")


# ── 內部合成 ────────────────────────────────────────────────

def _synthesize(text: str):
    # 有參考音檔 → 聲音複製
    if default_spk_vec and DEFAULT_REF_WAV and os.path.exists(DEFAULT_REF_WAV):
        return bm_model.generate(
            target_text=text,
            reference_wav_path=DEFAULT_REF_WAV,
            cfg_value=CFG_VALUE,
            inference_timesteps=INFER_STEPS,
            max_len=2000,
            retry_badcase=True,
        )
    # fallback：hung_yi_lee 向量
    model_dir = os.environ.get("MODEL_LOCAL_DIR", "/workspace/models/BlueMagpie-TTS")
    centroid_path = os.path.join(model_dir, "checkpoints", "hung_yi_lee_speaker_centroids.pt")
    if os.path.exists(centroid_path):
        centroids = torch.load(centroid_path, map_location="cpu", weights_only=True)
        spk_vec = centroids["centroids"][centroids["speaker_ids"].index("hung_yi_lee")]
        return bm_model.generate(
            target_text=text,
            speaker_centroid=spk_vec,
            cfg_value=CFG_VALUE,
            inference_timesteps=INFER_STEPS,
            max_len=2000,
            retry_badcase=True,
        )
    # 最終 fallback：無音色控制
    return bm_model.generate(
        target_text=text,
        cfg_value=CFG_VALUE,
        inference_timesteps=INFER_STEPS,
        max_len=2000,
    )


# ============================================================
# 主程式
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_id",  default="OpenFormosa/BlueMagpie-TTS")
    parser.add_argument("--model_dir", default="/workspace/models/BlueMagpie-TTS")
    parser.add_argument("--port",      type=int, default=8080)
    parser.add_argument("--host",      default="0.0.0.0")
    args = parser.parse_args()

    bm_model, bm_tokenizer = load_model(args.model_id, args.model_dir)
    default_spk_vec = precompute_speaker_vector(args.model_dir)

    uvicorn.run(app, host=args.host, port=args.port)
