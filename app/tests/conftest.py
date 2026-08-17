"""
orchestrator.py 匯入時會連帶載入 config.settings（要求 JWT_SECRET／
SESSION_SECRET）跟 db.session（要求 DATABASE_URL），這幾個環境變數平常
由 docker-compose 的 env_file 注入，本機直接跑 pytest 不會有——這裡給
測試用的假值，讓 import 不會在還沒進到測試邏輯前就先炸掉。真正的密鑰
不會被這幾個假值覆蓋（setdefault 只在還沒設定時才生效）。
"""
import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://user:password@localhost:5432/test")
