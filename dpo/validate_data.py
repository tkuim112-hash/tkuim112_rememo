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

# 本機模型偶爾會把「標籤：」單獨放一行，真正的內容寫在下一行，不是接在冒號後面
# （例如「場景文字：\n許奶奶坐在布行櫃檯後方…」）。舊版 extract_* 只看標籤那一行
# 本身，遇到這種格式會抓到空字串，把模型真正生成的內容當成空白漏掉——這裡加上
# 「同一行沒內容就往下一行找」的容錯。只有下一行不是「另一個已知欄位標籤」時才
# 採用，避免把下一個欄位的內容誤當成這個欄位的答案。
_FIELD_PREFIXES = ("場景文字：", "問題：", "問題類型：", "本回合已涵蓋的W：",
                   "承接語：", "收尾語：", "情緒回應：", "後續引導：")

def _fallback_next_line(lines: list[str], i: int) -> str:
    for j in range(i + 1, len(lines)):
        candidate = lines[j].strip()
        if not candidate:
            continue
        if candidate.startswith(_FIELD_PREFIXES):
            return ""
        return candidate
    return ""

def extract_question_a(content: str) -> str | None:
    """Track A：從「問題：」行取出問題文字。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("問題："):
            inline = line.strip()[3:].strip()
            return inline if inline else _fallback_next_line(lines, i)
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
            inline = clean[6:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None

def extract_ack_b(content: str) -> str | None:
    """Track B：取出「情緒回應：」區段是否存在。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        clean = line.strip().lstrip("*").rstrip("*").strip()
        if clean.startswith("情緒回應："):
            inline = clean[6:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None

def extract_ack_c(content: str) -> str | None:
    """Track C：取出「承接語：」是否存在。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("承接語："):
            inline = line.strip()[4:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None

def extract_closing_text(content: str) -> str | None:
    """Track D：取出「收尾語：」行。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("收尾語："):
            inline = line.strip()[4:].strip()
            return inline if inline else _fallback_next_line(lines, i)
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

def extract_elder_name(prompt: list[dict]) -> str:
    """Track A：從 user prompt 的「姓名：」行取出長者姓名，供 elder_as_photo_subject 規則使用。"""
    user_msg = next((m["content"] for m in prompt if m["role"] == "user"), "")
    match = re.search(r"姓名：(.+)", user_msg)
    return match.group(1).strip() if match else ""

def extract_scene_text(content: str) -> str | None:
    """Track A：從「場景文字：」行取出場景描述文字。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("場景文字："):
            inline = line.strip()[5:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None

def treats_elder_as_photo_subject(scene_text: str, elder_name: str) -> bool:
    """
    場景文字要用第三人稱描述畫面本身（像描述一幅畫），不能寫成長者正站在畫面裡
    （AI 生成的示意圖，長者本人不是照片裡的主角）。邏輯與 evaluate_model.py 一致：
    長者名字被當成動作主詞（排除「王伯伯，你看」這種稱呼語開場），或用「你」寫成
    正身處畫面中做動作。
    """
    if elder_name and elder_name in scene_text:
        idx = scene_text.index(elder_name)
        after = scene_text[idx + len(elder_name):idx + len(elder_name) + 1]
        if after not in ("，", ",", "的"):
            return True
    return bool(re.search(r"你(正|現在)?.{0,4}(站在|坐在|待在|走在)", scene_text))

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

def uses_polite_nin(text: str) -> bool:
    """稱呼一律用「你」，「您」念起來太正式，會破壞老朋友聊天的溫暖感。
    邏輯與 dpo/evaluate_model.py 一致，跨全部 track 都適用——2026-07 用
    test_claude_sample.py 小規模試跑時，實際在 Track B 的 chosen 範例裡
    抓到「您」，證實這不是理論風險，過去 chosen 完全沒檢查過這條規則。"""
    return "您" in text


_MARKDOWN_LEAK_RE = re.compile(r"\*\*|##|`|^\s*[-*]\s", re.MULTILINE)


def has_markdown_leak(text: str) -> bool:
    """禁止任何 markdown 語法——這段文字會直接餵給 TTS 唸給長者聽。"""
    return bool(_MARKDOWN_LEAK_RE.search(text))


def content_touches_taboo(text: str, taboos: list[str]) -> bool:
    """Layer 1 粗篩（比照 app/safety/taboo_checker.py 的 keyword_prescan）：
    純字面比對，抓不到語意相關但沒用到禁忌詞字面的情況，僅供訓練前粗篩。"""
    return any(t in text for t in taboos)

_TEMPLATE_ECHO_RE = re.compile(r"^[（(].*[）)]$")

def is_template_echo(q: str) -> bool:
    """template_echo 規則：整句問題被單一括號包住，看起來是格式提示本身
    （例如「（≤15字，開放式，開頭要有畫面中的具體物件）」），不是真正的問題內容。
    邏輯與 dpo/evaluate_model.py 的 is_leaked_or_empty 一致（该处還多處理空字串，
    這裡只判斷「rejected 是否真的做出範本回聲」，空字串不算是這條規則的示範）。"""
    return bool(_TEMPLATE_ECHO_RE.match(q.strip()))

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
        taboos = meta.get("taboos", [])
        info_base = {"line": lineno, "sid": sid, "step": step, "rule": rule}

        # ── 全軌跡通用：chosen 與 rejected 不得相同 ─────────────────────
        if chosen == rejected:
            self._fail("chosen==rejected", info_base)

        # ── 全軌跡通用：chosen 不得字面觸及這筆資料標記的禁忌話題 ─────────
        if taboos and content_touches_taboo(chosen, taboos):
            self._fail("chosen:touches_taboo", {**info_base, "taboos": taboos})

        # ── 全軌跡通用：chosen 不得使用「您」或洩漏 markdown ──────────────
        if uses_polite_nin(chosen):
            self._fail("chosen:uses_nin", info_base)
        if has_markdown_leak(chosen):
            self._fail("chosen:markdown_leak", info_base)

        # ── touches_taboo/dwell_on_taboo 規則：確認 rejected 真的觸及禁忌 ──
        if rule in ("touches_taboo", "dwell_on_taboo") and taboos:
            if not content_touches_taboo(rejected, taboos):
                self._fail("rejected:touches_taboo_not_violated",
                           {**info_base, "taboos": taboos})

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

            elder_name = extract_elder_name(prompt)
            chosen_scene = extract_scene_text(chosen)
            if chosen_scene is not None and treats_elder_as_photo_subject(chosen_scene, elder_name):
                self._fail("chosen:elder_as_photo_subject",
                           {**info_base, "q": chosen_scene})

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

            if rule == "template_echo" and not is_template_echo(rejected_q):
                self._fail("rejected:template_echo_not_violated",
                           {**info_base, "q": rejected_q})

            if rule == "elder_as_photo_subject":
                rejected_scene = extract_scene_text(rejected)
                if rejected_scene is not None and not treats_elder_as_photo_subject(rejected_scene, elder_name):
                    self._fail("rejected:elder_as_photo_subject_not_violated",
                               {**info_base, "q": rejected_scene})

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

            rejected_q_c = extract_question_c(rejected)
            if rule == "template_echo" and rejected_q_c is not None and not is_template_echo(rejected_q_c):
                self._fail("rejected_c:template_echo_not_violated",
                           {**info_base, "q": rejected_q_c})

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
            ("chosen:touches_taboo",       "Chosen 字面觸及該筆資料的禁忌話題"),
            ("chosen:uses_nin",            "Chosen 誤用「您」（全軌跡皆須用「你」）"),
            ("chosen:markdown_leak",       "Chosen 洩漏 markdown 語法"),
            ("chosen:missing_question_line","Track A Chosen 缺少「問題：」行"),
            ("chosen:too_long",            "Track A Chosen 問題 > 20 CJK 字"),
            ("chosen:is_yesno",            "Track A Chosen 問題是是非題"),
            ("chosen:memory_test",         "Track A Chosen 含「你還記得」"),
            ("chosen:double_question",     "Track A Chosen 包含兩個問號"),
            ("chosen:no_anchor",           "Track A Chosen 問題無場景錨點"),
            ("chosen:step1_asks_why",      "Track A STEP1 Chosen 問 Why"),
            ("chosen:elder_as_photo_subject", "Track A Chosen 場景文字把長者寫成照片主角"),
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
            ("rejected:template_echo_not_violated", "template_echo — rejected 不是格式提示回聲"),
            ("rejected:elder_as_photo_subject_not_violated",
                                                  "elder_as_photo_subject — rejected 未把長者寫成照片主角"),
            ("rejected_d:abrupt_end_not_violated",    "abrupt_end — rejected 仍有收尾語"),
            ("rejected_d:skip_feeling_not_violated",  "skip_feeling— rejected 仍有問題"),
            ("rejected_d:over_long_not_violated",     "over_long  — rejected 收尾語未變長"),
            ("rejected:touches_taboo_not_violated",   "touches_taboo/dwell_on_taboo — rejected 未字面觸及禁忌"),
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
