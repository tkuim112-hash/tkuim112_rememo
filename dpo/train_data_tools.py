#!/usr/bin/env python3
"""
train.jsonl 維護工具合集（診斷／報告／格式重算／一次性修復）。

原本是各自獨立的腳本，共用邏輯都已經在 data_quality.py／collect_data.py
裡，這裡合併成一支多用途 CLI，用 subcommand 區分：

  diversity       問題文字多樣性檢查（原 check_question_diversity.py）
                  Track A/C/D 的 chosen/rejected「問題：」欄位是否逐字相同，
                  抓出可能的標記錯誤。唯讀，不寫入任何檔案。

  review          抽樣產生人工複查用的 HTML 報告（原 review_train_data.py）
                  直接讀 train.jsonl，不需要本機模型服務。唯讀。

  refresh-prompts 重新渲染 Track A 的 prompt 欄位（原 refresh_track_a_prompts.py）
                  question_5w1h.txt 改過之後，用來把舊資料的 prompt 欄位
                  同步成最新版。不呼叫 API、不動 chosen/rejected，可重複執行。

  fix-leaked-a    修補 Track A「自我檢查過程洩漏到 chosen」的一次性修復
                  （原 fix_leaked_track_a.py，2026-07 稽核，27 組 scenario/step）。
                  會呼叫 Claude API 並寫回 train.jsonl。

  fix-no-anchor   修補 Track A no_anchor 規則裡 rejected 其實仍有錨點的一次性
                  修復（原 fix_no_anchor_rejected.py，2026-08 稽核，95 筆）。
                  會呼叫 Claude API 並寫回 train.jsonl，預設預覽模式。

  fix-grief-a     修補 sc059-063（哀傷之事情境）缺 touches_taboo 訓練訊號的
                  一次性修復（原 regen_grief_scenarios.py）。這幾筆 elder.taboos
                  原本是空陣列，導致 touches_taboo 這條 rejection rule 被跳過；
                  scenarios.json 補上 taboos 後，這裡移除舊 Track A pair、
                  只對這 5 個 scenario_id 重新呼叫 generate_track_a()。
                  會呼叫 Claude API 並寫回 train.jsonl，會重算 stats.json。

  backfill-track-c-rules
                  補上 TRACK_C_REJECTION_RULES 2026-08 新增的 7 條規則
                  （too_clinical/over_dramatize/give_advice/compare_suffering/
                  false_positivity/over_identify/focus_on_loss）在既有 Track C
                  情境上的訓練對。既有 chosen／inference prompt 不變，只針對
                  「這個情境還沒生成過這條新規則的 rejected」的組合呼叫 API，
                  新 pair 附加進 train.jsonl，不動任何既有資料。可重複執行
                  （已存在的組合會自動跳過）。會呼叫 Claude API，預設預覽模式。

  remove-step2-a  移除 Track A STEP2（已棄用格式）的所有 pair。2026-08 確認
                  正式環境 orchestrator 從不會用到「場景文字＋問題、無承接語」
                  這個格式，STEP1 之後一律走 Track C（自由追問）或 STEP3
                  （補問），collect_data.generate_track_a() 早已停止生成新的
                  STEP2，但 train.jsonl 裡的舊資料一直沒清掉（1310 筆，佔
                  Track A 總筆數 1/3）。不呼叫 API，執行前會先完整備份
                  train.jsonl。預設預覽模式。

執行範例：
  python dpo/train_data_tools.py diversity
  python dpo/train_data_tools.py review --sample 20 --seed 7
  python dpo/train_data_tools.py review --tracks A,D
  python dpo/train_data_tools.py refresh-prompts
  python dpo/train_data_tools.py fix-leaked-a
  python dpo/train_data_tools.py fix-no-anchor            # 預覽，不呼叫 API
  python dpo/train_data_tools.py fix-no-anchor --apply    # 實際呼叫 API 並寫回
  python dpo/train_data_tools.py fix-grief-a
  python dpo/train_data_tools.py backfill-track-c-rules           # 預覽
  python dpo/train_data_tools.py backfill-track-c-rules --apply   # 實際呼叫 API 並寫回
  python dpo/train_data_tools.py remove-step2-a           # 預覽
  python dpo/train_data_tools.py remove-step2-a --apply   # 先備份，再移除並寫回
"""

