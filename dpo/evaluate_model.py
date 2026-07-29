#!/usr/bin/env python3
"""
DPO 訓練後模型的自動化規則評測

對 dpo/scenarios.json 裡的每個案例組出跟生產環境一致的 prompt
（格式對齊 dpo/collect_data.py 的 build_inference_prompt），
送給指定的 Ollama 模型生成 STEP1/STEP2/STEP3 問題，
再用 dpo/validate_data.py 同一套規則（長度、是非題、記憶測試、
雙重提問、視覺錨點、STEP1問Why）檢查輸出，統計違規率。

涵蓋全部四條軌跡：
  Track A 問題品質（STEP1/2/3）  — 用 scenarios.json 重現
  Track B 情緒引導（危機處理）   — 用 collect_data.py 的 EMOTIONAL_SCENARIOS 重現
  Track C 情緒感知承接 + 問題    — 用 collect_data.py 的 TRACK_C_SCENARIOS 重現
  Track D 收尾引導              — 用 collect_data.py 的 TRACK_D_SCENARIOS 重現

B/C/D 的情境資料定義在 collect_data.py（不需要 ANTHROPIC_API_KEY 才能 import，
其 Anthropic client 是延遲建立的），這裡直接重用同一份情境與 taboos 標記，
確保「訓練資料長什麼樣」跟「評測時考什麼」不會兩邊各自維護、逐漸兜不起來。

使用前：
  Ollama 裡需要有要測試的模型，例如：
    ollama pull cwchang/llama-3-taiwan-8b-instruct:Q4_K_M   # 訓練前 base
    ollama create rememo-llama3 -f dpo/output/Modelfile      # 訓練後

執行：
  python dpo/evaluate_model.py --model rememo-llama3
  python dpo/evaluate_model.py --model rememo-llama3 --compare cwchang/llama-3-taiwan-8b-instruct:Q4_K_M
  python dpo/evaluate_model.py --model rememo-llama3 --sample 30
  python dpo/evaluate_model.py --model rememo-llama3 --tracks A,B

環境變數：
  OLLAMA_HOST（預設 http://localhost:11434）
"""

import argparse
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from collect_data import (
    EMOTIONAL_SCENARIOS,
    TRACK_C_SCENARIOS,
    TRACK_D_SCENARIOS,
    build_emotional_inference_prompt,
    build_track_c_inference_prompt,
    build_track_d_inference_prompt,
    build_inference_prompt as _cd_build_inference_prompt,
    _covered_w_before,
)

SCENARIOS_FILE = Path(__file__).parent / "scenarios.json"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# 與 dpo/collect_data.py generate_track_a 的 step_builders 保持一致
STEP_CONTEXT = {
    "STEP1": {"covered_w": [], "elder_response_key": None},
    "STEP2": {"covered_w": ["Where"], "elder_response_key": "elder_step1_response"},
    "STEP3": {"covered_w": ["Where", "Who"], "elder_response_key": "elder_step2_response"},
}


def build_inference_prompt(step: str, scenario: dict) -> list[dict]:
    """
    委派給 dpo/collect_data.py 的 build_inference_prompt，system prompt 直接讀
    app/prompts/question_5w1h.txt（正式環境實際使用的內容），不在這裡另外維護
    一份 hardcoded 字串——避免重演「question_5w1h.txt 加了新規則、這裡忘記
    同步」的問題（先前 STEP1 用「你」還是「您」、markdown 禁令都各自漏過一次）。
    """
    elder = scenario["elder"]
    ctx = STEP_CONTEXT[step]
    elder_response = scenario.get(ctx["elder_response_key"], "") if ctx["elder_response_key"] else ""

    return _cd_build_inference_prompt(
        step=step,
        elder=elder,
        scene=scenario["scene"],
        covered_w=ctx["covered_w"],
        topic_category=scenario.get("topic_category"),
        elder_response=elder_response,
        taboos=elder.get("taboos"),
    )


# ── 規則檢查（邏輯與 dpo/validate_data.py 一致） ────────────────────────────

