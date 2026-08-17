#!/usr/bin/env python3
"""
DPO 訓練資料／模型品質工具（合併自 validate_data.py／evaluate_model.py／
filter_data.py／fix_xian_wording.py 四支腳本，2026-08 整合）。

背景：這四支腳本原本各自獨立，各自維護一份幾乎一樣的格式檢查函式
（is_yesno、has_anchor、extract_question 等），複製貼上維護，實際稽核時
抓到已經走鐘的案例（is_yesno 漏字、has_anchor 少了 lead_in 比對、
extract_question 少了容錯）。2026-08 先抽成共用模組 quality_checks.py，
後來決定乾脆四支腳本也直接合併成一支，格式檢查邏輯只寫一次、四種用途
（驗證/評測/過濾/修詞）都在同一個檔案裡用同一份，不再散落各處。

四種用途、用子命令切換：

  1. validate    —— 在 train_dpo.py 訓練前執行，確認 train.jsonl 品質
                     （純靜態分析，不呼叫任何 API/模型）
  2. evaluate     —— 對已訓練/已部署的 Ollama 模型，送出跟生產環境一致的
                     prompt，統計輸出違規率（涵蓋 Track A/B/C/D）
  3. filter       —— 從 train.jsonl 移除 chosen 本身就違規的 pair
  4. fix-wording  —— 找出並重新生成用了特定不自然詞語（先/咱們/搭把手等）
                     的舊資料

執行：
  python dpo/data_quality.py validate
  python dpo/data_quality.py evaluate --model rememo-llama3
  python dpo/data_quality.py evaluate --model rememo-llama3 --compare cwchang/llama-3-taiwan-8b-instruct:Q4_K_M --sample 30 --tracks A,B
  python dpo/data_quality.py filter
  python dpo/data_quality.py fix-wording

環境變數：
  OLLAMA_HOST（evaluate 子命令用，預設 http://localhost:11434）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import collect_data as cd

DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"
BACKUP_FILE = Path(__file__).parent / "data" / "train.jsonl.bak"
SCENARIOS_FILE = Path(__file__).parent / "scenarios.json"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


# ══════════════════════════════════════════════════════════════════════════
# 共用格式檢查（四個子命令都會用到）
# ══════════════════════════════════════════════════════════════════════════

# ── 欄位解析 ──────────────────────────────────────────────────────────────

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


def extract_question(content: str) -> str | None:
    """Track A/C/D 共用：從「問題：」行取出問題文字（格式都一樣）。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("問題："):
            inline = line.strip()[3:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None


# extract_question_a/c/d 三個別名保留給既有呼叫端用（train_data_tools.py 的
# diversity／fix-no-anchor 子命令），內容都是同一個函式（Track A/C/D 用的是同一種
# 「問題：」格式）。
extract_question_a = extract_question
extract_question_c = extract_question
extract_question_d = extract_question

_ELDER_SAID_RE = re.compile(r"【長者(?:剛才|最近)說的話】\n(.+?)\n", re.S)


def extract_elder_said_from_prompt(stored_prompt: list[dict] | None) -> str:
    """從已存檔的 inference prompt（train.jsonl 的 "prompt" 欄位）裡抓出
    「【長者剛才/最近說的話】」那一段——不管這段值當初是怎麼來的（舊資料讀
    scenarios.json 固定劇本、新資料讀 cd.simulate_elder_response 動態模擬），
    存進 prompt 裡的就是「那次生成實際用的值」，直接抓出來最準，不用另外
    猜測或重新模擬一次。給 semantic_audit.py（cmd_scan 稽核既有pair）跟
    train_data_tools.py（cmd_refresh_prompts 重新渲染既有pair的prompt格式，
    不該連帶改變原本用的elder_response值）共用。"""
    if not stored_prompt:
        return ""
    user_content = next((m["content"] for m in stored_prompt if m.get("role") == "user"), "")
    match = _ELDER_SAID_RE.search(user_content)
    return match.group(1).strip() if match else ""


def extract_scene_text(content: str) -> str | None:
    """Track A：從「場景文字：」行取出場景描述文字。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("場景文字："):
            inline = line.strip()[5:].strip()
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


def extract_followup_b(content: str) -> str | None:
    """Track B：取出「後續引導：」的文字（去掉 Markdown bold 標記）。"""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        clean = line.strip().lstrip("*").rstrip("*").strip()
        if clean.startswith("後續引導："):
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


def extract_lead_in(content: str) -> str:
    """取出同一段 chosen 裡「場景文字：」或「承接語：」這一行的內容，當作額外的
    錨點比對來源——只比對 elements 清單太嚴格，2026-07 稽核發現「動作優先」
    「準備動作」等規則鼓勵問題從這段文字描述的動作延伸（例如「一路走回家，
    你都在想什麼呢」），不會逐字重複 elements 清單裡的名詞，需要放寬比對範圍。"""
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


def extract_elder_name(prompt: list[dict]) -> str:
    """從 user prompt 的「姓名：」行取出長者姓名，供 elder_as_photo_subject 規則使用。"""
    user_msg = next((m["content"] for m in prompt if m["role"] == "user"), "")
    match = re.search(r"姓名：(.+)", user_msg)
    return match.group(1).strip() if match else ""


# ── 內容是否「有輸出但不算數」 ────────────────────────────────────────────

_TEMPLATE_ECHO_RE = re.compile(r"^[（(].*[）)]$")


def is_leaked_or_empty(q: str | None) -> bool:
    """
    q 是 extract_question 的回傳值。q is None（整行「問題：」都沒輸出）由
    呼叫端的 missing_question_line 規則負責計算；這裡處理另外兩種「有問題：
    這一行，但內容不是真的問題」的情況：
      1. 內容是空字串（本地弱模型偶爾只吐格式標籤，沒接內容）
      2. 內容整句被單一括號包住（本地弱模型把 prompt 裡的格式說明或提示
         文字原封不動抄回來，例如「（≤15字，開放式，開頭要有畫面中的具體
         物件）」——這是 orchestrator.py 註解裡說的「範本回聲」，跟問題
         內容本身寫得好不好是兩回事）
    這兩種情況下 too_long/is_yesno/no_anchor 等規則的判斷沒有意義，
    混進去會把「輸出格式錯亂」誤記成「問題內容違反規則」，稀釋真正的違規率
    （曾實際發生：no_anchor 因此被灌高到 83%，其中一部分其實是空輸出或
    範本回聲，不是真的沒放視覺錨點）。
    """
    if q is None:
        return False
    stripped = q.strip()
    if not stripped:
        return True
    return bool(_TEMPLATE_ECHO_RE.match(stripped))


def is_template_echo(q: str) -> bool:
    """template_echo 規則：整句問題被單一括號包住，看起來是格式提示本身，
    不是真正的問題內容。邏輯與 is_leaked_or_empty 一致（該處還多處理空字串，
    這裡只判斷「rejected 是否真的做出範本回聲」，空字串不算是這條規則的示範）。"""
    return bool(_TEMPLATE_ECHO_RE.match(q.strip()))


_DASH_RE = re.compile(r"^-{3,}\s*$", re.MULTILINE)
_SELF_CORRECT_RE = re.compile(r"等等[，,]|重新檢查這個|需要改|需要修正|違反第\s*\d+\s*條|修正如下")


def has_leaked_self_check(content: str) -> bool:
    """偵測模型把自我檢查/多輪草稿過程洩漏到正式輸出裡（2026-07 稽核發現，
    Track A 有 273 筆 chosen 混進「---」分隔線＋「等等，我需要重新檢查...」
    這類自我修正旁白，本該只印最終定案版本）。只比對「規則\\d+」字面會誤判
    思考欄位裡合法提到規則編號的正常情況，所以不用那個當觸發條件，改抓
    「---」分隔線、明確的自我修正措辭，或同一個欄位重複出現兩次。"""
    if _DASH_RE.search(content):
        return True
    if _SELF_CORRECT_RE.search(content):
        return True
    for label in ("問題：", "場景文字：", "承接語：", "收尾語："):
        count = sum(1 for line in content.split("\n") if line.strip().startswith(label))
        if count >= 2:
            return True
    return False


# ── 問題內容規則 ──────────────────────────────────────────────────────────

def cjk_len(s: str) -> int:
    """中文字數（CJK 字元，不計標點）。"""
    return sum(1 for c in s if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")


def is_yesno(q: str) -> bool:
    """結尾的 嗎？ 或句中有 是不是/有沒有/會不會/要不要/對不對/好不好。"""
    return (bool(re.search(r"嗎[？?]?\s*$", q)) or
            bool(re.search(r"(是不是|有沒有|會不會|要不要|對不對|好不好)", q)))


def is_memory_test(q: str) -> bool:
    return bool(re.search(r"(你|您)(還)?記(得|不記得)", q))


def has_double_question(q: str) -> bool:
    return (q.count("？") + q.count("?")) >= 2


def has_anchor(q: str, elements: list[str], lead_in: str = "") -> bool:
    """問題前 10 字是否含有任一場景元素的錨點字符，或跟 lead_in（同一段 chosen
    裡的場景文字/承接語）有詞組重疊。

    三層檢查（任一通過即可）：
    1. 嚴格：元素前 2 字為前綴子字串（原邏輯，窗口從 6 擴至 10）
    2. 放寬：元素字符出現在前綴中的數量 ≥ threshold
       - 短元素（≤2字）：至少 1 個字符（處理「老街→那條街」這類同義詞）
       - 長元素（>2字）：至少 2 個字符（避免單字誤判，如「工」誤配「下班工人」）
    3. lead_in：跟同段 chosen 的場景文字/承接語有連續2字的詞組重疊
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
            if lead_in[i:i + 2] in prefix:
                return True
    return False


def asks_why(q: str) -> bool:
    return "為什麼" in q or "為何" in q


_PRECISE_FACT_RE = re.compile(r"哪一年|什麼名字|叫什麼|哪一位|幾年出生")


def asks_precise_fact(q: str) -> bool:
    """禁止問需要精確數字/年份/人名/地名的問題，長者答不出來時容易挫折。"""
    return bool(_PRECISE_FACT_RE.search(q))


_TREATS_SCENE_AS_REAL_RE = re.compile(
    r"(有沒有|是不是|會不會)[^？?]{0,6}(來過|去過|待過|認得|熟悉)|認不認得|認得這裡|認得這個地方"
)


def treats_scene_as_real(q: str) -> bool:
    """禁止把 AI 生成的示意圖問成長者真的去過、認得的特定地方（畫面只是引子）。"""
    return bool(_TREATS_SCENE_AS_REAL_RE.search(q))


def uses_polite_nin(text: str) -> bool:
    """稱呼一律用「你」，「您」念起來太正式，會破壞老朋友聊天的溫暖感。"""
    return "您" in text


_MARKDOWN_LEAK_RE = re.compile(r"\*\*|##|`|^\s*[-*]\s", re.MULTILINE)


def has_markdown_leak(text: str) -> bool:
    """禁止任何 markdown 語法——這段文字會直接餵給 TTS 唸給長者聽。"""
    return bool(_MARKDOWN_LEAK_RE.search(text))


def treats_elder_as_photo_subject(scene_text: str, elder_name: str) -> bool:
    """
    場景文字要用第三人稱描述畫面本身（像描述一幅畫），不能寫成長者正站在畫面裡
    （AI 生成的示意圖，長者本人不是照片裡的主角）。用兩個訊號抓這個違規：
      1. 長者的名字被當成畫面裡動作的主詞（例如「王伯伯穿著軍服，等待家人」）
         ——但排除「王伯伯，你看」這種用名字當稱呼語、後面接逗號的正常開場，
         那不是把長者寫成動作者
      2. 用第二人稱把「你」寫成正在畫面裡做動作、身處其中（「你站在」「你坐在」等）
    """
    if elder_name and elder_name in scene_text:
        idx = scene_text.index(elder_name)
        after = scene_text[idx + len(elder_name):idx + len(elder_name) + 1]
        if after not in ("，", ",", "的"):
            return True
    return bool(re.search(r"你(正|現在)?.{0,4}(站在|坐在|待在|走在)", scene_text))


def content_touches_taboo(text: str, taboos: list[str]) -> bool:
    """Layer 1 粗篩（比照 app/safety/taboo_checker.py 的 keyword_prescan）：
    純字面比對，抓不到語意相關但沒用到禁忌詞字面的情況，僅供訓練前粗篩。"""
    return any(t in text for t in taboos)


def load_existing() -> list[dict]:
    """讀取現有 train.jsonl，回傳 pair 清單（檔案不存在則回傳空清單）。"""
    if not cd.OUTPUT_FILE.exists():
        return []
    pairs = []
    with cd.OUTPUT_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


# ══════════════════════════════════════════════════════════════════════════
# validate 子命令（原 validate_data.py）——train_dpo.py 訓練前的靜態品質關卡
# ══════════════════════════════════════════════════════════════════════════

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
        if has_leaked_self_check(chosen):
            self._fail("chosen:leaked_self_check", info_base)

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

            if not has_anchor(chosen_q, elements, extract_lead_in(chosen)):
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
                if not has_anchor(chosen_q, elements, extract_lead_in(chosen)):
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

    # ── 報告 ─────────────────────────────────────────────────────────────

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
            ("chosen:leaked_self_check",   "Chosen 洩漏自我檢查/多輪草稿過程"),
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


def cmd_validate(args) -> None:
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


# ══════════════════════════════════════════════════════════════════════════
# evaluate 子命令（原 evaluate_model.py）——對已部署模型的線上評測
# ══════════════════════════════════════════════════════════════════════════

# 2026-08 Track A 拿掉 STEP2（見 collect_data.generate_track_a 的說明：這個
# 格式正式環境從不會用到，職業/主題多樣性想遷移進 Track C 試跑後也認定不
# 划算，改成單純刪除），評測流程只剩 STEP1/STEP3。
EVAL_STEP_CONTEXT = {
    "STEP1": {"elder_response_key": None},
    "STEP3": {"elder_response_key": "elder_step2_response"},
}


def build_eval_inference_prompt(
    step: str, scenario: dict, covered_w: list[str] | None = None, target_w: str | None = None,
    elder_response: str | None = None,
) -> list[dict]:
    """
    委派給 dpo/collect_data.py 的 build_inference_prompt，system prompt 直接讀
    app/prompts/question_5w1h.txt（正式環境實際使用的內容），不在這裡另外維護
    一份 hardcoded 字串——避免重演「question_5w1h.txt 加了新規則、這裡忘記
    同步」的問題（先前 STEP1 用「你」還是「您」、markdown 禁令都各自漏過一次）。

    covered_w/target_w 改由呼叫端（evaluate()）依序評測 STEP1→STEP3 時動態
    算出並傳入，不再用寫死的固定假設值。

    elder_response: STEP3專用，2026-08改版由呼叫端（evaluate()）用
    cd.simulate_elder_response() 根據「這次評測時模型STEP1實際生成的問題」
    現場模擬後傳入——不再退回 EVAL_STEP_CONTEXT 裡 scenarios.json 的固定
    劇本，那份劇本沒有對應到任何一次真正生成的STEP1問題，會讓STEP3的評測
    輸入跟模型自己在這次評測裡實際問出來的內容對不上。沒有傳值時（呼叫端
    還沒更新）才退回舊的 EVAL_STEP_CONTEXT 查表，保持向後相容。
    """
    elder = scenario["elder"]
    ctx = EVAL_STEP_CONTEXT[step]
    if elder_response is None:
        elder_response = scenario.get(ctx["elder_response_key"], "") if ctx["elder_response_key"] else ""

    return cd.build_inference_prompt(
        step=step,
        elder=elder,
        scene=scenario["scene"],
        covered_w=covered_w or [],
        topic_category=scenario.get("topic_category"),
        elder_response=elder_response,
        taboos=elder.get("taboos"),
        target_w=target_w,
    )


CHECKS = [
    ("missing_question_line", lambda q, els, step, content, elder_name: q is None),
    ("leaked_or_empty", lambda q, els, step, content, elder_name: is_leaked_or_empty(q)),
    ("too_long", lambda q, els, step, content, elder_name: (
        q is not None and not is_leaked_or_empty(q) and cjk_len(q) > 15
    )),
    ("is_yesno", lambda q, els, step, content, elder_name: (
        q is not None and not is_leaked_or_empty(q) and is_yesno(q)
    )),
    ("memory_test", lambda q, els, step, content, elder_name: (
        q is not None and not is_leaked_or_empty(q) and is_memory_test(q)
    )),
    ("double_question", lambda q, els, step, content, elder_name: (
        q is not None and not is_leaked_or_empty(q) and has_double_question(q)
    )),
    ("no_anchor", lambda q, els, step, content, elder_name: (
        q is not None and not is_leaked_or_empty(q) and not has_anchor(q, els)
    )),
    ("step1_asks_why", lambda q, els, step, content, elder_name: (
        q is not None and not is_leaked_or_empty(q) and step == "STEP1" and asks_why(q)
    )),
    ("uses_nin", lambda q, els, step, content, elder_name: uses_polite_nin(content)),
    ("markdown_leak", lambda q, els, step, content, elder_name: has_markdown_leak(content)),
    ("precise_fact", lambda q, els, step, content, elder_name: (
        q is not None and not is_leaked_or_empty(q) and asks_precise_fact(q)
    )),
    ("treats_scene_as_real", lambda q, els, step, content, elder_name: (
        q is not None and not is_leaked_or_empty(q) and treats_scene_as_real(q)
    )),
    ("elder_as_photo_subject", lambda q, els, step, content, elder_name: (
        treats_elder_as_photo_subject(extract_scene_text(content) or "", elder_name)
    )),
]


# Track B/C/D 的規則檢查（違反時回傳 True）。簽名統一為 (content, scenario) -> bool，
# 跟 Track A 的 (q, elements, step, content, elder_name) 簽名不同，因為 B/D
# 沒有場景元素、C 才有。

CHECKS_B = [
    ("missing_ack", lambda content, sc: extract_ack_b(content) is None),
    ("missing_followup", lambda content, sc: extract_followup_b(content) is None),
    ("followup_not_question", lambda content, sc: (
        extract_followup_b(content) is not None
        and "？" not in extract_followup_b(content)
        and "?" not in extract_followup_b(content)
    )),
    ("touches_taboo", lambda content, sc: content_touches_taboo(content, sc.get("taboos", []))),
    # 情緒回應／後續引導一樣直接餵給 TTS 唸給長者聽，一樣受「稱呼用你不用您」
    # 「禁止 markdown」規則約束——之前只有 Track A/C 的 CHECKS 有這兩項，
    # Track B（情緒回應）漏掉了，2026-07 稽核時補上。
    ("uses_nin", lambda content, sc: uses_polite_nin(content)),
    ("markdown_leak", lambda content, sc: has_markdown_leak(content)),
]

CHECKS_C = [
    ("missing_ack", lambda content, sc: extract_ack_c(content) is None),
    ("missing_question", lambda content, sc: extract_question(content) is None),
    ("leaked_or_empty", lambda content, sc: is_leaked_or_empty(extract_question(content))),
    ("too_long", lambda content, sc: (
        extract_question(content) is not None
        and not is_leaked_or_empty(extract_question(content))
        and cjk_len(extract_question(content)) > 15
    )),
    ("no_anchor", lambda content, sc: (
        extract_question(content) is not None
        and not is_leaked_or_empty(extract_question(content))
        and not has_anchor(extract_question(content), sc.get("scene_elements", []))
    )),
    ("is_yesno", lambda content, sc: (
        extract_question(content) is not None
        and not is_leaked_or_empty(extract_question(content))
        and is_yesno(extract_question(content))
    )),
    ("uses_nin", lambda content, sc: uses_polite_nin(content)),
    ("markdown_leak", lambda content, sc: has_markdown_leak(content)),
    ("precise_fact", lambda content, sc: (
        extract_question(content) is not None
        and not is_leaked_or_empty(extract_question(content))
        and asks_precise_fact(extract_question(content))
    )),
    ("treats_scene_as_real", lambda content, sc: (
        extract_question(content) is not None
        and not is_leaked_or_empty(extract_question(content))
        and treats_scene_as_real(extract_question(content))
    )),
    ("touches_taboo", lambda content, sc: content_touches_taboo(content, sc.get("taboos", []))),
]

CHECKS_D = [
    ("missing_closing_text", lambda content, sc: extract_closing_text(content) is None),
    ("missing_question", lambda content, sc: extract_question(content) is None),
    ("leaked_or_empty", lambda content, sc: is_leaked_or_empty(extract_question(content))),
    ("question_too_long", lambda content, sc: (
        extract_question(content) is not None
        and not is_leaked_or_empty(extract_question(content))
        and cjk_len(extract_question(content)) > 15
    )),
    ("is_yesno", lambda content, sc: (
        extract_question(content) is not None
        and not is_leaked_or_empty(extract_question(content))
        and is_yesno(extract_question(content))
    )),
    ("touches_taboo", lambda content, sc: content_touches_taboo(content, sc.get("taboos", []))),
    # 收尾語／問題一樣是餵給 TTS 唸出來的內容，同樣受「你不用您」「禁止 markdown」
    # 規則約束——之前只有 Track A/C 的 CHECKS 有這兩項，Track D（收尾）漏掉了，
    # 2026-07 稽核時補上。
    ("uses_nin", lambda content, sc: uses_polite_nin(content)),
    ("markdown_leak", lambda content, sc: has_markdown_leak(content)),
]


def _scenario_label(sc: dict) -> str:
    return sc.get("id") or sc.get("emotion_tone") or sc.get("elder_name") or sc.get("context", "")[:15]


def build_prompt_b(sc: dict) -> list[dict]:
    return cd.build_emotional_inference_prompt(sc["trigger"], taboos=sc.get("taboos", []))


def build_prompt_c(sc: dict) -> list[dict]:
    covered_w = cd._covered_w_before(sc["next_w"])
    return cd.build_track_c_inference_prompt(sc, covered_w=covered_w, skipped_w=[], taboos=sc.get("taboos", []))


def build_prompt_d(sc: dict) -> list[dict]:
    return cd.build_track_d_inference_prompt(sc, emotion=sc.get("emotion", "happy"))


def check_ollama_reachable() -> list[str]:
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        print(f"無法連線到 Ollama（{OLLAMA_HOST}）：{e}")
        sys.exit(1)


def call_ollama(model: str, messages: list[dict], timeout: int = 300) -> str:
    url = f"{OLLAMA_HOST}/api/chat"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.3},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["message"]["content"]


