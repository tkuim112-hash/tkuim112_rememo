"""
BlueMagpie-TTS FastAPI Server
- POST /v1/audio/speech  — 相容 OpenAI TTS 格式，用預計算的 speaker centroid
- POST /inference_clone  — 上傳參考音檔即時合成
- GET  /health
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
bm_model        = None
bm_tokenizer    = None
speaker_centroid = None  # 預載的 Peichi_centroid.pt

SPEAKER_CENTROID_PT = os.environ.get(
    "SPEAKER_CENTROID_PT", "/workspace/references/Peichi_centroid.pt"
)
CFG_VALUE   = float(os.environ.get("CFG_VALUE", "2.0"))
INFER_STEPS = int(os.environ.get("INFER_STEPS", "9"))


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


def load_speaker_centroid(model_dir: str):
    """
    載入語者向量，優先順序：
    1. SPEAKER_CENTROID_PT 指定的 .pt 檔（你的聲音）
    2. 內建 female_voice
    3. 內建 hung_yi_lee
    """
    # 方案 A：自訂語者向量
    if os.path.exists(SPEAKER_CENTROID_PT):
        vec = torch.load(SPEAKER_CENTROID_PT, map_location="cpu", weights_only=True)
        logger.info(f"載入語者向量 ✓ shape={vec.shape}（{SPEAKER_CENTROID_PT}）")
        return vec

    # 方案 B：內建向量
    centroid_path = os.path.join(model_dir, "checkpoints", "speaker_centroids.pt")
    if os.path.exists(centroid_path):
        centroids = torch.load(centroid_path, map_location="cpu", weights_only=True)
        speaker_ids = centroids["speaker_ids"]
        # 優先用 female_voice
        target = "female_voice" if "female_voice" in speaker_ids else "hung_yi_lee"
        vec = centroids["centroids"][speaker_ids.index(target)]
        logger.info(f"載入內建語者向量 ✓（{target}）")
        return vec

    logger.warning("找不到語者向量，將用無音色控制模式")
    return None


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

app = FastAPI(title="BlueMagpie-TTS Service", version="1.3.0")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": bm_model is not None,
        "speaker_centroid_loaded": speaker_centroid is not None,
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
            # 上傳的音檔 → 即時抽取向量
            from bluemagpie import extract_speaker_centroid
            tmp_path = f"/tmp/ref_{ref_wav.filename}"
            content = await ref_wav.read()
            with open(tmp_path, "wb") as f:
                f.write(content)
            tmp_centroid = extract_speaker_centroid(tmp_path)
            audio = _synthesize_with_centroid(tts_text, tmp_centroid)
        else:
            audio = _synthesize(tts_text)
    except Exception as e:
        logger.exception("clone 合成失敗")
        raise HTTPException(500, str(e))

    wav = numpy_to_wav_bytes(audio, bm_model.sample_rate)
    return Response(content=wav, media_type="audio/wav")


# ── 內部合成 ─────────────────────────────────────────────────

def _synthesize(text: str):
    return _synthesize_with_centroid(text, speaker_centroid)


def _synthesize_with_centroid(text: str, centroid):
    audio = bm_model.generate(
        target_text=text,
        speaker_centroid=centroid,
        cfg_value=CFG_VALUE,
        inference_timesteps=INFER_STEPS,
        max_len=2000,
        retry_badcase=True,
    )
    return audio


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
    speaker_centroid = load_speaker_centroid(args.model_dir)

    uvicorn.run(app, host=args.host, port=args.port)
