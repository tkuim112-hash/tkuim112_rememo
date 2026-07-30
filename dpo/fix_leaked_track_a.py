#!/usr/bin/env python3
"""
修補 Track A 裡「自我檢查過程洩漏到 chosen」的 27 個 (scenario, step) 組合。

2026-07 稽核發現：collect_data.py 完整跑完後，有 273 筆 Track A pair 的
chosen 混進了模型自我修正的旁白文字（「---」分隔線＋「等等，我需要重新
檢查...」），對應到 27 個不重複的 (scenario_id, step) 組合（同一個 chosen
會被 10 條 rejection rule 各配一次，所以 27 組 × 平均 10 筆 ≈ 273 筆）。

SYSTEM_PROMPT_THERAPIST 已經補上「禁止洩漏自我檢查」的防護，這支腳本
移除這 27 組的舊 pair，用修好的 prompt 重新生成，其餘 4913 筆不動。

執行：
  python dpo/fix_leaked_track_a.py
"""
import json
import re
import sys
import time
from collections import defaultdict

import collect_data as cd
import filter_data as fd


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


def find_leaked_scenario_steps(existing: list[dict]) -> set[tuple[str, str]]:
    leaked = set()
    for obj in existing:
        if obj["meta"]["track"] != "A":
            continue
        if fd.has_leaked_self_check(obj["chosen"][0]["content"]):
            leaked.add((obj["meta"]["scenario_id"], obj["meta"]["step"]))
    return leaked


def regenerate_scenario_step(sc: dict, step: str) -> list[dict]:
    """重跑 collect_data.py generate_track_a() 對單一 (scenario, step) 的邏輯。"""
    elder = sc["elder"]
    scene = sc["scene"]
    step_builders = {
        "STEP1": (cd.build_step1_user_prompt, [], []),
        "STEP2": (cd.build_step2_user_prompt, ["Where"], ["STEP1"]),
        "STEP3": (cd.build_step3_user_prompt, ["Where", "Who"], ["STEP1", "STEP2"]),
    }
    prompt_builder, covered_w, _ = step_builders[step]

    print(f"  [{sc['id']}] {step} — 重新生成 chosen...")
    user_prompt = prompt_builder(sc)
    try:
        chosen = cd.call_claude(user_prompt)
        time.sleep(cd.REQUEST_DELAY)
    except Exception as e:
        print(f"    x chosen 失敗：{e}")
        return []

    if fd.has_leaked_self_check(chosen):
        print(f"    ! 重新生成後仍偵測到洩漏，跳過此組（需要人工檢查）")
        return []

    step_responses = {
        "STEP1": "",
        "STEP2": sc.get("elder_step1_response", ""),
        "STEP3": sc.get("elder_step2_response", ""),
    }
    taboos = elder.get("taboos", [])
    inference_prompt = cd.build_inference_prompt(
        step, elder, scene, covered_w,
        topic_category=sc.get("topic_category"),
        elder_response=step_responses[step],
        taboos=taboos,
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
                print(f"      x rejected 失敗（attempt {attempt+1}）：{e}")
                continue
            if candidate == chosen:
                continue
            rejected = candidate
            break
        if rejected is None:
            print(f"      x [{rule_name}] 三次均失敗或 chosen==rejected，跳過")
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


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    existing = load_existing()
    print(f"現有 train.jsonl：{len(existing)} 筆")

    leaked_keys = find_leaked_scenario_steps(existing)
    print(f"偵測到洩漏的 (scenario, step) 組合：{len(leaked_keys)} 組")
    for sid, step in sorted(leaked_keys):
        print(f"  - {sid} {step}")

    scenarios = cd.json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))
    by_id = {sc["id"]: sc for sc in scenarios}

    keep = [
        p for p in existing
        if not (p["meta"]["track"] == "A"
                and (p["meta"]["scenario_id"], p["meta"]["step"]) in leaked_keys)
    ]
    print(f"\n保留：{len(keep)} 筆（丟棄 {len(existing) - len(keep)} 筆洩漏 pair）")

    new_pairs: list[dict] = []
    print("\n=== 重新生成受影響組合 ===")
    for sid, step in sorted(leaked_keys):
        sc = by_id.get(sid)
        if sc is None:
            print(f"  ! 找不到 {sid}，跳過")
            continue
        new_pairs.extend(regenerate_scenario_step(sc, step))

    all_pairs = keep + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"\n完成！新生成 {len(new_pairs)} 筆，train.jsonl 總計 {len(all_pairs)} 筆")
    print(f"輸出：{cd.OUTPUT_FILE}")


if __name__ == "__main__":
    main()
