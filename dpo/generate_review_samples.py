#!/usr/bin/env python3
"""
產生人工抽樣閱讀用的模型回應報告

抽樣 dpo/scenarios.json（Track A）與 dpo/collect_data.py 裡的
EMOTIONAL_SCENARIOS（Track B）／TRACK_C_SCENARIOS（Track C）／
TRACK_D_SCENARIOS（Track D），組出跟生產環境一致的 prompt
（重用 dpo/data_quality.py 的 build_eval_inference_prompt/build_prompt_b/c/d
與 call_ollama），送給指定的 Ollama 模型生成回應，整理成一份 HTML 報告，
方便直接用瀏覽器打開，人工判斷語氣自然度、溫暖度、懷舊療法引導效果。

2026-07 稽核發現：這份報告原本只涵蓋 Track A（問題品質），Track B（情緒
引導）／C（承接+追問）／D（收尾引導）完全沒有樣本可以人工看到——而這三條
軌跡裡有大量規則（例如 give_advice、compare_suffering、false_positivity、
no_emotional_lift、wrong_emotion_match 等語氣類規則）本來就無法用正規表示式
程式化驗證，只能靠人工複查，卻連一份可以看的報告都沒有。這裡補上 B/C/D，
讓 43 條 rejection 規則裡「無法程式驗證」的那些至少有人工複查的機會。

如果本機有 dpo/data/train.jsonl（訓練資料），會附上同一情境當時
Claude Sonnet 生成的「chosen」範例當對照參考（僅供參考，不是絕對標準；
Track B/C/D 的比對鍵是 best-effort，可能因為情境敘述變動而找不到對應範例）。

使用前：
  Ollama 裡需要有要測試的模型，例如：
    ollama create rememo-llama3 -f dpo/output/Modelfile

執行：
  python dpo/generate_review_samples.py --model rememo-llama3
  python dpo/generate_review_samples.py --model rememo-llama3 --compare cwchang/llama-3-taiwan-8b-instruct:Q4_K_M
  python dpo/generate_review_samples.py --model rememo-llama3 --sample 15 --output dpo/output/review.html
  python dpo/generate_review_samples.py --model rememo-llama3 --tracks A,B  # 只看部分軌跡

環境變數：
  OLLAMA_HOST（預設 http://localhost:11434）
"""

import argparse
import json
import random
import sys
import urllib.error
from pathlib import Path

from collect_data import EMOTIONAL_SCENARIOS, TRACK_C_SCENARIOS, TRACK_D_SCENARIOS
from data_quality import (
    SCENARIOS_FILE,
    build_eval_inference_prompt as build_inference_prompt,
    build_prompt_b,
    build_prompt_c,
    build_prompt_d,
    call_ollama,
    check_ollama_reachable,
)

TRAIN_DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"
# 2026-08 Track A 拿掉 STEP2（見 collect_data.generate_track_a 說明），
# 人工複查報告同步只看 STEP1/STEP3。
STEPS = ("STEP1", "STEP3")


def load_reference_chosen() -> dict[str, dict]:
    """
    從本機的 train.jsonl 撈出各軌跡當時的 chosen 範例（若檔案不存在則回傳空字典）。

    各軌跡的比對鍵不同，因為 meta.scenario_id 在 Track B 裡永遠是常數 "emotional"
    （EMOTIONAL_SCENARIOS 本身沒有 id 欄位），沒辦法拿來對應到特定情境，改用
    meta.trigger_context（=情境的 context 文字）當鍵；Track C/D 的 scenario_id
    是用 emotion_tone/elder_name 組出來的，只要情境資料沒改過名字就能對上。
    """
    lookup: dict[str, dict] = {"A": {}, "B": {}, "C": {}, "D": {}}
    if not TRAIN_DATA_FILE.exists():
        return lookup
    with TRAIN_DATA_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta = obj.get("meta", {})
            track = meta.get("track")
            content = obj["chosen"][0]["content"]

            if track == "A":
                key = (meta.get("scenario_id"), meta.get("step"))
                lookup["A"].setdefault(key, content)
            elif track == "B":
                key = meta.get("trigger_context")
                if key:
                    lookup["B"].setdefault(key, content)
            elif track == "C":
                key = meta.get("scenario_id")
                if key:
                    lookup["C"].setdefault(key, content)
            elif track == "D":
                key = meta.get("scenario_id")
                if key:
                    lookup["D"].setdefault(key, content)
    return lookup


def call_with_retry(model: str, messages: list[dict]) -> str:
    try:
        return call_ollama(model, messages)
    except (urllib.error.URLError, TimeoutError, KeyError) as e:
        try:
            return call_ollama(model, messages)
        except (urllib.error.URLError, TimeoutError, KeyError) as e2:
            return f"[呼叫失敗：{e2}]"


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def as_html_block(content: str) -> str:
    return html_escape(content).replace("\n", "<br>")


CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Noto Sans TC", "Microsoft JhengHei", sans-serif;
       max-width: 1100px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }
