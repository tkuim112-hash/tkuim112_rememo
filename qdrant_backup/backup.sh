#!/bin/sh
# 定期把 Qdrant 向量資料庫做 snapshot 備份，存到跟 qdrant_storage 分開的獨立資料夾。
# 邏輯：呼叫 Qdrant snapshot API 產生一致性快照 -> 下載到容器內暫存(不落地到掛載的 /backups)
#      -> 加密寫進 /backups -> 刪掉暫存與 Qdrant 端快照 -> 依天數輪替本機備份。
# 隱私要求：長者對話內容/情緒/session 都是個資，備份只留本機、不同步異地或雲端，
# 且落地前一定要加密，沒設 BACKUP_ENCRYPTION_PASSPHRASE 就直接失敗，不會偷偷存明文。
set -eu

QDRANT_URL="${QDRANT_URL:-http://qdrant:6333}"
QDRANT_COLLECTION="${QDRANT_COLLECTION:-safe_reminiscence}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
BACKUP_DIR="/backups"
TMP_DIR="/tmp/qdrant-backup"

if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
    echo "缺少 BACKUP_ENCRYPTION_PASSPHRASE，拒絕啟動（避免備份用明文落地個資）" >&2
    exit 1
fi

auth_header() {
    if [ -n "${QDRANT_API_KEY:-}" ]; then
        echo "api-key: ${QDRANT_API_KEY}"
    fi
}

do_backup() {
    ts="$(date +%Y%m%d-%H%M%S)"
    echo "[$ts] 開始備份 collection=${QDRANT_COLLECTION}"

    create_resp="$(curl -sS -X POST \
        -H "$(auth_header)" \
        "${QDRANT_URL}/collections/${QDRANT_COLLECTION}/snapshots?wait=true")"

    snapshot_name="$(echo "$create_resp" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p')"
    if [ -z "$snapshot_name" ]; then
        echo "[$ts] 建立 snapshot 失敗，回應內容：$create_resp" >&2
        return 1
    fi

    tmp_raw="${TMP_DIR}/${QDRANT_COLLECTION}-${ts}.raw"
    trap 'rm -f "$tmp_raw"' EXIT INT TERM

    curl -sS -H "$(auth_header)" \
        "${QDRANT_URL}/collections/${QDRANT_COLLECTION}/snapshots/${snapshot_name}" \
        -o "$tmp_raw"

    dest="${BACKUP_DIR}/${QDRANT_COLLECTION}-${ts}.snapshot.enc"
    openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt \
        -pass "pass:${BACKUP_ENCRYPTION_PASSPHRASE}" \
        -in "$tmp_raw" -out "$dest"
    rm -f "$tmp_raw"
    trap - EXIT INT TERM
    echo "[$ts] 已加密備份至 ${dest}"

    # 下載完就把 Qdrant 節點上的那份刪掉，本機加密檔才是唯一保留位置
    curl -sS -X DELETE -H "$(auth_header)" \
        "${QDRANT_URL}/collections/${QDRANT_COLLECTION}/snapshots/${snapshot_name}" > /dev/null

    find "$BACKUP_DIR" -name "${QDRANT_COLLECTION}-*.snapshot.enc" -mtime "+${BACKUP_KEEP_DAYS}" -delete
    echo "[$ts] 備份完成，保留天數=${BACKUP_KEEP_DAYS}"
}

mkdir -p "$BACKUP_DIR" "$TMP_DIR"

while true; do
    do_backup || echo "備份失敗，等下一輪重試"
    sleep 86400
done