def evaluate(model: str, scenarios: list[dict]) -> dict:
    fail_counts: dict[str, int] = {name: 0 for name, _ in CHECKS}
    examples: dict[str, list[dict]] = {name: [] for name, _ in CHECKS}
    total = 0

    for sc in scenarios:
        # 依序評測 STEP1→STEP3，動態累積 covered_w（取代原本每個STEP各自用
        # 寫死假設值），跟 generate_track_a 的做法保持一致——STEP3 開始時的
        # covered_w 是「STEP1結束後」的真實狀態，STEP1呼叫模型拿到 content
        # 後才更新。
        covered_w: list[str] = []
        dynamic_elder_reply = ""
        for step in ("STEP1", "STEP3"):
            total += 1
            target_w = _pick_target_w_eval(covered_w) if step == "STEP3" else None
            messages = build_eval_inference_prompt(
                step, sc, covered_w=covered_w, target_w=target_w,
                elder_response=dynamic_elder_reply if step == "STEP3" else None,
            )
            try:
                content = call_ollama(model, messages)
            except (urllib.error.URLError, TimeoutError, KeyError) as e:
                print(f"  [WARN] {sc['id']} {step} 呼叫失敗（{e}），重試一次...")
                try:
                    content = call_ollama(model, messages)
                except (urllib.error.URLError, TimeoutError, KeyError) as e2:
                    print(f"  [WARN] {sc['id']} {step} 重試仍失敗：{e2}")
                    continue

            new_covered = cd._extract_covered_w(content)
            if new_covered:
                covered_w = new_covered

            # STEP1評測完後，用這次模型「真正問出來」的問題模擬長者會怎麼
            # 回答，給下一步STEP3當「長者剛才說的話」——跟generate_track_a
            # 用的是同一套邏輯（見cd.simulate_elder_response docstring），
            # 確保評測時STEP3看到的輸入跟這次STEP1實際問的內容對得上。
            if step == "STEP1":
                q1 = extract_question(content)
                st1 = extract_scene_text(content)
                if q1 and st1:
                    try:
                        dynamic_elder_reply = cd.simulate_elder_response(sc, q1, st1)
                    except Exception as e:
                        print(f"  [WARN] {sc['id']} 模擬長者回答失敗（{e}），STEP3將退回無長者回應")
                        dynamic_elder_reply = ""

            q = extract_question(content)
            elements = sc["scene"]["elements"]
            elder_name = sc["elder"]["name"]

            for name, check in CHECKS:
                if check(q, elements, step, content, elder_name):
                    fail_counts[name] += 1
                    if len(examples[name]) < 3:
                        examples[name].append({"id": sc["id"], "step": step, "q": q})

    return {"model": model, "total": total, "fail_counts": fail_counts, "examples": examples}


