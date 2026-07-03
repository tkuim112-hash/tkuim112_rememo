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
from pathlib import Path

from datasets import Dataset
from unsloth import FastLanguageModel, is_bfloat16_supported
from trl import DPOConfig, DPOTrainer

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
            })

    print(f"載入 {len(records)} 筆訓練對")
    return Dataset.from_list(records)


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
        r=8,
        lora_alpha=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",  # Unsloth 優化版 gradient checkpointing
        random_state=42,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    return model, tokenizer


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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("載入訓練資料...")
    dataset = load_dataset_from_jsonl(DATA_FILE)

    split = dataset.train_test_split(test_size=0.1, seed=42)
    train_dataset = split["train"]
    eval_dataset = split["test"]
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
        load_best_model_at_end=False,
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