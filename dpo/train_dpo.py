#!/usr/bin/env python3
"""
DPO 微調腳本

用 dpo/data/train.jsonl 微調 yentinglin/Llama-3-Taiwan-8B-Instruct，
使模型學會懷舊療法問題設計規則與情緒引導。

微調完成後需要轉換成 GGUF 格式才能在 Ollama 上執行。

執行需求：
  - GPU（VRAM >= 8GB，已針對 8GB 優化：precompute_ref_log_probs + gradient_checkpointing）
  - pip install trl transformers datasets torch bitsandbytes peft

執行：
  python dpo/train_dpo.py
"""

import json
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import DPOConfig, DPOTrainer

# ─── 設定 ───────────────────────────────────────────────────────────────────

BASE_MODEL = "yentinglin/Llama-3-Taiwan-8B-Instruct"
DATA_FILE = Path(__file__).parent / "data" / "train.jsonl"
OUTPUT_DIR = Path(__file__).parent / "output"

# DPO 超參數（針對 8B 模型在有限 VRAM 下的保守設定）
DPO_BETA = 0.1          # 越高越保守，越低越激進；0.1 是常見起點
LEARNING_RATE = 5e-7
NUM_EPOCHS = 3
BATCH_SIZE = 1
GRAD_ACCUM = 8          # 等效 batch size = 8，節省 VRAM


# ─── 資料載入 ────────────────────────────────────────────────────────────────

def load_dataset_from_jsonl(path: Path) -> Dataset:
    """
    從 JSONL 載入資料，並轉換成 DPOTrainer 期望的格式。

    DPOTrainer 支援 messages 格式的 prompt/chosen/rejected：
      prompt:   list[dict]  — system + user messages
      chosen:   list[dict]  — [{"role": "assistant", "content": "..."}]
      rejected: list[dict]  — [{"role": "assistant", "content": "..."}]
    """
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            records.append({
                "prompt": rec["prompt"],
                "chosen": rec["chosen"],
                "rejected": rec["rejected"],
            })

    print(f"載入 {len(records)} 筆訓練對")
    return Dataset.from_list(records)


# ─── 模型載入（4-bit 量化，節省 VRAM） ───────────────────────────────────────

def load_model_and_tokenizer():
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.bfloat16,
    )

    return model, tokenizer


# ─── LoRA 設定（QLoRA，減少訓練參數量） ──────────────────────────────────────

def get_lora_config() -> LoraConfig:
    return LoraConfig(
        r=8,
        lora_alpha=16,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )


# ─── 訓練 ────────────────────────────────────────────────────────────────────

def main() -> None:
    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"找不到訓練資料：{DATA_FILE}\n"
            "請先執行 python dpo/collect_data.py 生成資料。"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("載入訓練資料...")
    dataset = load_dataset_from_jsonl(DATA_FILE)

    # 分割 90% 訓練、10% 驗證
    split = dataset.train_test_split(test_size=0.1, seed=42)
    train_dataset = split["train"]
    eval_dataset = split["test"]
    print(f"訓練集：{len(train_dataset)} 筆，驗證集：{len(eval_dataset)} 筆")

    print("載入基底模型（4-bit 量化）...")
    model, tokenizer = load_model_and_tokenizer()
    lora_config = get_lora_config()

    training_args = DPOConfig(
        output_dir=str(OUTPUT_DIR / "checkpoints"),
        num_train_epochs=NUM_EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LEARNING_RATE,
        beta=DPO_BETA,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        bf16=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        report_to="none",
        # 8GB VRAM 優化：預算完參考模型後移出 VRAM，訓練時只需一個模型
        precompute_ref_log_probs=True,
        # 資料實際長度 ~350-450 tokens，512 足夠
        max_length=512,
        max_prompt_length=384,
    )

    trainer = DPOTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        processing_class=tokenizer,
        peft_config=lora_config,
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
