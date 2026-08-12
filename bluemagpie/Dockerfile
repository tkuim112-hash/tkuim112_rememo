# ============================================================
# BlueMagpie-TTS - Blackwell (RTX 50xx / sm_120) 專用 Dockerfile
# 台灣華語 + 中英混合 TTS，用 soundfile 不依賴 torchaudio
# ============================================================

FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-devel

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ── 系統套件 ────────────────────────────────────────────────
RUN apt-get update -y && apt-get install -y \
    git \
    build-essential \
    curl wget \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── PyTorch Nightly cu128 (Blackwell 支援) ──────────────────
# BlueMagpie 本身用 soundfile，不碰 torchaudio.load，所以不需要 patch
# 但還是要裝 nightly 確保 CUDA kernel 支援 sm_120
RUN pip install --upgrade pip && \
    pip install --pre torch \
        --index-url https://download.pytorch.org/whl/nightly/cu128 \
        --force-reinstall --no-cache-dir && \
    pip install --pre torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/nightly/cu128 \
        --force-reinstall --no-deps --no-cache-dir

# ── clone BlueMagpie-TTS（含相依的 Barbet） ────────────────
WORKDIR /workspace
RUN git clone https://github.com/OpenFormosa/BlueMagpie-TTS.git

# ── 安裝套件 ────────────────────────────────────────────────
WORKDIR /workspace/BlueMagpie-TTS
RUN pip install -e ".[train]" --no-cache-dir || \
    pip install -e . --no-cache-dir
RUN pip install soundfile huggingface_hub fastapi uvicorn python-multipart --no-cache-dir
RUN apt-get update -y && apt-get install -y rubberband-cli && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN pip install pyrubberband --no-cache-dir
# speechbrain：供 extract_speaker_centroid 使用（avg_centroid.py）
RUN pip install -e ".[clone]" --no-cache-dir || pip install speechbrain --no-cache-dir

# ── 複製 API server ─────────────────────────────────────────
COPY server.py /workspace/server.py

WORKDIR /workspace

ENV MODEL_ID="OpenFormosa/BlueMagpie-TTS"
ENV MODEL_LOCAL_DIR="/workspace/models/BlueMagpie-TTS"
ENV PORT=8080

EXPOSE 8080

CMD python server.py \
        --model_id   "${MODEL_ID}" \
        --model_dir  "${MODEL_LOCAL_DIR}" \
        --port       "${PORT}"