# _pick_target_w_eval 別名：collect_data._pick_target_w 邏輯已對齊
# orchestrator._next_uncovered_w（依 _W_ORDER 優先序挑第一個未涵蓋的W）。
_pick_target_w_eval = cd._pick_target_w


def print_report(result: dict) -> None:
    total = result["total"]
    print(f"\n模型：{result['model']}　共測試 {total} 筆")
    print("-" * 62)
    for name, _ in CHECKS:
        n = result["fail_counts"][name]
        rate = f"{n / total * 100:5.1f}%" if total else "  -  "
        status = "✓" if n == 0 else "✗"
        print(f"  {status}  {name:<24}違規 {n:>4} 筆（{rate}）")
        for ex in result["examples"][name]:
            print(f"        → {ex['id']} {ex['step']}: {ex['q']}")
    print("-" * 62)


def evaluate_generic(model: str, scenarios: list[dict], build_prompt_fn, checks: list[tuple]) -> dict:
    fail_counts: dict[str, int] = {name: 0 for name, _ in checks}
    examples: dict[str, list[dict]] = {name: [] for name, _ in checks}
    total = 0

    for sc in scenarios:
        total += 1
        messages = build_prompt_fn(sc)
        try:
            content = call_ollama(model, messages)
        except (urllib.error.URLError, TimeoutError, KeyError) as e:
            print(f"  [WARN] {_scenario_label(sc)} 呼叫失敗（{e}），重試一次...")
            try:
                content = call_ollama(model, messages)
            except (urllib.error.URLError, TimeoutError, KeyError) as e2:
                print(f"  [WARN] {_scenario_label(sc)} 重試仍失敗：{e2}")
                continue

        for name, check in checks:
            if check(content, sc):
                fail_counts[name] += 1
                if len(examples[name]) < 3:
                    examples[name].append({"id": _scenario_label(sc), "content": content[:60].replace("\n", " ")})

    return {"model": model, "total": total, "fail_counts": fail_counts, "examples": examples}


