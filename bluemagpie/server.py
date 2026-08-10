"""
BlueMagpie-TTS FastAPI Server v1.7.0
- POST /v1/audio/speech  - 相容 OpenAI TTS 格式
- POST /inference_clone  - 上傳參考音檔即時合成
- GET  /health
"""

import argparse
import io
import logging
import os
import re

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bm_model         = None
bm_tokenizer     = None
speaker_centroid = None

SPEAKER_CENTROID_PT = os.environ.get(
    "SPEAKER_CENTROID_PT", "/workspace/references/Peichi_centroid.pt"
)
CFG_VALUE   = float(os.environ.get("CFG_VALUE", "2.5"))
INFER_STEPS = int(os.environ.get("INFER_STEPS", "10"))


def load_model(model_id: str, model_dir: str):
    from huggingface_hub import snapshot_download
    from transformers import PreTrainedTokenizerFast
    from bluemagpie import BlueMagpieModel

    if not os.path.isdir(model_dir) or not os.listdir(model_dir):
        logger.info(f"從 HuggingFace 下載模型：{model_id} -> {model_dir}")
        os.makedirs(model_dir, exist_ok=True)
        snapshot_download(model_id, local_dir=model_dir)
    else:
        logger.info(f"從本地載入模型：{model_dir}")

    logger.info("初始化 BlueMagpieModel ...")
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_file=os.path.join(model_dir, "tokenizer.json")
    )
    model = BlueMagpieModel.from_local(
        model_dir, tokenizer=tokenizer, training=False, device="cuda"
    )
    logger.info("BlueMagpieModel 載入完成")
    return model, tokenizer


def load_speaker_centroid(model_dir: str):
    if os.path.exists(SPEAKER_CENTROID_PT):
        vec = torch.load(SPEAKER_CENTROID_PT, map_location="cpu", weights_only=True)
        logger.info(f"載入語者向量 shape={vec.shape} ({SPEAKER_CENTROID_PT})")
        return vec

    centroid_path = os.path.join(model_dir, "checkpoints", "speaker_centroids.pt")
    if os.path.exists(centroid_path):
        centroids = torch.load(centroid_path, map_location="cpu", weights_only=True)
        speaker_ids = centroids["speaker_ids"]
        target = "hung_yi_lee" if "hung_yi_lee" in speaker_ids else "female_voice"
        vec = centroids["centroids"][speaker_ids.index(target)]
        logger.info(f"載入內建語者向量 ({target})")
        return vec

    logger.warning("找不到語者向量，將用無音色控制模式")
    return None


def count_cjk(text: str) -> int:
    return len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))


def to_wav_bytes(audio, sample_rate: int, text: str = "") -> bytes:
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    audio = audio.squeeze().astype(np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


app = FastAPI(title="BlueMagpie-TTS Service", version="1.7.0")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": bm_model is not None,
        "speaker_centroid_loaded": speaker_centroid is not None,
        "cfg_value": CFG_VALUE,
        "infer_steps": INFER_STEPS,
        "version": "1.7.0",
    }


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
        wav = to_wav_bytes(audio, bm_model.sample_rate, req.input)
    except Exception as e:
        logger.exception("合成失敗")
        raise HTTPException(500, f"合成失敗：{e}")
    return Response(content=wav, media_type="audio/wav")


@app.post("/inference_clone")
async def inference_clone(
    tts_text: str = Form(...),
    ref_wav: UploadFile = File(None),
):
    if bm_model is None:
        raise HTTPException(503, "模型尚未載入")
    try:
        if ref_wav is not None:
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
    return Response(content=to_wav_bytes(audio, bm_model.sample_rate, tts_text), media_type="audio/wav")


def _synthesize(text: str):
    return _synthesize_with_centroid(text, speaker_centroid)


def _synthesize_with_centroid(text: str, centroid):
    import numpy as np
    n = count_cjk(text)
    cfg = 3.0 if n <= 25 else CFG_VALUE

    # 暖機：在前面加句號讓模型先穩定，生完後截掉前 0.3 秒的暖機音訊
    audio = bm_model.generate(
        target_text="。" + text,
        speaker_centroid=centroid,
        cfg_value=cfg,
        inference_timesteps=INFER_STEPS,
        max_len=2000,
        retry_badcase=True,
    )
    if hasattr(audio, "detach"):
        audio_np = audio.detach().cpu().numpy().squeeze()
    else:
        audio_np = np.array(audio).squeeze()

    # 截掉前 0.3 秒（暖機音）
    warmup_samples = int(bm_model.sample_rate * 0.3)
    if len(audio_np) > warmup_samples * 2:
        audio_np = audio_np[warmup_samples:]

    return audio_np


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_id",  default="OpenFormosa/BlueMagpie-TTS")
    parser.add_argument("--model_dir", default="/workspace/models/BlueMagpie-TTS")
    parser.add_argument("--port",      type=int, default=8080)
    parser.add_argument("--host",      default="0.0.0.0")
    args = parser.parse_args()

    bm_model, bm_tokenizer = load_model(args.model_id, args.model_dir)
    speaker_centroid = load_speaker_centroid(args.model_dir)

    uvicorn.run(app, host=args.host, port=args.port, timeout_keep_alive=300)
