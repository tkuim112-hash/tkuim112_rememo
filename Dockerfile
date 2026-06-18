FROM python:3.10-slim

WORKDIR /app

# 安裝套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 把 app/ 內容倒進 /app
COPY app/ .

# 開放 port
EXPOSE 8000

# 用 uvicorn 啟動 FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
