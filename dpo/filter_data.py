#!/usr/bin/env python3
"""
DPO 訓練資料過濾腳本

移除 chosen 回應明顯違規的 pair（is_yesno、memory_test、double_question、
no_anchor、chosen_equals_rejected、chosen_touches_taboo）。
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


def extract_lead_in(content: str) -> str:
    """取出同一個 chosen 裡「場景文字：」或「承接語：」這一行的內容，當作額外的
    錨點比對來源——這段文字本身就是根據畫面元素/長者話語生成的，今天新增的
    「動作優先」「準備動作」等規則會鼓勵問題從這段文字描述的動作延伸（例如
    「一路走回家，你都在想什麼呢」），不會逐字重複 elements 清單裡的名詞，
    只比對 elements 太嚴格，需要放寬到也比對這段文字。"""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("場景文字："):
            return line[len("場景文字："):].strip()
        if line.startswith("承接語："):
            return line[len("承接語："):].strip()
    return ""


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


_MECHANICAL_ACTION_RE = re.compile(r"怎麼(走|湊|挪|移動)(過去|過來)?(的)?呢?[？?]?\s*$")


def is_mechanical_action(q: str) -> bool:
    """問法只剩「怎麼+空洞動作動詞」，沒有具體受詞或情境，答案通常只有一個動作詞
    （例：「你都怎麼走呢？」「都怎麼湊過來呢？」），跟「長耙你都怎麼用呢？」這種
    有意義動詞的問法不同，只抓固定句型，換句話說的版本抓不到，需搭配人工複查。"""
    return bool(_MECHANICAL_ACTION_RE.search(q))


def has_anchor(q: str, elements: list[str], lead_in: str = "") -> bool:
    """與 validate_data.py 的邏輯保持一致。

    elements 比對太嚴格會誤殺今天新增規則鼓勵的「動作/狀態延伸」問法（例如
    「一路走回家，你都在想什麼呢」不會逐字重複 elements 清單裡的名詞）——
    多一層比對 lead_in（同一個 chosen 裡的場景文字／承接語），用連續2字的
    詞組重疊來判斷問題是否真的承接自這段文字，比逐字元重疊更準確。
    """
    if not elements and not lead_in:
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
    if lead_in:
        for i in range(len(lead_in) - 1):
            bigram = lead_in[i:i + 2]
            if bigram in prefix:
                return True
    return False


def uses_polite_nin(text: str) -> bool:
    """稱呼一律用「你」，「您」念起來太正式，會破壞老朋友聊天的溫暖感。
    邏輯與 dpo/evaluate_model.py 一致，跨全部 track 都適用（chosen 之前只在
    Track A/C 檢查是非題/錨點等問題品質規則，沒檢查過「您」跟 markdown——
    2026-07 用 test_claude_sample.py 小規模試跑時，實際抓到 Track B 的
    chosen 範例混進「您」，才發現這個漏洞）。"""
    return "您" in text


_MARKDOWN_LEAK_RE = re.compile(r"\*\*|##|`|^\s*[-*]\s", re.MULTILINE)


def has_markdown_leak(text: str) -> bool:
    """禁止任何 markdown 語法——這段文字會直接餵給 TTS 唸給長者聽。"""
    return bool(_MARKDOWN_LEAK_RE.search(text))


def chosen_touches_taboo(obj: dict) -> bool:
    """
    Layer 1 粗篩（比照 app/safety/taboo_checker.py 的 keyword_prescan）：
    純字面比對，chosen 是否直接包含這筆資料標記的禁忌詞子字串。
    抓不到「語意相關但沒用到禁忌詞字面」的情況，那類需要人工/LLM複查，
    這裡只當作最後一道零成本防呆，不是完整的語意檢查。
    """
    taboos = obj.get("meta", {}).get("taboos", [])
    if not taboos:
        return False
    chosen = obj["chosen"][0]["content"]
    return any(t in chosen for t in taboos)


def chosen_is_bad(obj: dict) -> tuple[bool, str]:
    """回傳 (應移除, 原因)。"""
    chosen = obj["chosen"][0]["content"]
    rejected = obj["rejected"][0]["content"]

    if chosen.strip() == rejected.strip():
        return True, "chosen_equals_rejected"

    if has_leaked_self_check(chosen):
        return True, "chosen_leaked_self_check"

    if chosen_touches_taboo(obj):
        return True, "chosen_touches_taboo"

    if uses_polite_nin(chosen):
        return True, "chosen_uses_nin"

    if has_markdown_leak(chosen):
        return True, "chosen_markdown_leak"

    track = obj["meta"]["track"]
    if track not in ("A", "C", "D"):
        return False, ""

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
        lead_in = extract_lead_in(chosen)
        if not has_anchor(q, elements, lead_in):
            return True, "no_anchor"
        if is_mechanical_action(q):
            return True, "mechanical_action"

    return False, ""


_DASH_RE = re.compile(r"^-{3,}\s*$", re.MULTILINE)
_SELF_CORRECT_RE = re.compile(r"等等[，,]|重新檢查這個|需要改|需要修正|違反第\s*\d+\s*條|修正如下")


def has_leaked_self_check(content: str) -> bool:
    """偵測模型把自我檢查/多輪草稿過程洩漏到正式輸出裡（例如用「---」分隔線
    寫出「先錯一版、自我檢查、再修正」的過程）。真正的洩漏會有「---」分隔線、
    明確的自我修正措辭，或同一個欄位重複出現兩次——只比對「規則\\d+」字面
    會誤判思考欄位裡合法提到規則編號的正常情況，所以不用那個當觸發條件。"""
    if _DASH_RE.search(content):
        return True
    if _SELF_CORRECT_RE.search(content):
        return True
    for label in ("問題：", "場景文字：", "承接語：", "收尾語："):
        count = sum(1 for line in content.split("\n") if line.strip().startswith(label))
        if count >= 2:
            return True
    return False


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
