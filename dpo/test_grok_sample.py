#!/usr/bin/env python3
"""
Grok（xAI）小型試跑腳本。

只是借用 collect_data.py 裡已經寫好的場景資料和 prompt builder（跟正式的
Claude 版用同一套規則/taboo 邏輯），把生成呼叫換成 xAI 的 Grok API（跟
OpenAI SDK 相容），抽樣印出來讓你肉眼比對效果。**不會修改 collect_data.py，
也不會寫入 dpo/data/train.jsonl**，跑完看結果就好，不影響正式資料。

xAI 模型名稱常常更新，這裡的 GROK_CHOSEN/GROK_REJECTED 是 2026-07 從
https://docs.x.ai/developers/models 查到的名稱，如果呼叫時出現
model not found，去該網址確認最新名稱再改常數。

執行前：
  1. pip install openai python-dotenv（這台環境已經有 openai 2.45.0）
  2. 在專案根目錄的 .env 加一行：XAI_API_KEY=xai-...

執行：
  python dpo/test_grok_sample.py
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from openai import OpenAI

import collect_data as cd  # noqa: E402  借用場景資料 + prompt builder，不需要 ANTHROPIC_API_KEY（client 是延遲建立的）

# 比照正式版 Sonnet(品質)/Haiku(便宜快速) 的混合策略分工
# （2026-07 從 https://docs.x.ai/developers/models 查到的目前可用名稱：
#  grok-4.5 是旗艦品質模型；grok-build-0.1 是最便宜、適合高量簡單任務的模型。
#  xAI 常常更新模型陣容，呼叫失敗就回上面那個網址核對最新名稱。）
GROK_CHOSEN = "grok-4.5"
GROK_REJECTED = "grok-build-0.1"

if "XAI_API_KEY" not in os.environ:
    raise SystemExit("找不到 XAI_API_KEY，請先在 .env 加一行：XAI_API_KEY=xai-...")

_client = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")

call_count = 0


def call_grok(user_prompt: str, system: str = cd.SYSTEM_PROMPT_THERAPIST, model: str = GROK_CHOSEN) -> str:
    global call_count
    call_count += 1
    resp = _client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content.strip()


def sample_rules(rules: dict, taboos: list, taboo_rule: str, k: int = 2) -> list:
    """從規則字典抽 k 條：沒有 taboo 就排除 taboo 規則，有 taboo 就優先抽到它。"""
    items = list(rules.items())
    if not taboos:
        items = [(n, d) for n, d in items if n != taboo_rule]
    else:
        items.sort(key=lambda x: 0 if x[0] == taboo_rule else 1)
    return items[:k]


def show(label: str, text: str) -> None:
    print(f"\n  [{label}]")
    for line in text.strip().split("\n"):
        print(f"    {line}")


def sample_track_a(scenarios: list, n: int = 3) -> None:
    print("\n" + "=" * 70)
    print(f"Track A（問題品質，STEP1）— 抽 {n} 筆場景")
    print("=" * 70)
    for sc in scenarios[:n]:
        taboos = sc["elder"].get("taboos", [])
        print(f"\n場景 {sc['id']}：{sc['elder']['name']}／{sc['elder']['today_topic']}"
              f"（taboos={taboos or '無'}）")

        chosen = call_grok(cd.build_step1_user_prompt(sc))
        show("chosen", chosen)

        for rule_name, rule_desc in sample_rules(cd.QUESTION_REJECTION_RULES, taboos, "touches_taboo"):
            rejected = call_grok(
                cd.build_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos),
                model=GROK_REJECTED,
            )
            show(f"rejected（{rule_name}）", rejected)


def sample_track_b(scenarios: list, n: int = 2) -> None:
    print("\n" + "=" * 70)
    print(f"Track B（情緒引導）— 抽 {n} 筆場景")
    print("=" * 70)
    for sc in scenarios[:n]:
        taboos = sc.get("taboos", [])
        print(f"\n情境：{sc['context']}（taboos={taboos or '無'}）")
        print(f"  長者：{sc['trigger']}")

        chosen = call_grok(cd.build_emotional_chosen_prompt(sc["trigger"], sc["context"], taboos))
        show("chosen", chosen)

        for rule_name, rule_desc in sample_rules(cd.EMOTION_REJECTION_RULES, taboos, "dwell_on_taboo"):
            rejected = call_grok(
                cd.build_emotional_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos),
                model=GROK_REJECTED,
            )
            show(f"rejected（{rule_name}）", rejected)


def sample_track_c(scenarios: list, n: int = 2) -> None:
    print("\n" + "=" * 70)
    print(f"Track C（承接+追問）— 抽 {n} 筆場景")
    print("=" * 70)
    for sc in scenarios[:n]:
        taboos = sc.get("taboos", [])
        print(f"\n主題：{sc['current_topic']}／情緒：{sc['emotion_desc']}（taboos={taboos or '無'}）")
        print(f"  長者：{sc['elder_response']}")

        chosen = call_grok(cd.build_track_c_chosen_prompt(sc))
        show("chosen", chosen)

        for rule_name, rule_desc in sample_rules(cd.TRACK_C_REJECTION_RULES, taboos, "touches_taboo"):
            rejected = call_grok(
                cd.build_track_c_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos),
                model=GROK_REJECTED,
            )
            show(f"rejected（{rule_name}）", rejected)


def sample_track_d(scenarios: list, n: int = 2) -> None:
    print("\n" + "=" * 70)
    print(f"Track D（收尾）— 抽 {n} 筆場景")
    print("=" * 70)
    for sc in scenarios[:n]:
        taboos = sc.get("taboos", [])
        print(f"\n{sc['elder_name']}／{sc['today_topic']}（taboos={taboos or '無'}）")
        print(f"  長者最後說：{sc['last_elder_response']}")

        chosen = call_grok(cd.build_track_d_chosen_prompt(sc))
        show("chosen", chosen)

        for rule_name, rule_desc in sample_rules(cd.TRACK_D_REJECTION_RULES, taboos, "touches_taboo"):
            rejected = call_grok(
                cd.build_track_d_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos),
                model=GROK_REJECTED,
            )
            show(f"rejected（{rule_name}）", rejected)


def main() -> None:
    scenarios_a = cd.json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))

    sample_track_a(scenarios_a)
    sample_track_b(cd.EMOTIONAL_SCENARIOS)
    sample_track_c(cd.TRACK_C_SCENARIOS)
    sample_track_d(cd.TRACK_D_SCENARIOS)

    print("\n" + "=" * 70)
    print(f"完成，總共呼叫 Grok API {call_count} 次。")
    print("=" * 70)


if __name__ == "__main__":
    main()
