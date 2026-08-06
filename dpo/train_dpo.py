#!/usr/bin/env python3
import os
os.environ["CUDA_LAUNCH_BLOCKING"] = "1"
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:512"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"  # 停用 Rust 加速下載，避免記憶體分配失敗

"""
DPO 微調腳本（Unsloth 版）

用 dpo/data/train.jsonl 微調 yentinglin/Llama-3-Taiwan-8B-Instruct，
使模型學會懷舊療法問題設計規則與情緒引導。

微調完成後需要轉換成 GGUF 格式才能在 Ollama 上執行。

執行需求：
  - GPU（VRAM >= 8GB）
  - pip install unsloth trl transformers datasets torch

執行：
  python dpo/train_dpo.py
"""

import json
import random
import sys
from pathlib import Path

from datasets import Dataset
from unsloth import FastLanguageModel, is_bfloat16_supported
from trl import DPOConfig, DPOTrainer

from data_quality import Validator

# ─── 設定 ───────────────────────────────────────────────────────────────────

BASE_MODEL = str(Path(__file__).parent / "models" / "taiwan-llama")
DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"
OUTPUT_DIR = Path(__file__).parent / "output"

MAX_SEQ_LENGTH = 512
DPO_BETA = 0.1
LEARNING_RATE = 5e-7
NUM_EPOCHS = 3
BATCH_SIZE = 1
GRAD_ACCUM = 8

# LoRA：r=8/僅attn 在 eval_rewards/accuracies 於 epoch2 就飽和在 98.66%，
# epoch3 幾乎只把 margin 從 0.32 推到 0.37（已分開的 pair 被推更開，
# 邊際價值低，有輕微過擬合風險）。這裡把容量調大、並納入 MLP 層——
# 語氣/情緒暫存這類「軟性」判斷通常更依賴 MLP，而不只是注意力層。
# 建議跟舊的 r=8 attn-only 版本用同一套 data_quality.py 的 evaluate 子命令對照比較，
# 不要預設容量越大越好（訓練資料不到 3000 筆，也可能撐不住太大的 r）。
LORA_R = 16
LORA_ALPHA = 32
LORA_TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


# ─── 資料載入 ────────────────────────────────────────────────────────────────

def load_dataset_from_jsonl(path: Path) -> Dataset:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            records.append({
                "prompt": rec["prompt"],
                "chosen": rec["chosen"],
                "rejected": rec["rejected"],
                # 只留 track/scenario_id 字串（不是整個 meta dict），給下面的分層
                # 切分用，切分完後會移除，不會進到 DPOTrainer。
                "track": rec["meta"]["track"],
                "scenario_id": rec["meta"]["scenario_id"],
            })

    print(f"載入 {len(records)} 筆訓練對")
    return Dataset.from_list(records)


