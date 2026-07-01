FROM python:3.10-slim

WORKDIR /app

# 安裝必要的系統套件
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 複製並安裝套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製程式碼
COPY . .

# 保持容器運行
CMD ["python", "main.py"]