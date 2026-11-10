#!/usr/bin/env bash
# ollama 容器啟動腳本：每次容器啟動（含 Docker 引擎重啟後被 restart: always 拉起來）
# 都自動把 LLM 載進 GPU，搭配 OLLAMA_KEEP_ALIVE=-1 常駐，避免第一位長輩要等 30-100 秒。
#
# num_ctx 必須跟 app 送出的一致（app/services/llm.py 帶的 settings.ollama_num_ctx），
# 不一致的話 ollama 會在第一個真正請求進來時用新的 context 重新載入，暖機等於白做。
# 這個 image 沒有 curl/wget，所以用 bash 的 /dev/tcp 直接送 HTTP 請求。

warmup() {
    if [ -z "$WARMUP_MODEL" ]; then
        echo "[warmup] 沒有設定 WARMUP_MODEL，略過 LLM 暖機"
        return
    fi
    until /bin/ollama list >/dev/null 2>&1; do sleep 1; done

    local body="{\"model\":\"${WARMUP_MODEL}\",\"prompt\":\"\",\"keep_alive\":-1,\"options\":{\"num_ctx\":${WARMUP_NUM_CTX:-24576}}}"
    echo "[warmup] 載入 ${WARMUP_MODEL}（num_ctx=${WARMUP_NUM_CTX:-24576}）..."
    if exec 3<>/dev/tcp/127.0.0.1/11434; then
        printf 'POST /api/generate HTTP/1.0\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s' \
            "${#body}" "$body" >&3
        local status
        read -r _ status _ <&3
        exec 3<&-
        if [ "$status" = "200" ]; then
            echo "[warmup] ✅ LLM 暖機完成"
        else
            echo "[warmup] ⚠️  LLM 暖機失敗（HTTP ${status}），第一個真正請求時才會載入"
        fi
    fi
}

warmup &
exec /bin/ollama serve