h1 { font-size: 1.4rem; }
h2.track-title { margin-top: 2.5rem; padding-top: 1rem; border-top: 3px solid #8886; }
.scenario { border: 1px solid #8884; border-radius: 10px; margin-bottom: 1.5rem; padding: 1rem 1.2rem; }
.scenario h2 { margin-top: 0; font-size: 1.1rem; }
.meta { color: #888; font-size: 0.9rem; margin-bottom: 0.8rem; }
.step { border-top: 1px dashed #8884; padding-top: 0.8rem; margin-top: 0.8rem; }
.step h3 { font-size: 0.95rem; margin: 0 0 0.5rem; }
.cols { display: flex; gap: 1rem; flex-wrap: wrap; }
.col { flex: 1; min-width: 260px; background: rgba(128,128,128,0.10); border-radius: 8px; padding: 0.7rem 0.9rem; }
.col .label { font-weight: 600; font-size: 0.85rem; margin-bottom: 0.4rem; opacity: 0.8; }
.ref { background: rgba(255,193,7,0.12); }
.toc { columns: 6; margin-bottom: 1rem; }
.toc a { font-size: 0.85rem; }
"""


def _cols_html(model: str, compare: str | None, model_output: str, compare_output: str | None, ref: str | None) -> str:
    cols = [f'<div class="col"><div class="label">{html_escape(model)}</div>{as_html_block(model_output)}</div>']
    if compare and compare_output is not None:
        cols.append(
            f'<div class="col"><div class="label">{html_escape(compare)}（對照）</div>{as_html_block(compare_output)}</div>'
        )
    if ref:
        cols.append(f'<div class="col ref"><div class="label">訓練資料範例（僅供參考）</div>{as_html_block(ref)}</div>')
    return f'<div class="cols">{"".join(cols)}</div>'


def build_report_a(model: str, compare: str | None, scenarios: list[dict], reference: dict) -> tuple[str, str]:
    """回傳 (toc_html, sections_html)，Track A 維持原本的 STEP1/3 多欄位排版。"""
    toc = "".join(f'<a href="#{sc["id"]}">{sc["id"]}</a> ' for sc in scenarios)

    sections = []
    for i, sc in enumerate(scenarios, 1):
        elder = sc["elder"]
        step_blocks = []
        for step in STEPS:
            print(f"  [A {i}/{len(scenarios)}] {sc['id']} {step} — 呼叫 {model} ...")
            messages = build_inference_prompt(step, sc)
            model_output = call_with_retry(model, messages)

            compare_output = None
            if compare:
                print(f"  [A {i}/{len(scenarios)}] {sc['id']} {step} — 呼叫 {compare} ...")
                compare_output = call_with_retry(compare, messages)

            ref = reference["A"].get((sc["id"], step))
            step_blocks.append(
                f'<div class="step"><h3>{step}</h3>{_cols_html(model, compare, model_output, compare_output, ref)}</div>'
            )

        sections.append(f"""
<div class="scenario" id="{sc['id']}">
  <h2>[{sc['id']}] {elder['name']}（{elder['birth_year']}, {elder['birth_place']}）</h2>
  <div class="meta">職業：{elder['main_occupation']}　今日主題：{elder['today_topic']}
    主題類別：{'、'.join(sc.get('topic_category', []))}<br>
    眼前畫面元素：{'、'.join(sc['scene']['elements'])}</div>
  {''.join(step_blocks)}
</div>""")

    return toc, "".join(sections)


def build_report_generic(
    label: str,
    id_prefix: str,
    model: str,
    compare: str | None,
    scenarios: list[dict],
    build_prompt_fn,
    reference: dict[str, str],
    header_fn,
) -> tuple[str, str]:
    """Track B/C/D 共用的單區塊排版（每個情境只有一組回應，不像 Track A 分三個 STEP）。"""
    ids = [f"{id_prefix}{i}" for i in range(1, len(scenarios) + 1)]
    toc = "".join(f'<a href="#{sid}">{sid}</a> ' for sid in ids)

    sections = []
    for i, (sc, sid) in enumerate(zip(scenarios, ids), 1):
        print(f"  [{label} {i}/{len(scenarios)}] {sid} — 呼叫 {model} ...")
        messages = build_prompt_fn(sc)
        model_output = call_with_retry(model, messages)

        compare_output = None
        if compare:
            print(f"  [{label} {i}/{len(scenarios)}] {sid} — 呼叫 {compare} ...")
            compare_output = call_with_retry(compare, messages)

        ref = reference.get(_reference_key(label, sc))
        block = _cols_html(model, compare, model_output, compare_output, ref)

        sections.append(f"""
<div class="scenario" id="{sid}">
  <h2>[{sid}] {header_fn(sc)}</h2>
  {block}
</div>""")

    return toc, "".join(sections)


def _reference_key(label: str, sc: dict):
    if label == "B":
        return sc.get("context")
    if label == "C":
        return f"track_c_{sc['emotion_tone']}"
    if label == "D":
        return f"track_d_{sc['elder_name']}"
    return None


def _header_b(sc: dict) -> str:
    return f"情境：{html_escape(sc['context'])}"


def _header_c(sc: dict) -> str:
    return (f"情緒基調：{html_escape(sc['emotion_tone'])}（{html_escape(sc['emotion_desc'])}）　"
            f"主題：{html_escape(sc.get('current_topic', ''))}")


def _header_d(sc: dict) -> str:
    return f"{html_escape(sc['elder_name'])}　今日主題：{html_escape(sc['today_topic'])}"


def build_report(
    model: str, compare: str | None, tracks: set[str],
    scenarios_a: list[dict], scenarios_b: list[dict], scenarios_c: list[dict], scenarios_d: list[dict],
) -> str:
    reference = load_reference_chosen()

    toc_parts, body_parts = [], []

    if "A" in tracks and scenarios_a:
        toc, body = build_report_a(model, compare, scenarios_a, reference)
        toc_parts.append(f'<h2 class="track-title">Track A：問題品質（{len(scenarios_a)} 筆）</h2><div class="toc">{toc}</div>')
        body_parts.append(body)

    if "B" in tracks and scenarios_b:
        toc, body = build_report_generic("B", "b", model, compare, scenarios_b, build_prompt_b, reference["B"], _header_b)
        toc_parts.append(f'<h2 class="track-title">Track B：情緒引導（{len(scenarios_b)} 筆）</h2><div class="toc">{toc}</div>')
        body_parts.append(body)

    if "C" in tracks and scenarios_c:
        toc, body = build_report_generic("C", "c", model, compare, scenarios_c, build_prompt_c, reference["C"], _header_c)
        toc_parts.append(f'<h2 class="track-title">Track C：情緒感知承接＋問題（{len(scenarios_c)} 筆）</h2><div class="toc">{toc}</div>')
        body_parts.append(body)

    if "D" in tracks and scenarios_d:
        toc, body = build_report_generic("D", "d", model, compare, scenarios_d, build_prompt_d, reference["D"], _header_d)
        toc_parts.append(f'<h2 class="track-title">Track D：收尾引導（{len(scenarios_d)} 筆）</h2><div class="toc">{toc}</div>')
        body_parts.append(body)

    total = len(scenarios_a) + len(scenarios_b) + len(scenarios_c) + len(scenarios_d)
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>模型回應人工抽樣報告</title>
<style>{CSS}</style></head>
<body>
<h1>模型回應人工抽樣報告</h1>
<p class="meta">測試模型：{html_escape(model)}{f' ／ 對照：{html_escape(compare)}' if compare else ''}　共 {total} 個情境</p>
{''.join(toc_parts)}
{''.join(body_parts)}
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="產生人工抽樣閱讀用的模型回應 HTML 報告（涵蓋 Track A/B/C/D）")
    parser.add_argument("--model", required=True, help="要測試的 Ollama 模型名稱，例如 rememo-llama3")
    parser.add_argument("--compare", help="要並排對照的模型名稱，例如 cwchang/llama-3-taiwan-8b-instruct:Q4_K_M")
    parser.add_argument("--sample", type=int, default=15, help="每個 Track 各自抽樣的情境數（預設 15）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tracks", default="A,B,C,D", help="要產生的軌跡，逗號分隔，例如 A,B（預設全部）")
    parser.add_argument("--output", default="dpo/output/review_samples.html")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    tracks = {t.strip().upper() for t in args.tracks.split(",") if t.strip()}

    available = check_ollama_reachable()
    for m in (args.model, args.compare):
        if m and not any(m == a or a.startswith(m + ":") for a in available):
            print(f"  [提醒] 在 Ollama 模型清單中沒看到「{m}」，請確認名稱是否正確（現有：{', '.join(available)}）")

    def sampled(items: list) -> list:
        items = list(items)
        random.Random(args.seed).shuffle(items)
        return items[: args.sample] if args.sample else items

    scenarios_a: list[dict] = []
    if "A" in tracks:
        with SCENARIOS_FILE.open(encoding="utf-8") as f:
            scenarios_a = sampled(json.load(f))

    scenarios_b = sampled(EMOTIONAL_SCENARIOS) if "B" in tracks else []
    scenarios_c = sampled(TRACK_C_SCENARIOS) if "C" in tracks else []
    scenarios_d = sampled(TRACK_D_SCENARIOS) if "D" in tracks else []

    total = len(scenarios_a) + len(scenarios_b) + len(scenarios_c) + len(scenarios_d)
    print(f"抽樣 Track A:{len(scenarios_a)}／B:{len(scenarios_b)}／C:{len(scenarios_c)}／D:{len(scenarios_d)}"
          f"（共 {total} 個情境），開始生成回應...")

    html = build_report(args.model, args.compare, tracks, scenarios_a, scenarios_b, scenarios_c, scenarios_d)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"報告已產生：{output_path.resolve()}")
    print("直接用瀏覽器打開這個檔案即可閱讀。")


if __name__ == "__main__":
    main()