def print_report_generic(result: dict, label: str) -> None:
    total = result["total"]
    print(f"\n模型：{result['model']}　Track {label}　共測試 {total} 筆")
    print("-" * 62)
    for name in result["fail_counts"]:
        n = result["fail_counts"][name]
        rate = f"{n / total * 100:5.1f}%" if total else "  -  "
        status = "✓" if n == 0 else "✗"
        print(f"  {status}  {name:<24}違規 {n:>4} 筆（{rate}）")
        for ex in result["examples"][name]:
            print(f"        → {ex['id']}: {ex['content']}")
    print("-" * 62)


def _run_generic_track(
    label: str, model: str, compare: str | None,
    scenarios: list[dict], build_prompt_fn, checks: list[tuple],
) -> None:
    if not scenarios:
        return
    print(f"\n測試模型：{model}（Track {label}）...")
    result_model = evaluate_generic(model, scenarios, build_prompt_fn, checks)
    print_report_generic(result_model, label)

    if compare:
        print(f"\n測試對照模型：{compare}（Track {label}）...")
        result_base = evaluate_generic(compare, scenarios, build_prompt_fn, checks)
        print_report_generic(result_base, label)

        print("\n" + "=" * 62)
        print(f"  Track {label} 訓練前後比較（違規筆數，越少越好）")
        print("=" * 62)
        for name, _ in checks:
            n_base = result_base["fail_counts"][name]
            n_tuned = result_model["fail_counts"][name]
            delta = n_base - n_tuned
            arrow = "↓ 進步" if delta > 0 else ("↑ 退步" if delta < 0 else "—")
            print(f"  {name:<24}base={n_base:>4}  tuned={n_tuned:>4}  {arrow}")
        print("=" * 62)


