"""
BlueMagpie-TTS FastAPI Server v1.6.0
- POST /v1/audio/speech  - 相容 OpenAI TTS 格式
- POST /inference_clone  - 上傳參考音檔即時合成
- GET  /health

v1.6.0 新增：
- WSOLA 語速校正（目標 4.0 字/秒）
- 語意切句（>48字才切，每 chunk 最多 24 字）
- 雙值 CFG 交錯（短句 3.0，長句 CFG_VALUE）
- 開頭句號前綴，避免起始雜音
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
    "SPEAKER_CENTROID_PT", "/workspace/references/Peichi_avg_centroid.pt"
)
CFG_VALUE    = float(os.environ.get("CFG_VALUE", "2.5"))
INFER_STEPS  = int(os.environ.get("INFER_STEPS", "10"))
TARGET_RATE  = float(os.environ.get("TARGET_RATE", "4.0"))   # 目標語速 字/秒
CHUNK_UNITS  = int(os.environ.get("CHUNK_UNITS", "24"))       # 語意 chunk 上限字數
SPLIT_ABOVE  = int(os.environ.get("SPLIT_ABOVE", "48"))       # 超過幾字才切句


# ============================================================
# 模型載入
# ============================================================

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
        target = "female_voice" if "female_voice" in speaker_ids else "hung_yi_lee"
        vec = centroids["centroids"][speaker_ids.index(target)]
        logger.info(f"載入內建語者向量 ({target})")
        return vec

    logger.warning("找不到語者向量，將用無音色控制模式")
    return None


# ============================================================
# 文字處理工具
# ============================================================

def count_cjk(text: str) -> int:
    """計算 CJK 字元數（不含標點和空白）"""
    return len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\uff21-\uff3a\uff41-\uff5a]', text))


def semantic_split(text: str, max_units: int = 24) -> list:
    """
    語意切句：超過 SPLIT_ABOVE 字才切
    依句末標點切成語意完整的 chunk，每 chunk 最多 max_units 字
    """
    total = count_cjk(text)
    if total <= SPLIT_ABOVE:
        return [text]

    # 先依句末標點切
    raw = re.split(r'(?<=[。！？\n])', text)
    chunks = []
    buf = ""
    for part in raw:
        if not part.strip():
            continue
        if count_cjk(buf + part) <= max_units:
            buf += part
        else:
            if buf:
                chunks.append(buf.strip())
            # 若單句還是太長，再依逗號切
            if count_cjk(part) > max_units:
                sub_parts = re.split(r'(?<=[，,；])', part)
                sub_buf = ""
                for sp in sub_parts:
                    if count_cjk(sub_buf + sp) <= max_units:
                        sub_buf += sp
                    else:
                        if sub_buf:
                            chunks.append(sub_buf.strip())
                        sub_buf = sp
                if sub_buf:
                    buf = sub_buf
                else:
                    buf = ""
            else:
                buf = part
    if buf.strip():
        chunks.append(buf.strip())

    return chunks if chunks else [text]


# ============================================================
# 音訊工具
# ============================================================

def wsola_adjust(audio: np.ndarray, sample_rate: int, n_chars: int) -> np.ndarray:
    """
    WSOLA 語速校正：把音訊調整到目標語速 TARGET_RATE 字/秒
    只在語速偏差超過 20% 時才調整，限制 rate 在 0.85x～1.3x
    """
    import librosa
    duration = len(audio) / sample_rate
    if duration < 0.5 or n_chars == 0:
        return audio

    current_rate = n_chars / duration
    if abs(current_rate - TARGET_RATE) / TARGET_RATE < 0.2:
        # 偏差在 20% 以內，不調整
        return audio

    stretch_rate = current_rate / TARGET_RATE
    stretch_rate = max(0.85, min(1.3, stretch_rate))
    logger.info(f"WSOLA: {current_rate:.2f} 字/秒 -> {TARGET_RATE:.2f} 字/秒 (rate={stretch_rate:.2f}x)")
    return librosa.effects.time_stretch(audio.astype(np.float32), rate=stretch_rate)


def to_wav_bytes(audio, sample_rate: int, text: str = "") -> bytes:
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    audio = audio.squeeze().astype(np.float32)

    # WSOLA 語速校正
    if text:
        n = count_cjk(text)
        if n > 0:
            audio = wsola_adjust(audio, sample_rate, n)

    audio = np.clip(audio, -1.0, 1.0)
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


def merge_audio(chunks: list, sample_rate: int) -> np.ndarray:
    """合併多個音訊 chunk，中間加 0.15 秒靜音"""
    silence = np.zeros(int(sample_rate * 0.15), dtype=np.float32)
    merged = []
    for i, chunk in enumerate(chunks):
        if hasattr(chunk, "detach"):
            chunk = chunk.detach().cpu().numpy()
        merged.append(chunk.squeeze().astype(np.float32))
        if i < len(chunks) - 1:
            merged.append(silence)
    return np.concatenate(merged)


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(title="BlueMagpie-TTS Service", version="1.6.0")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": bm_model is not None,
        "speaker_centroid_loaded": speaker_centroid is not None,
        "cfg_value": CFG_VALUE,
        "infer_steps": INFER_STEPS,
        "target_rate": TARGET_RATE,
        "chunk_units": CHUNK_UNITS,
        "split_above": SPLIT_ABOVE,
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


# ── 內部合成 ─────────────────────────────────────────────────

def _synthesize(text: str):
    return _synthesize_with_centroid(text, speaker_centroid)


def _synthesize_with_centroid(text: str, centroid):
    # 雙值 CFG：短句 3.0，長句用環境變數的值
    n = count_cjk(text)
    cfg = 3.0 if n <= 25 else CFG_VALUE
    return bm_model.generate(
        target_text="。" + text,
        speaker_centroid=centroid,
        cfg_value=cfg,
        inference_timesteps=INFER_STEPS,
        max_len=2000,
        retry_badcase=True,
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
    speaker_centroid = load_speaker_centroid(args.model_dir)

    uvicorn.run(app, host=args.host, port=args.port, timeout_keep_alive=300)