import argparse
import json
import random
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"


# ============================================================
# diversity — 問題文字多樣性檢查
# ============================================================

_QUESTION_LEVEL_RULES = {
    "is_yesno", "double_question", "too_long", "memory_test",
    "no_anchor", "wrong_w_priority", "template_echo",
}


def cmd_diversity(args: argparse.Namespace) -> None:
    from data_quality import extract_question_a, extract_question_c, extract_question_d

    extractors = {"A": extract_question_a, "C": extract_question_c, "D": extract_question_d}

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
            extractor = extractors.get(track)
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


# ============================================================
# review — 抽樣產生人工複查用的 HTML 報告
# ============================================================

_REVIEW_CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Noto Sans TC", "Microsoft JhengHei", sans-serif;
       max-width: 1100px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }
h1 { font-size: 1.4rem; }
h2.track-title { margin-top: 2.5rem; padding-top: 1rem; border-top: 3px solid #8886; }
.pair { border: 1px solid #8884; border-radius: 10px; margin-bottom: 1.2rem; padding: 1rem 1.2rem; }
.pair h3 { margin-top: 0; font-size: 1rem; }
.meta { color: #888; font-size: 0.85rem; margin-bottom: 0.6rem; }
.cols { display: flex; gap: 1rem; flex-wrap: wrap; }
.col { flex: 1; min-width: 260px; border-radius: 8px; padding: 0.7rem 0.9rem; }
.chosen { background: rgba(46,160,67,0.10); }
.rejected { background: rgba(220,80,80,0.10); }
.col .label { font-weight: 600; font-size: 0.85rem; margin-bottom: 0.4rem; opacity: 0.8; }
.toc { columns: 6; margin-bottom: 1rem; }
.toc a { font-size: 0.85rem; }
.rule-tag { display: inline-block; background: rgba(128,128,128,0.15); border-radius: 4px;
            padding: 0.1rem 0.4rem; font-size: 0.8rem; margin-left: 0.4rem; }
"""


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _as_html_block(content: str) -> str:
    return _html_escape(content).replace("\n", "<br>")


def _load_pairs() -> list[dict]:
    if not DATA_FILE.exists():
        print(f"找不到 {DATA_FILE}，請先跑 collect_data.py")
        sys.exit(1)
    pairs = []
    with DATA_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def _review_group_key(obj: dict) -> str:
    """同一個 chosen 會配好幾條 rejection rule，用這個 key 把同一組的 pair
    聚在一起顯示，而不是每個 rejection rule 各自散開。"""
    meta = obj["meta"]
    track = meta["track"]
    if track == "A":
        return f"A|{meta.get('scenario_id')}|{meta.get('step')}"
    if track == "B":
        return f"B|{meta.get('trigger_context')}"
    if track == "C":
        return f"C|{meta.get('scenario_id')}"
    return f"D|{meta.get('scenario_id')}"


def _review_build_section(track: str, label: str, groups: list[list[dict]]) -> tuple[str, str]:
    ids = [f"{track.lower()}{i}" for i in range(1, len(groups) + 1)]
    toc = "".join(f'<a href="#{gid}">{gid}</a> ' for gid in ids)

    sections = []
    for gid, group in zip(ids, groups):
        first = group[0]
        meta = first["meta"]
        chosen = first["chosen"][0]["content"]

        header_bits = []
        if meta.get("scenario_id"):
            header_bits.append(str(meta["scenario_id"]))
        if meta.get("step"):
            header_bits.append(meta["step"])
        if meta.get("trigger_context"):
            header_bits.append(meta["trigger_context"][:24])
        if meta.get("today_topic"):
            header_bits.append(meta["today_topic"])
        header = _html_escape("　".join(header_bits))

        rejected_blocks = []
        for obj in group:
            rule = obj["meta"].get("rejection_rule", "")
            rejected = obj["rejected"][0]["content"]
            rejected_blocks.append(f"""
  <div class="col rejected">
    <div class="label">rejected<span class="rule-tag">{_html_escape(rule)}</span></div>
    {_as_html_block(rejected)}
  </div>""")

        sections.append(f"""
<div class="pair" id="{gid}">
  <h3>[{gid}] {header}</h3>
  <div class="cols">
    <div class="col chosen"><div class="label">chosen</div>{_as_html_block(chosen)}</div>
    {"".join(rejected_blocks)}
  </div>
</div>""")

    return (
        f'<h2 class="track-title">{label}（{len(groups)} 組）</h2><div class="toc">{toc}</div>',
        "".join(sections),
    )


def cmd_review(args: argparse.Namespace) -> None:
    tracks = {t.strip().upper() for t in args.tracks.split(",") if t.strip()}

    pairs = _load_pairs()
    print(f"train.jsonl 共 {len(pairs)} 筆")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for obj in pairs:
        grouped[_review_group_key(obj)].append(obj)

    by_track: dict[str, list[list[dict]]] = defaultdict(list)
    for key, group in grouped.items():
        track = key.split("|", 1)[0]
        by_track[track].append(group)

    rng = random.Random(args.seed)
    toc_parts, body_parts = [], []
    labels = {"A": "Track A：問題品質", "B": "Track B：情緒引導",
              "C": "Track C：承接＋追問", "D": "Track D：收尾引導"}

    total_groups = 0
    for track in ("A", "B", "C", "D"):
        if track not in tracks or track not in by_track:
            continue
        groups = by_track[track]
        rng.shuffle(groups)
        sampled = groups[: args.sample] if args.sample else groups
        total_groups += len(sampled)
        print(f"  Track {track}: 抽 {len(sampled)}/{len(groups)} 組")
        toc, body = _review_build_section(track, labels[track], sampled)
        toc_parts.append(toc)
        body_parts.append(body)

    html = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>train.jsonl 人工複查報告</title>
<style>{_REVIEW_CSS}</style></head>
<body>
<h1>train.jsonl 人工複查報告</h1>
<p class="meta">資料檔：{DATA_FILE}　總筆數：{len(pairs)}　本次抽樣：{total_groups} 組（chosen 相同 rejected 已合併顯示）</p>
{''.join(toc_parts)}
{''.join(body_parts)}
</body></html>"""

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"\n報告已產生：{output_path.resolve()}")
    print("直接用瀏覽器打開即可閱讀。")


# ============================================================
# refresh-prompts — 重新渲染 Track A 的 prompt 欄位
# ============================================================

_STEP_RESPONSE_FIELD = {
    "STEP1": None,
    "STEP2": "elder_step1_response",
    "STEP3": "elder_step2_response",
}


def cmd_refresh_prompts(args: argparse.Namespace) -> None:
    import collect_data as cd

    scenarios = json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))
    by_id = {sc["id"]: sc for sc in scenarios}

    with cd.OUTPUT_FILE.open(encoding="utf-8") as f:
        pairs = [json.loads(line) for line in f if line.strip()]

    # (scenario_id, step) → chosen 文字查表，供 cd.real_covered_w_from_lookup
    # 反推前一步驟真正涵蓋的W維度（見 collect_data.py 的說明）。
    chosen_by_step = cd.build_chosen_lookup(pairs)

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
        response_field = _STEP_RESPONSE_FIELD[step]
        elder_response = sc.get(response_field, "") if response_field else ""

        covered_w = cd.real_covered_w_from_lookup(chosen_by_step, sid, step)
        target_w = cd._pick_target_w(covered_w) if step == "STEP3" else None

        new_prompt = cd.build_inference_prompt(
            step, elder, scene, covered_w,
            topic_category=sc.get("topic_category"),
            elder_response=elder_response,
            taboos=meta.get("taboos", []),
            target_w=target_w,
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


# ============================================================
# fix-leaked-a — 修補 Track A 自我檢查過程洩漏（2026-07，27 組）
# ============================================================

def _fix_leaked_find_scenario_steps(existing: list[dict]) -> set[tuple[str, str]]:
    from data_quality import has_leaked_self_check

    leaked = set()
    for obj in existing:
        if obj["meta"]["track"] != "A":
            continue
        if has_leaked_self_check(obj["chosen"][0]["content"]):
            leaked.add((obj["meta"]["scenario_id"], obj["meta"]["step"]))
    return leaked


def _fix_leaked_regenerate_scenario_step(
    sc: dict, step: str, chosen_lookup: dict[tuple[str, str], str] | None = None,
) -> list[dict]:
    """重跑 collect_data.py generate_track_a() 對單一 (scenario, step) 的邏輯。"""
    import collect_data as cd
    from data_quality import has_leaked_self_check

    elder = sc["elder"]
    scene = sc["scene"]
    prompt_builders = {
        "STEP1": cd.build_step1_user_prompt,
        "STEP2": cd.build_step2_user_prompt,
        "STEP3": cd.build_step3_user_prompt,
    }
    covered_w = (
        cd.real_covered_w_from_lookup(chosen_lookup, sc["id"], step) if chosen_lookup else []
    )
    target_w = cd._pick_target_w(covered_w) if step == "STEP3" else None

    print(f"  [{sc['id']}] {step} — 重新生成 chosen...")
    if step == "STEP3":
        user_prompt = prompt_builders[step](sc, covered_w=covered_w, target_w=target_w)
    else:
        user_prompt = prompt_builders[step](sc)
    try:
        chosen = cd.call_claude(user_prompt)
        time.sleep(cd.REQUEST_DELAY)
    except Exception as e:
        print(f"    x chosen 失敗：{e}")
        return []

    if has_leaked_self_check(chosen):
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
        target_w=target_w,
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


def cmd_fix_leaked_a(args: argparse.Namespace) -> None:
    import collect_data as cd
    from data_quality import load_existing

    existing = load_existing()
    print(f"現有 train.jsonl：{len(existing)} 筆")
    # 供 _fix_leaked_regenerate_scenario_step 反推前一步驟真正涵蓋的W維度用
    # （見該函式說明）。用丟棄前的完整 existing 建查表。
    chosen_lookup = cd.build_chosen_lookup(existing)

    leaked_keys = _fix_leaked_find_scenario_steps(existing)
    print(f"偵測到洩漏的 (scenario, step) 組合：{len(leaked_keys)} 組")
    for sid, step in sorted(leaked_keys):
        print(f"  - {sid} {step}")

    scenarios = json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))
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
        new_pairs.extend(_fix_leaked_regenerate_scenario_step(sc, step, chosen_lookup))

    all_pairs = keep + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"\n完成！新生成 {len(new_pairs)} 筆，train.jsonl 總計 {len(all_pairs)} 筆")
    print(f"輸出：{cd.OUTPUT_FILE}")


