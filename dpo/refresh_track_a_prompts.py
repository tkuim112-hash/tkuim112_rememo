#!/usr/bin/env python3
"""
把 train.jsonl 裡所有 Track A 資料的 prompt 欄位重新算一次。

背景：prompt 欄位是用 build_inference_prompt() 讀取 app/prompts/question_5w1h.txt
即時渲染出來的（見 collect_data.py 的 _load_production_system_prompt() 註解），
不是寫死存在 train.jsonl 裡的固定文字。question_5w1h.txt 改過（拿掉過度僵化的
錨點語法規則、精簡「的時候」判準、去掉重複的切入角度清單）之後，只有這次重新
生成的 72 組會自動套用新版——其餘舊資料的 prompt 欄位還停在生成當下的舊版文字，
造成同一份 train.jsonl 裡新舊 prompt 版本並存。

這支腳本純粹重新渲染 prompt 欄位，不呼叫任何 API、不動 chosen/rejected 內容，
免費、可重複執行。

執行：
  python dpo/refresh_track_a_prompts.py
"""
import json
import sys

import collect_data as cd

sys.stdout.reconfigure(encoding="utf-8")

STEP_COVERED_W = {
    "STEP1": [],
    "STEP2": ["Where"],
    "STEP3": ["Where", "Who"],
}
STEP_RESPONSE_FIELD = {
    "STEP1": None,
    "STEP2": "elder_step1_response",
    "STEP3": "elder_step2_response",
}


def main() -> None:
    scenarios = json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))
    by_id = {sc["id"]: sc for sc in scenarios}

    with cd.OUTPUT_FILE.open(encoding="utf-8") as f:
        pairs = [json.loads(line) for line in f if line.strip()]

    updated = 0
    skipped_missing_scenario = 0
    unchanged = 0

    for p in pairs:
        meta = p["meta"]
        if meta["track"] != "A":
            continue
        sid = meta["scenario_id"]
        step = meta["step"]
        sc = by_id.get(sid)
        if sc is None:
            skipped_missing_scenario += 1
            continue

        elder = sc["elder"]
        scene = sc["scene"]
        response_field = STEP_RESPONSE_FIELD[step]
        elder_response = sc.get(response_field, "") if response_field else ""

        new_prompt = cd.build_inference_prompt(
            step, elder, scene, STEP_COVERED_W[step],
            topic_category=sc.get("topic_category"),
            elder_response=elder_response,
            taboos=meta.get("taboos", []),
        )

        if new_prompt != p["prompt"]:
            p["prompt"] = new_prompt
            updated += 1
        else:
            unchanged += 1

    print(f"Track A 總筆數：{updated + unchanged + skipped_missing_scenario}")
    print(f"已更新：{updated}")
    print(f"內容沒變化：{unchanged}")
    print(f"找不到對應 scenario、跳過：{skipped_missing_scenario}")

    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"\n完成，寫回：{cd.OUTPUT_FILE}")


if __name__ == "__main__":
    main()
