FROM python:3.10-slim

WORKDIR /app

# 安裝必要的系統套件
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 複製並安裝套件
COPY requirements.txt .
# 這個容器沒有 GPU（CKIP 跟 jina-embeddings-v3 都跑 CPU），但 PyPI 預設的
# torch wheel 會一起拉一堆 CUDA 相關套件（nvidia-cudnn-cu13 等，動輒數百MB），
# 完全沒用到卻要多下載好幾GB，所以先從官方 CPU-only index 單獨裝 torch，
# 下面 requirements.txt 裡的 torch 看到已安裝版本符合就不會再重裝。
RUN pip install --no-cache-dir torch --extra-index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# 複製程式碼
COPY . .

# 保持容器運行
CMD ["python", "main.py"]