# ============================================================
# fix-no-anchor — 修補 Track A no_anchor 的 rejected（2026-08，95 筆）
# ============================================================

_FIX_NO_ANCHOR_MAX_ATTEMPTS = 4


def _fix_no_anchor_find_broken(records: list[dict]) -> list[int]:
    """回傳需要修的 record index：track A、規則是 no_anchor、但 rejected 問題其實仍有錨點。"""
    from data_quality import extract_question_a, extract_scene_elements, has_anchor

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


def _fix_no_anchor_regenerate_one(rec: dict) -> str | None:
    """回傳通過 has_anchor 驗證的新 rejected content；MAX_ATTEMPTS 次都不過回傳 None。"""
    from collect_data import MODEL_REJECTED, QUESTION_REJECTION_RULES, REQUEST_DELAY, build_rejection_prompt, call_claude
    from data_quality import extract_question_a, extract_scene_elements, has_anchor

    chosen = rec["chosen"][0]["content"]
    taboos = rec["meta"].get("taboos", [])
    elements = extract_scene_elements(rec["prompt"])
    rejection_prompt = build_rejection_prompt(
        chosen, "no_anchor", QUESTION_REJECTION_RULES["no_anchor"], taboos=taboos
    )

    for attempt in range(_FIX_NO_ANCHOR_MAX_ATTEMPTS):
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


