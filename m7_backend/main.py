from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import redis
import psycopg2
from psycopg2.extras import RealDictCursor
import json

app = FastAPI(title="記憶拼圖劇場 - M7(Port 8080)")

# 連線到redis
try:
    r = redis.Redis(host='redis', port=6379, decode_responses=True)
except Exception as e:
    print(f"Redis 連線警告: {e}")

# 連線到 'db'
def get_db_connection():
    return psycopg2.connect(
        host="db",
        database="m6_db",
        user="user",
        password="password"
    )

# --- 測試用 API ---
@app.get("/")
def health_check():
    return {
        "service": "記憶拼圖劇場 - M7 系統編排器",
        "status": "online",
        "port": 8080
    }

@app.get("/test-db")
def test_db():
    """測試 PostgreSQL 是否連線成功"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT version();")
        db_version = cur.fetchone()
        cur.close()
        conn.close()
        return {"status": "success", "db_version": db_version[0]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PostgreSQL 連線失敗: {str(e)}")

@app.get("/test-redis")
def test_redis():
    """測試 Redis 是否連線成功"""
    try:
        r.set("m7_test_key", "Hello from M7 backend!")
        value = r.get("m7_test_key")
        return {"status": "success", "redis_value": value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Redis 連線失敗: {str(e)}")