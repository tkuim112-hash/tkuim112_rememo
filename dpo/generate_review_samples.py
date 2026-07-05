#!/usr/bin/env python3
"""
產生人工抽樣閱讀用的模型回應報告

抽樣 dpo/scenarios.json 裡的情境，組出跟生產環境一致的 prompt
（重用 dpo/evaluate_model.py 的 build_inference_prompt / call_ollama），
送給指定的 Ollama 模型生成 STEP1/STEP2/STEP3 回應，整理成一份 HTML 報告，
方便直接用瀏覽器打開，人工判斷語氣自然度、溫暖度、懷舊療法引導效果。

如果本機有 dpo/data/train.jsonl（訓練資料），會附上同一情境當時
Claude Sonnet 生成的「chosen」範例當對照參考（僅供參考，不是絕對標準）。

使用前：
  Ollama 裡需要有要測試的模型，例如：
    ollama create rememo-llama3 -f dpo/output/Modelfile

執行：
  python dpo/generate_review_samples.py --model rememo-llama3
  python dpo/generate_review_samples.py --model rememo-llama3 --compare cwchang/llama-3-taiwan-8b-instruct:Q4_K_M
  python dpo/generate_review_samples.py --model rememo-llama3 --sample 15 --output dpo/output/review.html

環境變數：
  OLLAMA_HOST（預設 http://localhost:11434）
"""

import argparse
import json
import random
import sys
import urllib.error
from pathlib import Path

from evaluate_model import (
    OLLAMA_HOST,
    SCENARIOS_FILE,
    build_inference_prompt,
    call_ollama,
    check_ollama_reachable,
)

TRAIN_DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"
STEPS = ("STEP1", "STEP2", "STEP3")


def load_reference_chosen() -> dict[tuple[str, str], str]:
    """從本機的 train.jsonl 撈出對應情境當時的 chosen 範例（若檔案不存在則回傳空字典）。"""
    lookup: dict[tuple[str, str], str] = {}
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
            if meta.get("track") != "A":
                continue
            key = (meta.get("scenario_id"), meta.get("step"))
            if key not in lookup:
                lookup[key] = obj["chosen"][0]["content"]
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
.scenario { border: 1px solid #8884; border-radius: 10px; margin-bottom: 1.5rem; padding: 1rem 1.2rem; }
.scenario h2 { margin-top: 0; font-size: 1.1rem; }
.meta { color: #888; font-size: 0.9rem; margin-bottom: 0.8rem; }
.step { border-top: 1px dashed #8884; padding-top: 0.8rem; margin-top: 0.8rem; }
.step h3 { font-size: 0.95rem; margin: 0 0 0.5rem; }
.cols { display: flex; gap: 1rem; flex-wrap: wrap; }
.col { flex: 1; min-width: 260px; background: rgba(128,128,128,0.10); border-radius: 8px; padding: 0.7rem 0.9rem; }
.col .label { font-weight: 600; font-size: 0.85rem; margin-bottom: 0.4rem; opacity: 0.8; }
.ref { background: rgba(255,193,7,0.12); }
.toc { columns: 6; margin-bottom: 2rem; }
.toc a { font-size: 0.85rem; }
"""


def build_report(model: str, compare: str | None, scenarios: list[dict]) -> str:
    reference = load_reference_chosen()

    toc = "".join(f'<a href="#{sc["id"]}">{sc["id"]}</a> ' for sc in scenarios)

    sections = []
    for i, sc in enumerate(scenarios, 1):
        elder = sc["elder"]
        step_blocks = []
        for step in STEPS:
            print(f"  [{i}/{len(scenarios)}] {sc['id']} {step} — 呼叫 {model} ...")
            messages = build_inference_prompt(step, sc)
            model_output = call_with_retry(model, messages)

            cols = [
                f'<div class="col"><div class="label">{html_escape(model)}</div>{as_html_block(model_output)}</div>'
            ]
            if compare:
                print(f"  [{i}/{len(scenarios)}] {sc['id']} {step} — 呼叫 {compare} ...")
                compare_output = call_with_retry(compare, messages)
                cols.append(
                    f'<div class="col"><div class="label">{html_escape(compare)}（對照）</div>{as_html_block(compare_output)}</div>'
                )
            ref = reference.get((sc["id"], step))
            if ref:
                cols.append(
                    f'<div class="col ref"><div class="label">訓練資料範例（僅供參考）</div>{as_html_block(ref)}</div>'
                )

            step_blocks.append(
                f'<div class="step"><h3>{step}</h3><div class="cols">{"".join(cols)}</div></div>'
            )

        sections.append(f"""
<div class="scenario" id="{sc['id']}">
  <h2>[{sc['id']}] {elder['name']}（{elder['birth_year']}, {elder['birth_place']}）</h2>
  <div class="meta">職業：{elder['main_occupation']}　今日主題：{elder['today_topic']}
    主題類別：{'、'.join(sc.get('topic_category', []))}<br>
    眼前畫面元素：{'、'.join(sc['scene']['elements'])}</div>
  {''.join(step_blocks)}
</div>""")

    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>模型回應人工抽樣報告</title>
<style>{CSS}</style></head>
<body>
<h1>模型回應人工抽樣報告</h1>
<p class="meta">測試模型：{html_escape(model)}{f' ／ 對照：{html_escape(compare)}' if compare else ''}　共 {len(scenarios)} 個情境</p>
<div class="toc">{toc}</div>
{''.join(sections)}
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="產生人工抽樣閱讀用的模型回應 HTML 報告")
    parser.add_argument("--model", required=True, help="要測試的 Ollama 模型名稱，例如 rememo-llama3")
    parser.add_argument("--compare", help="要並排對照的模型名稱，例如 cwchang/llama-3-taiwan-8b-instruct:Q4_K_M")
    parser.add_argument("--sample", type=int, default=15, help="抽樣情境數（預設 15）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="dpo/output/review_samples.html")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    available = check_ollama_reachable()
    for m in (args.model, args.compare):
        if m and not any(m == a or a.startswith(m + ":") for a in available):
            print(f"  [提醒] 在 Ollama 模型清單中沒看到「{m}」，請確認名稱是否正確（現有：{', '.join(available)}）")

    with SCENARIOS_FILE.open(encoding="utf-8") as f:
        scenarios = json.load(f)

    random.Random(args.seed).shuffle(scenarios)
    scenarios = scenarios[: args.sample]

    print(f"抽樣 {len(scenarios)} 個情境 × {len(STEPS)} 個 STEP，開始生成回應...")
    html = build_report(args.model, args.compare, scenarios)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"報告已產生：{output_path.resolve()}")
    print("直接用瀏覽器打開這個檔案即可閱讀。")


if __name__ == "__main__":
    main()
