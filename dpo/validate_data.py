#!/usr/bin/env python3
"""
DPO 訓練資料驗證腳本
在 train_dpo.py 訓練前執行，確認 train.jsonl 品質。

執行：
  python dpo/validate_data.py
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"

# ── 解析工具 ──────────────────────────────────────────────────────────────────

def extract_question_a(content: str) -> str | None:
    """Track A：從「問題：」行取出問題文字。"""
    for line in content.split("\n"):
        if line.strip().startswith("問題："):
            return line.strip()[3:].strip()
    return None

def extract_question_c(content: str) -> str | None:
    """Track C：從「問題：」行取出問題文字。"""
    return extract_question_a(content)

def extract_followup_b(content: str) -> str | None:
    """Track B：取出「後續引導：」的文字（去掉 Markdown bold 標記）。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        clean = line.strip().lstrip("*").rstrip("*").strip()
        if clean.startswith("後續引導："):
            # 後續引導可能在同行或下一行
            inline = clean[6:].strip()
            if inline:
                return inline
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return None

def extract_ack_b(content: str) -> str | None:
    """Track B：取出「情緒回應：」區段是否存在。"""
    for line in content.split("\n"):
        clean = line.strip().lstrip("*").rstrip("*").strip()
        if clean.startswith("情緒回應："):
            return clean[6:].strip()
    return None

def extract_ack_c(content: str) -> str | None:
    """Track C：取出「承接語：」是否存在。"""
    for line in content.split("\n"):
        if line.strip().startswith("承接語："):
            return line.strip()[4:].strip()
    return None

def extract_closing_text(content: str) -> str | None:
    """Track D：取出「收尾語：」行。"""
    for line in content.split("\n"):
        if line.strip().startswith("收尾語："):
            return line.strip()[4:].strip()
    return None

def extract_question_d(content: str) -> str | None:
    """Track D：從「問題：」行取出問題文字（格式同 Track A）。"""
    return extract_question_a(content)

def extract_scene_elements(prompt: list[dict]) -> list[str]:
    user_msg = next((m["content"] for m in prompt if m["role"] == "user"), "")
    match = re.search(r"【眼前畫面元素】\n(.+)", user_msg)
    if not match:
        return []
    return [e.strip() for e in match.group(1).split("、")]