def cmd_fix_no_anchor(args: argparse.Namespace) -> None:
    from data_quality import extract_question_a

    if not DATA_FILE.exists():
        print(f"找不到 {DATA_FILE}")
        sys.exit(1)

    lines = [line for line in DATA_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]

    broken = _fix_no_anchor_find_broken(records)
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
        new_rejected = _fix_no_anchor_regenerate_one(records[i])
        if new_rejected is None:
            print(f"    ✗ {_FIX_NO_ANCHOR_MAX_ATTEMPTS} 次都沒通過驗證，保留原資料，列入待人工處理")
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


# ============================================================
# fix-grief-a — 修補 sc059-063 缺 touches_taboo 訓練訊號（一次性）
# ============================================================

_GRIEF_TARGET_SCENARIO_IDS = {"sc059", "sc060", "sc061", "sc062", "sc063"}


def _fix_grief_recompute_stats(all_pairs: list[dict]) -> dict:
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


def cmd_fix_grief_a(args: argparse.Namespace) -> None:
    import collect_data as cd
    from data_quality import load_existing

    with cd.SCENARIOS_FILE.open(encoding="utf-8") as f:
        all_scenarios = json.load(f)
    target_scenarios = [sc for sc in all_scenarios if sc["id"] in _GRIEF_TARGET_SCENARIO_IDS]
    missing = _GRIEF_TARGET_SCENARIO_IDS - {sc["id"] for sc in target_scenarios}
    if missing:
        print(f"⚠ scenarios.json 裡找不到：{missing}")
    print(f"目標情境：{[sc['id'] for sc in target_scenarios]}")
    for sc in target_scenarios:
        print(f"  [{sc['id']}] taboos = {sc['elder'].get('taboos')}")

    existing = load_existing()
    print(f"\n現有 train.jsonl：{len(existing)} 筆")

    keep = [
        p for p in existing
        if not (p["meta"]["track"] == "A" and p["meta"]["scenario_id"] in _GRIEF_TARGET_SCENARIO_IDS)
    ]
    discarded = len(existing) - len(keep)
    print(f"移除舊的 Track A pair：{discarded} 筆（沒有 touches_taboo 訓練訊號的舊版）")

    print("\n=== 重新生成 Track A（僅限目標情境） ===")
    new_pairs = cd.generate_track_a(target_scenarios)
    print(f"新生成：{len(new_pairs)} 筆")

    by_rule: dict[str, int] = {}
    for p in new_pairs:
        by_rule[p["meta"]["rejection_rule"]] = by_rule.get(p["meta"]["rejection_rule"], 0) + 1
    print(f"新資料的 rejection_rule 分佈：{by_rule}")
    has_taboo_rule = any(p["meta"]["rejection_rule"] == "touches_taboo" for p in new_pairs)
    print(f"是否包含 touches_taboo 訓練樣本：{'是 ✓' if has_taboo_rule else '否 ✗（有問題，請檢查）'}")

    all_pairs = keep + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    stats = _fix_grief_recompute_stats(all_pairs)
    cd.STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成！train.jsonl 總計：{len(all_pairs)} 筆（原 {len(existing)} 筆）")
    print(f"輸出：{cd.OUTPUT_FILE}")
    print(f"統計：{cd.STATS_FILE}")