def stratified_split(dataset: Dataset, test_size: float, seed: int) -> tuple[Dataset, Dataset]:
    """
    依 track 分層切 train/eval，且同一個 scenario_id 的所有 pair 整組分到同一邊。

    原本用 dataset.train_test_split() 隨機切分，會直接複製訓練集本身的
    Track 比例（A 佔約 80%），導致 eval_loss/reward accuracy 這類總指標
    幾乎只反映 Track A（問題品質）的表現，看不出 Track B（情緒引導）、
    C（承接+追問）、D（收尾）到底有沒有學到東西——而 B/D 恰好是對長者
    最需要謹慎的兩個環節。這裡改成每個 track 各自抽 test_size 比例，
    確保 eval 集合裡四條軌跡的比例跟訓練集一致，不會被 A 稀釋掉。

    2026-08 稽核發現：光是按 track 分層還不夠——B/D 這兩軌每個 scenario_id
    平均都被拆成 10 幾筆 pair（同一個 chosen，配不同 rejection_rule 的
    rejected），舊版切分是直接對「pair 索引」洗牌切分，導致同一個
    scenario_id 的不同 pair 會同時出現在 train 跟 eval 裡——模型在訓練時
    已經看過這個情境的 chosen 文字（配另一種 rejected），eval 時只是認
    另一個沒看過的 rejected 變體，測的不是「有沒有見過新情境」，是「記不
    記得這個情境」，reward accuracy 會被高估。改成先依 scenario_id 分組，
    整組一起分進 train 或 eval，確保 eval 裡的每個情境訓練時真的沒看過。
    """
    by_track: dict[str, list[int]] = {}
    for i, track in enumerate(dataset["track"]):
        by_track.setdefault(track, []).append(i)

    scenario_ids = dataset["scenario_id"]

    rng = random.Random(seed)
    train_idx: list[int] = []
    test_idx: list[int] = []
    for track, idxs in sorted(by_track.items()):
        by_scenario: dict[str, list[int]] = {}
        for i in idxs:
            by_scenario.setdefault(scenario_ids[i], []).append(i)

        scenario_keys = list(by_scenario.keys())
        rng.shuffle(scenario_keys)
        n_test_scenarios = max(1, round(len(scenario_keys) * test_size))
        test_scenarios = set(scenario_keys[:n_test_scenarios])

        track_train, track_test = [], []
        for sid, sid_idxs in by_scenario.items():
            (track_test if sid in test_scenarios else track_train).extend(sid_idxs)

        train_idx.extend(track_train)
        test_idx.extend(track_test)
        print(f"    track={track}: 共 {len(idxs)} 筆／{len(scenario_keys)} 個情境 → "
              f"train {len(track_train)} 筆／{len(scenario_keys) - n_test_scenarios} 個情境"
              f"　eval {len(track_test)} 筆／{n_test_scenarios} 個情境")

    return dataset.select(train_idx), dataset.select(test_idx)


# ─── 模型載入（Unsloth 4-bit，比 bitsandbytes 更省 VRAM） ────────────────────

def load_model_and_tokenizer():
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ_LENGTH,
        load_in_4bit=True,
        dtype=None,  # 自動偵測：支援 bfloat16 就用，否則用 float16
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        target_modules=LORA_TARGET_MODULES,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",  # Unsloth 優化版 gradient checkpointing
        random_state=42,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    return model, tokenizer


# ─── 訓練前驗證關卡 ──────────────────────────────────────────────────────────

def _run_validation_gate() -> None:
    """
    訓練前強制跑一次 dpo/data_quality.py（validate 子命令）的檢查邏輯，有
    critical failure 就中止。

    2026-07 稽核發現：train_dpo.py 原本只檢查 train.jsonl 存不存在，完全不管
    裡面的資料有沒有問題（例如 chosen==rejected、chosen 本身違規、rejected
    沒有真的違反該筆記錄的規則）。這套檢查雖然存在，但沒有任何東西
    強制要求「訓練前一定要先跑過」，全靠使用者記得手動執行——已知至少一次
    train.jsonl 在有 critical failure 的狀態下仍被拿去訓練（見
    dpo/data/validate_report.txt 的歷史記錄）。這裡直接在訓練腳本裡內建同一套
    檢查，沒通過就不給訓練，避免同樣的事再發生一次。
    """
    v = Validator()
    with DATA_FILE.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                v.check(json.loads(raw), lineno)
            except json.JSONDecodeError as e:
                print(f"Line {lineno} JSON 解析失敗：{e}")

    passed = v.report()
    if not passed:
        print(
            "\n訓練已中止：train.jsonl 有上述關鍵檢查失敗項目，請先修正"
            "（或重新執行 python dpo/collect_data.py 產生資料）再訓練。\n"
            "若你已確認這些失敗可以接受，暫時可自行修改 train_dpo.py 略過此檢查，"
            "但不建議在關鍵檢查未通過的情況下訓練。"
        )
        sys.exit(1)


