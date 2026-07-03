import os
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"

from huggingface_hub import snapshot_download
from pathlib import Path

MODEL_ID = "yentinglin/Llama-3-Taiwan-8B-Instruct"
LOCAL_DIR = Path(__file__).parent / "models" / "taiwan-llama"

print(f"Downloading {MODEL_ID} to {LOCAL_DIR} ...")
print("This will take a while (~16 GB). Do not interrupt.")

snapshot_download(
    repo_id=MODEL_ID,
    local_dir=str(LOCAL_DIR),
    ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
)

print(f"Done. Model saved to: {LOCAL_DIR}")