def cmd_evaluate(args) -> None:
    tracks = {t.strip().upper() for t in args.tracks.split(",") if t.strip()}

    available = check_ollama_reachable()
    for m in (args.model, args.compare):
        if m and not any(m == a or a.startswith(m + ":") for a in available):
            print(f"  [提醒] 在 Ollama 模型清單中沒看到「{m}」，請確認名稱是否正確（現有：{', '.join(available)}）")

    def sampled(items: list) -> list:
        if not args.sample:
            return items
        items = list(items)
        random.Random(args.seed).shuffle(items)
        return items[: args.sample]

    if "A" in tracks:
        with SCENARIOS_FILE.open(encoding="utf-8") as f:
            scenarios = json.load(f)
        scenarios = sampled(scenarios)

        # 2026-08 Track A 拿掉 STEP2，每個情境現在只測 STEP1/STEP3 兩筆。
        print(f"\n=== Track A：問題品質 === 共 {len(scenarios)} 個情境 × 2 個 STEP = {len(scenarios) * 2} 筆測試")
        print(f"測試模型：{args.model} ...")
        result_model = evaluate(args.model, scenarios)
        print_report(result_model)

        if args.compare:
            print(f"\n測試對照模型：{args.compare} ...")
            result_base = evaluate(args.compare, scenarios)
            print_report(result_base)

            print("\n" + "=" * 62)
            print("  Track A 訓練前後比較（違規筆數，越少越好）")
            print("=" * 62)
            for name, _ in CHECKS:
                n_base = result_base["fail_counts"][name]
                n_tuned = result_model["fail_counts"][name]
                delta = n_base - n_tuned
                arrow = "↓ 進步" if delta > 0 else ("↑ 退步" if delta < 0 else "—")
                print(f"  {name:<24}base={n_base:>4}  tuned={n_tuned:>4}  {arrow}")
            print("=" * 62)

    if "B" in tracks:
        print(f"\n=== Track B：情緒引導（危機處理） === 共 {len(sampled(cd.EMOTIONAL_SCENARIOS))} 個情境")
        _run_generic_track("B", args.model, args.compare, sampled(cd.EMOTIONAL_SCENARIOS), build_prompt_b, CHECKS_B)

    if "C" in tracks:
        print(f"\n=== Track C：情緒感知承接 + 問題 === 共 {len(sampled(cd.TRACK_C_SCENARIOS))} 個情境")
        _run_generic_track("C", args.model, args.compare, sampled(cd.TRACK_C_SCENARIOS), build_prompt_c, CHECKS_C)

    if "D" in tracks:
        print(f"\n=== Track D：收尾引導 === 共 {len(sampled(cd.TRACK_D_SCENARIOS))} 個情境")
        _run_generic_track("D", args.model, args.compare, sampled(cd.TRACK_D_SCENARIOS), build_prompt_d, CHECKS_D)


# ══════════════════════════════════════════════════════════════════════════
# filter 子命令（原 filter_data.py）——移除 chosen 本身違規的 pair
# ══════════════════════════════════════════════════════════════════════════

_MECHANICAL_ACTION_RE = re.compile(r"怎麼(走|湊|挪|移動)(過去|過來)?(的)?呢?[？?]?\s*$")


def is_mechanical_action(q: str) -> bool:
    """問法只剩「怎麼+空洞動作動詞」，沒有具體受詞或情境，答案通常只有一個動作詞
    （例：「你都怎麼走呢？」「都怎麼湊過來呢？」），跟「長耙你都怎麼用呢？」這種
    有意義動詞的問法不同，只抓固定句型，換句話說的版本抓不到，需搭配人工複查。
    這是 filter 子命令專屬的檢查，不是共用格式規則。"""
    return bool(_MECHANICAL_ACTION_RE.search(q))


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


def cmd_filter(args) -> None:
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


# ══════════════════════════════════════════════════════════════════════════
# fix-wording 子命令（原 fix_xian_wording.py）——修補特定不自然用詞
# ══════════════════════════════════════════════════════════════════════════
#
# 修補 train.jsonl 裡 chosen 問題用了「先做什麼／先準備／先夾／先看」這種語法的
# (scenario, step) 組合（Track A）跟 (emotion_tone) 組合（Track C）。
#
# 2026-07 使用者回饋：不喜歡「V之前/之後，你都先做什麼」這個「先」字，且這個句型
# 在範例庫裡佔比過高、生成結果同質化。已經修正：
#   - app/prompts/question_5w1h.txt（正式環境 + Track A 的 inference prompt 共用）
#   - dpo/collect_data.py 的 build_step1/2/3_user_prompt、build_track_c_chosen_prompt
#     （這三支各自內嵌了一份獨立的規則文字，不是從 question_5w1h.txt 讀的，之前
#     沒發現這裡也要修，改完 question_5w1h.txt 沒有同步改到這裡的話，重新生成
#     出來的 chosen 還是會沿用舊語法）
#
# fix-wording 找出舊資料裡踩到「先」語法的 chosen，丟棄後用修好的 prompt 重新生成。

# 舊版只列了9個特定動詞（做/夾/準備/備/看/想/說/走/怎麼），漏抓「先忙」這類清單外
# 的動詞（2026-07 使用者回饋：sc101 STEP1「你們都先忙什麼呢」就是漏網案例）。改成
# 「先」後面接任何字都算，只排除「先生／先夫／先父／先母／先人／先前／先天」這幾個
# 「先」是固定詞語一部分、不是「先+動詞」贅字用法的合法詞（例如「你先生以前都做
# 什麼工作呢？」問的是「先生」這個人，不是要拿掉的贅字「先」）。
_XIAN_RE = re.compile(r"(?<!最)先(?!生|夫|父|母|人|前|天)")
_ZANMEN_RE = re.compile(r"咱")
_DABASHOU_RE = re.compile(r"搭把手|搭一把手")
_SHOUCHANG_RE = re.compile(r"收場.{0,3}(回家|下班|下工)")
_TOUYIJU_RE = re.compile(r"頭一句(?!話)")
_BOOKISH_VC_RE = re.compile(r"做下來|說下去")
_SCENE_AS_QUESTION_RE = re.compile(r"(呢|嗎)[。！.!]?\s*$")