# 本機模型偶爾會把「標籤：」單獨放一行，真正的內容寫在下一行，不是接在冒號後面
# （例如「場景文字：\n許奶奶坐在布行櫃檯後方…」）。舊版 extract_* 只看標籤那一行
# 本身，遇到這種格式會抓到空字串，把模型真正生成的內容當成空白漏掉——這裡加上
# 「同一行沒內容就往下一行找」的容錯，比照 extract_followup_b 原本就有的邏輯。
# 只有下一行不是「另一個已知欄位標籤」時才採用，避免把下一個欄位的內容誤當成這
# 個欄位的答案。
_FIELD_PREFIXES = ("場景文字：", "問題：", "問題類型：", "本回合已涵蓋的W：", "承接語：", "收尾語：")


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
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("問題："):
            inline = line.strip()[3:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None


def extract_scene_text(content: str) -> str | None:
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("場景文字："):
            inline = line.strip()[5:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None


def treats_elder_as_photo_subject(scene_text: str, elder_name: str) -> bool:
    """
    question_5w1h.txt 明訂場景文字要用第三人稱描述畫面本身（像描述一幅畫），
    不能寫成長者正站在畫面裡（範例：不寫「你正站在這個海港，望著夕陽」，改寫
    「夕陽下的海港，漁船正陸續返航」）——畫面是 AI 生成的示意圖，長者本人不是
    照片裡的主角。用兩個訊號抓這個違規：
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


_TEMPLATE_ECHO_RE = re.compile(r"^[（(].*[）)]$")


def is_leaked_or_empty(q: str | None) -> bool:
    """
    q 是 extract_question 的回傳值。q is None（整行「問題：」都沒輸出）由
    missing_question_line 負責計算；這裡處理另外兩種「有問題：這一行，
    但內容不是真的問題」的情況：
      1. 內容是空字串（本地弱模型偶爾只吐格式標籤，沒接內容）
      2. 內容整句被單一括號包住（本地弱模型把 prompt 裡的格式說明或提示
         文字原封不動抄回來，例如「（≤15字，開放式，開頭要有畫面中的具體
         物件）」或「（提示：鄉間車站月台…）」——這是 orchestrator.py
         註解裡說的「範本回聲」，跟問題內容本身寫得好不好是兩回事）
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


def cjk_len(s: str) -> int:
    return sum(1 for c in s if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")


def is_yesno(q: str) -> bool:
    return (bool(re.search(r"嗎[？?]?\s*$", q)) or
            bool(re.search(r"(是不是|有沒有|對不對|好不好|會不會)", q)))


def is_memory_test(q: str) -> bool:
    return bool(re.search(r"(你|您)(還)?記(得|不記得)", q))


def has_double_question(q: str) -> bool:
    return (q.count("？") + q.count("?")) >= 2


def has_anchor(q: str, elements: list[str]) -> bool:
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


# ── 對應 question_5w1h.txt 後續新增的規則（見該檔【提問規則】/【禁止事項】） ───

def uses_polite_nin(text: str) -> bool:
    """稱呼一律用「你」，「您」念起來太正式，會破壞老朋友聊天的溫暖感。"""
    return "您" in text


_MARKDOWN_LEAK_RE = re.compile(r"\*\*|##|`|^\s*[-*]\s", re.MULTILINE)


def has_markdown_leak(text: str) -> bool:
    """禁止任何 markdown 語法——這段文字會直接餵給 TTS 唸給長者聽。"""
    return bool(_MARKDOWN_LEAK_RE.search(text))


_PRECISE_FACT_RE = re.compile(r"哪一年|什麼名字|叫什麼|哪一位|幾年出生")


def asks_precise_fact(q: str) -> bool:
    """禁止問需要精確數字、年份、人名或地名的問題，長者答不出來容易挫折。"""
    return bool(_PRECISE_FACT_RE.search(q))


_TREATS_SCENE_AS_REAL_RE = re.compile(
    r"(有沒有|是不是|會不會)[^？?]{0,6}(來過|去過|待過|認得|熟悉)|認不認得|認得這裡|認得這個地方"
)


def treats_scene_as_real(q: str) -> bool:
    """禁止把 AI 生成的示意圖問成長者真的去過、認得的特定地方（畫面只是引子）。"""
    return bool(_TREATS_SCENE_AS_REAL_RE.search(q))


def content_touches_taboo(text: str, taboos: list[str]) -> bool:
    """Layer 1 粗篩（比照 app/safety/taboo_checker.py），純字面比對，抓不到語意違規。"""
    return any(t in text for t in taboos)


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


# ── Track B/C/D 的解析工具（邏輯與 validate_data.py 一致） ──────────────────

def extract_ack_b(content: str) -> str | None:
    lines = content.split("\n")
    for i, line in enumerate(lines):
        clean = line.strip().lstrip("*").rstrip("*").strip()
        if clean.startswith("情緒回應："):
            inline = clean[6:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None


def extract_followup_b(content: str) -> str | None:
    lines = content.split("\n")
    for i, line in enumerate(lines):
        clean = line.strip().lstrip("*").rstrip("*").strip()
        if clean.startswith("後續引導："):
            inline = clean[6:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None


def extract_ack_c(content: str) -> str | None:
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("承接語："):
            inline = line.strip()[4:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None


def extract_closing_text(content: str) -> str | None:
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("收尾語："):
            inline = line.strip()[4:].strip()
            return inline if inline else _fallback_next_line(lines, i)
    return None


# ── Track B/C/D 的規則檢查（違反時回傳 True） ────────────────────────────
# 簽名統一為 (content: str, scenario: dict) -> bool，跟 Track A 的
# (q, elements, step) 簽名不同，因為 B/D 沒有場景元素、C 才有。

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
    return build_emotional_inference_prompt(sc["trigger"], taboos=sc.get("taboos", []))


def build_prompt_c(sc: dict) -> list[dict]:
    covered_w = _covered_w_before(sc["next_w"])
    return build_track_c_inference_prompt(sc, covered_w=covered_w, skipped_w=[], taboos=sc.get("taboos", []))


def build_prompt_d(sc: dict) -> list[dict]:
    return build_track_d_inference_prompt(sc, emotion=sc.get("emotion", "happy"))


# ── Ollama 呼叫 ──────────────────────────────────────────────────────────

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


# ── 評測主流程 ──────────────────────────────────────────────────────────

def evaluate(model: str, scenarios: list[dict]) -> dict:
    fail_counts: dict[str, int] = {name: 0 for name, _ in CHECKS}
    examples: dict[str, list[dict]] = {name: [] for name, _ in CHECKS}
    total = 0

    for sc in scenarios:
        for step in ("STEP1", "STEP2", "STEP3"):
            total += 1
            messages = build_inference_prompt(step, sc)
            try:
                content = call_ollama(model, messages)
            except (urllib.error.URLError, TimeoutError, KeyError) as e:
                print(f"  [WARN] {sc['id']} {step} 呼叫失敗（{e}），重試一次...")
                try:
                    content = call_ollama(model, messages)
                except (urllib.error.URLError, TimeoutError, KeyError) as e2:
                    print(f"  [WARN] {sc['id']} {step} 重試仍失敗：{e2}")
                    continue

            q = extract_question(content)
            elements = sc["scene"]["elements"]
            elder_name = sc["elder"]["name"]

            for name, check in CHECKS:
                if check(q, elements, step, content, elder_name):
                    fail_counts[name] += 1
                    if len(examples[name]) < 3:
                        examples[name].append({"id": sc["id"], "step": step, "q": q})

    return {"model": model, "total": total, "fail_counts": fail_counts, "examples": examples}


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


# ── Track B/C/D 的通用評測流程 ──────────────────────────────────────────

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


def main() -> None:
    parser = argparse.ArgumentParser(description="自動化規則評測訓練後的模型（涵蓋 Track A/B/C/D）")
    parser.add_argument("--model", required=True, help="要測試的 Ollama 模型名稱，例如 rememo-llama3")
    parser.add_argument("--compare", help="要比較的基準模型名稱，例如 cwchang/llama-3-taiwan-8b-instruct:Q4_K_M")
    parser.add_argument("--sample", type=int, default=0, help="每個 Track 只抽樣測試 N 筆情境（預設 %(default)s = 全部）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tracks", default="A,B,C,D", help="要測試的軌跡，逗號分隔，例如 A,B（預設全部）")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

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

        print(f"\n=== Track A：問題品質 === 共 {len(scenarios)} 個情境 × 3 個 STEP = {len(scenarios) * 3} 筆測試")
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
        print(f"\n=== Track B：情緒引導（危機處理） === 共 {len(sampled(EMOTIONAL_SCENARIOS))} 個情境")
        _run_generic_track("B", args.model, args.compare, sampled(EMOTIONAL_SCENARIOS), build_prompt_b, CHECKS_B)

    if "C" in tracks:
        print(f"\n=== Track C：情緒感知承接 + 問題 === 共 {len(sampled(TRACK_C_SCENARIOS))} 個情境")
        _run_generic_track("C", args.model, args.compare, sampled(TRACK_C_SCENARIOS), build_prompt_c, CHECKS_C)

    if "D" in tracks:
        print(f"\n=== Track D：收尾引導 === 共 {len(sampled(TRACK_D_SCENARIOS))} 個情境")
        _run_generic_track("D", args.model, args.compare, sampled(TRACK_D_SCENARIOS), build_prompt_d, CHECKS_D)


if __name__ == "__main__":
    main()
