#!/usr/bin/env python3
"""
修復 Track A no_anchor 規則的 rejected 範例

collect_data.py 生成 rejected 時（generate_track_a 的 retry 迴圈），只檢查
candidate == chosen（完全逐字重複才重試），從來沒有驗證 rejected 是否真的違反
該筆指定的規則。這在 no_anchor 規則下暴露出來：95 筆 rejected 問題其實跟 chosen
一樣有場景錨點，沒有真正違反 no_anchor，訓練訊號是空的（見
dpo/check_question_diversity.py、python dpo/validate_data.py 的
「no_anchor — rejected 仍有錨點」，2026-08 稽核發現）。

這裡針對這批資料重新呼叫 Claude 生成 rejected，沿用 collect_data.py 原本的
build_rejection_prompt + call_claude 呼叫方式，但寫回前額外用 has_anchor() 驗證
新生成的問題真的沒有錨點，驗證不過就重試，重試用盡就保留原資料、列入待人工處理。

執行：
  python dpo/fix_no_anchor_rejected.py          # 預覽會修哪幾筆，不呼叫 API、不寫入
  python dpo/fix_no_anchor_rejected.py --apply  # 實際呼叫 API 重新生成並寫回 train.jsonl
"""

import argparse
import json
import sys
import time
from pathlib import Path

from collect_data import (
    MODEL_REJECTED,
    QUESTION_REJECTION_RULES,
    REQUEST_DELAY,
    build_rejection_prompt,
    call_claude,
)
from validate_data import extract_question_a, extract_scene_elements, has_anchor

DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"
MAX_ATTEMPTS = 4


def find_broken(records: list[dict]) -> list[int]:
    """回傳需要修的 record index：track A、規則是 no_anchor、但 rejected 問題其實仍有錨點。"""
    idxs = []
    for i, rec in enumerate(records):
        meta = rec["meta"]
        if meta["track"] != "A" or meta["rejection_rule"] != "no_anchor":
            continue
        elements = extract_scene_elements(rec["prompt"])
        rejected_q = extract_question_a(rec["rejected"][0]["content"])
        if rejected_q is not None and has_anchor(rejected_q, elements):
            idxs.append(i)
    return idxs


def regenerate_one(rec: dict) -> str | None:
    """回傳通過 has_anchor 驗證的新 rejected content；MAX_ATTEMPTS 次都不過回傳 None。"""
    chosen = rec["chosen"][0]["content"]
    taboos = rec["meta"].get("taboos", [])
    elements = extract_scene_elements(rec["prompt"])
    rejection_prompt = build_rejection_prompt(
        chosen, "no_anchor", QUESTION_REJECTION_RULES["no_anchor"], taboos=taboos
    )

    for attempt in range(MAX_ATTEMPTS):
        try:
            candidate = call_claude(rejection_prompt, model=MODEL_REJECTED)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"      ✗ API 失敗（attempt {attempt + 1}）：{e}")
            continue
        if candidate == chosen:
            print(f"      ⚠ rejected==chosen，重試（attempt {attempt + 1}）...")
            continue
        candidate_q = extract_question_a(candidate)
        if candidate_q is None:
            print(f"      ⚠ 抓不到「問題：」行，重試（attempt {attempt + 1}）...")
            continue
        if has_anchor(candidate_q, elements):
            print(f"      ⚠ 新生成的問題仍有錨點，重試（attempt {attempt + 1}）：{candidate_q}")
            continue
        return candidate

    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="實際呼叫 API 並寫回 train.jsonl")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    if not DATA_FILE.exists():
        print(f"找不到 {DATA_FILE}")
        sys.exit(1)

    lines = [line for line in DATA_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]

    broken = find_broken(records)
    print(f"找到 {len(broken)} 筆 no_anchor 的 rejected 其實仍有錨點，需要重新生成。")
    for i in broken[:10]:
        sid = records[i]["meta"]["scenario_id"]
        step = records[i]["meta"]["step"]
        q = extract_question_a(records[i]["rejected"][0]["content"])
        print(f"  → line {i + 1} {sid} {step}: {q}")
    if len(broken) > 10:
        print(f"  ...共 {len(broken)} 筆")

    if not args.apply:
        print("\n（預覽模式，未呼叫 API、未寫入檔案。加上 --apply 才會實際執行並花費 API 額度。）")
        return

    fixed = 0
    failed = []
    for i in broken:
        sid = records[i]["meta"]["scenario_id"]
        step = records[i]["meta"]["step"]
        print(f"  [{sid} {step}] 重新生成 rejected...")
        new_rejected = regenerate_one(records[i])
        if new_rejected is None:
            print(f"    ✗ {MAX_ATTEMPTS} 次都沒通過驗證，保留原資料，列入待人工處理")
            failed.append(i)
            continue
        records[i]["rejected"] = [{"role": "assistant", "content": new_rejected}]
        fixed += 1

    with DATA_FILE.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n完成：修好 {fixed} 筆，{len(failed)} 筆仍需人工處理。")
    if failed:
        print("需要人工處理的 line：", [i + 1 for i in failed])


if __name__ == "__main__":
    main()
