#!/usr/bin/env python3
"""
DPO 訓練資料過濾腳本

移除 chosen 回應明顯違規的 pair（is_yesno、memory_test、double_question）。
這些 pair 的 chosen 本身就犯了 DPO 要教模型避免的行為，會讓訓練方向混亂。

執行：
  python dpo/filter_data.py

會在同目錄建立 train.jsonl.bak 備份，再寫回清理後的 train.jsonl。
"""

import json
import re
import sys
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"
BACKUP_FILE = Path(__file__).parent / "data" / "train.jsonl.bak"


def extract_question(content: str) -> str | None:
    for line in content.split("\n"):
        if line.strip().startswith("問題："):
            return line.strip()[3:].strip()
    return None


def extract_scene_elements(prompt: list[dict]) -> list[str]:
    user_msg = next((m["content"] for m in prompt if m["role"] == "user"), "")
    match = re.search(r"【眼前畫面元素】\n(.+)", user_msg)
    if not match:
        return []
    return [e.strip() for e in match.group(1).split("、")]


def is_yesno(q: str) -> bool:
    return (bool(re.search(r"嗎[？?]?\s*$", q)) or
            bool(re.search(r"(是不是|有沒有|對不對|好不好)", q)))


def is_memory_test(q: str) -> bool:
    return bool(re.search(r"(你|您)(還)?記(得|不記得)", q))


def has_double_question(q: str) -> bool:
    return (q.count("？") + q.count("?")) >= 2


def has_anchor(q: str, elements: list[str]) -> bool:
    """與 validate_data.py 的邏輯保持一致。"""
    if not elements:
        return True
    prefix = q[:10]
    for e in elements:
        if len(e) < 2:
            continue
        if e[:2] in prefix:
            return True
        threshold = 1 if len(e) <= 2 else 2
        overlap = sum(1 for c in set(e) if c in prefix)
        if overlap >= threshold:
            return True
    return False


def chosen_is_bad(obj: dict) -> tuple[bool, str]:
    """回傳 (應移除, 原因)。"""
    track = obj["meta"]["track"]
    if track not in ("A", "C", "D"):
        return False, ""

    chosen = obj["chosen"][0]["content"]
    q = extract_question(chosen)
    if q is None:
        return False, ""

    if is_yesno(q):
        return True, "is_yesno"
    if is_memory_test(q):
        return True, "memory_test"
    if has_double_question(q):
        return True, "double_question"

    if track in ("A", "C"):
        elements = extract_scene_elements(obj["prompt"])
        if not has_anchor(q, elements):
            return True, "no_anchor"

    return False, ""


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    if not DATA_FILE.exists():
        print(f"找不到 {DATA_FILE}")
        sys.exit(1)

    pairs = []
    with DATA_FILE.open(encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if raw:
                pairs.append(json.loads(raw))

    kept, removed_counts = [], {}
    for obj in pairs:
        bad, reason = chosen_is_bad(obj)
        if bad:
            removed_counts[reason] = removed_counts.get(reason, 0) + 1
        else:
            kept.append(obj)

    total_removed = sum(removed_counts.values())
    if total_removed == 0:
        print("沒有需要移除的 pair，train.jsonl 保持不變。")
        return

    # 備份
    BACKUP_FILE.write_bytes(DATA_FILE.read_bytes())
    print(f"備份已儲存：{BACKUP_FILE}")

    # 寫回
    with DATA_FILE.open("w", encoding="utf-8") as f:
        for obj in kept:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"\n移除明細：")
    for reason, n in removed_counts.items():
        print(f"  {reason}：{n} 筆")
    print(f"\n原始：{len(pairs)} 筆  →  清理後：{len(kept)} 筆（移除 {total_removed} 筆）")
    print(f"輸出：{DATA_FILE}")


if __name__ == "__main__":
    main()