# ============================================================
# backfill-track-c-rules — 補上 TRACK_C_REJECTION_RULES 2026-08 新增的 7 條規則
# ============================================================

_TRACK_C_NEW_RULES = {
    "too_clinical", "over_dramatize", "give_advice", "compare_suffering",
    "false_positivity", "over_identify", "focus_on_loss",
}


def cmd_backfill_track_c_rules(args: argparse.Namespace) -> None:
    import collect_data as cd
    from data_quality import load_existing

    existing = load_existing()
    print(f"現有 train.jsonl：{len(existing)} 筆")

    # 每個 emotion_tone 只需要一筆參考 pair 就能拿到 chosen／prompt／taboos
    # （同一情境的所有 pair 共用同一個 chosen，只有 rejected 不同）。
    ref_by_tone: dict[str, dict] = {}
    for p in existing:
        meta = p["meta"]
        if meta["track"] != "C":
            continue
        tone = meta.get("emotion_tone")
        if tone and tone not in ref_by_tone:
            ref_by_tone[tone] = p
    print(f"現有 Track C 情境：{len(ref_by_tone)} 組")

    already_have = {
        (p["meta"].get("emotion_tone"), p["meta"]["rejection_rule"])
        for p in existing
        if p["meta"]["track"] == "C"
    }

    new_rules = {
        name: desc for name, desc in cd.TRACK_C_REJECTION_RULES.items()
        if name in _TRACK_C_NEW_RULES
    }
    to_generate = [
        (tone, rule_name)
        for tone in ref_by_tone
        for rule_name in new_rules
        if (tone, rule_name) not in already_have
    ]

    print(f"需要新生成的 (情境, 規則) 組合：{len(to_generate)} 筆")
    for tone, rule_name in to_generate[:10]:
        print(f"  - {tone} / {rule_name}")
    if len(to_generate) > 10:
        print(f"  ...共 {len(to_generate)} 筆")

    if not args.apply:
        print("\n（預覽模式，未呼叫 API、未寫入檔案。加上 --apply 才會實際執行並花費 API 額度。）")
        return

    new_pairs = []
    for tone, rule_name in to_generate:
        ref = ref_by_tone[tone]
        chosen = ref["chosen"][0]["content"]
        taboos = ref["meta"].get("taboos", [])
        rule_desc = new_rules[rule_name]

        print(f"  [{tone}] [{rule_name}] 生成 rejected...")
        rejection_prompt = cd.build_track_c_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)
        rejected = None
        for attempt in range(3):
            try:
                candidate = cd.call_claude(rejection_prompt, model=cd.MODEL_REJECTED)
                time.sleep(cd.REQUEST_DELAY)
            except Exception as e:
                print(f"    ✗ rejected 失敗（attempt {attempt + 1}）：{e}")
                continue
            if candidate == chosen:
                print(f"    ⚠ rejected==chosen，重試（attempt {attempt + 1}）...")
                continue
            rejected = candidate
            break

        if rejected is None:
            print(f"    ✗ [{tone}/{rule_name}] 三次均失敗或 chosen==rejected，跳過")
            continue

        new_pairs.append({
            "prompt": ref["prompt"],
            "chosen": [{"role": "assistant", "content": chosen}],
            "rejected": [{"role": "assistant", "content": rejected}],
            "meta": {
                "scenario_id": f"track_c_{tone}",
                "step": "TRACK_C",
                "rejection_rule": rule_name,
                "track": "C",
                "emotion_tone": tone,
                "taboos": taboos,
            },
        })

    all_pairs = existing + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for p in all_pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"\n完成：新增 {len(new_pairs)} 筆，train.jsonl 總計 {len(all_pairs)} 筆")
    print(f"輸出：{cd.OUTPUT_FILE}")


