# ============================================================
# CosyVoice 2 - Blackwell (RTX 50xx / sm_120) 專用 Dockerfile
# 參考 breezyvoice/Dockerfile 的 PyTorch Nightly cu128 做法
# ============================================================

# 用跟 BreezyVoice 一樣的基底，cu128 才有 Blackwell CUDA kernel
FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-devel

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ── 系統套件 ────────────────────────────────────────────────
RUN apt-get update -y && apt-get install -y \
    git git-lfs \
    build-essential \
    curl wget \
    ffmpeg \
    sox libsox-dev \
    unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

# ── PyTorch Nightly cu128 (Blackwell 支援) ──────────────────
# 官方 requirements.txt 用的是 torch==2.3.1+cu121，RTX 5060 跑不起來
# 改裝 nightly，跟組長的 BreezyVoice 做法相同
RUN pip install --upgrade pip

# ── clone CosyVoice（含 submodule Matcha-TTS）──────────────
WORKDIR /workspace
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git

# ── Python 相依套件（先裝，torch nightly 最後覆蓋）─────────
COPY requirements_blackwell.txt /workspace/requirements_blackwell.txt
# openai-whisper 需要 setuptools + --no-build-isolation
RUN pip install setuptools --no-cache-dir && \
    pip install openai-whisper==20231117 --no-build-isolation --no-cache-dir
RUN pip install -r /workspace/requirements_blackwell.txt --no-cache-dir

# ── PyTorch Nightly cu128 最後裝，覆蓋 requirements 拉回的舊版 ──
# torch 先裝；torchvision/torchaudio 用 --no-deps 跳過版本互查
RUN pip install --pre torch \
        --index-url https://download.pytorch.org/whl/nightly/cu128 \
        --force-reinstall --no-cache-dir && \
    pip install --pre torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/nightly/cu128 \
        --force-reinstall --no-deps --no-cache-dir

# ── pynini（WeTextProcessing 需要，conda 版改用 pip wheel）──
# 官方建議用 conda，但我們不用 conda，改裝 pynini binary wheel
RUN pip install pynini==2.1.6.post1 --no-cache-dir || \
    pip install pynini --no-cache-dir || \
    echo "[WARN] pynini 安裝失敗，WeTextProcessing 將 fallback 到 wetext"

# ── torchaudio soundfile patch ───────────────────────────────
# Blackwell 上 torchaudio.load 會觸發 torchcodec 報錯，改用 soundfile 讀檔
RUN pip install soundfile --no-cache-dir

# 把 CosyVoice 原始碼裡的 torchaudio.load 呼叫改成 soundfile
# 做法參考 FishSpeech-Blackwell Step 6
COPY patch_torchaudio.py /workspace/patch_torchaudio.py
RUN python3 /workspace/patch_torchaudio.py

# ── PYTHONPATH 設定 ──────────────────────────────────────────
ENV PYTHONPATH="/workspace/CosyVoice:/workspace/CosyVoice/third_party/Matcha-TTS:${PYTHONPATH}"

# ── 複製自訂的 API server ────────────────────────────────────
COPY server.py /workspace/server.py

WORKDIR /workspace

# 容器啟動時下載模型並啟動 FastAPI
# MODEL_PATH 可透過 docker-compose environment 覆寫
ENV MODEL_PATH="FunAudioLLM/CosyVoice2-0.5B"
ENV MODEL_LOCAL_DIR="/workspace/models/CosyVoice2-0.5B"
ENV PORT=8080

EXPOSE 8080

CMD python server.py \
        --model_path "${MODEL_PATH}" \
        --model_dir  "${MODEL_LOCAL_DIR}" \
        --port       "${PORT}"