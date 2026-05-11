import requests

# 你 Docker 映射的 Port 是 9233
base_url = "http://localhost:9233"
# 常見的 CosyVoice 門牌清單
endpoints = ["/", "/tts", "/api/tts", "/inference", "/v1/audio/speech", "/synthesize"]

print("--- 開始掃描 CosyVoice 正確路徑 ---")
for ep in endpoints:
    url = base_url + ep
    try:
        # 使用 GET 測試看看有沒有反應
        res = requests.get(url, timeout=2)
        print(f"測試 {url} -> 狀態碼: {res.status_code}")
    except:
        print(f"測試 {url} -> 連線失敗")

print("\n--- 提示 ---")
print("如果狀態碼出現 200 或 405 (Method Not Allowed)，代表那個就是正確的門牌！")