# ============================================================
# remove-step2-a — 移除 Track A STEP2（已棄用格式）的所有 pair
# ============================================================

def cmd_remove_step2_a(args: argparse.Namespace) -> None:
    if not DATA_FILE.exists():
        print(f"找不到 {DATA_FILE}")
        sys.exit(1)

    lines = [line for line in DATA_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    print(f"現有 train.jsonl：{len(records)} 筆")

    is_step2_a = lambda r: r["meta"]["track"] == "A" and r["meta"]["step"] == "STEP2"
    to_remove = [r for r in records if is_step2_a(r)]
    keep = [r for r in records if not is_step2_a(r)]

    print(f"找到 {len(to_remove)} 筆 Track A STEP2（已棄用格式，正式環境從不會用到）")
    affected_scenarios = sorted({r["meta"]["scenario_id"] for r in to_remove})
    print(f"涉及 {len(affected_scenarios)} 個 scenario_id：{affected_scenarios[:10]}"
          f"{'...' if len(affected_scenarios) > 10 else ''}")

    if not args.apply:
        print(f"\n（預覽模式，未寫入檔案。移除後將剩餘 {len(keep)} 筆。加上 --apply 才會實際備份並寫回。）")
        return

    backup_path = DATA_FILE.parent / f"train.jsonl.bak_before_remove_step2a_{date.today().isoformat()}"
    shutil.copy(DATA_FILE, backup_path)
    print(f"已備份完整 train.jsonl 至：{backup_path}")

    with DATA_FILE.open("w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n完成：移除 {len(to_remove)} 筆，train.jsonl 剩餘 {len(keep)} 筆")
    print(f"輸出：{DATA_FILE}")


# ============================================================
# entry point
# ============================================================

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="train.jsonl 維護工具合集（診斷／報告／格式重算／一次性修復）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("diversity", help="問題文字多樣性檢查（唯讀）").set_defaults(func=cmd_diversity)

    p_review = sub.add_parser("review", help="抽樣產生人工複查用的 HTML 報告（唯讀）")
    p_review.add_argument("--sample", type=int, default=15, help="每個 track 各抽幾組（預設15）")
    p_review.add_argument("--seed", type=int, default=42)
    p_review.add_argument("--tracks", default="A,B,C,D")
    p_review.add_argument("--output", default="dpo/output/train_data_review.html")
    p_review.set_defaults(func=cmd_review)

    sub.add_parser(
        "refresh-prompts", help="重新渲染 Track A 的 prompt 欄位（不呼叫 API）"
    ).set_defaults(func=cmd_refresh_prompts)

    sub.add_parser(
        "fix-leaked-a", help="修補 Track A 自我檢查洩漏（2026-07，27組，呼叫 API）"
    ).set_defaults(func=cmd_fix_leaked_a)

    p_fix_anchor = sub.add_parser(
        "fix-no-anchor", help="修補 Track A no_anchor 的 rejected（2026-08，95筆，呼叫 API）"
    )
    p_fix_anchor.add_argument("--apply", action="store_true", help="實際呼叫 API 並寫回 train.jsonl")
    p_fix_anchor.set_defaults(func=cmd_fix_no_anchor)

    sub.add_parser(
        "fix-grief-a", help="修補 sc059-063 缺 touches_taboo 訓練訊號（一次性，呼叫 API）"
    ).set_defaults(func=cmd_fix_grief_a)

    p_backfill_c = sub.add_parser(
        "backfill-track-c-rules",
        help="補上 TRACK_C_REJECTION_RULES 2026-08 新增的 7 條規則（呼叫 API）",
    )
    p_backfill_c.add_argument("--apply", action="store_true", help="實際呼叫 API 並寫回 train.jsonl")
    p_backfill_c.set_defaults(func=cmd_backfill_track_c_rules)

    p_remove_step2 = sub.add_parser(
        "remove-step2-a",
        help="移除 Track A STEP2（已棄用格式，1310筆）。不呼叫API，會先備份",
    )
    p_remove_step2.add_argument("--apply", action="store_true", help="實際備份並寫回 train.jsonl")
    p_remove_step2.set_defaults(func=cmd_remove_step2_a)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
