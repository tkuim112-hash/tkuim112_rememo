import argparse
import os
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"

from huggingface_hub import snapshot_download
from pathlib import Path

MODEL_ID = "yentinglin/Llama-3-Taiwan-8B-Instruct"
DEFAULT_LOCAL_DIR = Path(__file__).parent / "models" / "taiwan-llama"

parser = argparse.ArgumentParser()
parser.add_argument(
    "--local-dir",
    default=str(DEFAULT_LOCAL_DIR),
    help="模型下載目的地（例如 Colab 上先下載到本機硬碟 /content/... 再搬進 Drive，"
         "避免直接寫入 Drive 掛載路徑拖慢下載速度）",
)
args = parser.parse_args()
LOCAL_DIR = Path(args.local_dir)

print(f"Downloading {MODEL_ID} to {LOCAL_DIR} ...")
print("This will take a while (~16 GB). Do not interrupt.")

snapshot_download(
    repo_id=MODEL_ID,
    local_dir=str(LOCAL_DIR),
    ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
)

print(f"Done. Model saved to: {LOCAL_DIR}")