# 以下 4 條是 QUESTION_REJECTION_RULES / TRACK_C_REJECTION_RULES 裡結構性、
# 不需要語意判斷就能穩定判斷的規則，改用確定性 regex 而不是丟給 Haiku 判斷——
# 實測同一句明顯的是非題丟給 Haiku 判斷 4 次只抓到 1 次，這種格式層級的規則
# regex 比 LLM 判斷可靠得多。
_YESNO_END_RE = re.compile(r"嗎[？?]?\s*$")
_YESNO_PHRASE_RE = re.compile(r"有沒有|是不是|會不會|要不要|對不對|好不好|想不想")
_PUNCT_RE = re.compile(r"[，。、！？!?,.\s「」『』（）()]")
_MEMORY_TEST_START_RE = re.compile(r"^你?(還記得|記不記得)")

# 這 4 條交給 Haiku 語意判斷（覆蓋 QUESTION_REJECTION_RULES 扣掉上面 4 條後的
# 其餘 7 條：no_anchor、wrong_w_priority、leading_question、touches_taboo、
# treats_image_as_real、template_echo、elder_as_photo_subject）
_DETERMINISTIC_RULE_NAMES = {"is_yesno", "double_question", "too_long", "memory_test"}


def has_yesno_wording(content: str) -> bool:
    q = extract_question(content) or ""
    return bool(_YESNO_END_RE.search(q)) or bool(_YESNO_PHRASE_RE.search(q))


def _content_has_double_question(content: str) -> bool:
    """跟共用的 has_double_question(q) 不同：這裡吃整段 content 自己抽問題行，
    只給 check_deterministic_rules 用，避免跟共用版本同名造成混淆。"""
    q = extract_question(content) or ""
    return (q.count("？") + q.count("?")) > 1


def has_too_long_question(content: str) -> bool:
    q = extract_question(content) or ""
    return len(_PUNCT_RE.sub("", q)) > 15


def has_memory_test_wording(content: str) -> bool:
    q = extract_question(content) or ""
    return bool(_MEMORY_TEST_START_RE.match(q))


def check_deterministic_rules(content: str) -> str | None:
    """回傳第一個命中的確定性規則名稱，全部通過回傳 None。"""
    if has_yesno_wording(content):
        return "is_yesno"
    if _content_has_double_question(content):
        return "double_question"
    if has_too_long_question(content):
        return "too_long"
    if has_memory_test_wording(content):
        return "memory_test"
    return None


def has_xian_wording(content: str) -> bool:
    q = extract_question(content) or ""
    return bool(_XIAN_RE.search(q))


def has_zanmen_wording(content: str) -> bool:
    """「咱們／咱」不限於問題句，承接語等其他欄位也可能出現，所以掃整段內容。"""
    return bool(_ZANMEN_RE.search(content))


def has_dabashou_wording(content: str) -> bool:
    """「搭把手」是北方/大陸口語，不限於問題句，掃整段內容。"""
    return bool(_DABASHOU_RE.search(content))


def has_shouchang_wording(content: str) -> bool:
    """「收場」接「回家/下班/下工」是誤用——「收場」是抽象語境（事情/戲怎麼收場），
    具體收拾東西離開要用「收工/收拾」，不限於問題句，掃整段內容。"""
    return bool(_SHOUCHANG_RE.search(content))


def has_touyiju_wording(content: str) -> bool:
    """「頭一句」省略「話」字語法不完整，同樣不限於問題句，掃整段內容。"""
    return bool(_TOUYIJU_RE.search(content))


def has_bookish_verb_complement(content: str) -> bool:
    """只攔已知踩到過的生硬動補搭配（做下來/說下去），不是窮舉所有書面翻譯腔。"""
    return bool(_BOOKISH_VC_RE.search(content))


def has_scene_text_as_question(content: str) -> bool:
    """場景文字偷埋問句（用「呢/嗎」結尾），會跟後面的「問題：」重複問兩次。
    只檢查「場景文字：」這一行，Track C 的「承接語：」不適用這條規則。
    """
    scene = extract_scene_text(content) or ""
    return bool(scene) and bool(_SCENE_AS_QUESTION_RE.search(scene))


# 不屬於 QUESTION_REJECTION_RULES / TRACK_C_REJECTION_RULES、但同樣需要語意判斷
# 的額外檢查項目，併入同一次 Haiku 呼叫，不多花一次 API 成本。
_EXTRA_SEMANTIC_RULES = {
    "fabricated_biography": "在場景文字、思考、或問題裡把長者本人沒有根據的具體人生"
    "事實、事件或人際關係當成既定事實來寫（例如編造具體服務年資、跟誰的關係、某個"
    "事件的具體經過），而不是根據已知的長者背景資料或長者剛才親口說過的話",
}


def build_chosen_validation_prompt(
    chosen: str, rules: dict[str, str], taboos: list[str] | None = None
) -> str:
    taboo_str = "、".join(taboos) if taboos else "無"
    rules_text = "\n".join(f"{i + 1}. {name}：{desc}" for i, (name, desc) in enumerate(rules.items()))
    return f"""以下是一則懷舊治療 AI 要對長者說的問題回應，理論上應該是完全遵守規則的正面
示範（chosen）：

{chosen}

【這位長者的禁忌話題】
{taboo_str}

請檢查這則回應有沒有不小心違反下面任何一條規則。這些規則原本是設計來故意生成
「違規負面範例」用的，這裡要反過來用——檢查這則「理論上應該正確」的範例有沒有
意外也踩到其中任何一條：

{rules_text}

嚴格照這個格式輸出，不要多寫任何說明或理由：
如果沒有違反任何一條，只輸出：PASS
如果違反了其中一條或多條，輸出：FAIL: <違反的規則名稱，用逗號分隔>"""


def check_chosen_against_rules(
    chosen: str, rules: dict[str, str], taboos: list[str] | None = None
) -> str | None:
    """反向檢查 chosen 有沒有不小心違反 rules 裡任何一條規則（這些規則原本只用來
    生成 rejected 反例，從沒反過來驗證過 chosen 本身）。回傳 None 代表通過；
    否則回傳違規規則名稱（字串）。

    格式層級的規則（is_yesno/double_question/too_long/memory_test，見
    _DETERMINISTIC_RULE_NAMES）先用確定性 regex 判斷——實測同一句明顯的是非題
    丟給 Haiku 判斷 4 次只抓到 1 次，這類規則 regex 遠比 LLM 判斷可靠。剩下需要
    語意判斷的規則（no_anchor/wrong_w_priority/leading_question/touches_taboo/
    treats_image_as_real/template_echo/elder_as_photo_subject）才呼叫 Haiku。
    """
    det = check_deterministic_rules(chosen)
    if det and det in rules:
        return det

    llm_rules = {name: desc for name, desc in rules.items() if name not in _DETERMINISTIC_RULE_NAMES}
    llm_rules.update(_EXTRA_SEMANTIC_RULES)

    prompt = build_chosen_validation_prompt(chosen, llm_rules, taboos=taboos)
    try:
        result = cd.call_claude(prompt, model=cd.MODEL_REJECTED)
        time.sleep(cd.REQUEST_DELAY)
    except Exception as e:
        print(f"    ✗ chosen 規則驗證呼叫失敗：{e}（視為通過，不阻擋）")
        return None
    result = result.strip()
    if result.upper().startswith("PASS"):
        return None
    return result


