#!/usr/bin/env python3
"""
問題文字多樣性檢查

Track A/C/D 的 DPO 訓練對子裡，如果 chosen 跟 rejected 的「問題：」欄位逐字相同，
代表這筆資料的對比訊號不是來自「問題怎麼問」，而是來自其他欄位（場景文字、承接語、
收尾語等）。這類資料本身未必是壞資料——例如 elder_as_photo_subject 規則就是刻意
只改場景文字的人稱寫法——但如果拿來評估「問題品質」的訓練效果，這些筆數會稀釋訊號，
應該先篩掉。依 rejection_rule 分布也能抓出標記錯誤的情況：如果某筆被標成
no_anchor（照理該規則要靠問題本身有沒有錨點來區分），但問題文字卻跟 chosen
逐字相同，代表 rejected 根本沒有真的違反 no_anchor，屬於資料品質問題。

執行：
  python dpo/check_question_diversity.py
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from validate_data import extract_question_a, extract_question_c, extract_question_d

DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"

_EXTRACTORS = {"A": extract_question_a, "C": extract_question_c, "D": extract_question_d}

# rejection_rule 本身就該讓 rejected 問題文字跟 chosen 不同；如果問題逐字相同卻
# 標成這幾種規則，代表對比訊號沒有真的落在問題欄位上，屬於標記錯誤。
_QUESTION_LEVEL_RULES = {
    "is_yesno", "double_question", "too_long", "memory_test",
    "no_anchor", "wrong_w_priority", "template_echo",
}


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    if not DATA_FILE.exists():
        print(f"找不到 {DATA_FILE}")
        sys.exit(1)

    total_by_track = Counter()
    same_by_track = Counter()
    same_by_rule = defaultdict(Counter)
    mislabeled = defaultdict(list)
    examples = defaultdict(list)

    with DATA_FILE.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            rec = json.loads(raw)
            track = rec["meta"]["track"]
            extractor = _EXTRACTORS.get(track)
            if extractor is None:
                continue  # Track B 沒有「問題：」欄位，不適用這個檢查

            total_by_track[track] += 1

            chosen = rec["chosen"][0]["content"]
            rejected = rec["rejected"][0]["content"]
            q_chosen = extractor(chosen)
            q_rejected = extractor(rejected)

            if q_chosen is not None and q_chosen == q_rejected:
                same_by_track[track] += 1
                rule = rec["meta"]["rejection_rule"]
                same_by_rule[track][rule] += 1

                info = {
                    "line": lineno,
                    "sid": rec["meta"]["scenario_id"],
                    "rule": rule,
                    "q": q_chosen,
                }
                if len(examples[track]) < 3:
                    examples[track].append(info)
                if rule in _QUESTION_LEVEL_RULES:
                    mislabeled[track].append(info)

    W = 66
    print("=" * W)
    print("  問題文字多樣性檢查（chosen/rejected「問題：」欄位逐字相同）")
    print("=" * W)

    any_track = False
    for track in ("A", "C", "D"):
        total = total_by_track[track]
        if total == 0:
            continue
        any_track = True
        same = same_by_track[track]
        pct = same / total if total else 0
        print(f"\nTrack {track}：{same}/{total} 筆（{pct:.1%}）問題欄位逐字相同")
        if not same:
            continue

        print("  依 rejection_rule 分布：")
        for rule, n in same_by_rule[track].most_common():
            flag = "  ← 應該靠問題本身區分，逐字相同代表可能標記錯誤" \
                if rule in _QUESTION_LEVEL_RULES else ""
            print(f"    {rule}: {n} 筆{flag}")

        print("  範例：")
        for ex in examples[track]:
            print(f"    → line {ex['line']} {ex['sid']} rule={ex['rule']}: {ex['q']}")

    if not any_track:
        print("找不到 Track A/C/D 的資料。")
        return

    print()
    print("=" * W)
    any_mislabel = any(mislabeled.values())
    if any_mislabel:
        print("  ⚠  以下筆數的 rejection_rule 屬於「應由問題本身呈現差異」的規則，")
        print("     但問題文字跟 chosen 逐字相同，rejected 並未真的違反該規則：")
        for track, items in mislabeled.items():
            print(f"     Track {track}: {len(items)} 筆")
            for it in items[:5]:
                print(f"       → line {it['line']} {it['sid']} rule={it['rule']}")
        print("     建議重新生成這幾筆的 rejected（或修正 rejection_rule 標記）。")
    else:
        print("  ✅  沒有發現「問題文字相同但規則宣稱靠問題區分」的標記錯誤。")
        print("     其餘逐字相同的筆數屬於場景文字/承接語/收尾語等其他規則，")
        print("     是合理的訓練訊號，只是不適合拿來評估「問題措辭」品質。")
    print("=" * W)


if __name__ == "__main__":
    main()
