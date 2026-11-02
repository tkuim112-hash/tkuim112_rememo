#!/usr/bin/env bash
# stt 容器啟動腳本：每次容器啟動都自動送一次轉錄請求，把 Whisper 權重載進 GPU。
# 這個 image 的 PRELOAD_MODELS 只會註冊模型名稱、不會真的載入權重，所以只能靠實際
# 打一次轉錄；搭配 WHISPER__TTL=-1 常駐。app 啟動時的 _warmup_stt（app/main.py）
# 只涵蓋 app 一起重啟的情況，stt 自己單獨重啟時靠這裡補上。

warmup() {
    python3 - <<'PY'
import io, json, os, time, urllib.request, uuid, wave

base = f"http://127.0.0.1:{os.environ.get('UVICORN_PORT', '8000')}"
model = os.environ.get("WARMUP_MODEL") or os.environ.get("WHISPER__MODEL", "")

for _ in range(300):  # 最多等 5 分鐘讓 server 起來
    try:
        urllib.request.urlopen(f"{base}/health", timeout=2)
        break
    except Exception:
        time.sleep(1)

buf = io.BytesIO()
with wave.open(buf, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(16000)
    wf.writeframes(b"\x00\x00" * 16000)

boundary = uuid.uuid4().hex
parts = [
    f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'.encode(),
    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="warmup.wav"\r\n'
    f"Content-Type: audio/wav\r\n\r\n".encode() + buf.getvalue() + b"\r\n",
    f"--{boundary}--\r\n".encode(),
]
req = urllib.request.Request(
    f"{base}/v1/audio/transcriptions",
    data=b"".join(parts),
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
print(f"[warmup] 載入 STT 模型 {model} ...", flush=True)
try:
    urllib.request.urlopen(req, timeout=600)
    print("[warmup] ✅ STT 暖機完成", flush=True)
except Exception as e:
    print(f"[warmup] ⚠️  STT 暖機失敗，第一次真正轉錄時才會載入: {e}", flush=True)
PY
}

warmup &
exec uv run uvicorn --factory faster_whisper_server.main:create_app