def _has_any_wording_issue_a(content: str) -> bool:
    """Track A 專用：含 scene_as_question（只檢查「場景文字：」這一行，Track C
    的「承接語：」不適用這條規則，所以獨立一支給 Track A 用，不要跟 Track C
    共用同一支檢查函式）。"""
    return (
        has_xian_wording(content)
        or has_zanmen_wording(content)
        or has_dabashou_wording(content)
        or has_shouchang_wording(content)
        or has_touyiju_wording(content)
        or has_bookish_verb_complement(content)
        or has_scene_text_as_question(content)
    )


def _has_any_wording_issue_c(content: str) -> bool:
    return (
        has_xian_wording(content)
        or has_zanmen_wording(content)
        or has_dabashou_wording(content)
        or has_shouchang_wording(content)
        or has_touyiju_wording(content)
        or has_bookish_verb_complement(content)
    )


def find_affected_track_a(existing: list[dict]) -> set[tuple[str, str]]:
    affected = set()
    for obj in existing:
        if obj["meta"]["track"] != "A":
            continue
        if _has_any_wording_issue_a(obj["chosen"][0]["content"]):
            affected.add((obj["meta"]["scenario_id"], obj["meta"]["step"]))
    return affected


def find_affected_track_c(existing: list[dict]) -> set[str]:
    affected = set()
    for obj in existing:
        if obj["meta"]["track"] != "C":
            continue
        if _has_any_wording_issue_c(obj["chosen"][0]["content"]):
            affected.add(obj["meta"]["emotion_tone"])
    return affected


def regenerate_track_a_scenario_step(
    sc: dict, step: str, chosen_lookup: dict[tuple[str, str], str] | None = None,
) -> list[dict]:
    """
    重跑 collect_data.py generate_track_a() 對單一 (scenario, step) 的邏輯。

    chosen_lookup: 用 cd.build_chosen_lookup(existing) 從既有 train.jsonl 建立的
    (scenario_id, step) → chosen 查表，透過 cd.real_covered_w_from_lookup 反推
    前一步驟真正涵蓋的W維度。查不到（例如沒傳 chosen_lookup，或前一步驟資料
    缺失）就退回空清單，讓 build_inference_prompt 顯示「無」，不套用寫死假設值。
    """
    elder = sc["elder"]
    scene = sc["scene"]
    # STEP2 保留給既有 train.jsonl 裡可能還殘留的舊資料修補用（2026-08 Track A
    # 已經拿掉 STEP2，不會再有新的 STEP2 產生，這裡純粹是相容性支援）。
    prompt_builders = {
        "STEP1": cd.build_step1_user_prompt,
        "STEP2": cd.build_step2_user_prompt,
        "STEP3": cd.build_step3_user_prompt,
    }
    covered_w = (
        cd.real_covered_w_from_lookup(chosen_lookup, sc["id"], step)
        if chosen_lookup else []
    )
    target_w = cd._pick_target_w(covered_w) if step == "STEP3" else None
    # 2026-08：STEP3的「長者最近說的話」改成動態模擬（見
    # get_dynamic_elder_response_for_step3 docstring），不再讀scenarios.json
    # 裡固定寫死的elder_step2_response——固定劇本沒有對應到這次真正生成的
    # STEP1問題，容易兜不上。
    dynamic_elder_reply = (
        cd.get_dynamic_elder_response_for_step3(chosen_lookup, sc["id"], sc)
        if step == "STEP3" and chosen_lookup else ""
    )

    print(f"  [{sc['id']}] {step} — 重新生成 chosen...")
    if step == "STEP3":
        user_prompt = prompt_builders[step](
            sc, covered_w=covered_w, target_w=target_w, elder_response=dynamic_elder_reply,
        )
    else:
        user_prompt = prompt_builders[step](sc)
    try:
        chosen = cd.call_claude(user_prompt)
        time.sleep(cd.REQUEST_DELAY)
    except Exception as e:
        print(f"    ✗ chosen 失敗：{e}")
        return []

    if has_xian_wording(chosen):
        print("    ! 重新生成後仍是「先」語法，跳過此組（需要人工檢查）")
        return []
    if has_zanmen_wording(chosen):
        print("    ! 重新生成後出現「咱們／咱」，跳過此組（需要人工檢查）")
        return []
    if has_dabashou_wording(chosen):
        print("    ! 重新生成後出現「搭把手」，跳過此組（需要人工檢查）")
        return []
    if has_shouchang_wording(chosen):
        print("    ! 重新生成後出現「收場回家」誤用，跳過此組（需要人工檢查）")
        return []
    if has_touyiju_wording(chosen):
        print("    ! 重新生成後出現「頭一句」（省略話字），跳過此組（需要人工檢查）")
        return []
    if has_bookish_verb_complement(chosen):
        print("    ! 重新生成後出現生硬動補搭配（做下來/說下去），跳過此組（需要人工檢查）")
        return []
    if has_scene_text_as_question(chosen):
        print("    ! 重新生成後場景文字偷埋問句（呢/嗎結尾），跳過此組（需要人工檢查）")
        return []
    if has_leaked_self_check(chosen):
        print("    ! 重新生成後偵測到自我檢查洩漏，跳過此組（需要人工檢查）")
        return []
    taboos = elder.get("taboos", [])
    violation = check_chosen_against_rules(chosen, cd.QUESTION_REJECTION_RULES, taboos=taboos)
    if violation:
        print(f"    ! chosen 違反規則檢查：{violation}，跳過此組（需要人工檢查）")
        return []

    step_responses = {
        "STEP1": "",
        "STEP2": sc.get("elder_step1_response", ""),
        "STEP3": dynamic_elder_reply,
    }
    inference_prompt = cd.build_inference_prompt(
        step, elder, scene, covered_w,
        topic_category=sc.get("topic_category"),
        elder_response=step_responses[step],
        taboos=taboos,
        target_w=target_w,
    )

    pairs = []
    for rule_name, rule_desc in cd.QUESTION_REJECTION_RULES.items():
        if rule_name == "touches_taboo" and not taboos:
            continue
        print(f"    [{rule_name}] 生成 rejected...")
        rejection_prompt = cd.build_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)
        rejected = None
        for attempt in range(3):
            try:
                candidate = cd.call_claude(rejection_prompt, model=cd.MODEL_REJECTED)
                time.sleep(cd.REQUEST_DELAY)
            except Exception as e:
                print(f"      ✗ rejected 失敗（attempt {attempt+1}）：{e}")
                continue
            if candidate == chosen:
                continue
            rejected = candidate
            break
        if rejected is None:
            print(f"      ✗ [{rule_name}] 三次均失敗或 chosen==rejected，跳過")
            continue
        pairs.append({
            "prompt": inference_prompt,
            "chosen": [{"role": "assistant", "content": chosen}],
            "rejected": [{"role": "assistant", "content": rejected}],
            "meta": {
                "scenario_id": sc["id"],
                "step": step,
                "rejection_rule": rule_name,
                "track": "A",
                "taboos": taboos,
            },
        })
    return pairs


