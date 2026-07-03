import os
os.environ["CUDA_LAUNCH_BLOCKING"] = "1"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"

import traceback
from pathlib import Path

MODEL_PATH = str(Path(__file__).parent / "models" / "taiwan-llama")

print("Testing model load...")

try:
    from unsloth import FastLanguageModel
    print("Loading model...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_PATH,
        max_seq_length=512,
        load_in_4bit=True,
        dtype=None,
    )
    print("SUCCESS: Model loaded!")
    import torch
    print(f"VRAM used: {torch.cuda.memory_allocated()/1024**3:.2f} GB")

except Exception as e:
    print(f"\nERROR TYPE: {type(e).__name__}")
    print(f"ERROR: {e}")
    traceback.print_exc()
