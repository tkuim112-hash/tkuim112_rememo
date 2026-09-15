# ============================================================
# Face Emotion Service — py-feat（FACS Action Unit 分析）
# 取代 Kinect 內建 Face API，供 app 服務的 /sensor/emotion 呼叫。
# ============================================================

# RTX 50 系列/Blackwell (sm_120) 需要 2.6+/cuda12.8 起才有對應的 CUDA kernel，
# 跟 bluemagpie/Dockerfile 用同一顆底層映像對齊。
FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update -y && apt-get install -y \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV PORT=8000
EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