def regenerate_track_c_scenario(sc: dict) -> list[dict]:
    """重跑 collect_data.py generate_track_c() 對單一情境的邏輯。"""
    taboos = sc.get("taboos", [])
    print(f"  [Track C / {sc['emotion_tone']}] 重新生成 chosen...")
    chosen_prompt = cd.build_track_c_chosen_prompt(sc)
    try:
        chosen = cd.call_claude(chosen_prompt)
        time.sleep(cd.REQUEST_DELAY)
    except Exception as e:
        print(f"    ✗ chosen 失敗：{e}")
        return []

    if has_xian_wording(chosen):
        print("    ! 重新生成後仍是「先」語法，跳過此組（需要人工檢查）")
        return []
    if has_zanmen_wording(chosen):
        print("    ! 重新生成後出現「咱們／咱」，跳過此組（需要人工檢查）")
        return []
    if has_dabashou_wording(chosen):
        print("    ! 重新生成後出現「搭把手」，跳過此組（需要人工檢查）")
        return []
    if has_shouchang_wording(chosen):
        print("    ! 重新生成後出現「收場回家」誤用，跳過此組（需要人工檢查）")
        return []
    if has_touyiju_wording(chosen):
        print("    ! 重新生成後出現「頭一句」（省略話字），跳過此組（需要人工檢查）")
        return []
    if has_bookish_verb_complement(chosen):
        print("    ! 重新生成後出現生硬動補搭配（做下來/說下去），跳過此組（需要人工檢查）")
        return []
    if has_leaked_self_check(chosen):
        print("    ! 重新生成後偵測到自我檢查洩漏，跳過此組（需要人工檢查）")
        return []
    violation = check_chosen_against_rules(chosen, cd.TRACK_C_REJECTION_RULES, taboos=taboos)
    if violation:
        print(f"    ! chosen 違反規則檢查：{violation}，跳過此組（需要人工檢查）")
        return []

    covered_w = cd._covered_w_before(sc["next_w"])
    inference_prompt = cd.build_track_c_inference_prompt(sc, covered_w=covered_w, skipped_w=[], taboos=taboos)

    pairs = []
    for rule_name, rule_desc in cd.TRACK_C_REJECTION_RULES.items():
        if rule_name == "touches_taboo" and not taboos:
            continue
        print(f"    [{rule_name}] 生成 rejected...")
        rejection_prompt = cd.build_track_c_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)
        rejected = None
        for attempt in range(3):
            try:
                candidate = cd.call_claude(rejection_prompt, model=cd.MODEL_REJECTED)
                time.sleep(cd.REQUEST_DELAY)
            except Exception as e:
                print(f"      ✗ rejected 失敗（attempt {attempt+1}）：{e}")
                continue
            if candidate == chosen:
                continue
            rejected = candidate
            break
        if rejected is None:
            print(f"      ✗ [{rule_name}] 三次均失敗或 chosen==rejected，跳過")
            continue
        pairs.append({
            "prompt": inference_prompt,
            "chosen": [{"role": "assistant", "content": chosen}],
            "rejected": [{"role": "assistant", "content": rejected}],
            "meta": {
                "scenario_id": f"track_c_{sc['emotion_tone']}",
                "step": "TRACK_C",
                "rejection_rule": rule_name,
                "track": "C",
                "emotion_tone": sc["emotion_tone"],
                "taboos": taboos,
            },
        })
    return pairs


def cmd_fix_wording(args) -> None:
    existing = load_existing()
    print(f"現有 train.jsonl：{len(existing)} 筆")
    # 供 regenerate_track_a_scenario_step 反推前一步驟真正涵蓋的W維度用
    # （見該函式說明）。用丟棄前的完整 existing 建查表，即使前一步驟這次
    # 也剛好要被重新生成，至少還是拿它「丟棄前」的真實內容當參考，比繼續
    # 套用寫死假設值準確。
    chosen_by_step = cd.build_chosen_lookup(existing)

    a_keys = find_affected_track_a(existing)
    c_keys = find_affected_track_c(existing)
    print(f"偵測到「先」語法的 Track A (scenario, step) 組合：{len(a_keys)} 組")
    for sid, step in sorted(a_keys):
        print(f"  - {sid} {step}")
    print(f"偵測到「先」語法的 Track C emotion_tone 組合：{len(c_keys)} 組")
    for tone in sorted(c_keys):
        print(f"  - {tone}")

    scenarios = json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))
    by_id = {sc["id"]: sc for sc in scenarios}
    track_c_by_tone = {sc["emotion_tone"]: sc for sc in cd.TRACK_C_SCENARIOS}

    keep = [
        p for p in existing
        if not (
            (p["meta"]["track"] == "A" and (p["meta"]["scenario_id"], p["meta"]["step"]) in a_keys)
            or (p["meta"]["track"] == "C" and p["meta"].get("emotion_tone") in c_keys)
        )
    ]
    print(f"\n保留：{len(keep)} 筆（丟棄 {len(existing) - len(keep)} 筆「先」語法 pair）")

    new_pairs: list[dict] = []
    print("\n=== 重新生成受影響組合（Track A）===")
    for sid, step in sorted(a_keys):
        sc = by_id.get(sid)
        if sc is None:
            print(f"  ! 找不到 {sid}，跳過")
            continue
        new_pairs.extend(regenerate_track_a_scenario_step(sc, step, chosen_by_step))

    print("\n=== 重新生成受影響組合（Track C）===")
    for tone in sorted(c_keys):
        sc = track_c_by_tone.get(tone)
        if sc is None:
            print(f"  ! 找不到 track_c_{tone}，跳過")
            continue
        new_pairs.extend(regenerate_track_c_scenario(sc))

    all_pairs = keep + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"\n完成！新生成 {len(new_pairs)} 筆，train.jsonl 總計 {len(all_pairs)} 筆")
    print(f"輸出：{cd.OUTPUT_FILE}")


# ══════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DPO 訓練資料／模型品質工具（validate/evaluate/filter/fix-wording）"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="訓練前驗證 train.jsonl 品質（純靜態分析）")

    p_evaluate = subparsers.add_parser("evaluate", help="對已部署的 Ollama 模型評測輸出違規率")
    p_evaluate.add_argument("--model", required=True, help="要測試的 Ollama 模型名稱，例如 rememo-llama3")
    p_evaluate.add_argument("--compare", help="要比較的基準模型名稱，例如 cwchang/llama-3-taiwan-8b-instruct:Q4_K_M")
    p_evaluate.add_argument("--sample", type=int, default=0, help="每個 Track 只抽樣測試 N 筆情境（預設 %(default)s = 全部）")
    p_evaluate.add_argument("--seed", type=int, default=42)
    p_evaluate.add_argument("--tracks", default="A,B,C,D", help="要測試的軌跡，逗號分隔，例如 A,B（預設全部）")

    subparsers.add_parser("filter", help="從 train.jsonl 移除 chosen 本身違規的 pair")

    subparsers.add_parser("fix-wording", help="修補用了特定不自然詞語（先/咱們/搭把手等）的舊資料")

    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "validate":
        cmd_validate(args)
    elif args.command == "evaluate":
        cmd_evaluate(args)
    elif args.command == "filter":
        cmd_filter(args)
    elif args.command == "fix-wording":
        cmd_fix_wording(args)


if __name__ == "__main__":
    main()
