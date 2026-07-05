#!/usr/bin/env python3
"""
DPO 訓練後模型的自動化規則評測

對 dpo/scenarios.json 裡的每個案例組出跟生產環境一致的 prompt
（格式對齊 dpo/collect_data.py 的 build_inference_prompt），
送給指定的 Ollama 模型生成 STEP1/STEP2/STEP3 問題，
再用 dpo/validate_data.py 同一套規則（長度、是非題、記憶測試、
雙重提問、視覺錨點、STEP1問Why）檢查輸出，統計違規率。

只涵蓋 Track A（問題品質），這是訓練資料中佔比最大的軌跡（約78%），
也是唯一可以純用 scenarios.json 重現、不需額外情境資料的部分。

使用前：
  Ollama 裡需要有要測試的模型，例如：
    ollama pull cwchang/llama-3-taiwan-8b-instruct:Q4_K_M   # 訓練前 base
    ollama create rememo-llama3 -f dpo/output/Modelfile      # 訓練後

執行：
  python dpo/evaluate_model.py --model rememo-llama3
  python dpo/evaluate_model.py --model rememo-llama3 --compare cwchang/llama-3-taiwan-8b-instruct:Q4_K_M
  python dpo/evaluate_model.py --model rememo-llama3 --sample 30

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

SCENARIOS_FILE = Path(__file__).parent / "scenarios.json"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

SYSTEM_CONTENT_STEP = (
    "你是溫柔的懷舊療法引導師，正在透過語音陪伴日間照護中心的長者。"
    "長者可能有輕微認知障礙，你說的話會直接被念出來給長者聽。"
    "問題必須念起來自然、溫和、不超過15個字，且開頭要包含畫面中看得到的具體物件。"
)

STEP_INSTRUCTIONS = {
    "STEP1": "生成第一個【開場問題】，引導長者進入回憶（優先問 Where 或 What）",
    "STEP2": "根據長者剛才說的話，順著內容自然追問，不限制哪個W，完全跟著長者走",
    "STEP3": "生成一個【補充問題】，探索還未涵蓋的W維度（Why 僅在長者狀態良好時詢問）",
}
STEP_LABELS = {
    "STEP1": "STEP1開場",
    "STEP2": "STEP2自由追問",
    "STEP3": "STEP3補問",
}
# 與 dpo/collect_data.py generate_track_a 的 step_builders 保持一致
STEP_CONTEXT = {
    "STEP1": {"covered_w": [], "elder_response_key": None},
    "STEP2": {"covered_w": ["Where"], "elder_response_key": "elder_step1_response"},
    "STEP3": {"covered_w": ["Where", "Who"], "elder_response_key": "elder_step2_response"},
}


def build_inference_prompt(step: str, scenario: dict) -> list[dict]:
    elder = scenario["elder"]
    scene = scenario["scene"]
    ctx = STEP_CONTEXT[step]

    elements_str = "、".join(scene["elements"])
    covered_str = "、".join(ctx["covered_w"]) if ctx["covered_w"] else "無"
    topic_str = "、".join(scenario.get("topic_category", [])) or "未指定"
    taboo_str = "、".join(elder.get("taboos", [])) or "無"

    elder_response = scenario.get(ctx["elder_response_key"], "") if ctx["elder_response_key"] else ""
    elder_section = f"\n【長者剛才說的話】\n{elder_response}\n" if elder_response else ""

    user_content = (
        f"【長者資料】\n"
        f"姓名：{elder['name']}\n"
        f"職業背景：{elder['main_occupation']}\n"
        f"今日主題：{elder['today_topic']}\n"
        f"懷舊治療主題類別：{topic_str}\n"
        f"\n【眼前畫面元素】\n{elements_str}\n"
        f"\n【已涵蓋的W維度】\n{covered_str}\n"
        f"{elder_section}"
        f"\n【禁忌話題（絕對不可提及）】\n{taboo_str}\n"
        f"\n【任務】\n{STEP_INSTRUCTIONS[step]}\n"
        f"\n【輸出格式】\n"
        f"場景文字：（30-60字，給長者聽的場景描述）\n"
        f"問題：（≤15字，開放式，開頭要有畫面中的具體物件）\n"
        f"問題類型：{STEP_LABELS[step]}\n"
        f"本回合已涵蓋的W："
    )

    return [
        {"role": "system", "content": SYSTEM_CONTENT_STEP},
        {"role": "user", "content": user_content},
    ]


# ── 規則檢查（邏輯與 dpo/validate_data.py 一致） ────────────────────────────

def extract_question(content: str) -> str | None:
    for line in content.split("\n"):
        if line.strip().startswith("問題："):
            return line.strip()[3:].strip()
    return None


def cjk_len(s: str) -> int:
    return sum(1 for c in s if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")


def is_yesno(q: str) -> bool:
    return (bool(re.search(r"嗎[？?]?\s*$", q)) or
            bool(re.search(r"(是不是|有沒有|對不對|好不好)", q)))


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


CHECKS = [
    ("missing_question_line", lambda q, els, step: q is None),
    ("too_long", lambda q, els, step: q is not None and cjk_len(q) > 15),
    ("is_yesno", lambda q, els, step: q is not None and is_yesno(q)),
    ("memory_test", lambda q, els, step: q is not None and is_memory_test(q)),
    ("double_question", lambda q, els, step: q is not None and has_double_question(q)),
    ("no_anchor", lambda q, els, step: q is not None and not has_anchor(q, els)),
    ("step1_asks_why", lambda q, els, step: q is not None and step == "STEP1" and asks_why(q)),
]


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

            for name, check in CHECKS:
                if check(q, elements, step):
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


def main() -> None:
    parser = argparse.ArgumentParser(description="自動化規則評測訓練後的模型（Track A 問題品質）")
    parser.add_argument("--model", required=True, help="要測試的 Ollama 模型名稱，例如 rememo-llama3")
    parser.add_argument("--compare", help="要比較的基準模型名稱，例如 cwchang/llama-3-taiwan-8b-instruct:Q4_K_M")
    parser.add_argument("--sample", type=int, default=0, help="只抽樣測試 N 筆情境（預設全部 %(default)s = 全部）")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    available = check_ollama_reachable()
    for m in (args.model, args.compare):
        if m and not any(m == a or a.startswith(m + ":") for a in available):
            print(f"  [提醒] 在 Ollama 模型清單中沒看到「{m}」，請確認名稱是否正確（現有：{', '.join(available)}）")

    with SCENARIOS_FILE.open(encoding="utf-8") as f:
        scenarios = json.load(f)

    if args.sample:
        random.Random(args.seed).shuffle(scenarios)
        scenarios = scenarios[: args.sample]

    print(f"共 {len(scenarios)} 個情境 × 3 個 STEP = {len(scenarios) * 3} 筆測試")

    print(f"\n測試模型：{args.model} ...")
    result_model = evaluate(args.model, scenarios)
    print_report(result_model)

    if args.compare:
        print(f"\n測試對照模型：{args.compare} ...")
        result_base = evaluate(args.compare, scenarios)
        print_report(result_base)

        print("\n" + "=" * 62)
        print("  訓練前後比較（違規筆數，越少越好）")
        print("=" * 62)
        for name, _ in CHECKS:
            n_base = result_base["fail_counts"][name]
            n_tuned = result_model["fail_counts"][name]
            delta = n_base - n_tuned
            arrow = "↓ 進步" if delta > 0 else ("↑ 退步" if delta < 0 else "—")
            print(f"  {name:<24}base={n_base:>4}  tuned={n_tuned:>4}  {arrow}")
        print("=" * 62)


if __name__ == "__main__":
    main()
