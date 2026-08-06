#!/usr/bin/env python3
"""
Claude（正式版模型）小型試跑腳本。

四條軌跡各抽幾筆場景印出來看效果，直接呼叫 collect_data.py 裡現成的
call_claude()（用正式的 claude-sonnet-4-6 / claude-haiku-4-5-20251001），
不用另外接 client，因為 collect_data.py 本身就是設計給 Claude 用的。

**不會寫入 dpo/data/train.jsonl**，跑完看終端機印出來的結果就好，
確認效果沒問題後，再用 python dpo/collect_data.py 跑完整資料生成
（那個會產生全部 ~4029 筆並寫檔，也會花比較多 API 費用）。

執行前：
  pip install anthropic python-dotenv（.env 裡的 ANTHROPIC_API_KEY 已經有了）

執行：
  python dpo/test_claude_sample.py
"""
import collect_data as cd

call_count = 0
_orig_call_claude = cd.call_claude


def call_claude_counted(*args, **kwargs):
    global call_count
    call_count += 1
    return _orig_call_claude(*args, **kwargs)


cd.call_claude = call_claude_counted


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

        chosen = cd.call_claude(cd.build_step1_user_prompt(sc))
        show("chosen", chosen)

        for rule_name, rule_desc in sample_rules(cd.QUESTION_REJECTION_RULES, taboos, "touches_taboo"):
            rejected = cd.call_claude(
                cd.build_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos),
                model=cd.MODEL_REJECTED,
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

        chosen = cd.call_claude(cd.build_emotional_chosen_prompt(sc["trigger"], sc["context"], taboos))
        show("chosen", chosen)

        for rule_name, rule_desc in sample_rules(cd.EMOTION_REJECTION_RULES, taboos, "dwell_on_taboo"):
            rejected = cd.call_claude(
                cd.build_emotional_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos),
                model=cd.MODEL_REJECTED,
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

        chosen = cd.call_claude(cd.build_track_c_chosen_prompt(sc))
        show("chosen", chosen)

        for rule_name, rule_desc in sample_rules(cd.TRACK_C_REJECTION_RULES, taboos, "touches_taboo"):
            rejected = cd.call_claude(
                cd.build_track_c_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos),
                model=cd.MODEL_REJECTED,
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

        chosen = cd.call_claude(cd.build_track_d_chosen_prompt(sc))
        show("chosen", chosen)

        for rule_name, rule_desc in sample_rules(cd.TRACK_D_REJECTION_RULES, taboos, "touches_taboo"):
            rejected = cd.call_claude(
                cd.build_track_d_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos),
                model=cd.MODEL_REJECTED,
            )
            show(f"rejected（{rule_name}）", rejected)


def main() -> None:
    scenarios_a = cd.json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))

    sample_track_a(scenarios_a)
    sample_track_b(cd.EMOTIONAL_SCENARIOS)
    sample_track_c(cd.TRACK_C_SCENARIOS)
    sample_track_d(cd.TRACK_D_SCENARIOS)

    print("\n" + "=" * 70)
    print(f"完成，總共呼叫 Claude API {call_count} 次。")
    print("=" * 70)


if __name__ == "__main__":
    main()
