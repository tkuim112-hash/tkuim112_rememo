#!/usr/bin/env python3
"""
修補 train.jsonl 裡 chosen 問題用了「先做什麼／先準備／先夾／先看」這種語法的
(scenario, step) 組合（Track A）跟 (emotion_tone) 組合（Track C）。

2026-07 使用者回饋：不喜歡「V之前/之後，你都先做什麼」這個「先」字，且這個句型
在範例庫裡佔比過高、生成結果同質化。已經修正：
  - app/prompts/question_5w1h.txt（正式環境 + Track A 的 inference prompt 共用）
  - dpo/collect_data.py 的 build_step1/2/3_user_prompt、build_track_c_chosen_prompt
    （這三支各自內嵌了一份獨立的規則文字，不是從 question_5w1h.txt 讀的，之前
    沒發現這裡也要修，改完 question_5w1h.txt 沒有同步改到這裡的話，重新生成
    出來的 chosen 還是會沿用舊語法）

這支腳本找出舊資料裡踩到「先」語法的 chosen，丟棄後用修好的 prompt 重新生成。

執行：
  python dpo/fix_xian_wording.py
"""
import json
import re
import sys
import time

import collect_data as cd
import filter_data as fd

_XIAN_RE = re.compile(r"先(做|夾|準備|備|看|想|說|走|怎麼)")


def extract_question(content: str) -> str:
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("問題："):
            return line[len("問題："):].strip()
        if line.startswith("承接語：") or line.startswith("場景文字："):
            continue
    return ""


def has_xian_wording(content: str) -> bool:
    q = extract_question(content)
    return bool(_XIAN_RE.search(q))


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


def find_affected_track_a(existing: list[dict]) -> set[tuple[str, str]]:
    affected = set()
    for obj in existing:
        if obj["meta"]["track"] != "A":
            continue
        if has_xian_wording(obj["chosen"][0]["content"]):
            affected.add((obj["meta"]["scenario_id"], obj["meta"]["step"]))
    return affected


def find_affected_track_c(existing: list[dict]) -> set[str]:
    affected = set()
    for obj in existing:
        if obj["meta"]["track"] != "C":
            continue
        if has_xian_wording(obj["chosen"][0]["content"]):
            affected.add(obj["meta"]["emotion_tone"])
    return affected


def regenerate_track_a_scenario_step(sc: dict, step: str) -> list[dict]:
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
        print(f"    ✗ chosen 失敗：{e}")
        return []

    if has_xian_wording(chosen):
        print("    ! 重新生成後仍是「先」語法，跳過此組（需要人工檢查）")
        return []
    if fd.has_leaked_self_check(chosen):
        print("    ! 重新生成後偵測到自我檢查洩漏，跳過此組（需要人工檢查）")
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
    if fd.has_leaked_self_check(chosen):
        print("    ! 重新生成後偵測到自我檢查洩漏，跳過此組（需要人工檢查）")
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


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    existing = load_existing()
    print(f"現有 train.jsonl：{len(existing)} 筆")

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
        new_pairs.extend(regenerate_track_a_scenario_step(sc, step))

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


if __name__ == "__main__":
    main()
