#!/usr/bin/env python3
"""
直接從 dpo/data/train.jsonl 抽樣，整理成人工複查用的 HTML 報告。

跟 dpo/generate_review_samples.py 不同——那支要呼叫本機 Ollama 模型即時生成
回應，這支直接讀「已經生成、已經過 filter_data.py 清理」的 train.jsonl 本身，
不需要任何本機模型服務，適合在跑完 collect_data.py + filter_data.py 之後，
訓練前先人工看一輪 chosen/rejected 品質。

執行：
  python dpo/review_train_data.py
  python dpo/review_train_data.py --sample 20 --seed 7
  python dpo/review_train_data.py --tracks A,D
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"

CSS = """
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


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def as_html_block(content: str) -> str:
    return html_escape(content).replace("\n", "<br>")


def load_pairs() -> list[dict]:
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


def group_key(obj: dict) -> str:
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


def build_section(track: str, label: str, groups: list[list[dict]]) -> tuple[str, str]:
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
        header = html_escape("　".join(header_bits))

        rejected_blocks = []
        for obj in group:
            rule = obj["meta"].get("rejection_rule", "")
            rejected = obj["rejected"][0]["content"]
            rejected_blocks.append(f"""
  <div class="col rejected">
    <div class="label">rejected<span class="rule-tag">{html_escape(rule)}</span></div>
    {as_html_block(rejected)}
  </div>""")

        sections.append(f"""
<div class="pair" id="{gid}">
  <h3>[{gid}] {header}</h3>
  <div class="cols">
    <div class="col chosen"><div class="label">chosen</div>{as_html_block(chosen)}</div>
    {"".join(rejected_blocks)}
  </div>
</div>""")

    return (
        f'<h2 class="track-title">{label}（{len(groups)} 組）</h2><div class="toc">{toc}</div>',
        "".join(sections),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="直接從 train.jsonl 抽樣產生人工複查 HTML 報告")
    parser.add_argument("--sample", type=int, default=15, help="每個 track 各抽幾組（預設15）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tracks", default="A,B,C,D")
    parser.add_argument("--output", default="dpo/output/train_data_review.html")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    tracks = {t.strip().upper() for t in args.tracks.split(",") if t.strip()}

    pairs = load_pairs()
    print(f"train.jsonl 共 {len(pairs)} 筆")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for obj in pairs:
        grouped[group_key(obj)].append(obj)

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
        toc, body = build_section(track, labels[track], sampled)
        toc_parts.append(toc)
        body_parts.append(body)

    html = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>train.jsonl 人工複查報告</title>
<style>{CSS}</style></head>
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


if __name__ == "__main__":
    main()
