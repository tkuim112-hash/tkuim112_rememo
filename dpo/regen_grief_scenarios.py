#!/usr/bin/env python3
"""
針對特定 scenario_id 重新生成 Track A 訓練資料，不用整批重跑 collect_data.py。

背景：sc059-063（哀傷之事情境）先前 elder.taboos 是空陣列，導致
touches_taboo 這條 rejection rule 被跳過（generate_track_a 裡
`if rule_name == "touches_taboo" and not taboos: continue`）。
scenarios.json 已經補上這 5 筆的 taboos，這支腳本負責：
  1. 從 train.jsonl 移除這幾個 scenario_id 舊的 Track A pair（沒有
     touches_taboo 訓練訊號的舊資料）
  2. 只對這幾個 scenario_id 重新呼叫 generate_track_a()
  3. 合併寫回 train.jsonl，重新計算 stats.json

跟 resume_collect_data.py 不同：resume_collect_data.py 是補「完全沒
生成過」的 Track B/C/D 情境；這支是「已生成過但資料本身有缺陷、
需要整批汰換重生」的 Track A 情境，兩者情境不同所以分開寫，避免
共用邏輯反而把兩種語意混在一起。

執行前設定：
  export ANTHROPIC_API_KEY="sk-ant-..."

執行（會呼叫真實 API，非免費）：
  python dpo/regen_grief_scenarios.py
"""
import json
import sys

import collect_data as cd

TARGET_SCENARIO_IDS = {"sc059", "sc060", "sc061", "sc062", "sc063"}


def load_existing() -> list[dict]:
    if not cd.OUTPUT_FILE.exists():
        return []
    pairs = []
    with cd.OUTPUT_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def recompute_stats(all_pairs: list[dict]) -> dict:
    from collections import defaultdict

    stats = {
        "total": len(all_pairs),
        "track_a": sum(1 for p in all_pairs if p["meta"]["track"] == "A"),
        "track_b": sum(1 for p in all_pairs if p["meta"]["track"] == "B"),
        "track_c": sum(1 for p in all_pairs if p["meta"]["track"] == "C"),
        "track_d": sum(1 for p in all_pairs if p["meta"]["track"] == "D"),
        "by_step": {},
        "by_rejection_rule": {},
    }
    by_step: dict[str, int] = defaultdict(int)
    by_rule: dict[str, int] = defaultdict(int)
    for p in all_pairs:
        by_step[p["meta"]["step"]] += 1
        by_rule[p["meta"]["rejection_rule"]] += 1
    stats["by_step"] = dict(by_step)
    stats["by_rejection_rule"] = dict(by_rule)
    return stats


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    with cd.SCENARIOS_FILE.open(encoding="utf-8") as f:
        all_scenarios = json.load(f)
    target_scenarios = [sc for sc in all_scenarios if sc["id"] in TARGET_SCENARIO_IDS]
    missing = TARGET_SCENARIO_IDS - {sc["id"] for sc in target_scenarios}
    if missing:
        print(f"⚠ scenarios.json 裡找不到：{missing}")
    print(f"目標情境：{[sc['id'] for sc in target_scenarios]}")
    for sc in target_scenarios:
        print(f"  [{sc['id']}] taboos = {sc['elder'].get('taboos')}")

    existing = load_existing()
    print(f"\n現有 train.jsonl：{len(existing)} 筆")

    keep = [
        p for p in existing
        if not (p["meta"]["track"] == "A" and p["meta"]["scenario_id"] in TARGET_SCENARIO_IDS)
    ]
    discarded = len(existing) - len(keep)
    print(f"移除舊的 Track A pair：{discarded} 筆（沒有 touches_taboo 訓練訊號的舊版）")

    print("\n=== 重新生成 Track A（僅限目標情境） ===")
    new_pairs = cd.generate_track_a(target_scenarios)
    print(f"新生成：{len(new_pairs)} 筆")

    by_rule = {}
    for p in new_pairs:
        by_rule[p["meta"]["rejection_rule"]] = by_rule.get(p["meta"]["rejection_rule"], 0) + 1
    print(f"新資料的 rejection_rule 分佈：{by_rule}")
    has_taboo_rule = any(p["meta"]["rejection_rule"] == "touches_taboo" for p in new_pairs)
    print(f"是否包含 touches_taboo 訓練樣本：{'是 ✓' if has_taboo_rule else '否 ✗（有問題，請檢查）'}")

    all_pairs = keep + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    stats = recompute_stats(all_pairs)
    cd.STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成！train.jsonl 總計：{len(all_pairs)} 筆（原 {len(existing)} 筆）")
    print(f"輸出：{cd.OUTPUT_FILE}")
    print(f"統計：{cd.STATS_FILE}")


if __name__ == "__main__":
    main()