# ─── 訓練 ────────────────────────────────────────────────────────────────────

def main() -> None:
    if not Path(BASE_MODEL).exists():
        raise FileNotFoundError(
            f"找不到本地模型：{BASE_MODEL}\n"
            "請先執行 python dpo/download_model.py 下載模型。"
        )

    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"找不到訓練資料：{DATA_FILE}\n"
            "請先執行 python dpo/collect_data.py 生成資料。"
        )

    print("訓練前先驗證 train.jsonl（等同執行一次 python dpo/data_quality.py validate）...")
    _run_validation_gate()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("載入訓練資料...")
    dataset = load_dataset_from_jsonl(DATA_FILE)

    print("依 track 分層、並依 scenario_id 整組切分 train/eval...")
    train_dataset, eval_dataset = stratified_split(dataset, test_size=0.1, seed=42)
    train_dataset = train_dataset.remove_columns(["track", "scenario_id"])
    eval_dataset = eval_dataset.remove_columns(["track", "scenario_id"])
    print(f"訓練集：{len(train_dataset)} 筆，驗證集：{len(eval_dataset)} 筆")

    print("載入基底模型（Unsloth 4-bit 量化）...")
    model, tokenizer = load_model_and_tokenizer()

    training_args = DPOConfig(
        output_dir=str(OUTPUT_DIR / "checkpoints"),
        num_train_epochs=NUM_EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LEARNING_RATE,
        beta=DPO_BETA,
        lr_scheduler_type="cosine",
        warmup_steps=50,
        bf16=is_bfloat16_supported(),
        fp16=not is_bfloat16_supported(),
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        # 之前固定用最後一個 epoch 的 checkpoint；但 reward accuracy 在 epoch2
        # 就已經飽和(98.66%)，epoch3 只把 margin 拉大，不代表真的學到更多東西，
        # 甚至可能開始輕微過擬合。改成自動挑 eval_loss 最低的 checkpoint。
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        save_total_limit=3,
        report_to="none",
        precompute_ref_log_probs=False,
        max_length=MAX_SEQ_LENGTH,
        max_prompt_length=384,
        dataloader_num_workers=0,
    )

    trainer = DPOTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        processing_class=tokenizer,
    )

    print("開始 DPO 訓練...")
    trainer.train()

    # 儲存 LoRA adapter
    adapter_path = OUTPUT_DIR / "lora_adapter"
    trainer.save_model(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))
    print(f"LoRA adapter 已儲存到：{adapter_path}")

    print("\n" + "=" * 60)
    print("訓練完成！後續步驟：")
    print("1. 合併 LoRA 到基底模型（見下方指令）")
    print("2. 轉換成 GGUF 格式並量化")
    print("3. 放入 Ollama 的 models 目錄")
    print("=" * 60)
    print("""
# Step 1：合併 LoRA adapter
python -c "
from peft import AutoPeftModelForCausalLM
from transformers import AutoTokenizer
model = AutoPeftModelForCausalLM.from_pretrained('dpo/output/lora_adapter')
merged = model.merge_and_unload()
merged.save_pretrained('dpo/output/merged_model')
AutoTokenizer.from_pretrained('dpo/output/lora_adapter').save_pretrained('dpo/output/merged_model')
"

# Step 2：轉換 GGUF（需要先 clone llama.cpp）
# git clone https://github.com/ggerganov/llama.cpp
# pip install -r llama.cpp/requirements.txt
python llama.cpp/convert_hf_to_gguf.py dpo/output/merged_model \\
    --outfile dpo/output/rememo-llama3-8b.gguf \\
    --outtype q4_K_M

# Step 3：建立 Modelfile 並匯入 Ollama
# 在 dpo/output/ 建立 Modelfile：
# FROM ./rememo-llama3-8b.gguf
ollama create rememo-llama3 -f dpo/output/Modelfile
ollama run rememo-llama3
""")


if __name__ == "__main__":
    main()