def cjk_len(s: str) -> int:
    """中文字數（CJK 字元，不計標點）。"""
    return sum(1 for c in s if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")

def is_yesno(q: str) -> bool:
    # 結尾的 嗎？ or 句中有 是不是/有沒有/對不對
    return (bool(re.search(r"嗎[？?]?\s*$", q)) or
            bool(re.search(r"(是不是|有沒有|對不對|好不好)", q)))

def is_memory_test(q: str) -> bool:
    return bool(re.search(r"(你|您)(還)?記(得|不記得)", q))

def has_double_question(q: str) -> bool:
    return (q.count("？") + q.count("?")) >= 2

def has_anchor(q: str, elements: list[str]) -> bool:
    """問題前 10 字是否含有任一場景元素的錨點字符。

    兩層檢查（任一通過即可）：
    1. 嚴格：元素前 2 字為前綴子字串（原邏輯，窗口從 6 擴至 10）
    2. 放寬：元素字符出現在前綴中的數量 ≥ threshold
       - 短元素（≤2字）：至少 1 個字符（處理「老街→那條街」這類同義詞）
       - 長元素（>2字）：至少 2 個字符（避免單字誤判，如「工」誤配「下班工人」）
    """
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

def asks_why(q: str) -> bool:
    return "為什麼" in q or "為何" in q

# ── 主驗證邏輯 ────────────────────────────────────────────────────────────────

class Validator:
    def __init__(self):
        self.failures: dict[str, list[dict]] = defaultdict(list)
        self.total = 0
        self.track_counts: dict[str, int] = defaultdict(int)

    def _fail(self, key: str, info: dict):
        self.failures[key].append(info)

    def check(self, obj: dict, lineno: int):
        self.total += 1
        meta = obj["meta"]
        track = meta["track"]
        step = meta["step"]
        rule = meta["rejection_rule"]
        sid = meta["scenario_id"]
        self.track_counts[track] += 1

        chosen = obj["chosen"][0]["content"]
        rejected = obj["rejected"][0]["content"]
        prompt = obj["prompt"]
        info_base = {"line": lineno, "sid": sid, "step": step, "rule": rule}

        # ── 全軌跡通用：chosen 與 rejected 不得相同 ─────────────────────
        if chosen == rejected:
            self._fail("chosen==rejected", info_base)

        # ── Track A：問題品質 ────────────────────────────────────────────
        if track == "A":
            elements = extract_scene_elements(prompt)

            chosen_q = extract_question_a(chosen)
            if chosen_q is None:
                self._fail("chosen:missing_question_line", info_base)
                return

            # Chosen 品質檢查
            if cjk_len(chosen_q) > 20:
                self._fail("chosen:too_long",
                           {**info_base, "q": chosen_q, "cjk": cjk_len(chosen_q)})

            if is_yesno(chosen_q):
                self._fail("chosen:is_yesno", {**info_base, "q": chosen_q})

            if is_memory_test(chosen_q):
                self._fail("chosen:memory_test", {**info_base, "q": chosen_q})

            if has_double_question(chosen_q):
                self._fail("chosen:double_question", {**info_base, "q": chosen_q})

            if not has_anchor(chosen_q, elements):
                self._fail("chosen:no_anchor",
                           {**info_base, "q": chosen_q, "elements": elements})

            if step == "STEP1" and asks_why(chosen_q):
                self._fail("chosen:step1_asks_why", {**info_base, "q": chosen_q})

            # Rejected 確實違反規則
            rejected_q = extract_question_a(rejected)
            if rejected_q is None:
                return

            if rule == "is_yesno" and not is_yesno(rejected_q):
                self._fail("rejected:is_yesno_not_violated",
                           {**info_base, "q": rejected_q})

            if rule == "double_question" and not has_double_question(rejected_q):
                self._fail("rejected:double_q_not_violated",
                           {**info_base, "q": rejected_q})

            if rule == "too_long" and cjk_len(rejected_q) <= cjk_len(chosen_q):
                self._fail("rejected:too_long_not_violated",
                           {**info_base, "q": rejected_q,
                            "chosen_cjk": cjk_len(chosen_q),
                            "rejected_cjk": cjk_len(rejected_q)})

            if rule == "memory_test" and not is_memory_test(rejected_q):
                self._fail("rejected:memory_test_not_violated",
                           {**info_base, "q": rejected_q})

            if rule == "no_anchor" and has_anchor(rejected_q, elements):
                self._fail("rejected:no_anchor_not_violated",
                           {**info_base, "q": rejected_q, "elements": elements})

            if rule == "wrong_w_priority" and not asks_why(rejected_q):
                self._fail("rejected:wrong_w_not_violated",
                           {**info_base, "q": rejected_q})

        # ── Track B：情緒回應結構 ────────────────────────────────────────
        elif track == "B":
            if extract_ack_b(chosen) is None:
                self._fail("chosen_b:missing_ack", info_base)

            followup = extract_followup_b(chosen)
            if followup is None:
                self._fail("chosen_b:missing_followup", info_base)
            elif "？" not in followup and "?" not in followup:
                self._fail("chosen_b:followup_not_question",
                           {**info_base, "followup": followup})

        # ── Track C：承接 + 問題結構 ────────────────────────────────────
        elif track == "C":
            elements = extract_scene_elements(prompt)

            if extract_ack_c(chosen) is None:
                self._fail("chosen_c:missing_ack", info_base)

            chosen_q = extract_question_c(chosen)
            if chosen_q is None:
                self._fail("chosen_c:missing_question_line", info_base)
            else:
                if cjk_len(chosen_q) > 20:
                    self._fail("chosen_c:too_long",
                               {**info_base, "q": chosen_q, "cjk": cjk_len(chosen_q)})
                if not has_anchor(chosen_q, elements):
                    self._fail("chosen_c:no_anchor",
                               {**info_base, "q": chosen_q, "elements": elements})

        # ── Track D：收尾引導結構 ────────────────────────────────────
        elif track == "D":
            closing = extract_closing_text(chosen)
            if closing is None:
                self._fail("chosen_d:missing_closing_text", info_base)

            chosen_q = extract_question_d(chosen)
            if chosen_q is None:
                self._fail("chosen_d:missing_question_line", info_base)
            else:
                if cjk_len(chosen_q) > 20:
                    self._fail("chosen_d:question_too_long",
                               {**info_base, "q": chosen_q, "cjk": cjk_len(chosen_q)})
                if is_yesno(chosen_q):
                    self._fail("chosen_d:is_yesno", {**info_base, "q": chosen_q})

            # Rejected 規則驗證（可程式化判斷的三種）
            if rule == "abrupt_end":
                if extract_closing_text(rejected) is not None:
                    self._fail("rejected_d:abrupt_end_not_violated", info_base)

            if rule == "skip_feeling":
                rejected_q = extract_question_d(rejected)
                has_q = rejected_q is not None and ("？" in rejected_q or "?" in rejected_q)
                if has_q:
                    self._fail("rejected_d:skip_feeling_not_violated",
                               {**info_base, "q": rejected_q})

            if rule == "over_long_closing":
                rejected_closing = extract_closing_text(rejected)
                if closing is not None and rejected_closing is not None:
                    if cjk_len(rejected_closing) <= cjk_len(closing):
                        self._fail("rejected_d:over_long_not_violated",
                                   {**info_base,
                                    "chosen_cjk": cjk_len(closing),
                                    "rejected_cjk": cjk_len(rejected_closing)})

    # ── 報告 ─────────────────────────────────────────────────────────────────

    def report(self) -> bool:
        W = 62
        print("=" * W)
        print("  DPO 訓練資料驗證報告")
        print("=" * W)
        print(f"  資料檔：{DATA_FILE}")
        print(f"  總計：{self.total} 筆  "
              f"（A:{self.track_counts['A']}  B:{self.track_counts['B']}  "
              f"C:{self.track_counts['C']}  D:{self.track_counts['D']}）")
        print()

        any_fail = False

        # ── 關鍵檢查（必須全部通過才能訓練）─────────────────────────────
        print("【關鍵檢查】（任何失敗都應在訓練前修正）")
        critical = [
            ("chosen==rejected",           "Chosen 與 Rejected 完全相同"),
            ("chosen:missing_question_line","Track A Chosen 缺少「問題：」行"),
            ("chosen:too_long",            "Track A Chosen 問題 > 20 CJK 字"),
            ("chosen:is_yesno",            "Track A Chosen 問題是是非題"),
            ("chosen:memory_test",         "Track A Chosen 含「你還記得」"),
            ("chosen:double_question",     "Track A Chosen 包含兩個問號"),
            ("chosen:no_anchor",           "Track A Chosen 問題無場景錨點"),
            ("chosen:step1_asks_why",      "Track A STEP1 Chosen 問 Why"),
            ("chosen_b:missing_ack",       "Track B Chosen 缺少情緒回應"),
            ("chosen_b:followup_not_question", "Track B 後續引導不是問句"),
            ("chosen_c:missing_ack",       "Track C Chosen 缺少承接語"),
            ("chosen_c:missing_question_line", "Track C Chosen 缺少「問題：」行"),
            ("chosen_c:no_anchor",         "Track C Chosen 問題無場景錨點"),
            ("chosen_d:missing_closing_text",  "Track D Chosen 缺少「收尾語：」行"),
            ("chosen_d:missing_question_line", "Track D Chosen 缺少「問題：」行"),
            ("chosen_d:question_too_long",     "Track D Chosen 問題 > 20 CJK 字"),
            ("chosen_d:is_yesno",              "Track D Chosen 問題是是非題"),
        ]
        for key, desc in critical:
            items = self.failures.get(key, [])
            n = len(items)
            status = "✗ FAIL" if n else "✓ PASS"
            if n:
                any_fail = True
            print(f"  {status}  {desc}：{n} 筆")
            for item in items[:3]:
                q = item.get("q", "")
                extra = f"  cjk={item['cjk']}" if "cjk" in item else ""
                print(f"         → {item['sid']} {item['step']} "
                      f"rule={item['rule']}{extra}: {q}")
            if n > 3:
                print(f"         （...共 {n} 筆）")

        # ── 規則驗證（Rejected 確實違反規則）────────────────────────────
        print()
        print("【Rejected 規則違反驗證】（確認負面範例確實有違規）")
        rule_checks = [
            ("rejected:is_yesno_not_violated",    "is_yesno   — rejected 不是是非題"),
            ("rejected:double_q_not_violated",    "double_q   — rejected 只有一個問號"),
            ("rejected:too_long_not_violated",    "too_long   — rejected ≤ chosen 字數"),
            ("rejected:memory_test_not_violated", "memory_test— rejected 無「你還記得」"),
            ("rejected:no_anchor_not_violated",   "no_anchor  — rejected 仍有錨點"),
            ("rejected:wrong_w_not_violated",     "wrong_w    — rejected 沒有問 Why"),
            ("rejected_d:abrupt_end_not_violated",    "abrupt_end — rejected 仍有收尾語"),
            ("rejected_d:skip_feeling_not_violated",  "skip_feeling— rejected 仍有問題"),
            ("rejected_d:over_long_not_violated",     "over_long  — rejected 收尾語未變長"),
        ]
        rule_fail = False
        for key, desc in rule_checks:
            items = self.failures.get(key, [])
            n = len(items)
            status = "✗" if n else "✓"
            if n:
                rule_fail = True
            print(f"  {status}  {desc}：{n} 筆")
            for item in items[:2]:
                q = item.get("q", "")
                extra = (f"  chosen={item.get('chosen_cjk','?')} "
                         f"rejected={item.get('rejected_cjk','?')}"
                         if "chosen_cjk" in item else "")
                print(f"         → {item['sid']} {item['step']}{extra}: {q}")

        # ── 總結 ─────────────────────────────────────────────────────────
        print()
        print("=" * W)
        if any_fail:
            print("  ⚠  有關鍵失敗，請修正後再執行 train_dpo.py")
        elif rule_fail:
            print("  ⚡  關鍵檢查通過，但部分 rejected 違規不夠明顯")
            print("     建議手動抽查後再訓練，或重新生成問題對")
        else:
            print("  ✅  全部通過，可以執行 train_dpo.py")
        print("=" * W)

        return not any_fail


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    if not DATA_FILE.exists():
        print(f"找不到 {DATA_FILE}")
        print("請先執行：python dpo/collect_data.py")
        sys.exit(1)

    v = Validator()
    with DATA_FILE.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                v.check(json.loads(raw), lineno)
            except json.JSONDecodeError as e:
                print(f"Line {lineno} JSON 解析失敗：{e}")

    passed = v.report()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
