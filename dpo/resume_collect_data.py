#!/usr/bin/env python3
"""
補跑 collect_data.py 因帳號額度用完而中斷的部分。

上一次執行在 Track B 跑到約 44/59 個情境時額度被打光，Track C、Track D
完全沒跑到（詳見 dpo/data/stats.json：track_a=3555 完整、track_b=495
不完整、track_c=0、track_d=0）。

這支腳本：
1. 讀現有 train.jsonl，用 meta.trigger_context 比對每個 Track B 情境
   是否已經生成完所有適用的 rejection rule——不完整的情境會被視為「待補」，
   其殘留的部分 pair 會被丟棄重新生成（避免同一情境裡混雜兩種不同的
   chosen 文字，那樣訓練資料會不一致）
2. 只補生成待補的 Track B 情境 + 全部 Track C + 全部 Track D（Track A
   已經 100% 完整，不會重新呼叫，不會多花錢）
3. 把「保留的舊資料」+「新生成的資料」一起覆寫回 train.jsonl，並重新
   計算 stats.json

執行：
  python dpo/resume_collect_data.py
"""
import json
import sys
from collections import defaultdict

import collect_data as cd


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


def split_track_b(existing: list[dict]) -> tuple[list[dict], list[dict]]:
    """回傳 (keep, incomplete_scenarios)：keep 是可以保留的完整 Track B pair，
    incomplete_scenarios 是 EMOTIONAL_SCENARIOS 裡需要重新生成的情境
    （沒生成過，或只生成一部分）。"""
    have: dict[str, set[str]] = defaultdict(set)
    for p in existing:
        if p["meta"]["track"] == "B":
            have[p["meta"]["trigger_context"]].add(p["meta"]["rejection_rule"])

    complete_contexts: set[str] = set()
    incomplete_scenarios: list[dict] = []
    for emo in cd.EMOTIONAL_SCENARIOS:
        taboos = emo.get("taboos", [])
        expected = {
            r for r in cd.EMOTION_REJECTION_RULES
            if not (r == "dwell_on_taboo" and not taboos)
        }
        if expected.issubset(have.get(emo["context"], set())):
            complete_contexts.add(emo["context"])
        else:
            incomplete_scenarios.append(emo)

    keep = [
        p for p in existing
        if p["meta"]["track"] != "B" or p["meta"]["trigger_context"] in complete_contexts
    ]
    return keep, incomplete_scenarios


def recompute_stats(all_pairs: list[dict]) -> dict:
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

    existing = load_existing()
    print(f"現有 train.jsonl：{len(existing)} 筆")

    keep, incomplete_b = split_track_b(existing)
    discarded = len(existing) - len(keep)
    print(f"Track B 待補：{len(incomplete_b)}/{len(cd.EMOTIONAL_SCENARIOS)} 個情境"
          f"（丟棄 {discarded} 筆不完整的殘留 pair，重新生成）")

    new_pairs: list[dict] = []

    if incomplete_b:
        print("\n=== 補 Track B ===")
        original_b = cd.EMOTIONAL_SCENARIOS
        cd.EMOTIONAL_SCENARIOS = incomplete_b
        try:
            new_b = cd.generate_track_b()
        finally:
            cd.EMOTIONAL_SCENARIOS = original_b
        print(f"Track B 補完：{len(new_b)} 筆")
        new_pairs.extend(new_b)

    print("\n=== 補 Track C（全部） ===")
    new_c = cd.generate_track_c()
    print(f"Track C 完成：{len(new_c)} 筆")
    new_pairs.extend(new_c)

    print("\n=== 補 Track D（全部） ===")
    new_d = cd.generate_track_d()
    print(f"Track D 完成：{len(new_d)} 筆")
    new_pairs.extend(new_d)

    all_pairs = keep + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    stats = recompute_stats(all_pairs)
    cd.STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成！本次新增 {len(new_pairs)} 筆")
    print(f"train.jsonl 總計：{len(all_pairs)} 筆")
    print(f"輸出：{cd.OUTPUT_FILE}")
    print(f"統計：{cd.STATS_FILE}")


if __name__ == "__main__":
    main()
